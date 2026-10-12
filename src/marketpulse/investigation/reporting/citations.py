"""Deterministic CitationFactory: the only trusted citation materializer.

Materializes the chain NarrativeUnit -> Claim -> latest ValidationResult ->
ClaimEvidenceRelation -> Evidence -> Snapshot/Artifact -> locator/quote ->
Source. It never searches, fetches, creates evidence, or trusts Writer
metadata. Any broken or drifted link produces a HARD finding (fail closed).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketpulse.investigation.domain.claims import (
    Claim,
    ClaimEvidenceRelation,
    ValidationResult,
)
from marketpulse.investigation.domain.enums import (
    EntailmentStatus,
    FindingSeverity,
    RelationStance,
    ReportValidatorKind,
)
from marketpulse.investigation.domain.reports import Report
from marketpulse.investigation.domain.sources import (
    DocumentArtifact,
    Evidence,
    Source,
    SourceSnapshot,
)
from marketpulse.investigation.persistence.models import ClaimEvidenceRelationRow
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.reporting.assembler import (
    _claim_semantic_hash,
    _source_semantic_key,
    _validation_semantic_hash,
)
from marketpulse.investigation.reporting.models import (
    Citation,
    CitationSemanticIdentity,
    ReportInputSnapshot,
    ReportValidationFinding,
)
from marketpulse.investigation.reporting.writer import ContentClass, ReportDraft
from marketpulse.investigation.validation.relations import validated_relation

CITATION_SCHEMA_VERSION = "citation-v1"
ENTAILMENT_VERSION = "semantic-entailment-v1"


class CitationFactory:
    def __init__(self, repository: InvestigationRepository, quant_service=None) -> None:
        self._repository = repository
        self._quant_service = quant_service

    def build(
        self,
        session: Session,
        *,
        snapshot: ReportInputSnapshot,
        draft: ReportDraft,
        report: Report,
        now: datetime,
    ) -> tuple[list[Citation], list[ReportValidationFinding]]:
        citations: list[Citation] = []
        findings: list[ReportValidationFinding] = []
        claim_keys = {claim.stable_key for claim in snapshot.semantic_payload.claims}
        ordinal = 0
        finding_seq = 0
        computation_batch = None
        if snapshot.semantic_payload.quantitative_material is not None:
            from marketpulse.quant.citations import _ComputationCitationBatch

            computation_batch = _ComputationCitationBatch(self._quant_service)
        for section in draft.sections:
            for unit in section.units:
                if unit.content_class is not ContentClass.FACTUAL_ASSERTION:
                    continue
                for claim_key in unit.claim_refs:
                    claim_id = snapshot.runtime_references.claim_ids.get(claim_key)
                    if claim_key not in claim_keys or claim_id is None:
                        findings.append(
                            _finding(
                                report,
                                finding_seq,
                                "CITATION_UNKNOWN_CLAIM_REF",
                                f"unit {unit.unit_key} references unknown claim {claim_key}",
                                section.section_key,
                                unit.unit_key,
                                claim_key,
                                now,
                            )
                        )
                        finding_seq += 1
                        continue
                    chain, failure = self._materialize(
                        session,
                        snapshot=snapshot,
                        claim_key=claim_key,
                        claim_id=claim_id,
                        section_key=section.section_key,
                        unit_key=unit.unit_key,
                        report=report,
                        ordinal=ordinal,
                        computation_batch=computation_batch,
                    )
                    if chain is not None:
                        citations.append(chain)
                        ordinal += 1
                    if failure is not None:
                        findings.append(
                            _finding(
                                report,
                                finding_seq,
                                failure[0],
                                failure[1],
                                section.section_key,
                                unit.unit_key,
                                claim_key,
                                now,
                            )
                        )
                        finding_seq += 1
        return citations, findings

    def _materialize(
        self,
        session: Session,
        *,
        snapshot: ReportInputSnapshot,
        claim_key: str,
        claim_id: str,
        section_key: str,
        unit_key: str,
        report: Report,
        ordinal: int,
        computation_batch=None,
    ) -> tuple[Citation | None, tuple[str, str] | None]:
        if snapshot.semantic_payload.quantitative_material is not None and claim_key.startswith(
            "qclaim_"
        ):
            from marketpulse.quant.citations import computation_citation

            try:
                return computation_citation(
                    session,
                    self._quant_service,
                    snapshot=snapshot,
                    claim_key=claim_key,
                    section_key=section_key,
                    unit_key=unit_key,
                    report=report,
                    ordinal=ordinal,
                    batch=computation_batch,
                ), None
            except (ValueError, PermissionError, KeyError):
                return None, (
                    "COMPUTATION_CITATION_INTEGRITY",
                    "frozen computation chain is missing or drifted",
                )
        try:
            claim = self._repository.get_in_session(session, Claim, claim_id)
        except KeyError:
            return None, ("CITATION_CLAIM_MISSING", f"claim {claim_id} no longer exists")
        claim_hash = _claim_hash_for(snapshot, claim_key)
        if _claim_semantic_hash(claim) != claim_hash:
            return None, (
                "CITATION_CLAIM_DRIFT",
                f"claim {claim_id} semantic drift since snapshot",
            )
        if claim.latest_validation_id is None:
            return None, ("CITATION_VALIDATION_MISSING", f"claim {claim_id} has no validation")
        try:
            validation = self._repository.get_in_session(
                session, ValidationResult, claim.latest_validation_id
            )
        except KeyError:
            return None, (
                "CITATION_VALIDATION_MISSING",
                f"validation {claim.latest_validation_id} missing",
            )
        validation_hash = _validation_semantic_hash(validation)
        if validation_hash != _validation_hash_for(snapshot, claim_key):
            return None, (
                "CITATION_VALIDATION_DRIFT",
                f"validation for claim {claim_id} drifted since snapshot",
            )

        relation = self._best_relation(session, claim_id, validation)
        if relation is None:
            return None, (
                "CITATION_NO_ENTAILED_RELATION",
                f"claim {claim_id} has no entailed supporting relation",
            )
        evidence = self._repository.get_in_session(session, Evidence, relation.evidence_id)
        snapshot_row = self._repository.get_in_session(
            session, SourceSnapshot, evidence.snapshot_id
        )
        artifact: DocumentArtifact | None = None
        if evidence.artifact_id is not None:
            artifact = self._repository.get_in_session(
                session, DocumentArtifact, evidence.artifact_id
            )

        locator = evidence.locator
        quote = evidence.content
        quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        if quote_hash != locator.quote_hash or len(quote) != locator.end - locator.start:
            return None, (
                "CITATION_QUOTE_MISMATCH",
                f"evidence {evidence.evidence_id} quote hash mismatch",
            )
        snapshot_content_hash = snapshot_row.cleaned_sha256 or snapshot_row.raw_sha256
        artifact_hash = artifact.sha256 if artifact else snapshot_content_hash
        source = self._repository.get_in_session(session, Source, snapshot_row.source_id)

        identity = CitationSemanticIdentity(
            report_input_snapshot_hash=snapshot.snapshot_hash,
            claim_set_hash=snapshot.claim_set_hash,
            section_key=section_key,
            unit_key=unit_key,
            claim_semantic_hash=claim_hash,
            validation_semantic_hash=validation_hash,
            relation_semantics=f"{relation.stance}:{relation.entailment_status}",
            evidence_semantic_hash=_evidence_hash_for(snapshot, evidence.evidence_id),
            canonical_locator=locator.model_dump(mode="json"),
            resolved_quote_hash=quote_hash,
            artifact_content_hash=artifact_hash,
            snapshot_content_hash=snapshot_content_hash,
            source_semantic_identity=_source_semantic_key(source),
            entailment_judgment=str(relation.entailment_status),
            entailment_version=ENTAILMENT_VERSION,
            schema_version=CITATION_SCHEMA_VERSION,
        )
        citation = Citation.issue(
            citation_id=f"CIT-{report.report_id}-{ordinal:04d}",
            report_id=report.report_id,
            display_ordinal=ordinal,
            claim_id=claim_id,
            evidence_id=evidence.evidence_id,
            created_at=report.created_at,
            identity=identity,
        )
        return citation, None

    def _best_relation(
        self, session: Session, claim_id: str, validation: ValidationResult
    ) -> ClaimEvidenceRelation | None:
        rows = session.scalars(
            select(ClaimEvidenceRelationRow)
            .where(ClaimEvidenceRelationRow.claim_id == claim_id)
            .order_by(ClaimEvidenceRelationRow.relation_id)
        ).all()
        candidates: list[tuple[str, ClaimEvidenceRelation]] = []
        for row in rows:
            relation = self._repository.get_in_session(
                session, ClaimEvidenceRelation, row.relation_id
            )
            relation = validated_relation(relation, validation)
            if relation.stance is not RelationStance.SUPPORTS:
                continue
            if relation.entailment_status is EntailmentStatus.ENTAILED:
                evidence = self._repository.get_in_session(session, Evidence, relation.evidence_id)
                candidates.append((evidence.content_hash, relation))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _finding(
    report: Report,
    sequence: int,
    code: str,
    detail: str,
    section_key: str,
    unit_key: str,
    claim_key: str,
    now: datetime,
) -> ReportValidationFinding:
    return ReportValidationFinding(
        finding_id=f"FND-{report.report_id}-CIT-{sequence:04d}",
        report_id=report.report_id,
        validator=ReportValidatorKind.CITATION,
        severity=FindingSeverity.HARD,
        code=code,
        detail=detail,
        section_key=section_key,
        unit_key=unit_key,
        claim_stable_key=claim_key,
        validator_version=CITATION_SCHEMA_VERSION,
        created_at=now,
    )


def _claim_hash_for(snapshot: ReportInputSnapshot, claim_key: str) -> str:
    for claim in snapshot.semantic_payload.claims:
        if claim.stable_key == claim_key:
            return claim.semantic_hash
    raise KeyError(claim_key)


def _validation_hash_for(snapshot: ReportInputSnapshot, claim_key: str) -> str:
    for claim in snapshot.semantic_payload.claims:
        if claim.stable_key == claim_key:
            return claim.validation_semantic_hash
    raise KeyError(claim_key)


def _evidence_hash_for(snapshot: ReportInputSnapshot, evidence_id: str) -> str:
    for stable_key, runtime_id in snapshot.runtime_references.evidence_ids.items():
        if runtime_id == evidence_id:
            for evidence in snapshot.semantic_payload.evidence:
                if evidence.stable_key == stable_key:
                    return evidence.semantic_hash
    raise KeyError(evidence_id)
