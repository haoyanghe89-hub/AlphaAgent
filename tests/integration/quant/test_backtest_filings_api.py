"""Synthetic documents and SDK packets; never used as preview evidence."""

import asyncio
import hashlib
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_api_o import freeze_inputs
from tests.integration.quant.test_backtest_inputs_api import source
from tests.integration.quant.test_history_acquisition_api import completed, create_parent


def fixture(app, client, monkeypatch, mode="complete"):
    from marketpulse.quant.data.statement_official import PROFILES

    freeze_inputs(app)
    parent = create_parent(client)
    history = client.post(
        f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
    ).json()["run_id"]
    client.portal.call(completed, app, history)
    original = client.get("/api/quant/backtests/" + history).json()
    instrument = original["data_check"]["instruments"][0]["instrument_id"]
    sample = client.post(
        f"/api/quant/backtests/{history}/inputs",
        json=dict(request_key=str(uuid4()), instrument_id=instrument, signal_year=2024),
    ).json()["run_id"]
    client.portal.call(completed, app, sample)
    monkeypatch.setattr("marketpulse.quant.backtest.filings.ISSUER", instrument)
    profiles, rows, documents = deepcopy(PROFILES), {}, {}
    for index, (profile_id, profile) in enumerate(profiles.items()):
        document = b"SYNTHETIC TEST DOCUMENT " + profile_id.encode()
        profile["sha256"] = hashlib.sha256(document).hexdigest()
        documents[profile_id] = document
        rows[profile_id] = dict(
            profile_id=profile_id,
            period_end=profile["period_end"],
            publication_date=profile["publication_date"],
            source_url=profile["url"],
            source_hash=profile["sha256"],
            document_sha256=profile["sha256"],
            source_pages=dict(summary=1, income=2),
            parent_net_profit=["20", "100", "30"][index],
            consolidated_net_profit=["21", "101", "31"][index],
            minority_profit="1",
            revenue=["100", "500", "110"][index],
            previous_revenue=["90", "450", "100"][index],
            weighted_roe="0.1",
            revenue_yoy=["0.1111", "0.1111", "0.1"][index],
        )
    monkeypatch.setattr("marketpulse.quant.backtest.filings.PROFILES", profiles)
    monkeypatch.setattr(
        "marketpulse.quant.backtest.filings.extract_statement", lambda doc, pid: rows[pid]
    )
    calls = []

    async def fetch(profile_id, store):
        calls.append(profile_id)
        if len(calls) > 1 and mode in {"failure", "stop"}:
            if mode == "stop":
                await asyncio.sleep(30)
            raise TimeoutError("synthetic test provider failure")
        document = documents[profile_id]
        store.blobs.put_bytes(document)
        return dict(
            provider="cninfo_official_reviewed",
            query=dict(kind="official_filing", profile_id=profile_id),
            rows=[rows[profile_id]],
            document_size=len(document),
            retrieved_at="2026-10-09T00:00:00Z",
        )

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_statement", fetch)
    return sample, profiles


def setup_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    async def fetch(query, **kwargs):
        result = source(query)
        if query["kind"] == "profit":
            # Exceed the CNY 1,000 rounding tolerance on purpose; tiny synthetic
            # statement amounts alone would correctly compare as MATCH.
            result["rows"][0].update(netProfit="100000", roeAvg="0.2")
        return result

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)


def test_filing_versions_replay_export_uuid_scope_and_document_corruption(tmp_path, monkeypatch):
    setup_sources(tmp_path, monkeypatch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent, profiles = fixture(app, client, monkeypatch)
        original = client.get("/api/quant/backtests/" + parent).json()
        body = dict(request_key=str(uuid4()))
        endpoint = f"/api/quant/backtests/{parent}/filings"
        response = client.post(endpoint, json=body)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        client.portal.call(completed, app, run_id)
        result = client.get("/api/quant/backtests/" + run_id)
        assert result.status_code == 200, result.text
        report = result.json()
        audit = report["filing_audit"]
        assert report["check_status"] == "COMPLETED" and audit["complete"]
        assert audit["derived"]["ttm_parent_profit"]["value"] == "110"
        assert audit["original_revision_archive_complete"] is False
        assert audit["metric_status"] == "UNVERIFIED" and report["execution_status"] == "BLOCKED"
        assert "CONFLICT" in {c["status"] for c in audit["checks"]}
        assert "METHOD_DIFFERENCE" in {c["status"] for c in audit["checks"]}
        assert "metrics" not in report and "nav" not in report
        assert report["input_audit"] == original["input_audit"]
        assert client.get("/api/quant/backtests/" + parent).json() == original
        assert client.post(endpoint, json=body).json()["run_id"] == run_id
        assert (
            client.post(endpoint, json={**body, "url": "https://example.invalid"}).status_code
            == 422
        )
        assert client.get(f"/api/quant/backtests/{run_id}/export").json() == report
        assert (
            client.post(
                f"/api/quant/backtests/{original['parent_run_id']}/filings", json=body
            ).status_code
            == 422
        )
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get("/api/quant/backtests/" + run_id).json() == report
        sha = next(iter(profiles.values()))["sha256"]
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"corrupted PDF document")
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409
        assert client.get(f"/api/quant/backtests/{run_id}/export").status_code == 409


@pytest.mark.parametrize("mode", ["failure", "stop"])
def test_filing_partial_failure_or_stop_never_makes_current_ttm(tmp_path, monkeypatch, mode):
    setup_sources(tmp_path, monkeypatch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent, _ = fixture(app, client, monkeypatch, mode)
        run_id = client.post(
            f"/api/quant/backtests/{parent}/filings", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        if mode == "stop":
            assert client.post(f"/api/quant/backtests/{run_id}/stop").status_code == 200
        else:
            client.portal.call(completed, app, run_id)
        report = client.get("/api/quant/backtests/" + run_id).json()
        assert report["check_status"] == ("CANCELLED" if mode == "stop" else "FAILED")
        assert len(report["filing_audit"]["sources"]) == 1
        assert report["filing_audit"]["derived"]["ttm_parent_profit"]["value"] is None
        assert report["filing_audit"]["complete"] is False
        assert report["execution_status"] == "BLOCKED"


def test_filing_restart_interrupts_and_target_corruption_blocks_replay(tmp_path, monkeypatch):
    from marketpulse.infrastructure.storage.models import BlobRef
    from marketpulse.investigation.persistence.models import AuditEventRow
    from marketpulse.quant.backtest.acquisition import prepare
    from marketpulse.quant.backtest.filings import filing_target

    setup_sources(tmp_path, monkeypatch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent, _ = fixture(app, client, monkeypatch)
        target = filing_target(app.state.quant_runtime, parent)
        run_id, created = prepare(app.state.quant_runtime, parent, uuid4(), filing_target=target)
        assert created
        with app.state.quant_runtime.service.sessions() as session:
            binding = session.get(AuditEventRow, "QH-BASE-" + run_id).metadata_payload
            target_sha = BlobRef.from_uri(binding["filing_target_ref"]).sha256
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        result = client.get("/api/quant/backtests/" + run_id)
        assert result.status_code == 200, result.text
        report = result.json()
        assert report["check_status"] == "INTERRUPTED"
        assert report["filing_audit"]["sources"] == []
        assert report["filing_audit"]["derived"]["ttm_parent_profit"]["value"] is None
        assert report["execution_status"] == "BLOCKED"
        next((tmp_path / "blobs").rglob(target_sha)).write_bytes(b"target changed")
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409
