"""ReportInputAssembler: the only Phase 5 reader of Phase 4.3 persisted state.

Assembly is fail closed: any stale projection, missing reference, or
integrity mismatch aborts with ``AssemblyError`` and no snapshot is persisted.
The emitted snapshot is content-addressed — assembling the same semantic state
repeatedly (live or replay) yields identical semantic hashes, and persistence
is idempotent on ``snapshot_hash``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from marketpulse.investigation.domain.base import DomainModel
from marketpulse.investigation.domain.claims import (
    Claim,
    ClaimEvidenceRelation,
    ConflictSet,
    ResearchGap,
    TimelineEvent,
    ValidationResult,
)
from marketpulse.investigation.domain.enums import (
    ConflictResolutionStatus,
    ConflictStatus,
    GapStatus,
    ReportType,
    RunStatus,
    ValidationStatus,
)
from marketpulse.investigation.domain.runtime import Investigation, InvestigationRun
from marketpulse.investigation.domain.sources import (
    DocumentArtifact,
    Evidence,
    Source,
    SourceSnapshot,
)
from marketpulse.investigation.persistence.models import (
    ClaimEvidenceRelationRow,
    ClaimRow,
    ConflictSetRow,
    ReportInputSnapshotRow,
    ResearchGapRow,
    SourceFamilyMemberRow,
    SourceFamilyRow,
    SourceSnapshotRow,
    TimelineEventRow,
    ValidationResultRow,
)
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.reporting.hashing import canonical_hash
from marketpulse.investigation.reporting.models import (
    ReportInputSemanticPayload,
    ReportInputSnapshot,
    SnapshotClaim,
    SnapshotConflict,
    SnapshotEvidence,
    SnapshotRelation,
    SnapshotResearchGap,
    SnapshotRuntimeReferences,
    SnapshotSource,
    SnapshotTimelineEvent,
)
from marketpulse.investigation.validation.relations import validated_relation

ASSEMBLY_SCHEMA_VERSION = "phase5-report-input-v1"

T = TypeVar("T", bound=DomainModel)


class AssemblyError(RuntimeError):
    """Fail-closed assembly abort: stale, missing, or inconsistent state."""


def _short(prefix: str, domain: str, payload: object) -> str:
    return f"{prefix}:{canonical_hash(domain, payload)[:24]}"


class ReportInputAssembler:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        repository: InvestigationRepository,
    ) -> None:
        self._sessions = sessions
        self._repository = repository

    def assemble(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        assembled_at: datetime,
        report_type: ReportType = ReportType.FULL_INVESTIGATION,
    ) -> ReportInputSnapshot:
        """Assemble and immutably persist; idempotent on semantic hash."""
        with self._sessions.begin() as session:
            snapshot = self.assemble_in_session(
                session,
                run_id=run_id,
                snapshot_id=snapshot_id,
                assembled_at=assembled_at,
                report_type=report_type,
            )
            existing = session.scalar(
                select(ReportInputSnapshotRow).where(
                    ReportInputSnapshotRow.snapshot_hash == snapshot.snapshot_hash
                )
            )
            if existing is not None:
                table = existing.__table__
                return ReportInputSnapshot.model_validate(
                    {column.key: getattr(existing, column.key) for column in table.columns}
                )
            self._repository.add_in_session(session, snapshot)
            return snapshot

    def assemble_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        snapshot_id: str,
        assembled_at: datetime,
        report_type: ReportType = ReportType.FULL_INVESTIGATION,
    ) -> ReportInputSnapshot:
        run = self._get(session, InvestigationRun, run_id)
        allowed = {RunStatus.READY_FOR_REPORT}
        if report_type is ReportType.INVESTIGATION_STATUS:
            allowed.update(
                {RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
            )
        elif run.status is RunStatus.BLOCKED and session.scalar(
            select(ClaimRow.claim_id)
            .where(
                ClaimRow.run_id == run_id,
                ClaimRow.validation_status.in_(
                    (ValidationStatus.VERIFIED, ValidationStatus.PROBABLE)
                ),
                ClaimRow.latest_validation_id.is_not(None),
            )
            .limit(1)
        ):
            # A bounded stop is not a publication approval. Existing validation,
            # citation, critical-gap and release gates still all apply below.
            allowed.add(RunStatus.BLOCKED)
        if run.status not in allowed:
            raise AssemblyError(f"run {run_id} is {run.status}, not READY_FOR_REPORT")
        investigation = self._get(session, Investigation, run.investigation_id)

        claim_rows = session.scalars(
            select(ClaimRow).where(ClaimRow.run_id == run_id).order_by(ClaimRow.claim_id)
        ).all()
        claims = [self._get(session, Claim, row.claim_id) for row in claim_rows]
        # Computation claims have a separate locator/validation materialization
        # branch, never masquerade as text Evidence in the legacy assembler.
        claims = [c for c in claims if c.qualifiers.get("evidence_kind") != "COMPUTATION"]
        status_only = report_type is ReportType.INVESTIGATION_STATUS
        omitted_claims = [claim.claim_id for claim in claims if claim.latest_validation_id is None]
        if status_only:
            claims = [claim for claim in claims if claim.latest_validation_id is not None]

        validations: dict[str, ValidationResult] = {}
        relations: list[ClaimEvidenceRelation] = []
        evidence_by_id: dict[str, Evidence] = {}
        snapshots: dict[str, SourceSnapshot] = {}
        artifacts: dict[str, DocumentArtifact] = {}
        sources: dict[str, Source] = {}

        for claim in claims:
            if claim.latest_validation_id is None:
                raise AssemblyError(f"claim {claim.claim_id} has no latest validation")
            validation = self._get(session, ValidationResult, claim.latest_validation_id)
            if validation.claim_id != claim.claim_id:
                raise AssemblyError(
                    f"claim {claim.claim_id} latest validation {validation.validation_id} "
                    f"belongs to {validation.claim_id}"
                )
            if validation.status != claim.validation_status:
                raise AssemblyError(
                    f"claim {claim.claim_id} projection {claim.validation_status} is stale "
                    f"against validation {validation.status}"
                )
            if validation.confidence != claim.confidence:
                raise AssemblyError(f"claim {claim.claim_id} confidence projection is stale")
            validations[claim.claim_id] = validation

            relation_rows = session.scalars(
                select(ClaimEvidenceRelationRow)
                .where(ClaimEvidenceRelationRow.claim_id == claim.claim_id)
                .order_by(ClaimEvidenceRelationRow.relation_id)
            ).all()
            for relation_row in relation_rows:
                relation = self._get(session, ClaimEvidenceRelation, relation_row.relation_id)
                relations.append(validated_relation(relation, validation))
                if relation.evidence_id in evidence_by_id:
                    continue
                evidence = self._get(session, Evidence, relation.evidence_id)
                if evidence.run_id != run_id:
                    raise AssemblyError(
                        f"evidence {evidence.evidence_id} belongs to run {evidence.run_id}, "
                        f"not {run_id}"
                    )
                snapshot = self._get(session, SourceSnapshot, evidence.snapshot_id)
                snapshots[snapshot.snapshot_id] = snapshot
                if evidence.artifact_id is not None:
                    artifact = self._get(session, DocumentArtifact, evidence.artifact_id)
                    if artifact.snapshot_id != snapshot.snapshot_id:
                        raise AssemblyError(
                            f"artifact {artifact.artifact_id} does not belong to snapshot "
                            f"{snapshot.snapshot_id}"
                        )
                    artifacts[artifact.artifact_id] = artifact
                evidence_by_id[evidence.evidence_id] = evidence

        for row in session.scalars(
            select(SourceSnapshotRow).where(SourceSnapshotRow.run_id == run_id)
        ):
            snapshots.setdefault(
                row.snapshot_id, self._get(session, SourceSnapshot, row.snapshot_id)
            )
        for snapshot in snapshots.values():
            sources[snapshot.source_id] = self._get(session, Source, snapshot.source_id)

        conflict_rows = session.scalars(
            select(ConflictSetRow)
            .where(ConflictSetRow.run_id == run_id)
            .order_by(ConflictSetRow.conflict_id)
        ).all()
        conflicts = [self._get(session, ConflictSet, row.conflict_id) for row in conflict_rows]
        claim_ids = {claim.claim_id for claim in claims}
        if status_only:
            conflicts = [item for item in conflicts if set(item.claim_ids) <= claim_ids]
        for conflict in conflicts:
            unknown = set(conflict.claim_ids) - claim_ids
            if unknown:
                raise AssemblyError(
                    f"conflict {conflict.conflict_id} references unknown claims {sorted(unknown)}"
                )

        gap_rows = session.scalars(
            select(ResearchGapRow)
            .where(ResearchGapRow.run_id == run_id)
            .order_by(ResearchGapRow.gap_id)
        ).all()
        gaps = [self._get(session, ResearchGap, row.gap_id) for row in gap_rows]
        for gap in gaps:
            if (
                not status_only
                and gap.target_claim_id is not None
                and gap.target_claim_id not in claim_ids
            ):
                raise AssemblyError(f"gap {gap.gap_id} targets unknown claim {gap.target_claim_id}")

        timeline_rows = session.scalars(
            select(TimelineEventRow)
            .where(TimelineEventRow.run_id == run_id)
            .order_by(TimelineEventRow.timeline_event_id)
        ).all()
        timeline = [
            self._get(session, TimelineEvent, row.timeline_event_id) for row in timeline_rows
        ]
        evidence_ids = set(evidence_by_id)
        if status_only:
            timeline = [
                event for event in timeline if set(event.supporting_evidence_ids) <= evidence_ids
            ]
        for event in timeline:
            unknown_evidence = set(event.supporting_evidence_ids) - evidence_ids
            if unknown_evidence:
                raise AssemblyError(
                    f"timeline event {event.timeline_event_id} references unknown evidence "
                    f"{sorted(unknown_evidence)}"
                )

        claims = sorted(claims, key=_claim_stable_key)
        payload, claim_key_map, evidence_key_map = self._payload(
            investigation=investigation,
            run=run,
            claims=claims,
            validations=validations,
            relations=relations,
            evidence_by_id=evidence_by_id,
            snapshots=snapshots,
            artifacts=artifacts,
            sources=sources,
            conflicts=conflicts,
            gaps=gaps,
            timeline=timeline,
            session=session,
        )
        if status_only or run.status is RunStatus.BLOCKED:
            extra = [f"Run ended with status {run.status}. " + (run.interruption_reason or "")]
            if omitted_claims:
                extra.append(
                    f"{len(omitted_claims)} claims have not completed validation; "
                    "they are omitted from findings and remain available in the run record."
                )
            payload = payload.model_copy(update={"limitations": payload.limitations + tuple(extra)})
        runtime = SnapshotRuntimeReferences(
            claim_ids={claim_key_map[claim.claim_id]: claim.claim_id for claim in claims},
            evidence_ids={
                evidence_key_map[item.evidence_id]: item.evidence_id
                for item in evidence_by_id.values()
            },
        )
        from marketpulse.quant.contracts import canonical, digest
        from marketpulse.quant.reporting import load_material, snapshot_claims

        material = load_material(session, run_id)
        if material is not None:
            from marketpulse.investigation.persistence.models import AuditEventRow

            plan = session.get(AuditEventRow, "QPLAN-" + run_id)
            question = plan.metadata_payload.get("question", "").strip() if plan else ""
            payload = payload.model_copy(
                update={
                    "schema_version": "quant-report-input-v2",
                    "questions": (question,) if question else payload.questions,
                    "claims": payload.claims + snapshot_claims(material),
                    "quantitative_material": canonical(
                        material.model_dump(mode="json", exclude={"job_id"})
                    ),
                }
            )
            runtime = runtime.model_copy(
                update={
                    "claim_ids": {
                        **runtime.claim_ids,
                        **{
                            c.claim_id: c.claim_id + "-" + digest(run_id)[:8]
                            for c in material.claims
                        },
                    }
                }
            )
        from marketpulse.quant.comparison import load as load_comparison

        comparison = load_comparison(session, run_id)
        if comparison is not None:
            payload = payload.model_copy(
                update={
                    "schema_version": "quant-comparison-input-v1",
                    "comparison_material": canonical(comparison),
                }
            )
        return ReportInputSnapshot.build(
            snapshot_id=snapshot_id,
            investigation_id=investigation.investigation_id,
            run_id=run.run_id,
            run_mode=run.mode,
            assembled_at=assembled_at,
            semantic_payload=payload,
            runtime_references=runtime,
        )

    # -- semantic material -----------------------------------------------------

    def _payload(
        self,
        *,
        investigation: Investigation,
        run: InvestigationRun,
        claims: list[Claim],
        validations: dict[str, ValidationResult],
        relations: list[ClaimEvidenceRelation],
        evidence_by_id: dict[str, Evidence],
        snapshots: dict[str, SourceSnapshot],
        artifacts: dict[str, DocumentArtifact],
        sources: dict[str, Source],
        conflicts: list[ConflictSet],
        gaps: list[ResearchGap],
        timeline: list[TimelineEvent],
        session: Session,
    ) -> tuple[ReportInputSemanticPayload, dict[str, str], dict[str, str]]:
        question_texts = tuple(question.text for question in investigation.questions)
        question_key_by_id = {
            question.question_id: _short(
                "question", "phase5-question-key-v1", {"text": question.text}
            )
            for question in investigation.questions
        }
        claim_keys = {claim.claim_id: _claim_stable_key(claim) for claim in claims}
        evidence_keys = {
            evidence_id: _evidence_stable_key(evidence, snapshots, artifacts)
            for evidence_id, evidence in evidence_by_id.items()
        }
        source_keys = {
            source_id: _source_semantic_key(source) for source_id, source in sources.items()
        }

        snapshot_claims = tuple(
            SnapshotClaim(
                stable_key=claim_keys[claim.claim_id],
                semantic_hash=_claim_semantic_hash(claim),
                statement=claim.statement,
                claim_type=claim.claim_type,
                validation_status=claim.validation_status,
                confidence=claim.confidence if claim.confidence is not None else 0.0,
                validation_semantic_hash=_validation_semantic_hash(validations[claim.claim_id]),
                importance=claim.importance,
                is_critical=claim.is_critical,
                report_section=str(claim.qualifiers["report_section"])
                if claim.qualifiers.get("report_section")
                else None,
            )
            for claim in claims
        )
        snapshot_evidence = tuple(
            _snapshot_evidence(
                evidence, evidence_keys[evidence.evidence_id], snapshots, artifacts, source_keys
            )
            for evidence in sorted(
                evidence_by_id.values(), key=lambda item: evidence_keys[item.evidence_id]
            )
        )
        snapshot_relations = tuple(
            SnapshotRelation(
                stable_key=_short(
                    "relation",
                    "phase5-relation-key-v1",
                    {
                        "claim": claim_keys[relation.claim_id],
                        "evidence": evidence_keys[relation.evidence_id],
                        "stance": relation.stance,
                    },
                ),
                claim_stable_key=claim_keys[relation.claim_id],
                evidence_stable_key=evidence_keys[relation.evidence_id],
                stance=relation.stance,
                entailment_status=relation.entailment_status,
            )
            for relation in sorted(
                relations,
                key=lambda item: (
                    claim_keys[item.claim_id],
                    evidence_keys.get(item.evidence_id, ""),
                    str(item.stance),
                ),
            )
        )
        snapshot_conflicts = tuple(
            SnapshotConflict(
                stable_key=_short(
                    "conflict",
                    "phase5-conflict-key-v1",
                    {
                        "type": conflict.conflict_type,
                        "claims": sorted(claim_keys[claim_id] for claim_id in conflict.claim_ids),
                    },
                ),
                conflict_type=conflict.conflict_type,
                resolution_summary=conflict.resolution_summary,
                possible_explanations=conflict.possible_explanations,
                competing_values=_conflict_values(conflict, evidence_keys),
                severity=conflict.severity,
                status=conflict.status,
                claim_stable_keys=tuple(
                    sorted(
                        {claim_keys[claim_id] for claim_id in conflict.claim_ids}
                        | {
                            claim_keys[relation.claim_id]
                            for relation in relations
                            if relation.evidence_id in conflict.evidence_ids
                        }
                    )
                ),
                semantic_hash=canonical_hash(
                    "phase5-conflict-semantic-v1",
                    {
                        "type": conflict.conflict_type,
                        "severity": conflict.severity,
                        "status": conflict.status,
                        "resolution_status": conflict.resolution_status,
                        "claims": sorted(claim_keys[claim_id] for claim_id in conflict.claim_ids),
                        "competing_values": list(_conflict_values(conflict, evidence_keys)),
                    },
                ),
            )
            for conflict in sorted(
                conflicts,
                key=lambda item: (
                    str(item.conflict_type),
                    sorted(claim_keys[claim_id] for claim_id in item.claim_ids),
                ),
            )
        )
        snapshot_gaps = tuple(
            SnapshotResearchGap(
                stable_key=_short(
                    "gap",
                    "phase5-gap-key-v1",
                    {
                        "type": gap.gap_type,
                        "reason": gap.reason,
                        "target_claim": claim_keys.get(gap.target_claim_id or ""),
                        "target_question": question_key_by_id.get(gap.target_question_id or ""),
                    },
                ),
                gap_type=gap.gap_type,
                severity=gap.severity,
                status=gap.status,
                reason=gap.reason,
                target_claim_stable_key=claim_keys.get(gap.target_claim_id or ""),
                target_question_stable_key=question_key_by_id.get(gap.target_question_id or ""),
                suggested_actions=tuple(
                    dict.fromkeys(
                        action
                        for action in (gap.suggested_action, *gap.suggested_actions)
                        if action
                    )
                ),
            )
            for gap in sorted(gaps, key=lambda item: (str(item.gap_type), item.reason))
        )
        snapshot_timeline = tuple(
            SnapshotTimelineEvent(
                stable_key=_short(
                    "timeline",
                    "phase5-timeline-key-v1",
                    {
                        "event_time": (event.event_time.isoformat() if event.event_time else None),
                        "time_precision": event.time_precision,
                        "description": event.description,
                    },
                ),
                event_time=event.event_time,
                time_precision=event.time_precision,
                description=event.description,
                validation_status=event.validation_status,
                supporting_evidence_stable_keys=tuple(
                    sorted(
                        evidence_keys[evidence_id] for evidence_id in event.supporting_evidence_ids
                    )
                ),
            )
            for event in sorted(
                timeline,
                key=lambda item: (
                    item.event_time.isoformat() if item.event_time else "",
                    item.description,
                ),
            )
        )
        snapshot_sources = tuple(
            self._snapshot_source(session, run.run_id, source, source_keys[source_id])
            for source_id, source in sorted(sources.items(), key=lambda item: source_keys[item[0]])
        )
        limitations = tuple(
            sorted(
                {gap.reason for gap in gaps if gap.status is GapStatus.OPEN}
                | {
                    f"Unresolved {conflict.conflict_type} conflict over "
                    f"{len(conflict.claim_ids)} claims"
                    for conflict in conflicts
                    if conflict.status is not ConflictStatus.RESOLVED
                    or conflict.resolution_status is ConflictResolutionStatus.UNRESOLVED
                }
            )
        )
        policy_versions = sorted({validation.policy_version for validation in validations.values()})
        payload = ReportInputSemanticPayload(
            schema_version=ASSEMBLY_SCHEMA_VERSION,
            validation_policy_version=",".join(policy_versions) or "none",
            investigation_key=_short(
                "investigation",
                "phase5-investigation-key-v1",
                {
                    "title": investigation.title,
                    "event_description": investigation.event_description,
                    "investigation_goal": investigation.investigation_goal,
                    "questions": list(question_texts),
                },
            ),
            terminal_run_status=str(run.status),
            execution_provenance=(
                "CURATED_OFFLINE"
                if run.workflow_version.startswith("curated-offline")
                else "PROVIDER_WORKFLOW"
            ),
            questions=question_texts,
            claims=snapshot_claims,
            evidence=snapshot_evidence,
            relations=snapshot_relations,
            conflicts=snapshot_conflicts,
            research_gaps=snapshot_gaps,
            timeline_events=snapshot_timeline,
            sources=snapshot_sources,
            limitations=limitations,
        )
        return payload, claim_keys, evidence_keys

    def _snapshot_source(
        self,
        session: Session,
        run_id: str,
        source: Source,
        source_key: str,
    ) -> SnapshotSource:
        member = session.scalars(
            select(SourceFamilyMemberRow)
            .join(
                SourceFamilyRow,
                SourceFamilyMemberRow.family_record_id == SourceFamilyRow.family_record_id,
            )
            .where(
                SourceFamilyMemberRow.source_id == source.source_id,
                SourceFamilyRow.run_id == run_id,
            )
            .join(
                ValidationResultRow,
                SourceFamilyRow.validation_id == ValidationResultRow.validation_id,
            )
            .order_by(ValidationResultRow.created_at.desc(), SourceFamilyRow.family_id)
        ).first()
        family_key = "UNGROUPED"
        role = "UNGROUPED"
        if member is not None:
            family = session.get(SourceFamilyRow, member.family_record_id)
            if family is not None and family.run_id == run_id:
                family_key = str(family.family_id)
                role = str(family.origin_type)
        return SnapshotSource(
            source_semantic_key=source_key,
            family_key=family_key,
            independence_role=role,
        )

    def _get(self, session: Session, entity_type: type[T], entity_id: str) -> T:
        try:
            return self._repository.get_in_session(session, entity_type, entity_id)
        except KeyError as exc:
            raise AssemblyError(f"missing {entity_type.__name__} {entity_id}") from exc


def _conflict_values(conflict: ConflictSet, evidence_keys: dict[str, str]) -> tuple[JsonValue, ...]:
    values: list[JsonValue] = []
    for value in conflict.competing_values:
        if isinstance(value, dict):
            value = dict(value)
            evidence_id = value.pop("evidence_id", None)
            if isinstance(evidence_id, str):
                value["evidence_key"] = evidence_keys.get(evidence_id, "UNAVAILABLE")
        values.append(value)
    return tuple(values)


def _claim_stable_key(claim: Claim) -> str:
    return _short(
        "claim",
        "phase5-claim-key-v1",
        {
            "statement": claim.statement,
            "claim_type": claim.claim_type,
            "subject": claim.subject,
            "predicate": claim.predicate,
            "object": claim.object,
            "qualifiers": claim.qualifiers,
        },
    )


def _claim_semantic_hash(claim: Claim) -> str:
    return canonical_hash(
        "phase5-claim-semantic-v1",
        {
            "stable_key": _claim_stable_key(claim),
            "importance": claim.importance,
            "is_critical": claim.is_critical,
        },
    )


def _validation_semantic_hash(validation: ValidationResult) -> str:
    return canonical_hash(
        "phase5-validation-semantic-v1",
        {
            "status": validation.status,
            "confidence": validation.confidence,
            "entailment_result": validation.entailment_result,
            "independent_source_count": validation.independent_source_count,
            "strong_contradiction": validation.strong_contradiction,
            "sufficiency_result": validation.sufficiency_result,
            "validation_basis": validation.validation_basis,
            "policy_version": validation.policy_version,
            "profile_version": validation.profile_version,
            "input_fingerprint": validation.input_fingerprint,
            "evidence_set_hash": validation.evidence_set_hash,
        },
    )


def _evidence_semantic_material(
    evidence: Evidence,
    snapshots: dict[str, SourceSnapshot],
    artifacts: dict[str, DocumentArtifact],
) -> dict[str, object]:
    snapshot = snapshots[evidence.snapshot_id]
    snapshot_hash = snapshot.cleaned_sha256 or snapshot.raw_sha256
    artifact_hash = (
        artifacts[evidence.artifact_id].sha256 if evidence.artifact_id else snapshot_hash
    )
    return {
        "content_hash": evidence.content_hash,
        "locator": evidence.locator.model_dump(mode="json"),
        "snapshot_content_hash": snapshot_hash,
        "artifact_content_hash": artifact_hash,
    }


def _evidence_stable_key(
    evidence: Evidence,
    snapshots: dict[str, SourceSnapshot],
    artifacts: dict[str, DocumentArtifact],
) -> str:
    return _short(
        "evidence",
        "phase5-evidence-key-v1",
        _evidence_semantic_material(evidence, snapshots, artifacts),
    )


def _snapshot_evidence(
    evidence: Evidence,
    stable_key: str,
    snapshots: dict[str, SourceSnapshot],
    artifacts: dict[str, DocumentArtifact],
    source_keys: dict[str, str],
) -> SnapshotEvidence:
    material = _evidence_semantic_material(evidence, snapshots, artifacts)
    snapshot = snapshots[evidence.snapshot_id]
    return SnapshotEvidence(
        stable_key=stable_key,
        semantic_hash=canonical_hash("phase5-evidence-semantic-v1", material),
        content_hash=evidence.content_hash,
        artifact_content_hash=str(material["artifact_content_hash"]),
        snapshot_content_hash=str(material["snapshot_content_hash"]),
        source_semantic_key=source_keys[snapshot.source_id],
    )


def _source_semantic_key(source: Source) -> str:
    return _short(
        "source",
        "phase5-source-key-v1",
        {
            "canonical_url": str(source.canonical_url),
            "publisher": source.publisher,
            "organization": source.organization,
        },
    )
