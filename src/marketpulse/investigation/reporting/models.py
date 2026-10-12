"""Frozen typed models for the immutable Phase 5 report input snapshot.

Trust boundary: ``ReportInputAssembler`` is the only Phase 5 component allowed
to read Phase 4.3 persisted state. It produces an immutable, content-addressed
``ReportInputSnapshot`` whose semantic hashes cover canonical semantic content
only. Runtime envelope fields (snapshot/run/investigation IDs, ``run_mode``,
``assembled_at``, and ``runtime_references``) never enter semantic identity, so
live and replay executions of the same semantic state hash identically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import Field, JsonValue, model_serializer

from marketpulse.investigation.domain.base import (
    Confidence,
    DomainModel,
    EntityId,
    NonEmptyText,
    Sha256,
)
from marketpulse.investigation.domain.enums import (
    ClaimImportance,
    ClaimType,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EntailmentStatus,
    FindingSeverity,
    GapSeverity,
    GapStatus,
    RelationStance,
    ReleaseDecision,
    ReportReleaseStatus,
    ReportReviewStatus,
    ReportValidatorKind,
    ResearchGapType,
    RunMode,
    TimePrecision,
    ValidationStatus,
)
from marketpulse.investigation.reporting.hashing import canonical_hash

SNAPSHOT_HASH_DOMAIN = "phase5-report-input-snapshot-v1"
CLAIM_SET_HASH_DOMAIN = "phase5-report-claim-set-v1"
SOURCE_STATE_HASH_DOMAIN = "phase5-report-source-state-v1"
CITATION_HASH_DOMAIN = "phase5-citation-v1"
EVALUATION_HASH_DOMAIN = "phase5-release-evaluation-v1"


class SnapshotClaim(DomainModel):
    """Claim state addressed by stable semantic key, not runtime row ID."""

    stable_key: NonEmptyText
    semantic_hash: Sha256
    statement: NonEmptyText
    claim_type: ClaimType
    validation_status: ValidationStatus
    confidence: Confidence
    validation_semantic_hash: Sha256
    importance: ClaimImportance
    is_critical: bool
    report_section: str | None = None


class SnapshotEvidence(DomainModel):
    """Evidence state addressed by stable key plus immutable content hashes."""

    stable_key: NonEmptyText
    semantic_hash: Sha256
    content_hash: Sha256
    artifact_content_hash: Sha256
    snapshot_content_hash: Sha256
    source_semantic_key: NonEmptyText


class SnapshotRelation(DomainModel):
    """Claim-Evidence relation in stable-key form."""

    stable_key: NonEmptyText
    claim_stable_key: NonEmptyText
    evidence_stable_key: NonEmptyText
    stance: RelationStance
    entailment_status: EntailmentStatus


class SnapshotConflict(DomainModel):
    stable_key: NonEmptyText
    conflict_type: ConflictType
    severity: ConflictSeverity
    status: ConflictStatus
    claim_stable_keys: tuple[NonEmptyText, ...]
    semantic_hash: Sha256
    resolution_summary: str | None = None
    possible_explanations: tuple[str, ...] = ()
    competing_values: tuple[JsonValue, ...] = ()


class SnapshotResearchGap(DomainModel):
    stable_key: NonEmptyText
    gap_type: ResearchGapType
    severity: GapSeverity
    status: GapStatus
    reason: NonEmptyText
    target_claim_stable_key: str | None = None
    target_question_stable_key: str | None = None
    suggested_actions: tuple[NonEmptyText, ...] = ()


class SnapshotTimelineEvent(DomainModel):
    stable_key: NonEmptyText
    event_time: datetime | None
    time_precision: TimePrecision
    description: NonEmptyText
    validation_status: ValidationStatus
    supporting_evidence_stable_keys: tuple[NonEmptyText, ...] = ()


class SnapshotSource(DomainModel):
    """Per-source semantic state contributing to the source fingerprint."""

    source_semantic_key: NonEmptyText
    family_key: NonEmptyText
    independence_role: NonEmptyText


class ReportInputSemanticPayload(DomainModel):
    """Canonical semantic content of a report input snapshot.

    Only this payload is covered by ``snapshot_hash``. Collections default to
    empty so the assembler can extend coverage without changing existing hash
    semantics for absent categories.
    """

    schema_version: NonEmptyText
    validation_policy_version: NonEmptyText
    investigation_key: NonEmptyText
    terminal_run_status: NonEmptyText
    execution_provenance: str = "PROVIDER_WORKFLOW"
    questions: tuple[NonEmptyText, ...]
    claims: tuple[SnapshotClaim, ...]
    evidence: tuple[SnapshotEvidence, ...]
    relations: tuple[SnapshotRelation, ...] = ()
    conflicts: tuple[SnapshotConflict, ...] = ()
    research_gaps: tuple[SnapshotResearchGap, ...] = ()
    timeline_events: tuple[SnapshotTimelineEvent, ...] = ()
    sources: tuple[SnapshotSource, ...] = ()
    limitations: tuple[NonEmptyText, ...] = ()
    quantitative_material: str | None = None
    comparison_material: str | None = None

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        value = handler(self)
        if self.quantitative_material is None:
            value.pop("quantitative_material", None)
        if self.comparison_material is None:
            value.pop("comparison_material", None)
        return value


class SnapshotRuntimeReferences(DomainModel):
    """Runtime row IDs captured for traceability only; never hashed."""

    claim_ids: dict[str, EntityId] = Field(default_factory=dict)
    evidence_ids: dict[str, EntityId] = Field(default_factory=dict)


class ReportInputSnapshot(DomainModel):
    """Immutable, content-addressed report input.

    The runtime envelope (``snapshot_id``, ``investigation_id``, ``run_id``,
    ``run_mode``, ``assembled_at``, ``runtime_references``) is excluded from
    every semantic hash.
    """

    snapshot_id: EntityId
    investigation_id: EntityId
    run_id: EntityId
    run_mode: RunMode
    assembled_at: datetime
    semantic_payload: ReportInputSemanticPayload
    runtime_references: SnapshotRuntimeReferences = Field(default_factory=SnapshotRuntimeReferences)
    snapshot_hash: Sha256
    claim_set_hash: Sha256
    source_state_fingerprint: Sha256

    @classmethod
    def build(
        cls,
        *,
        snapshot_id: EntityId,
        investigation_id: EntityId,
        run_id: EntityId,
        run_mode: RunMode,
        assembled_at: datetime,
        semantic_payload: ReportInputSemanticPayload,
        runtime_references: SnapshotRuntimeReferences | None = None,
    ) -> Self:
        return cls(
            snapshot_id=snapshot_id,
            investigation_id=investigation_id,
            run_id=run_id,
            run_mode=run_mode,
            assembled_at=assembled_at,
            semantic_payload=semantic_payload,
            runtime_references=runtime_references or SnapshotRuntimeReferences(),
            snapshot_hash=compute_snapshot_hash(semantic_payload),
            claim_set_hash=compute_claim_set_hash(semantic_payload),
            source_state_fingerprint=compute_source_state_fingerprint(semantic_payload),
        )


def compute_snapshot_hash(payload: ReportInputSemanticPayload) -> str:
    """Hash the complete canonical semantic payload."""
    if payload.comparison_material is not None:
        return canonical_hash("quant-comparison-input-snapshot-v1", payload)
    domain = (
        "quant-report-input-snapshot-v2"
        if payload.quantitative_material is not None
        else SNAPSHOT_HASH_DOMAIN
    )
    return canonical_hash(domain, payload)


def compute_claim_set_hash(payload: ReportInputSemanticPayload) -> str:
    """Hash the claim set, including statements and validation semantics."""
    domain = (
        "quant-report-claim-set-v2"
        if payload.quantitative_material is not None
        else CLAIM_SET_HASH_DOMAIN
    )
    return canonical_hash(domain, list(payload.claims))


def compute_source_state_fingerprint(payload: ReportInputSemanticPayload) -> str:
    """Hash stable provenance state: claim keys, validation hashes, relations,
    conflict/gap/timeline state, artifact/snapshot hashes, source identity, and
    schema/policy versions."""
    material = {
        "schema_version": payload.schema_version,
        "validation_policy_version": payload.validation_policy_version,
        "claims": [
            {
                "stable_key": claim.stable_key,
                "semantic_hash": claim.semantic_hash,
                "validation_semantic_hash": claim.validation_semantic_hash,
                "validation_status": claim.validation_status,
            }
            for claim in payload.claims
        ],
        "relations": list(payload.relations),
        "conflicts": list(payload.conflicts),
        "research_gaps": list(payload.research_gaps),
        "timeline_events": list(payload.timeline_events),
        "evidence": [
            {
                "stable_key": evidence.stable_key,
                "semantic_hash": evidence.semantic_hash,
                "content_hash": evidence.content_hash,
                "artifact_content_hash": evidence.artifact_content_hash,
                "snapshot_content_hash": evidence.snapshot_content_hash,
                "source_semantic_key": evidence.source_semantic_key,
            }
            for evidence in payload.evidence
        ],
        "sources": list(payload.sources),
    }
    if payload.quantitative_material is not None:
        material["quantitative_material"] = payload.quantitative_material
        return canonical_hash("quant-report-source-state-v2", material)
    if payload.comparison_material is not None:
        material["comparison_material"] = payload.comparison_material
        return canonical_hash("quant-comparison-source-state-v1", material)
    return canonical_hash(SOURCE_STATE_HASH_DOMAIN, material)


class Citation(DomainModel):
    """Persisted citation: stable semantic identity plus runtime envelope."""

    citation_id: EntityId
    report_id: EntityId
    display_ordinal: int = Field(ge=0)
    claim_id: EntityId
    evidence_id: EntityId
    created_at: datetime
    report_input_snapshot_hash: Sha256
    claim_set_hash: Sha256
    section_key: NonEmptyText
    unit_key: NonEmptyText
    claim_semantic_hash: Sha256
    validation_semantic_hash: Sha256
    relation_semantics: NonEmptyText
    evidence_semantic_hash: Sha256
    canonical_locator: dict[str, JsonValue]
    resolved_quote_hash: Sha256
    artifact_content_hash: Sha256
    snapshot_content_hash: Sha256
    source_semantic_identity: NonEmptyText
    entailment_judgment: NonEmptyText
    entailment_version: NonEmptyText
    schema_version: NonEmptyText
    citation_hash: Sha256

    def semantic_identity(self) -> CitationSemanticIdentity:
        return CitationSemanticIdentity(
            report_input_snapshot_hash=self.report_input_snapshot_hash,
            claim_set_hash=self.claim_set_hash,
            section_key=self.section_key,
            unit_key=self.unit_key,
            claim_semantic_hash=self.claim_semantic_hash,
            validation_semantic_hash=self.validation_semantic_hash,
            relation_semantics=self.relation_semantics,
            evidence_semantic_hash=self.evidence_semantic_hash,
            canonical_locator=self.canonical_locator,
            resolved_quote_hash=self.resolved_quote_hash,
            artifact_content_hash=self.artifact_content_hash,
            snapshot_content_hash=self.snapshot_content_hash,
            source_semantic_identity=self.source_semantic_identity,
            entailment_judgment=self.entailment_judgment,
            entailment_version=self.entailment_version,
            schema_version=self.schema_version,
        )

    @classmethod
    def issue(
        cls,
        *,
        citation_id: EntityId,
        report_id: EntityId,
        display_ordinal: int,
        claim_id: EntityId,
        evidence_id: EntityId,
        created_at: datetime,
        identity: CitationSemanticIdentity,
    ) -> Self:
        return cls(
            citation_id=citation_id,
            report_id=report_id,
            display_ordinal=display_ordinal,
            claim_id=claim_id,
            evidence_id=evidence_id,
            created_at=created_at,
            citation_hash=identity.semantic_hash,
            **identity.model_dump(),
        )


class ReportValidationFinding(DomainModel):
    """Append-only validator finding; any HARD finding blocks release."""

    finding_id: EntityId
    report_id: EntityId
    validator: ReportValidatorKind
    severity: FindingSeverity
    code: NonEmptyText
    detail: NonEmptyText
    section_key: NonEmptyText | None = None
    unit_key: NonEmptyText | None = None
    claim_stable_key: NonEmptyText | None = None
    validator_version: NonEmptyText
    created_at: datetime


class ReleasePolicyEvaluation(DomainModel):
    """Immutable deterministic release-policy evaluation for a report version."""

    evaluation_id: EntityId
    report_id: EntityId
    policy_version: NonEmptyText
    decision: ReleaseDecision
    release_status: ReportReleaseStatus
    review_status: ReportReviewStatus
    evaluation_hash: Sha256
    report_hash: Sha256
    claim_set_hash: Sha256
    citation_set_hash: Sha256
    hard_finding_count: int = Field(ge=0)
    governance_finding_count: int = Field(ge=0)
    basis: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def build(
        cls,
        *,
        evaluation_id: EntityId,
        report_id: EntityId,
        policy_version: NonEmptyText,
        decision: ReleaseDecision,
        release_status: ReportReleaseStatus,
        review_status: ReportReviewStatus,
        report_hash: Sha256,
        claim_set_hash: Sha256,
        citation_set_hash: Sha256,
        hard_finding_count: int,
        governance_finding_count: int,
        basis: dict[str, JsonValue],
        created_at: datetime,
    ) -> Self:
        evaluation_hash = canonical_hash(
            EVALUATION_HASH_DOMAIN,
            {
                "policy_version": policy_version,
                "decision": decision,
                "release_status": release_status,
                "review_status": review_status,
                "report_hash": report_hash,
                "claim_set_hash": claim_set_hash,
                "citation_set_hash": citation_set_hash,
                "hard_finding_count": hard_finding_count,
                "governance_finding_count": governance_finding_count,
                "basis": basis,
            },
        )
        return cls(
            evaluation_id=evaluation_id,
            report_id=report_id,
            policy_version=policy_version,
            decision=decision,
            release_status=release_status,
            review_status=review_status,
            evaluation_hash=evaluation_hash,
            report_hash=report_hash,
            claim_set_hash=claim_set_hash,
            citation_set_hash=citation_set_hash,
            hard_finding_count=hard_finding_count,
            governance_finding_count=governance_finding_count,
            basis=basis,
            created_at=created_at,
        )


class CitationSemanticIdentity(DomainModel):
    """Stable semantic identity of a citation.

    Runtime row IDs (``citation_id``), replay-created IDs, timestamps, and
    display ordinals are never part of this model, so they can never enter the
    citation semantic hash.
    """

    report_input_snapshot_hash: Sha256
    claim_set_hash: Sha256
    section_key: NonEmptyText
    unit_key: NonEmptyText
    claim_semantic_hash: Sha256
    validation_semantic_hash: Sha256
    relation_semantics: NonEmptyText
    evidence_semantic_hash: Sha256
    canonical_locator: dict[str, JsonValue]
    resolved_quote_hash: Sha256
    artifact_content_hash: Sha256
    snapshot_content_hash: Sha256
    source_semantic_identity: NonEmptyText
    entailment_judgment: NonEmptyText
    entailment_version: NonEmptyText
    schema_version: NonEmptyText

    def semantic_hash_payload(self) -> dict[str, JsonValue]:
        """Canonical semantic fields; runtime IDs and display ordinals excluded."""
        return self.model_dump(mode="json")

    @property
    def semantic_hash(self) -> str:
        domain = (
            "quant-citation-v2"
            if self.schema_version == "computation-citation-v2"
            else CITATION_HASH_DOMAIN
        )
        return canonical_hash(domain, self.semantic_hash_payload())
