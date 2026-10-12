"""History lifecycle tests use synthetic provider packets, never exported to previews."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings


def packet(query):
    kind = query["kind"]
    if kind == "basic":
        rows = [dict(code="sz.000001", type="1", ipoDate="1991-04-03", outDate="", status="1")]
    elif kind == "members":
        rows = [dict(code="sz.000001", code_name="TEST ONLY", tradeStatus="0")]
    else:
        first, last = date.fromisoformat(query["start"]), date.fromisoformat(query["end"])
        days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        rows = (
            [dict(calendar_date=str(d), is_trading_day=str(int(d.weekday() < 5))) for d in days]
            if kind == "calendar"
            else [dict(date=str(d), code="sh.000300", close="100") for d in days if d.weekday() < 5]
        )
    return dict(
        provider="baostock", query=query, rows=rows, retrieved_at=datetime.now(UTC).isoformat()
    )


async def completed(app, run_id):
    task = app.state.quant_runtime.runner.tasks.get(run_id)
    if task:
        await task


def create_parent(client):
    return client.post("/api/quant/backtests", json=dict(request_key=str(uuid4()), years=3)).json()[
        "run_id"
    ]


def test_history_freezes_retry_original_export_reproduction_and_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    async def fetch(query, **kwargs):
        return packet(query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = create_parent(client)
        original = client.get("/api/quant/backtests/" + parent).json()
        request = dict(request_key=str(uuid4()))
        created = client.post(f"/api/quant/backtests/{parent}/history", json=request)
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        client.portal.call(completed, app, run_id)
        result = client.get("/api/quant/backtests/" + run_id)
        assert result.status_code == 200, result.text
        result = result.json()
        assert result["check_status"] == "COMPLETED" and result["execution_status"] == "BLOCKED"
        assert result["historical_data"]["benchmark"]["coverage"] == "COVERED"
        assert len(result["historical_data"]["annual"]) == 3
        assert all(
            a["suspended_count"] == 1 and a["eligible_count"] is None
            for a in result["historical_data"]["annual"]
        )
        assert "nav" not in result and "metrics" not in result
        assert result["acquisition"]["calls_started"] == result["acquisition"]["sources_frozen"]
        assert all(s["pit_level"] == "PARTIAL" for s in result["historical_data"]["sources"])
        assert client.get("/api/quant/backtests/" + parent).json() == original
        assert (
            client.post(f"/api/quant/backtests/{parent}/history", json=request).json()["run_id"]
            == run_id
        )
        another = create_parent(client)
        assert (
            client.post(f"/api/quant/backtests/{another}/history", json=request).status_code == 409
        )
        assert client.get(f"/api/quant/backtests/{run_id}/export").json() == result
        assert client.get("/api/quant/runs").json()[1]["scenario"] == "backtest"
        client.portal.call(app.state.recovery_watchdog.tick)
        assert (
            client.post(
                f"/api/runs/{run_id}/resume", json={"expected_state_version": 1}
            ).status_code
            == 409
        )
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert (
            client.post(f"/api/quant/backtests/{parent}/history", json=request).json()["run_id"]
            == run_id
        )
        assert client.get("/api/quant/backtests/" + run_id).json() == result
        source = result["historical_data"]["sources"][0]["sha256"]
        next((tmp_path / "blobs").rglob(source)).write_bytes(b"tamper")
        assert (
            client.post(f"/api/quant/backtests/{parent}/history", json=request).status_code == 409
        )
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409


@pytest.mark.parametrize("stop", [True, False])
def test_stop_or_provider_failure_preserves_partial_input_without_return(
    tmp_path, monkeypatch, stop
):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    calls = []

    async def fetch(query, **kwargs):
        calls.append(query)
        if len(calls) > 1:
            if stop:
                await asyncio.sleep(30)
            raise TimeoutError("no connection")
        return packet(query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = create_parent(client)
        run_id = client.post(
            f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        if stop:
            assert client.post(f"/api/quant/backtests/{run_id}/stop").status_code == 200
        else:
            client.portal.call(completed, app, run_id)
        result = client.get("/api/quant/backtests/" + run_id).json()
        assert result["check_status"] == ("CANCELLED" if stop else "FAILED")
        assert result["acquisition"]["error"] and result["acquisition"]["sources_frozen"] == 1
        assert result["execution_status"] == "BLOCKED" and "metrics" not in result


def test_no_executor_after_restart_is_interrupted_not_running(tmp_path, monkeypatch):
    from marketpulse.quant.backtest.acquisition import prepare

    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = create_parent(client)
        run_id, created = prepare(app.state.quant_runtime, parent, uuid4())
        assert created
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        result = client.get("/api/quant/backtests/" + run_id)
        assert result.status_code == 200, result.text
        assert result.json()["check_status"] == "INTERRUPTED"
        assert result.json()["acquisition"]["sources_frozen"] == 0
        assert result.json()["execution_status"] == "BLOCKED"


def test_shutdown_preserves_history_report_without_web_report(tmp_path, monkeypatch):
    from sqlalchemy import select

    from marketpulse.investigation.persistence.models import ReportRow

    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    calls = []

    async def fetch(query, **kwargs):
        calls.append(query)
        if len(calls) > 1:
            await asyncio.sleep(30)
        return packet(query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = create_parent(client)
        run_id = client.post(
            f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(app.state.quant_runtime.runner.shutdown)
        assert client.get("/api/quant/backtests/" + run_id).json()["check_status"] == "CANCELLED"
        with app.state.quant_runtime.service.sessions() as session:
            assert session.scalar(select(ReportRow).where(ReportRow.run_id == run_id)) is None
