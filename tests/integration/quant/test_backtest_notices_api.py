"""Synthetic official notice workflow, restart, cancellation and integrity gates."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_backtest_benchmark_api import history
from tests.integration.quant.test_backtest_lifecycles_api import setup_sources
from tests.integration.quant.test_history_acquisition_api import completed, packet
from tests.unit.quant.test_backtest_notices import packets, setup_notices


def setup(monkeypatch, tmp_path, mode="complete"):
    setup_sources(monkeypatch, tmp_path)
    raw = setup_notices(monkeypatch)

    async def history_source(query, **kwargs):
        p = packet(query)
        if query["kind"] == "basic":
            p["rows"].append(
                dict(code="sz.300114", type="1", ipoDate="2010-08-27", outDate="", status="0")
            )
        if query["kind"] == "members":
            p["rows"].append(dict(code="sz.300114", code_name="TEST ONLY", tradeStatus="1"))
        return p

    calls = []

    async def official(query, store):
        calls.append(query)
        if len(calls) > 1 and mode != "complete":
            if mode == "stop":
                await asyncio.sleep(30)
            raise TimeoutError("TEST ONLY unavailable")
        return next(p for p in packets(store, raw) if p["query"] == query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", history_source)
    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_notice", official)


def parent(client, app):
    source = history(app, client)
    run = client.post(
        f"/api/quant/backtests/{source}/lifecycles", json=dict(request_key=str(uuid4()))
    ).json()["run_id"]
    client.portal.call(completed, app, run)
    return run


def test_notice_uuid_parent_export_restart_raw_tamper(tmp_path, monkeypatch):
    setup(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        source = parent(client, app)
        original = client.get("/api/quant/backtests/" + source).json()
        body = dict(request_key=str(uuid4()))
        path = f"/api/quant/backtests/{source}/notices"
        response = client.post(path, json=body)
        assert response.status_code == 202, response.text
        run = response.json()["run_id"]
        client.portal.call(completed, app, run)
        response = client.get("/api/quant/backtests/" + run)
        assert response.status_code == 200, response.text
        report = response.json()
        audit = report["notice_audit"]
        assert report["check_status"] == "COMPLETED" and audit["complete"]
        assert len(audit["documents"]) == 2 and audit["events"][0]["status"] == "MATCH"
        assert report["lifecycle_audit"] == original["lifecycle_audit"]
        assert report["historical_data"] == original["historical_data"]
        assert report["execution_status"] == "BLOCKED" and "metrics" not in report
        assert client.get("/api/quant/backtests/" + source).json() == original
        assert client.post(path, json=body).json()["run_id"] == run
        assert client.post(path, json={**body, "url": "https://arbitrary"}).status_code == 422
        assert client.get(f"/api/quant/backtests/{run}/export").json() == report
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get("/api/quant/backtests/" + run).json() == report
        sha = audit["documents"][0]["document_sha256"]
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"tampered notice")
        assert client.get("/api/quant/backtests/" + run).status_code == 409
        assert client.get(f"/api/quant/backtests/{run}/export").status_code == 409
        assert client.post(path, json=body).status_code == 409


@pytest.mark.parametrize("mode", ["failure", "stop"])
def test_partial_notice_never_confirms_identity(tmp_path, monkeypatch, mode):
    setup(monkeypatch, tmp_path, mode)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        source = parent(client, app)
        run = client.post(
            f"/api/quant/backtests/{source}/notices", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        if mode == "stop":
            assert client.post(f"/api/quant/backtests/{run}/stop").status_code == 200
        else:
            client.portal.call(completed, app, run)
        report = client.get("/api/quant/backtests/" + run).json()
        assert report["check_status"] == ("CANCELLED" if mode == "stop" else "FAILED")
        assert len(report["notice_audit"]["documents"]) == 1
        assert report["notice_audit"]["events"][0]["status"] == "UNAVAILABLE"
        assert not report["notice_audit"]["complete"]
        assert report["execution_status"] == "BLOCKED"


def test_shared_sources_validate_once_per_read_but_tamper_is_never_cached(tmp_path, monkeypatch):
    from collections import Counter

    from tests.integration.quant.test_api_o import freeze_inputs

    setup(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        freeze_inputs(app)
        source = parent(client, app)
        run = client.post(
            f"/api/quant/backtests/{source}/notices", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(completed, app, run)
        store = app.state.quant_runtime.service.store
        original_load, calls = store.load, Counter()

        def counted(snapshot_id, **kwargs):
            calls[snapshot_id] += 1
            return original_load(snapshot_id, **kwargs)

        monkeypatch.setattr(store, "load", counted)
        result = client.get("/api/quant/backtests/" + run)
        assert result.status_code == 200, result.text
        ids = {s["snapshot_id"] for s in result.json()["data_check"]["snapshots"]}
        assert ids and calls == Counter({s: 1 for s in ids})
        assert client.get("/api/quant/backtests/" + run).status_code == 200
        assert calls == Counter({s: 2 for s in ids})
        snapshot = original_load(next(iter(ids)))
        sha = snapshot.partitions[0].sha256
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"tampered shared snapshot")
        assert client.get("/api/quant/backtests/" + run).status_code == 409
