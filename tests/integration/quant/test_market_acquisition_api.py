"""Isolated synthetic market acquisition; no fake inputs enter the real demo."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_backtest_benchmark_api import history
from tests.integration.quant.test_history_acquisition_api import packet


def setup(monkeypatch, tmp_path, fail_after=None, stop=False):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    calls = []

    async def basic(query, **kwargs):
        return packet(query)

    async def source(query, **kwargs):
        calls.append(query)
        if fail_after is not None and len(calls) > fail_after:
            if stop:
                await asyncio.sleep(30)
            raise RuntimeError("synthetic source failure")
        return dict(provider="baostock", query=query, rows=[], retrieved_at="2026-10-10T00:00:00Z")

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", basic)
    monkeypatch.setattr("marketpulse.quant.backtest.market.fetch_query", source)
    return calls


async def completed(app, parent):
    task = app.state.quant_runtime.runner.tasks.get("MARKET-" + parent)
    if task:
        await task


def test_market_uuid_export_restart_and_tamper(tmp_path, monkeypatch):
    calls = setup(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        path = f"/api/quant/backtests/{parent}/market"
        original = client.get(f"/api/quant/backtests/{parent}").json()
        assert not client.get(path).json()["exists"]
        body = dict(request_key=str(uuid4()))
        created = client.post(path, json=body)
        assert created.status_code == 202, created.text
        client.portal.call(completed, app, parent)
        report = client.get(path).json()
        assert report["observation_complete"] and report["admission_status"] == "BLOCKED"
        assert report["coverage"]["stock_count"] == 1
        assert report["coverage"]["frozen_count"] == len(calls)
        assert report["strict_partition_count"] == 0
        assert client.post(path, json=body).json() == created.json()
        assert client.post(path, json={**body, "batches": 2}).status_code == 409
        assert client.post(path, json={**body, "strict": True}).status_code == 422
        assert client.post(path, json={**body, "batches": 21}).status_code == 422
        assert client.get(f"/api/quant/backtests/{parent}").json() == original
        exported = client.get(path + "/export").json()
        assert len(exported["partitions"]) == len(calls)
        assert "metrics" not in exported
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get(path).json() == report
        assert client.get(path + "/export").json() == exported
        sha = exported["partitions"][0]["packet_hash"]
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"tampered")
        assert client.get(path).status_code == 409
        assert client.get(path + "/export").status_code == 409
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 409


@pytest.mark.parametrize("stop", [False, True])
def test_partial_and_cancel_resume_missing_without_repeating_success(tmp_path, monkeypatch, stop):
    calls = setup(monkeypatch, tmp_path, fail_after=1, stop=stop)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        path = f"/api/quant/backtests/{parent}/market"
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 202
        if stop:
            assert client.post(path + "/stop").status_code == 200
        client.portal.call(completed, app, parent)
        report = client.get(path).json()
        assert report["acquisition_status"] == ("CANCELLED" if stop else "FAILED")
        assert report["coverage"]["frozen_count"] == 1
        first = calls[0]
        recovered = setup(monkeypatch, tmp_path)
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 202
        client.portal.call(completed, app, parent)
        assert first not in recovered
        assert client.get(path).json()["observation_complete"]


def test_budget_partitions_and_interrupted_request_explicit_resume(tmp_path, monkeypatch):
    calls = setup(monkeypatch, tmp_path)
    monkeypatch.setattr("marketpulse.quant.backtest.market.MAX_CALLS", 2)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        path = f"/api/quant/backtests/{parent}/market"
        body = dict(request_key=str(uuid4()), batches=2)
        response = client.post(path, json=body)
        client.portal.call(completed, app, parent)
        assert len(calls) == 4
        assert not client.get(path).json()["observation_complete"]
        from marketpulse.quant.backtest.market import MarketIn, prepare

        lost = dict(request_key=str(uuid4()))
        _, pending, lost_batch = prepare(app.state.quant_runtime, parent, MarketIn(**lost))
        assert len(pending) == 2
        app.state.quant_runtime.market_owner = "restarted-process"
        assert client.get(path).json()["acquisition_status"] == "INTERRUPTED"
        assert client.post(path, json=body).json() == response.json()
        assert client.post(path, json=lost).json()["batch_id"] == lost_batch
        assert len(calls) == 4
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 202
        client.portal.call(completed, app, parent)
        assert len(calls) == 6


@pytest.mark.parametrize("bound", ["time", "bytes"])
def test_hard_budget_failure_never_persists_unbounded_packet(tmp_path, monkeypatch, bound):
    calls = (
        setup(monkeypatch, tmp_path, fail_after=0, stop=True)
        if bound == "time"
        else setup(monkeypatch, tmp_path)
    )
    monkeypatch.setattr(
        "marketpulse.quant.backtest.market." + ("MAX_SECONDS" if bound == "time" else "MAX_BYTES"),
        0.02 if bound == "time" else 10,
    )
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        path = f"/api/quant/backtests/{parent}/market"
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 202
        client.portal.call(completed, app, parent)
        report = client.get(path).json()
        assert len(calls) == 1
        assert report["coverage"]["frozen_count"] == 0
        assert report["acquisition_status"] == "FAILED"
        assert report["admission_status"] == "BLOCKED"


def test_concurrent_new_requests_and_stop_before_first_response(tmp_path, monkeypatch):
    setup(monkeypatch, tmp_path, fail_after=0, stop=True)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        path = f"/api/quant/backtests/{parent}/market"
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 202
        assert client.post(path, json=dict(request_key=str(uuid4()))).status_code == 409
        report = client.post(path + "/stop").json()
        assert report["acquisition_status"] == "CANCELLED"
        assert report["coverage"]["frozen_count"] == 0
