import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from marketpulse.investigation.domain.enums import ReportType, RunMode, RunStatus, WorkflowPhase
from marketpulse.investigation.domain.runtime import (
    Investigation,
    InvestigationRun,
    InvestigationScope,
)
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.reporting.persistence import ReportGovernanceRepository
from marketpulse.investigation.reporting.pipeline import ReportPipeline
from marketpulse.investigation.reporting.writer import DeterministicWriter
from marketpulse.quant.contracts import canonical
from marketpulse.quant.domain import MetricSpec
from marketpulse.quant.evidence import build_computation_evidence
from marketpulse.quant.execution.service import QuantService
from marketpulse.quant.reporting import build_material, persist_material
from marketpulse.quant.storage.models import ComputeJobRow
from marketpulse.quant.validation import verify_cell, verify_computation_claim
from tests.integration.quant.test_snapshots import store  # noqa: F401
from tests.unit.quant.test_contracts_data import ID, result


@pytest.fixture
def setup_quant(store):  # noqa: F811
    repository = InvestigationRepository(store.sessions)
    now = datetime.now(UTC)
    repository.add(
        Investigation(
            investigation_id="QI-1",
            title="Synthetic quant fixture",
            event_description="Synthetic prices, not real market evidence",
            investigation_goal="Test computation",
            scope=InvestigationScope(summary="fixture"),
            created_at=now,
            updated_at=now,
        )
    )
    repository.add(
        InvestigationRun(
            run_id="QR-1",
            investigation_id="QI-1",
            mode=RunMode.LIVE,
            status=RunStatus.READY_FOR_REPORT,
            current_phase=WorkflowPhase.REPORT,
            workflow_version="quant-v1",
            checkpoint_version=1,
            state_version=1,
            created_at=now,
            updated_at=now,
        )
    )
    data = result()
    rows = [
        {
            "instrument_id": ID,
            "date": f"2024-06-{day:02d}",
            "close": close,
            "provisional": False,
            "trading_status": True,
        }
        for day, close in [(12, 100), (13, 110), (14, 99)]
    ]
    snapshot = store.freeze(
        data.model_copy(
            update={
                "records_json": canonical(rows),
                "raw_records_json": canonical(rows),
                "units": (("close", "CNY/share"),),
            }
        )
    )
    store.attach(snapshot.snapshot_id, "QI-1")
    return QuantService(store, root=Path.cwd()), repository, snapshot


async def submit(service, snapshot, **extra):
    return await service.submit(
        run_id="QR-1",
        spec=MetricSpec(),
        inputs=(snapshot.snapshot_id,),
        instrument_id=ID,
        asof=snapshot.result.request.asof,
        idempotency_key="metrics-1",
        **extra,
    )


async def test_subprocess_exact_reproduce_recording_and_idempotency(setup_quant):
    service, _, snapshot = setup_quant
    job = await submit(service, snapshot)
    first = service.bundle_for(job)
    second = await service.reproduce(job_id=job)
    assert first.output_hash == second.output_hash
    assert first.manifest_hash == second.manifest_hash
    assert await submit(service, snapshot) == job
    with service.sessions() as session:
        input_hash = session.get(ComputeJobRow, job).input_hash
    replay = service.journal.replay(
        job_id=job,
        input_hash=input_hash,
        load_artifact=service.load_artifact,
    )
    assert replay.output_hash == first.output_hash
    assert not service.worker.active_pids


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit", "percent"),
        ("start", "1990-01-01"),
        ("artifact_hash", "0" * 64),
        ("value", "0"),
        ("producer_families", ("mirror_A", "mirror_B")),
    ],
)
async def test_cell_tamper_and_same_source_wrappers_rejected(setup_quant, field, value):
    service, _, snapshot = setup_quant
    job = await submit(service, snapshot)
    bundle = service.bundle_for(job)
    index = next(i for i, v in enumerate(bundle.values) if v.name == "return")
    evidence = build_computation_evidence(bundle, metric_path=f"/values/{index}")
    validation = await verify_computation_claim(service, job_id=job, evidence=evidence)
    assert validation.reproduction == "REPRODUCIBLE"
    assert validation.status == "UNVERIFIED"
    assert "independence" in validation.missing
    with pytest.raises(ValueError):
        verify_cell(bundle, evidence.model_copy(update={field: value}))
    with pytest.raises(ValueError):
        verify_cell(bundle.model_copy(update={"output_hash": "0" * 64}), evidence)


async def test_report_reproduces_once_but_checks_each_claim(setup_quant, monkeypatch):
    service, _, snapshot = setup_quant
    job = await submit(service, snapshot)
    original = service.reproduce
    calls = 0

    async def counted(**kwargs):
        nonlocal calls
        calls += 1
        return await original(**kwargs)

    monkeypatch.setattr(service, "reproduce", counted)
    material = await build_material(service, job_id=job)
    assert len(material.claims) > 1
    assert calls == 1
    assert all(c.validation.reproduction == "REPRODUCIBLE" for c in material.claims)
    assert all(c.validation.status == "UNVERIFIED" for c in material.claims)
    from marketpulse.quant.validation import verify_computation_claims

    evidence = tuple(c.evidence for c in material.claims)
    forged = evidence[:-1] + (evidence[-1].model_copy(update={"unit": "forged"}),)
    with pytest.raises(ValueError, match="binding mismatch"):
        await verify_computation_claims(service, job_id=job, evidence=forged)
    assert calls == 1  # Reject any forged cell before spending a reproduction worker.
    validations = await verify_computation_claims(service, job_id=job, evidence=evidence)
    assert validations == tuple(c.validation for c in material.claims)
    assert calls == 2


async def test_quant_report_v2_citations_partial_and_folded(setup_quant, monkeypatch):
    service, repository, snapshot = setup_quant
    job = await submit(service, snapshot)
    material = await build_material(service, job_id=job)
    persist_material(service.sessions, run_id="QR-1", material=material)
    original_input = service.input_for
    input_reads = 0

    def counted_input(job_id, **kwargs):
        nonlocal input_reads
        input_reads += 1
        return original_input(job_id, **kwargs)

    monkeypatch.setattr(service, "input_for", counted_input)
    report = await ReportPipeline(
        service.sessions, repository, DeterministicWriter(), quant_service=service
    ).generate(run_id="QR-1", report_type=ReportType.INVESTIGATION_STATUS, now=datetime.now(UTC))
    assert report.snapshot.semantic_payload.schema_version == "quant-report-input-v2"
    assert report.report.schema_version == "quant-report-v2"
    assert report.hard_finding_count == 0, report.findings
    assert input_reads == 2  # Fresh source validation at build AND validation boundaries.
    assert len(report.citations) >= 3
    assert all(c.canonical_locator["locator_type"] == "COMPUTATION_CELL" for c in report.citations)
    assert "<details>" in report.markdown
    assert "本支路不调用模型生成数值" in report.markdown
    assert "运行结束时状态为“调查完成”" not in report.markdown
    assert not any(not s.units for s in report.draft.sections)
    assert material.completeness == "部分完整"
    for claim in material.claims:
        assert report.markdown.count(claim.statement) == 1
    with service.sessions() as session:
        citations = ReportGovernanceRepository(service.sessions).citations_for_report_in_session(
            session, report.report.report_id
        )
    assert len(citations) == len(report.citations)
    # Quant facts are read-only: a writer cannot change them then supply a trusted hash.
    from marketpulse.investigation.reporting.validation import ReportValidator

    section = next(s for s in report.draft.sections if s.section_key == "QUANTITATIVE_FINDINGS")
    changed = section.model_copy(
        update={
            "units": (
                section.units[0].model_copy(update={"text": "尚未证实：收益99%"}),
                *section.units[1:],
            )
        }
    )
    draft = report.draft.model_copy(
        update={"sections": tuple(changed if s == section else s for s in report.draft.sections)}
    )
    findings = ReportValidator().validate(
        draft=draft,
        snapshot=report.snapshot,
        citations=list(report.citations),
        report=report.report,
        now=datetime.now(UTC),
    )
    assert any(f.code == "QUANT_NUMERIC_WORDING_DRIFT" for f in findings)
    # A new validation boundary must reload sources, even after a successful build.
    from marketpulse.infrastructure.storage.models import BlobRef
    from marketpulse.investigation.reporting.validation import CitationValidator

    ref = BlobRef.from_uri(snapshot.manifest_ref)
    path = service.blobs._path(ref)
    path.write_bytes(b"corrupt original input after report build")
    with service.sessions() as session:
        broken = CitationValidator(repository, service).validate(
            session,
            citations=list(report.citations),
            snapshot=report.snapshot,
            report=report.report,
            now=datetime.now(UTC),
        )
    assert len(broken) == len(report.citations)
    assert all(f.code == "COMPUTATION_CITATION_INTEGRITY" for f in broken)


async def test_hard_timeout_clears_process_and_no_artifact(setup_quant):
    service, _, snapshot = setup_quant
    with pytest.raises(TimeoutError):
        await submit(service, snapshot, budget_seconds=0.000001)
    assert not service.worker.active_pids
    with service.sessions() as session:
        job = session.scalar(select(ComputeJobRow))
        assert job.status == "FAILED" and job.artifact_id is None


async def test_cancellation_kills_waits_and_records(setup_quant):
    service, _, snapshot = setup_quant
    task = asyncio.create_task(submit(service, snapshot))
    async with asyncio.timeout(10):
        await service.worker.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service.worker.active_pids
    with service.sessions() as session:
        job = session.scalar(select(ComputeJobRow))
        assert job.status == "CANCELLED" and job.artifact_id is None


async def test_expired_lease_recovery_and_idempotency_drift(setup_quant):
    from datetime import timedelta

    service, _, snapshot = setup_quant
    with pytest.raises(TimeoutError):
        await submit(service, snapshot, budget_seconds=0.000001)
    with service.sessions.begin() as session:
        old = session.scalar(select(ComputeJobRow))
        old.status = "RUNNING"
        old.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        old_id = old.job_id
    assert await submit(service, snapshot) == old_id
    with service.sessions() as session:
        assert session.get(ComputeJobRow, old_id).attempt == 2
    with pytest.raises(ValueError, match="idempotency"):
        await service.submit(
            run_id="QR-1",
            spec=MetricSpec(annual_sessions=250),
            inputs=(snapshot.snapshot_id,),
            instrument_id=ID,
            asof=snapshot.result.request.asof,
            idempotency_key="metrics-1",
        )


async def test_frozen_input_hash_and_descriptor_tamper_rejected(setup_quant):
    import hashlib
    import json

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from marketpulse.quant.execution.service import check_input_binding

    service, _, snapshot = setup_quant
    job = await submit(service, snapshot)
    bundle = service.bundle_for(job)
    with pytest.raises(IntegrityError), service.sessions.begin() as session:
        session.execute(
            text("UPDATE inv_quant_compute_job SET input_ref=:ref WHERE job_id=:job"),
            {"ref": "sha256:" + "0" * 64, "job": job},
        )
    changed = json.loads(bundle.manifest_json)
    changed["inputs"][0]["family"] = "fake_independent_producer"
    manifest = canonical(changed)
    manifest_hash = hashlib.sha256(manifest.encode()).hexdigest()
    tampered = bundle.model_copy(
        update={
            "manifest_json": manifest,
            "manifest_hash": manifest_hash,
            "artifact_id": "qart_" + manifest_hash,
        }
    )
    with pytest.raises(ValueError, match="descriptor"):
        check_input_binding(tampered, service.input_for(job))


async def test_worker_cannot_label_another_python_environment(setup_quant):
    import json

    service, _, snapshot = setup_quant
    request = service._input(
        "QR-1", MetricSpec(), (snapshot.snapshot_id,), ID, snapshot.result.request.asof
    )
    changed = json.loads(request.environment_json)
    changed["python"] = "fake-python"
    with pytest.raises(ValueError, match="offline computation failed"):
        await service.worker.run(
            request.model_copy(update={"environment_json": canonical(changed)})
        )
    assert not service.worker.active_pids


@pytest.mark.parametrize(
    "shares,profit,equity,beginning",
    [
        ("1000", "100", "400", "200"),
        ("2000", "500", "600", "400"),
        ("3000", "-100", "800", "500"),
    ],
)
async def test_frozen_synthetic_financials_recomputed_in_worker(
    setup_quant, shares, profit, equity, beginning
):
    from datetime import date
    from decimal import Decimal

    from tests.unit.quant.test_contracts_data import request

    service, _, snapshot = setup_quant
    rows = [
        {
            "instrument_id": ID,
            "period_end": end,
            "period_start": start,
            "available_at": available,
            "share_basis": "ordinary_total_v1",
            "statement_basis": "parent",
            "period_basis": basis,
            "revision": "synthetic-v1-not-real-market-evidence",
            name: value,
        }
        for name, value, end, start, available, basis in [
            ("total_shares", shares, "2024-06-14", None, "2024-06-14T06:00:00+00:00", "point"),
            (
                "parent_net_profit",
                profit,
                "2024-03-31",
                "2023-04-01",
                "2024-04-25T00:00:00+00:00",
                "ttm",
            ),
            ("parent_equity", equity, "2024-03-31", None, "2024-04-25T00:00:00+00:00", "point"),
            (
                "parent_equity_begin",
                beginning,
                "2023-03-31",
                None,
                "2023-04-25T00:00:00+00:00",
                "point",
            ),
        ]
    ]
    units = (
        ("total_shares", "share"),
        ("parent_net_profit", "CNY"),
        ("parent_equity", "CNY"),
        ("parent_equity_begin", "CNY"),
        ("net_profit", "CNY"),
        ("revenue", "CNY"),
    )
    rows.extend(
        {
            "instrument_id": ID,
            "period_end": f"{year}-03-31",
            "period_start": f"{year}-01-01",
            "available_at": f"{year}-04-25T00:00:00+00:00",
            "share_basis": "ordinary_total_v1",
            "statement_basis": "parent",
            "period_basis": "ytd",
            "revision": "synthetic-v1-not-real-market-evidence",
            "net_profit": net_profit,
            "revenue": revenue,
        }
        for year, net_profit, revenue in [(2023, "25", "1000"), (2024, "30", "1100")]
    )
    data = result(
        req=request(
            dataset="financial", start=date(2023, 3, 31), fields=tuple(name for name, _ in units)
        )
    )
    frozen = service.store.freeze(
        data.model_copy(
            update={
                "records_json": canonical(rows),
                "raw_records_json": canonical(rows),
                "units": units,
            }
        )
    )
    service.store.attach(frozen.snapshot_id, "QI-1")
    job = await service.submit(
        run_id="QR-1",
        spec=MetricSpec(),
        inputs=(snapshot.snapshot_id, frozen.snapshot_id),
        instrument_id=ID,
        asof=snapshot.result.request.asof,
        idempotency_key="synthetic-financials",
    )
    bundle = service.bundle_for(job)
    values = {v.name: v for v in bundle.values if not v.row_keys}
    expected_pe = Decimal(99) * Decimal(shares) / Decimal(profit)
    if Decimal(profit) > 0:
        assert values["pe"].value == expected_pe
    else:
        assert values["pe"].value is None
        assert values["pe"].missing_reason == "nonpositive_earnings"
    assert values["pb"].value == Decimal(99) * Decimal(shares) / Decimal(equity)
    expected_roe = Decimal(profit) / ((Decimal(equity) + Decimal(beginning)) / 2)
    assert abs(values["roe"].value - expected_roe) < Decimal("1e-27")
    assert values["net_profit_growth"].value == Decimal("0.2")
    assert values["revenue_growth"].value == Decimal("0.1")
    assert (await service.reproduce(job_id=job)).output_hash == bundle.output_hash


async def test_report_rechecks_inputs_after_validation(setup_quant):
    from marketpulse.infrastructure.storage.models import BlobRef

    service, repository, snapshot = setup_quant
    job = await submit(service, snapshot)
    material = await build_material(service, job_id=job)
    persist_material(service.sessions, run_id="QR-1", material=material)
    # Corrupt only this test's exact frozen blob, after latest validation exists.
    ref = BlobRef.from_uri(snapshot.partitions[0].blob_ref)
    service.blobs._path(ref).write_bytes(b"synthetic corrupt partition")
    report = await ReportPipeline(
        service.sessions, repository, DeterministicWriter(), quant_service=service
    ).generate(run_id="QR-1", report_type=ReportType.INVESTIGATION_STATUS, now=datetime.now(UTC))
    assert report.hard_finding_count > 0
    assert not report.citations
    assert any(f.code == "COMPUTATION_CITATION_INTEGRITY" for f in report.findings)


async def test_other_adjustment_does_not_inflate_claim_independence(setup_quant):
    service, _, snapshot = setup_quant
    primary = service.store.freeze(
        snapshot.result.model_copy(
            update={
                "lineage_status": "verified",
                "source_family": "original_a",
                "provenance_json": canonical({"quality_score": 0.8}),
            }
        )
    )
    other = service.store.freeze(
        snapshot.result.model_copy(
            update={
                "request": snapshot.result.request.model_copy(
                    update={
                        "adjustment": "hfq",
                        "adjustment_anchor": snapshot.result.request.end,
                    }
                ),
                "lineage_status": "verified",
                "source_family": "original_b",
                "provenance_json": canonical({"quality_score": 0.8}),
            }
        )
    )
    for frozen in (primary, other):
        service.store.attach(frozen.snapshot_id, "QI-1")
    job = await service.submit(
        run_id="QR-1",
        spec=MetricSpec(),
        inputs=(primary.snapshot_id, other.snapshot_id),
        instrument_id=ID,
        asof=primary.result.request.asof,
        idempotency_key="price-basis-gate",
    )
    bundle = service.bundle_for(job)
    index = next(i for i, value in enumerate(bundle.values) if value.name == "return")
    evidence = build_computation_evidence(bundle, metric_path=f"/values/{index}")
    validation = await verify_computation_claim(service, job_id=job, evidence=evidence)
    assert validation.independent_families == 1
    assert validation.adequate_sources == 1
    assert "independence" in validation.missing and validation.status == "UNVERIFIED"


async def test_quant_persistence_failure_never_completes(setup_quant, monkeypatch):
    from marketpulse.config import Settings
    from marketpulse.investigation.live_runtime import LiveInvestigationService
    from marketpulse.investigation.persistence.models import InvestigationRunRow, ReportRow

    service, repository, snapshot = setup_quant
    runtime = LiveInvestigationService(
        sessions=service.sessions,
        repository=repository,
        settings=Settings(_env_file=None),
        blob_root=service.blobs._root,
        quant_service=service,
    )

    async def failed(*args, **kwargs):
        raise OSError("fixture storage unavailable")

    monkeypatch.setattr(runtime, "_report", failed)
    monkeypatch.setattr(ReportPipeline, "minimal", failed)
    runtime.start_quant(
        run_id="QR-1",
        spec=MetricSpec(),
        inputs=(snapshot.snapshot_id,),
        instrument_id=ID,
        asof=snapshot.result.request.asof,
        idempotency_key="storage-failure",
    )
    await runtime.tasks["QR-1"]
    with service.sessions() as session:
        run = session.get(InvestigationRunRow, "QR-1")
        assert run.status is RunStatus.BLOCKED and run.completed_at is None
        assert not session.scalar(select(ReportRow).where(ReportRow.run_id == "QR-1"))
    assert not service.worker.active_pids


@pytest.mark.parametrize("cancel", [False, True])
async def test_live_quant_terminal_requires_durable_report_without_model(setup_quant, cancel):
    from marketpulse.config import Settings
    from marketpulse.investigation.live_runtime import LiveInvestigationService
    from marketpulse.investigation.persistence.models import InvestigationRunRow, ReportRow

    service, repository, snapshot = setup_quant
    runtime = LiveInvestigationService(
        sessions=service.sessions,
        repository=repository,
        settings=Settings(_env_file=None),
        blob_root=service.blobs._root,
        quant_service=service,
    )
    runtime.start_quant(
        run_id="QR-1",
        spec=MetricSpec(),
        inputs=(snapshot.snapshot_id,),
        instrument_id=ID,
        asof=snapshot.result.request.asof,
        idempotency_key="live-quant",
    )
    if cancel:
        async with asyncio.timeout(10):
            await service.worker.started.wait()
        assert await runtime.cancel("QR-1")
    else:
        await runtime.tasks["QR-1"]
    with service.sessions() as session:
        assert session.get(InvestigationRunRow, "QR-1").status == RunStatus.COMPLETED
        assert session.scalar(select(ReportRow).where(ReportRow.run_id == "QR-1"))
    assert not service.worker.active_pids
