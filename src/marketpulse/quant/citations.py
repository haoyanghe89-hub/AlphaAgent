from __future__ import annotations

from .contracts import canonical, digest
from .evidence import computation_evidence_cells
from .reporting import load_material
from .storage.models import ComputeJobRow, SnapshotOwnershipRow


class _ComputationCitationBatch:
    """One build/validation boundary, never shared across reports or requests."""

    def __init__(self, service):
        self.service = service
        self._checked = {}

    def bundle(self, job):
        key = (job.job_id, job.artifact_id)
        if key not in self._checked:
            from marketpulse.infrastructure.storage.models import BlobError

            from .execution.service import check_input_binding

            bundle = self.service.load_artifact(job.artifact_id)
            try:
                check_input_binding(
                    bundle, self.service.input_for(job.job_id, require_current_environment=False)
                )
            except BlobError as error:
                raise ValueError("frozen citation input is missing or corrupt") from error
            proofs = {e.metric_path: e for e in computation_evidence_cells(bundle)}
            self._checked[key] = (bundle, proofs)
        return self._checked[key]


def public_computation_citation(session, request, citation_id):
    from marketpulse.investigation.api import (
        CitationClaimOut,
        CitationDetailOut,
        CitationEvidenceOut,
        _citation_out,
        _error,
    )
    from marketpulse.investigation.persistence.models import ClaimRow, ReportRow
    from marketpulse.investigation.reporting.models import Citation

    from .storage.models import QuantCitationRow

    row = session.get(QuantCitationRow, citation_id)
    if row is None:
        return None
    runtime = getattr(request.app.state, "quant_runtime", None)
    if runtime is None:
        raise _error(503, "QUANT_NOT_CONFIGURED", "计算引用需要已装配的量化服务。")
    citation = Citation.model_validate(row.payload)
    report = session.get(ReportRow, row.report_id)
    try:
        data = runtime.presentation(report.run_id)
        cell = next(
            v
            for v in data["values"]
            if v["metric_path"] == citation.canonical_locator["metric_path"]
        )
        if (
            cell["cell_hash"] != citation.canonical_locator["cell_hash"]
            or cell["artifact_id"] != citation.canonical_locator["artifact_id"]
        ):
            raise ValueError("citation cell changed")
    except (ValueError, KeyError, StopIteration, PermissionError) as error:
        raise _error(
            409, "QUANT_INTEGRITY_FAILED", "计算引用与最新验证不一致，停止展示。"
        ) from error
    from .contracts import digest

    claim_row = session.get(ClaimRow, citation.claim_id + "-" + digest(report.run_id)[:8])
    if claim_row is None:
        raise _error(409, "QUANT_INTEGRITY_FAILED", "持久化计算声明不存在。")
    return CitationDetailOut(
        citation=_citation_out(citation),
        claim=CitationClaimOut(
            claim_id=claim_row.claim_id,
            statement=claim_row.statement,
            claim_type=claim_row.claim_type.value,
            validation_status=claim_row.validation_status.value,
            confidence=claim_row.confidence,
            validation_basis="冻结输入确定性复算；可复现不等于独立验证。",
        ),
        evidence=CitationEvidenceOut(
            evidence_id=citation.evidence_id,
            snapshot_id=data["input_snapshot_ids"][0],
            artifact_id=cell["artifact_id"],
            excerpt=f"{cell['value']} {cell['unit']} · {cell['definition']}",
            exact_quote=None,
            locator_type="COMPUTATION_CELL",
            locator_payload=cell,
        ),
        source=None,
    )


def computation_citation(
    session, service, *, snapshot, claim_key, section_key, unit_key, report, ordinal, batch=None
):
    if service is None:
        raise ValueError("trusted quant service not bound")
    material = load_material(session, snapshot.run_id)
    if (
        material is None
        or canonical(material.model_dump(mode="json", exclude={"job_id"}))
        != snapshot.semantic_payload.quantitative_material
    ):
        raise ValueError("quantitative material is not the latest persisted validation")
    claim = next((c for c in material.claims if c.claim_id == claim_key), None)
    if claim is None:
        raise ValueError("unknown quantitative claim")
    job = session.get(ComputeJobRow, material.job_id)
    if (
        not job
        or job.run_id != snapshot.run_id
        or job.status != "COMPLETED"
        or job.artifact_id != material.artifact_id
    ):
        raise ValueError("artifact job binding mismatch")
    batch = batch or _ComputationCitationBatch(service)
    if batch.service is not service:
        raise ValueError("citation batch service mismatch")
    bundle, proofs = batch.bundle(job)
    if claim.evidence != proofs.get(claim.evidence.metric_path):
        raise ValueError("computation cell/lineage binding mismatch")
    from marketpulse.investigation.persistence.models import ClaimRow, ValidationResultRow

    claim_row_id = snapshot.runtime_references.claim_ids[claim_key]
    persisted_claim = session.get(ClaimRow, claim_row_id)
    validation_row = (
        session.get(ValidationResultRow, persisted_claim.latest_validation_id)
        if persisted_claim
        else None
    )
    if (
        not persisted_claim
        or persisted_claim.run_id != snapshot.run_id
        or persisted_claim.statement != claim.statement
        or not validation_row
    ):
        raise ValueError("persisted quantitative claim missing/drifted")
    if validation_row.validation_basis_payload.get("computation") != claim.validation.model_dump(
        mode="json"
    ):
        raise ValueError("latest quantitative validation changed")
    if str(persisted_claim.validation_status) != claim.validation.status:
        raise ValueError("quantitative status projection drift")
    for snapshot_id in bundle.input_snapshot_ids:
        if not session.get(SnapshotOwnershipRow, (snapshot.investigation_id, snapshot_id)):
            raise PermissionError("snapshot owner mismatch")
    if (
        not claim.validation.integrity
        or claim.validation.reproduction != "REPRODUCIBLE"
        or not claim.validation.exact_entailment
    ):
        raise ValueError("computation validation incomplete")
    expected = next(c for c in snapshot.semantic_payload.claims if c.stable_key == claim_key)
    if (
        claim.validation.validation_hash != expected.validation_semantic_hash
        or claim.statement != expected.statement
    ):
        raise ValueError("numeric claim drift")
    from marketpulse.investigation.reporting.models import Citation, CitationSemanticIdentity

    ev = claim.evidence
    identity = CitationSemanticIdentity(
        report_input_snapshot_hash=snapshot.snapshot_hash,
        claim_set_hash=snapshot.claim_set_hash,
        section_key=section_key,
        unit_key=unit_key,
        claim_semantic_hash=expected.semantic_hash,
        validation_semantic_hash=claim.validation.validation_hash,
        relation_semantics="SUPPORTS:ENTAILED",
        evidence_semantic_hash=ev.cell_hash,
        canonical_locator={
            "locator_type": "COMPUTATION_CELL",
            "artifact_id": ev.artifact_id,
            "metric_path": ev.metric_path,
            "row_keys": list(ev.row_keys),
            "cell_hash": ev.cell_hash,
        },
        resolved_quote_hash=ev.cell_hash,
        artifact_content_hash=ev.artifact_hash,
        snapshot_content_hash=digest(ev.input_snapshot_hashes),
        source_semantic_identity=digest(ev.producer_families),
        entailment_judgment="ENTAILED",
        entailment_version="deterministic-numeric-entailment-v1",
        schema_version="computation-citation-v2",
    )
    return Citation.issue(
        citation_id=f"CIT-{report.report_id}-{ordinal:04d}",
        report_id=report.report_id,
        display_ordinal=ordinal,
        claim_id=claim.claim_id,
        evidence_id=ev.evidence_id,
        created_at=report.created_at,
        identity=identity,
    )
