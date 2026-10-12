"""Phase 5.2 report validation: independent CitationValidator + ReportValidator.

Neither validator mutates Claim status or replaces ValidationPolicy. Any
integrity failure is a HARD finding and fails closed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.investigation.domain.claims import Claim, ClaimEvidenceRelation, ValidationResult
from marketpulse.investigation.domain.enums import (
    ConflictStatus,
    EntailmentStatus,
    FindingSeverity,
    GapStatus,
    RelationStance,
    ReportType,
    ReportValidatorKind,
    ValidationStatus,
)
from marketpulse.investigation.domain.reports import Report
from marketpulse.investigation.domain.sources import DocumentArtifact, Evidence, SourceSnapshot
from marketpulse.investigation.persistence.models import ClaimEvidenceRelationRow
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.reporting.assembler import _validation_semantic_hash
from marketpulse.investigation.reporting.models import (
    Citation,
    ReportInputSnapshot,
    ReportValidationFinding,
)
from marketpulse.investigation.reporting.writer import (
    ContentClass,
    ReportDraft,
    SectionStatus,
    allowed_sections,
    required_sections,
)
from marketpulse.investigation.validation.relations import validated_relation

REPORT_VALIDATOR_VERSION = "report-validator-v1"

_PROBABLE_MARKERS = (
    "可能",
    "不充分",
    "不确定",
    "suggest",
    "likely",
    "probable",
    "appears",
    "indicates",
    "incomplete",
    "uncertain",
)
_DISPUTED_MARKERS = (
    "争议",
    "冲突",
    "分歧",
    "disputed",
    "conflict",
    "conflicting",
    "unsettled",
    "contradict",
    "disagree",
)
_UNVERIFIED_MARKERS = (
    "尚未证实",
    "尚不能确认",
    "证据不足",
    "未验证",
    "not established",
    "could not establish",
    "could not be established",
    "insufficient",
    "unverified",
    "remains unresolved",
)


class CitationValidator:
    """Independently re-verifies every persisted citation chain."""

    def __init__(self, repository: InvestigationRepository, quant_service=None) -> None:
        self._repository = repository
        self._quant_service = quant_service

    def validate(
        self,
        session: Session,
        *,
        citations: list[Citation],
        snapshot: ReportInputSnapshot,
        report: Report,
        now: datetime,
    ) -> list[ReportValidationFinding]:
        findings: list[ReportValidationFinding] = []
        computation_batch = None
        if snapshot.semantic_payload.quantitative_material is not None:
            from marketpulse.quant.citations import _ComputationCitationBatch

            computation_batch = _ComputationCitationBatch(self._quant_service)
        for citation in citations:
            failure = self._validate_one(
                session, citation=citation, snapshot=snapshot, computation_batch=computation_batch
            )
            if failure is not None:
                code, detail = failure
                findings.append(
                    ReportValidationFinding(
                        finding_id=f"FND-{report.report_id}-CVAL-{len(findings):04d}",
                        report_id=report.report_id,
                        validator=ReportValidatorKind.CITATION,
                        severity=FindingSeverity.HARD,
                        code=code,
                        detail=detail,
                        section_key=citation.section_key,
                        unit_key=citation.unit_key,
                        validator_version=REPORT_VALIDATOR_VERSION,
                        created_at=now,
                    )
                )
        return findings

    def _validate_one(
        self,
        session: Session,
        *,
        citation: Citation,
        snapshot: ReportInputSnapshot,
        computation_batch=None,
    ) -> tuple[str, str] | None:
        if citation.report_input_snapshot_hash != snapshot.snapshot_hash:
            return "CITATION_SNAPSHOT_BINDING", "citation not bound to current snapshot"
        if citation.semantic_identity().semantic_hash != citation.citation_hash:
            return "CITATION_HASH_MISMATCH", "citation semantic hash does not recompute"
        if citation.schema_version == "computation-citation-v2":
            from marketpulse.quant.citations import computation_citation

            try:
                expected = computation_citation(
                    session,
                    self._quant_service,
                    snapshot=snapshot,
                    claim_key=citation.claim_id,
                    section_key=citation.section_key,
                    unit_key=citation.unit_key,
                    report=Report(
                        report_id=citation.report_id,
                        investigation_id=snapshot.investigation_id,
                        run_id=snapshot.run_id,
                        version=1,
                        report_type=ReportType.INVESTIGATION_STATUS,
                        report_input_snapshot_hash=snapshot.snapshot_hash,
                        report_hash="0" * 64,
                        claim_set_hash=snapshot.claim_set_hash,
                        citation_set_hash="0" * 64,
                        release_policy_version="phase5-release-policy-v1",
                        created_at=citation.created_at,
                    ),
                    ordinal=citation.display_ordinal,
                    batch=computation_batch,
                )
                if expected.citation_hash != citation.citation_hash:
                    raise ValueError("citation drift")
            except (ValueError, PermissionError, KeyError):
                return (
                    "COMPUTATION_CITATION_INTEGRITY",
                    "computation citation is not current or intact",
                )
            return None
        claim = self._repository.get_in_session(session, Claim, citation.claim_id)
        if claim.latest_validation_id is None:
            return "CITATION_VALIDATION_MISSING", "claim has no current validation"
        validation = self._repository.get_in_session(
            session, ValidationResult, claim.latest_validation_id
        )
        if _validation_semantic_hash(validation) != citation.validation_semantic_hash:
            return "CITATION_VALIDATION_DRIFT", "claim validation changed after citation assembly"
        row = session.scalar(
            select(ClaimEvidenceRelationRow).where(
                ClaimEvidenceRelationRow.claim_id == citation.claim_id,
                ClaimEvidenceRelationRow.evidence_id == citation.evidence_id,
            )
        )
        if row is None:
            return "CITATION_NO_ENTAILED_RELATION", "supporting relationship is absent"
        relation = validated_relation(
            self._repository.get_in_session(session, ClaimEvidenceRelation, row.relation_id),
            validation,
        )
        if (
            relation.stance is not RelationStance.SUPPORTS
            or relation.entailment_status is not EntailmentStatus.ENTAILED
        ):
            return (
                "CITATION_NO_ENTAILED_RELATION",
                "latest validation does not entail this evidence",
            )
        try:
            evidence = self._repository.get_in_session(session, Evidence, citation.evidence_id)
        except KeyError:
            return "CITATION_EVIDENCE_MISSING", f"evidence {citation.evidence_id} missing"
        locator = evidence.locator
        if locator.model_dump(mode="json") != citation.canonical_locator:
            return "CITATION_LOCATOR_MISMATCH", "locator drifted from citation"
        quote = evidence.content
        if hashlib.sha256(quote.encode("utf-8")).hexdigest() != citation.resolved_quote_hash:
            return "CITATION_QUOTE_MISMATCH", "resolved quote hash mismatch"
        snapshot_row = self._repository.get_in_session(
            session, SourceSnapshot, evidence.snapshot_id
        )
        expected_snapshot_hash = snapshot_row.cleaned_sha256 or snapshot_row.raw_sha256
        if expected_snapshot_hash != citation.snapshot_content_hash:
            return "CITATION_SNAPSHOT_HASH_MISMATCH", "snapshot content hash mismatch"
        if evidence.artifact_id is not None:
            artifact = self._repository.get_in_session(
                session, DocumentArtifact, evidence.artifact_id
            )
            if artifact.sha256 != citation.artifact_content_hash:
                return "CITATION_ARTIFACT_HASH_MISMATCH", "artifact content hash mismatch"
        return None


class ReportValidator:
    """Schema, scope, wording, and coverage checks over draft + snapshot."""

    def validate(
        self,
        *,
        draft: ReportDraft,
        snapshot: ReportInputSnapshot,
        citations: list[Citation],
        report: Report,
        now: datetime,
    ) -> list[ReportValidationFinding]:
        findings: list[ReportValidationFinding] = []
        seq = 0

        def emit(
            code: str,
            detail: str,
            section: str | None = None,
            unit: str | None = None,
            claim: str | None = None,
            severity: FindingSeverity = FindingSeverity.HARD,
        ) -> None:
            nonlocal seq
            findings.append(
                ReportValidationFinding(
                    finding_id=f"FND-{report.report_id}-RPT-{seq:04d}",
                    report_id=report.report_id,
                    validator=ReportValidatorKind.REPORT,
                    severity=severity,
                    code=code,
                    detail=detail,
                    section_key=section,
                    unit_key=unit,
                    claim_stable_key=claim,
                    validator_version=REPORT_VALIDATOR_VERSION,
                    created_at=now,
                )
            )
            seq += 1

        # Section completeness: every required section present exactly once.
        required = required_sections(draft.report_type)
        allowed = allowed_sections(draft.report_type)
        present = [section.section_key for section in draft.sections]
        for key in required:
            if key not in present:
                emit("SCHEMA_SECTION_MISSING", f"required section {key} missing")
        for key in present:
            if key not in allowed:
                emit("SCHEMA_SECTION_UNKNOWN", f"section {key} not in schema", section=key)
            if present.count(key) > 1:
                emit("SCHEMA_SECTION_DUPLICATED", f"section {key} duplicated", section=key)

        claim_by_key = {claim.stable_key: claim for claim in snapshot.semantic_payload.claims}
        quant_statements = {}
        if snapshot.semantic_payload.quantitative_material is not None:
            from marketpulse.quant.reporting import QuantReportMaterial

            material = QuantReportMaterial.model_validate_json(
                snapshot.semantic_payload.quantitative_material
            )
            quant_statements = {c.claim_id: c.statement for c in material.claims}
            if any(not c.validation.rights_valid for c in material.claims):
                emit(
                    "QUANT_RIGHTS_REVIEW_REQUIRED",
                    "data entitlement is not established",
                    severity=FindingSeverity.GOVERNANCE,
                )
            if draft.report_type is ReportType.FULL_INVESTIGATION and any(
                c.validation.status != "VERIFIED" for c in material.claims
            ):
                emit(
                    "QUANT_PUBLICATION_INCOMPLETE",
                    "unverified quantitative material cannot be fully published",
                )
        cited_units = {(citation.section_key, citation.unit_key) for citation in citations}
        covered_claims: set[str] = set()

        for section in draft.sections:
            if section.status is SectionStatus.CONTENT and not section.units:
                emit(
                    "SCHEMA_SECTION_EMPTY",
                    f"section {section.section_key} marked CONTENT without units",
                    section=section.section_key,
                )
            for unit in section.units:
                lowered = unit.text.lower()
                if unit.content_class is ContentClass.FACTUAL_ASSERTION:
                    if not unit.claim_refs:
                        emit(
                            "UNSUPPORTED_REPORT_CONTENT",
                            f"factual unit {unit.unit_key} has no claim refs",
                            section.section_key,
                            unit.unit_key,
                        )
                    if unit.evidence_intents and not unit.claim_refs:
                        emit(
                            "EVIDENCE_BYPASSES_CLAIM",
                            f"unit {unit.unit_key} cites evidence without claims",
                            section.section_key,
                            unit.unit_key,
                        )
                    if (section.section_key, unit.unit_key) not in cited_units:
                        emit(
                            "CITATION_INCOMPLETE",
                            f"factual unit {unit.unit_key} lacks a materialized citation",
                            section.section_key,
                            unit.unit_key,
                        )
                for claim_key in unit.claim_refs:
                    quant_status = claim_by_key.get(claim_key)
                    prefix = (
                        "已验证"
                        if quant_status
                        and quant_status.validation_status is ValidationStatus.VERIFIED
                        else "尚未证实的可复现数值观察"
                    )
                    if (
                        claim_key in quant_statements
                        and unit.text != prefix + "：" + quant_statements[claim_key]
                    ):
                        emit(
                            "QUANT_NUMERIC_WORDING_DRIFT",
                            "quantitative wording differs from frozen read-only statement",
                            section.section_key,
                            unit.unit_key,
                            claim_key,
                        )
                    claim = claim_by_key.get(claim_key)
                    if claim is None:
                        emit(
                            "UNSUPPORTED_REPORT_CONTENT",
                            f"unit {unit.unit_key} references unknown claim {claim_key}",
                            section.section_key,
                            unit.unit_key,
                            claim_key,
                        )
                        continue
                    covered_claims.add(claim_key)
                    if claim.validation_status is ValidationStatus.PROBABLE and not any(
                        marker in lowered for marker in _PROBABLE_MARKERS
                    ):
                        emit(
                            "QUALIFIER_LOSS",
                            f"PROBABLE claim {claim_key} stated without uncertainty",
                            section.section_key,
                            unit.unit_key,
                            claim_key,
                        )
                    if claim.validation_status is ValidationStatus.DISPUTED and not any(
                        marker in lowered for marker in _DISPUTED_MARKERS
                    ):
                        emit(
                            "STATUS_WORDING_VIOLATION",
                            f"DISPUTED claim {claim_key} stated as settled",
                            section.section_key,
                            unit.unit_key,
                            claim_key,
                        )
                    if claim.validation_status is ValidationStatus.UNVERIFIED and not any(
                        marker in lowered for marker in _UNVERIFIED_MARKERS
                    ):
                        emit(
                            "STATUS_WORDING_VIOLATION",
                            f"UNVERIFIED claim {claim_key} presented as established",
                            section.section_key,
                            unit.unit_key,
                            claim_key,
                        )

        # Critical claim coverage.
        for claim in snapshot.semantic_payload.claims:
            if claim.is_critical and claim.stable_key not in covered_claims:
                emit(
                    "CRITICAL_CLAIM_OMITTED",
                    f"critical claim {claim.stable_key} not covered by report",
                    claim=claim.stable_key,
                )

        # Unresolved conflicts must be surfaced.
        for conflict in snapshot.semantic_payload.conflicts:
            if conflict.status is not ConflictStatus.RESOLVED and conflict.claim_stable_keys:
                if not any(key in covered_claims for key in conflict.claim_stable_keys):
                    emit(
                        "CRITICAL_CONFLICT_OMITTED",
                        f"unresolved conflict {conflict.stable_key} not surfaced",
                        claim=conflict.stable_key,
                    )

        # Open gaps must be disclosed in FULL/RESTRICTED reports.
        if (
            any(gap.status is GapStatus.OPEN for gap in snapshot.semantic_payload.research_gaps)
            and draft.report_type is not ReportType.INVESTIGATION_STATUS
        ):
            limitation_section = next(
                (
                    s
                    for s in draft.sections
                    if s.section_key
                    in {"LIMITATIONS_AND_RESEARCH_GAPS", "BLOCKING_GAPS_AND_LIMITATIONS"}
                ),
                None,
            )
            if limitation_section is None or not limitation_section.units:
                emit("OPEN_GAP_NOT_DISCLOSED", "open research gaps not disclosed")

        return findings
