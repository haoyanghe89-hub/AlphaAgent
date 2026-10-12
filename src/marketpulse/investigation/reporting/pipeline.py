"""Phase 5 report pipeline orchestration.

Chain: persisted Phase 4.3 state -> ReportInputAssembler -> immutable snapshot
-> bounded WriterProjection -> structured Writer draft -> CitationFactory ->
CitationValidator -> ReportValidator -> (release policy injected upstream) ->
atomic persistence of report version, sections, citations, and findings.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from marketpulse.investigation.domain.base import DomainModel, NonEmptyText
from marketpulse.investigation.domain.enums import (
    ConflictStatus,
    ReportReviewStatus,
    ReportType,
    ValidationStatus,
)
from marketpulse.investigation.domain.reports import Report, ReportProjection, ReportSection
from marketpulse.investigation.domain.runtime import Investigation, InvestigationRun
from marketpulse.investigation.persistence.models import ReportRow, SourceSnapshotRow
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.reporting.assembler import ReportInputAssembler
from marketpulse.investigation.reporting.citations import CitationFactory
from marketpulse.investigation.reporting.models import (
    Citation,
    ReportInputSemanticPayload,
    ReportInputSnapshot,
    ReportValidationFinding,
)
from marketpulse.investigation.reporting.release import (
    RELEASE_POLICY_VERSION,
    ReleasePolicyInput,
    ReportReleasePolicy,
)
from marketpulse.investigation.reporting.renderer import (
    compute_citation_set_hash,
    compute_report_hash,
    render_markdown,
)
from marketpulse.investigation.reporting.validation import CitationValidator, ReportValidator
from marketpulse.investigation.reporting.writer import (
    ContentClass,
    DraftSection,
    NarrativeUnit,
    ReportDraft,
    SectionStatus,
    Writer,
    WriterProjection,
)


class PipelineResult(DomainModel):
    report: Report
    snapshot: ReportInputSnapshot
    draft: ReportDraft
    citations: tuple[Citation, ...]
    findings: tuple[ReportValidationFinding, ...]
    markdown: NonEmptyText

    @property
    def hard_finding_count(self) -> int:
        return sum(1 for finding in self.findings if str(finding.severity) == "HARD")


def _compact_draft(draft: ReportDraft) -> ReportDraft:
    """Remove empty/non-applicable chapters before hashing, validation and persistence."""
    return draft.model_copy(
        update={
            "sections": tuple(
                section
                for section in draft.sections
                if section.status is SectionStatus.CONTENT and section.units
            )
        }
    )


def _public_stop_reason(reason: str) -> str | None:
    """Keep a useful Chinese explanation in the body without exposing an error code."""
    match = re.match(r"^[A-Z][A-Z0-9_]{3,}[：:]\s*(.+)$", reason)
    if match and any("\u4e00" <= char <= "\u9fff" for char in match[1]):
        return match[1]
    if any("\u4e00" <= char <= "\u9fff" for char in reason) and not re.search(
        r"(?:Error|Exception|Traceback)", reason
    ):
        return reason
    return None


class ReportPipeline:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        repository: InvestigationRepository,
        writer: Writer,
        quant_service=None,
    ) -> None:
        self._sessions = sessions
        self._repository = repository
        self._writer = writer
        self._quant_service = quant_service
        self._assembler = ReportInputAssembler(sessions, repository)
        self._citations = CitationFactory(repository, quant_service)
        self._citation_validator = CitationValidator(repository, quant_service)
        self._report_validator = ReportValidator()

    async def generate(
        self,
        *,
        run_id: str,
        report_type: ReportType,
        now: datetime,
        finalization_reason: str | None = None,
        completion_disclosure: bool = False,
    ) -> PipelineResult:
        snapshot = self._assembler.assemble(
            run_id=run_id,
            snapshot_id=f"SNAP-{uuid.uuid4().hex[:12]}",
            assembled_at=now,
            report_type=report_type,
        )
        with self._sessions() as session:
            run = self._repository.get_in_session(session, InvestigationRun, run_id)
            investigation = self._repository.get_in_session(
                session, Investigation, run.investigation_id
            )
        projection = WriterProjection.build(
            snapshot,
            report_type,
            investigation_title=investigation.title,
            investigation_goal=investigation.investigation_goal,
        )
        material = None
        if snapshot.semantic_payload.quantitative_material is not None:
            from marketpulse.quant.reporting import QuantReportMaterial

            material = QuantReportMaterial.model_validate_json(
                snapshot.semantic_payload.quantitative_material
            )
            # Numerical material is appended by the trusted deterministic template,
            # never handed to a model writer for paraphrasing or recalculation.
            projection = projection.model_copy(
                update={
                    "claims": tuple(
                        c for c in projection.claims if not c.stable_key.startswith("qclaim_")
                    )
                }
            )
        if completion_disclosure:
            from marketpulse.investigation.feedback.store import FeedbackStore

            state = FeedbackStore(self._sessions, self._repository).state(run_id)
            supported = sum(
                c.validation_status in (ValidationStatus.VERIFIED, ValidationStatus.PROBABLE)
                for c in state.claims
            )
            open_gaps = sum(str(g.status) in {"OPEN", "IN_PROGRESS"} for g in state.gaps)
            unresolved = any(str(c.status) != "RESOLVED" for c in state.conflicts)
            supported_tasks = {
                c.research_task_id
                for c in state.claims
                if c.validation_status in (ValidationStatus.VERIFIED, ValidationStatus.PROBABLE)
                and c.research_task_id
            }
            covered_questions = {
                t.target_question_id
                for t in state.tasks
                if t.task_id in supported_tasks and t.target_question_id
            }
            question_ids = {q.question_id for q in investigation.questions}
            coverage_complete = bool(question_ids) and question_ids <= covered_questions
            level = "资料不足"
            if supported:
                level = "部分完整"
                if supported / len(state.claims) >= 0.8 and open_gaps <= 2:
                    level = "较完整"
                if (
                    supported == len(state.claims)
                    and not open_gaps
                    and not finalization_reason
                    and not unresolved
                    and coverage_complete
                ):
                    level = "完整"
            projection = projection.model_copy(
                update={
                    "completion_level": level,
                    "stop_reason": finalization_reason,
                    "unassessed_claims": len(state.claims) - len(projection.claims),
                    "source_statistics": projection.source_statistics.model_copy(
                        update={
                            "total_sources": len({s.source_id for s in state.snapshots}),
                            "evidence_count": len(state.evidence),
                        }
                    ),
                }
            )
        draft = await self._writer.draft(projection)
        if snapshot.semantic_payload.comparison_material is not None:
            import json

            from marketpulse.quant.comparison import append_draft

            draft = append_draft(draft, json.loads(snapshot.semantic_payload.comparison_material))
        if material is not None:
            from marketpulse.investigation.reporting.chinese_writer import (
                append_chinese_quant_block,
            )

            draft = append_chinese_quant_block(draft, material)
            # Frozen-input integrity checks and report persistence must not block
            # the event loop. Numerical recomputation still runs in the worker.
            return await asyncio.to_thread(
                self._finalize,
                snapshot=snapshot,
                draft=draft,
                investigation_id=investigation.investigation_id,
                run_id=run_id,
                now=now,
            )
        return self._finalize(
            snapshot=snapshot,
            draft=draft,
            investigation_id=investigation.investigation_id,
            run_id=run_id,
            now=now,
        )

    async def minimal(self, *, run_id: str, now: datetime, reason: str) -> PipelineResult:
        """Fail closed on broken evidence: disclose only execution metadata, never facts.

        Existing material remains untouched. This separate empty factual snapshot
        cannot bypass citation/validation gates and always yields a restricted report.
        """
        run = self._repository.get(InvestigationRun, run_id)
        investigation = self._repository.get(Investigation, run.investigation_id)
        snapshot = ReportInputSnapshot.build(
            snapshot_id=f"SNAP-{uuid.uuid4().hex[:12]}",
            investigation_id=run.investigation_id,
            run_id=run_id,
            run_mode=run.mode,
            assembled_at=now,
            semantic_payload=ReportInputSemanticPayload(
                schema_version="taskj-minimal-finalization-v1",
                validation_policy_version="none",
                investigation_key=run.investigation_id,
                terminal_run_status=str(run.status),
                questions=tuple(q.text for q in investigation.questions),
                claims=(),
                evidence=(),
                limitations=(
                    "报告完整性检查未通过，既有材料和记录已保留，不能据此确认事实。",
                    reason,
                ),
            ),
        )
        # Content-addressed snapshots may already exist after a partial persistence retry.
        from marketpulse.investigation.persistence.models import ReportInputSnapshotRow

        with self._sessions.begin() as session:
            if (
                session.scalar(
                    select(ReportInputSnapshotRow.snapshot_id).where(
                        ReportInputSnapshotRow.snapshot_hash == snapshot.snapshot_hash
                    )
                )
                is None
            ):
                self._repository.add_in_session(session, snapshot)
        with self._sessions() as session:
            source_count = session.scalar(
                select(func.count(func.distinct(SourceSnapshotRow.source_id))).where(
                    SourceSnapshotRow.run_id == run_id
                )
            )
        # Deliberately independent of both normal writers and their projection.
        # A writer programming error must not also break the emergency template.
        public_reason = _public_stop_reason(reason)
        content = {
            "EXECUTIVE_STATUS": (
                "完整度为“资料不足”；目前不能作出已验证结论，也没有可供发布的很可能成立结论。",
                "当前只能确认调查已经收尾并保留既有记录，尚不能确认调查问题中的实体结论。",
            ),
            "EVIDENCE_BASE": (
                f"已归档 {source_count} 个来源，但报告完整性检查未通过，"
                "这些材料未在本最简报告中作为已确认事实。"
                if source_count
                else "未能获取到可用资料，当前没有可用于形成确认结论的证据基础。",
            ),
            "BLOCKING_GAPS_AND_LIMITATIONS": (
                "局限：报告完整性检查未通过"
                + (f"，本轮收尾原因是“{public_reason}”" if public_reason else "")
                + "。后续建议：检查服务与存储，修复后核查归档材料，并补充一手原文、"
                "独立来源及缺失限定条件。",
            ),
            "RESEARCH_APPENDIX": (
                f"调查目标：{investigation.investigation_goal}",
                "调查问题："
                + (
                    "；".join(q.text for q in investigation.questions)
                    or "本次未记录明确的调查问题。"
                ),
            ),
        }
        if reason and (not public_reason or re.match(r"^[A-Z][A-Z0-9_]{3,}[：:]", reason)):
            content["TECHNICAL_APPENDIX"] = (reason,)
        draft = ReportDraft(
            report_type=ReportType.INVESTIGATION_STATUS,
            schema_version="taskk-emergency-writer-v2",
            sections=tuple(
                DraftSection(
                    section_key=key,
                    status=SectionStatus.CONTENT,
                    units=tuple(
                        NarrativeUnit(
                            unit_key=f"emergency-{key.lower()}-{index}",
                            section_key=key,
                            text=text,
                            content_class=ContentClass.GOVERNANCE_DISCLOSURE,
                        )
                        for index, text in enumerate(values, 1)
                    ),
                )
                for key, values in content.items()
            ),
        )
        return self._finalize(
            snapshot=snapshot,
            draft=draft,
            investigation_id=run.investigation_id,
            run_id=run_id,
            now=now,
        )

    def _finalize(
        self,
        *,
        snapshot: ReportInputSnapshot,
        draft: ReportDraft,
        investigation_id: str,
        run_id: str,
        now: datetime,
    ) -> PipelineResult:
        draft = _compact_draft(draft)
        report_id = f"RPT-{uuid.uuid4().hex[:12]}"
        report_hash = compute_report_hash(draft)
        with self._sessions() as session:
            version = (
                session.scalar(
                    select(func.count())
                    .select_from(ReportRow)
                    .where(ReportRow.investigation_id == investigation_id)
                )
                or 0
            ) + 1

        placeholder = Report(
            report_id=report_id,
            investigation_id=investigation_id,
            run_id=run_id,
            version=version,
            report_type=draft.report_type,
            report_input_snapshot_hash=snapshot.snapshot_hash,
            report_hash=report_hash,
            claim_set_hash=snapshot.claim_set_hash,
            citation_set_hash="0" * 64,
            release_policy_version=RELEASE_POLICY_VERSION,
            created_at=now,
            **(
                {"schema_version": "quant-report-v2"}
                if snapshot.semantic_payload.quantitative_material is not None
                else {}
            ),
        )

        with self._sessions() as session:
            citations, citation_findings = self._citations.build(
                session, snapshot=snapshot, draft=draft, report=placeholder, now=now
            )
        citation_set_hash = compute_citation_set_hash(citations)
        report = placeholder.model_copy(update={"citation_set_hash": citation_set_hash})

        with self._sessions() as session:
            citation_findings += self._citation_validator.validate(
                session, citations=citations, snapshot=snapshot, report=report, now=now
            )
        report_findings = self._report_validator.validate(
            draft=draft, snapshot=snapshot, citations=citations, report=report, now=now
        )
        findings = [*citation_findings, *report_findings]

        key_section_keys = {
            "EXECUTIVE_SUMMARY",
            "EXECUTIVE_STATUS",
            "CONCLUSIONS_AND_NEXT_STEPS",
        }
        claim_status_by_key = {
            claim.stable_key: claim.validation_status for claim in snapshot.semantic_payload.claims
        }
        probable_or_disputed_in_key_sections = any(
            claim_status_by_key.get(ref) in (ValidationStatus.PROBABLE, ValidationStatus.DISPUTED)
            for section in draft.sections
            if section.section_key in key_section_keys
            for unit in section.units
            for ref in unit.claim_refs
        )
        unresolved_conflicts = tuple(
            conflict.stable_key
            for conflict in snapshot.semantic_payload.conflicts
            if conflict.status is not ConflictStatus.RESOLVED
        )
        evaluation = ReportReleasePolicy().evaluate(
            ReleasePolicyInput(
                report_id=report.report_id,
                report_type=report.report_type,
                report_hash=report.report_hash,
                claim_set_hash=report.claim_set_hash,
                citation_set_hash=report.citation_set_hash,
                findings=tuple(findings),
                claims=snapshot.semantic_payload.claims,
                probable_or_disputed_in_key_sections=probable_or_disputed_in_key_sections,
                unresolved_nonblocking_conflicts=unresolved_conflicts,
            ),
            evaluation_id=f"EVA-{report.report_id}",
            created_at=now,
        )

        sections = self._sections(draft, snapshot, report, now)
        with self._sessions.begin() as session:
            self._repository.add_in_session(session, report)
            for section in sections:
                self._repository.add_in_session(session, section)
            for citation in citations:
                if citation.schema_version == "computation-citation-v2":
                    from marketpulse.quant.storage.models import QuantCitationRow

                    session.add(
                        QuantCitationRow(
                            citation_id=citation.citation_id,
                            report_id=report_id,
                            payload=citation.model_dump(mode="json"),
                        )
                    )
                else:
                    self._repository.add_in_session(session, citation)
            for finding in findings:
                self._repository.add_in_session(session, finding)
            self._repository.add_in_session(session, evaluation)
            self._repository.add_in_session(
                session,
                ReportProjection(
                    report_id=report.report_id,
                    investigation_id=investigation_id,
                    review_status=evaluation.review_status,
                    release_status=evaluation.release_status,
                    latest_evaluation_id=evaluation.evaluation_id,
                    updated_at=now,
                ),
            )

        if evaluation.review_status is ReportReviewStatus.PENDING:
            from marketpulse.investigation.review.service import ReportReviewService

            ReportReviewService(self._sessions).open_review_request(
                report_id=report.report_id,
                validator_version="report-validator-v1",
                trigger_reason=", ".join(
                    str(trigger)
                    for trigger in evaluation.basis.get("governance_triggers", [])  # type: ignore[union-attr]
                )
                or "governance review required",
                now=now,
                expires_at=now + timedelta(days=7),
            )

        return PipelineResult(
            report=report,
            snapshot=snapshot,
            draft=draft,
            citations=tuple(citations),
            findings=tuple(findings),
            markdown=render_markdown(draft, citations),
        )

    def _sections(
        self,
        draft: ReportDraft,
        snapshot: ReportInputSnapshot,
        report: Report,
        now: datetime,
    ) -> list[ReportSection]:
        sections: list[ReportSection] = []
        for order, section in enumerate(draft.sections):
            claim_ids: list[str] = []
            for unit in section.units:
                for claim_key in unit.claim_refs:
                    claim_id = snapshot.runtime_references.claim_ids.get(claim_key)
                    if claim_id is not None and claim_id not in claim_ids:
                        claim_ids.append(claim_id)
            sections.append(
                ReportSection(
                    section_id=f"RS-{report.report_id}-{order:02d}",
                    report_id=report.report_id,
                    section_type=section.section_key,
                    order_index=order,
                    structured_content={
                        "status": str(section.status),
                        "presentation": (
                            "APPENDIX"
                            if section.section_key in {"RESEARCH_APPENDIX", "TECHNICAL_APPENDIX"}
                            else "BODY"
                        ),
                        "collapsed": section.section_key
                        in {"RESEARCH_APPENDIX", "TECHNICAL_APPENDIX"},
                        "units": [
                            {
                                "unit_key": unit.unit_key,
                                "text": unit.text,
                                "content_class": str(unit.content_class),
                                "claim_refs": list(unit.claim_refs),
                            }
                            for unit in section.units
                        ],
                    },
                    claim_ids=tuple(claim_ids),
                    created_at=now,
                )
            )
        return sections
