"""Actual persistence over synthetic bounded provider responses, not page data."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_api_o import freeze_inputs
from tests.integration.quant.test_history_acquisition_api import completed, create_parent, packet


def source(query):
    if query["kind"] not in {"stock_daily", "dividends", "profit"}:
        return packet(query)
    code = query["code"]
    if query["kind"] == "stock_daily":
        start, end = date.fromisoformat(query["start"]), date.fromisoformat(query["end"])
        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
        rows = [
            dict(
                code=code,
                date=str(d),
                open="10",
                high="11",
                low="9",
                close="10",
                preclose="10",
                volume="100",
                amount="1000",
                tradestatus="1",
                isST="0",
                adjustflag="3",
            )
            for d in days
            if d.weekday() < 5
        ]
    elif query["kind"] == "profit":
        end = f"{query['year']}-03-31" if query["quarter"] == 1 else f"{query['year']}-12-31"
        rows = [
            dict(
                code=code,
                statDate=end,
                pubDate=str(date.fromisoformat(end) + timedelta(days=20)),
                netProfit="100",
            )
        ]
    else:
        rows = [
            dict(
                code=code,
                dividOperateDate=f"{query['year']}-06-20",
                dividCashPsBeforeTax="0.5",
                dividStocksPs="0",
                dividReserveToStockPs="",
            )
        ]
    return dict(provider="baostock", retrieved_at="2026-10-07T00:00:00Z", query=query, rows=rows)


def test_input_scope_retry_original_export_and_source_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    async def fetch(query, **kwargs):
        return source(query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        freeze_inputs(app)
        parent = create_parent(client)
        history = client.post(
            f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(completed, app, history)
        original = client.get("/api/quant/backtests/" + history).json()
        year = original["historical_data"]["annual"][-1]["year"]
        instrument = original["data_check"]["instruments"][0]["instrument_id"]
        body = dict(request_key=str(uuid4()), instrument_id=instrument, signal_year=year)
        started = client.post(f"/api/quant/backtests/{history}/inputs", json=body)
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        client.portal.call(completed, app, run_id)
        response = client.get("/api/quant/backtests/" + run_id)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["check_status"] == "COMPLETED" and report["execution_status"] == "BLOCKED", (
            report["acquisition"]
        )
        audit = report["input_audit"]
        assert audit["prices"]["coverage"] == "COVERED" and audit["actions"]["event_count"] > 0
        assert len(audit["financials"]["observations"]) == 3
        assert audit["financials"]["original_versions_available"] is False
        assert "nav" not in report and "metrics" not in report
        assert client.get("/api/quant/backtests/" + history).json() == original

        assert (
            client.post(f"/api/quant/backtests/{history}/inputs", json=body).json()["run_id"]
            == run_id
        )
        assert (
            client.post(
                f"/api/quant/backtests/{history}/inputs", json={**body, "signal_year": year - 1}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/quant/backtests/{history}/inputs", json={**body, "signal_year": 1990}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/quant/backtests/{history}/inputs",
                json={**body, "instrument_id": "ins_not_in_parent_001"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/quant/backtests/{history}/inputs",
                json={**body, "provider_code": "sz.000002"},
            ).status_code
            == 422
        )
        assert client.get(f"/api/quant/backtests/{run_id}/export").json() == report
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get("/api/quant/backtests/" + run_id).json() == report
        sha = audit["sources"][0]["sha256"]
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"corrupted original price")
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409
        assert client.get(f"/api/quant/backtests/{run_id}/export").status_code == 409


@pytest.mark.parametrize("stop", [False, True])
def test_input_failure_or_stop_keeps_real_partial_packet(tmp_path, monkeypatch, stop):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    calls = []

    async def fetch(query, **kwargs):
        if query["kind"] in {"stock_daily", "dividends", "profit"}:
            calls.append(query)
            if len(calls) > 1:
                if stop:
                    await asyncio.sleep(30)
                raise TimeoutError("provider unavailable")
        return source(query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        freeze_inputs(app)
        parent = create_parent(client)
        history = client.post(
            f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(completed, app, history)
        original = client.get("/api/quant/backtests/" + history).json()
        run_id = client.post(
            f"/api/quant/backtests/{history}/inputs",
            json=dict(
                request_key=str(uuid4()),
                instrument_id=original["data_check"]["instruments"][0]["instrument_id"],
                signal_year=original["historical_data"]["annual"][-1]["year"],
            ),
        ).json()["run_id"]
        if stop:
            assert client.post(f"/api/quant/backtests/{run_id}/stop").status_code == 200
        else:
            client.portal.call(completed, app, run_id)
        response = client.get("/api/quant/backtests/" + run_id)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["check_status"] == ("CANCELLED" if stop else "FAILED")
        assert len(report["input_audit"]["sources"]) == 1
        assert report["input_audit"]["prices"]["row_count"] > 0
        assert report["input_audit"]["financials"]["observations"] == []
        assert report["execution_status"] == "BLOCKED" and "nav" not in report
        assert client.get("/api/quant/backtests/" + history).json() == original


def test_input_executor_lost_on_restart_and_target_integrity(tmp_path, monkeypatch):
    from sqlalchemy import select

    from marketpulse.investigation.persistence.models import AuditEventRow
    from marketpulse.quant.backtest.acquisition import input_target, prepare

    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    async def fetch(query, **kwargs):
        return source(query)

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        freeze_inputs(app)
        parent = create_parent(client)
        history = client.post(
            f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(completed, app, history)
        original = client.get("/api/quant/backtests/" + history).json()
        target = input_target(
            app.state.quant_runtime,
            history,
            original["data_check"]["instruments"][0]["instrument_id"],
            original["historical_data"]["annual"][-1]["year"],
        )
        run_id, created = prepare(app.state.quant_runtime, history, uuid4(), input_target=target)
        assert created
        with app.state.quant_runtime.service.sessions() as session:
            base = session.scalar(
                select(AuditEventRow).where(AuditEventRow.audit_event_id == "QH-BASE-" + run_id)
            ).metadata_payload
            target_sha = base["input_target_ref"].split(":")[-1].split("/")[-1]
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        report = client.get("/api/quant/backtests/" + run_id)
        assert report.status_code == 200, report.text
        assert report.json()["check_status"] == "INTERRUPTED"
        assert report.json()["input_audit"]["sources"] == []
        next((tmp_path / "blobs").rglob(target_sha)).write_bytes(b"tampered target scope")
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409
