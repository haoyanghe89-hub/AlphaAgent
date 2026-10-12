"""Live jobs fail closed, deduplicate retries and retain immutable scenario inputs."""

import hashlib
import threading
import time
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.domain import DcfAssumptions
from marketpulse.quant.live import api
from marketpulse.quant.live.calculate import dcf_result, simulate


def data_fixture():
    rows = [
        dict(date=(date(2025, 1, 1) + timedelta(days=i)).isoformat(), open=100 + i, close=100.5 + i)
        for i in range(160)
    ]
    return dict(
        dcf=dcf_result(
            dict(
                period="2025-12-31",
                basis=dict(fcff="100", cash="20", debt="10", minority="5", shares="10"),
            ),
            DcfAssumptions(),
        ),
        simulation=simulate(rows, rows),
        calculation_inputs=dict(prices=rows, benchmark=rows),
    )


@pytest.fixture
def live_client(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.quant_runtime = SimpleNamespace(journal_root=tmp_path / "calls")
    app.include_router(api.router)
    monkeypatch.setattr(api, "build", lambda *args: data_fixture())
    with TestClient(app) as client:
        yield client


def poll(client, run_id):
    for _ in range(50):
        response = client.get("/api/quant/live/runs/" + run_id)
        assert response.status_code == 200
        if response.json()["status"] != "RUNNING":
            return response.json()
        time.sleep(0.02)
    raise AssertionError("job did not terminate")


def test_start_busy_idempotency_fresh_fetch_and_versioned_recalculation(live_client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def collect(start, end, save, progress):
        calls.append((start, end))
        entered.set()
        assert release.wait(3)
        raw = b'{"price": 100}'
        save("prices", raw)
        progress(1)
        return {}, [dict(key="prices", sha256=hashlib.sha256(raw).hexdigest())]

    monkeypatch.setattr(api, "collect", collect)
    client = live_client
    payload = dict(request_key=str(uuid4()), code="000333")
    first = client.post("/api/quant/live/runs", json=payload)
    assert first.status_code == 202
    run_id = first.json()["run_id"]
    assert entered.wait(2)
    retry = client.post("/api/quant/live/runs", json=payload)
    assert retry.json()["run_id"] == run_id
    assert (
        client.post("/api/quant/live/runs", json=dict(request_key=str(uuid4()))).status_code == 409
    )
    release.set()
    original = poll(client, run_id)
    assert original["status"] == "COMPLETED"
    assert len(calls) == 1
    assert "calculation_inputs" not in original["data"]
    scenario = dict(request_key=str(uuid4()), dcf=dict(g1="0.12"), cost_pct=0.3)
    child = client.post(f"/api/quant/live/runs/{run_id}/scenario", json=scenario)
    assert child.status_code == 201, child.text
    assert child.json()["data"]["dcf"]["per_share"] > original["data"]["dcf"]["per_share"]
    assert child.json()["data"]["sources"] == original["data"]["sources"]
    same = client.post(f"/api/quant/live/runs/{run_id}/scenario", json=scenario)
    assert same.json()["run_id"] == child.json()["run_id"]
    changed = {**scenario, "cost_pct": 0.2}
    assert client.post(f"/api/quant/live/runs/{run_id}/scenario", json=changed).status_code == 409
    assert client.get("/api/quant/live/runs/" + run_id).json() == original
    second = client.post("/api/quant/live/runs", json=dict(request_key=str(uuid4())))
    assert poll(client, second.json()["run_id"])["status"] == "COMPLETED"
    assert len(calls) == 2
    assert len(client.get("/api/quant/live/runs").json()) == 3
    assert (
        client.post(
            "/api/quant/live/runs", json=dict(request_key=str(uuid4()), code="600519")
        ).status_code
        == 422
    )


def test_failed_acquisition_never_returns_an_older_report(live_client, monkeypatch):
    def fail(*args):
        raise TimeoutError("upstream timed out")

    monkeypatch.setattr(api, "collect", fail)
    result = live_client.post("/api/quant/live/runs", json=dict(request_key=str(uuid4())))
    final = poll(live_client, result.json()["run_id"])
    assert final["status"] == "FAILED"
    assert "data" not in final
    assert any(p["status"] == "FAILED" for p in final["process"])
    assert "TimeoutError" in final["error"]


def test_quote_failures_do_not_serve_last_good_quote(live_client, monkeypatch):
    def fail():
        raise OSError("offline")

    monkeypatch.setattr(api, "latest_quotes", fail)
    response = live_client.get("/api/quant/live/quotes")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "LIVE_QUOTE_FAILED"
