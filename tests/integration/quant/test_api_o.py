"""Synthetic API fixtures; never used as real-market demonstration evidence."""

import time

from fastapi.testclient import TestClient
from sqlalchemy import text

from marketpulse.investigation.server import create_app
from marketpulse.quant.contracts import canonical
from tests.integration.investigation.test_investigation_server import _settings
from tests.unit.quant.test_contracts_data import ID, instrument, request, result


def test_missing_quant_dependencies_preserves_web(tmp_path, monkeypatch):
    monkeypatch.setattr("marketpulse.quant.api.missing_dependencies", lambda: ["pyarrow"])
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get("/api/quant/instruments")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "QUANT_NOT_CONFIGURED"


def freeze_inputs(app):
    store = app.state.quant_runtime.service.store
    store.register(instrument())
    rows = [
        {
            "instrument_id": ID,
            "date": f"2024-06-{d}",
            "close": p,
            "provisional": False,
            "trading_status": True,
        }
        for d, p in [(12, 100), (13, 110), (14, 99)]
    ]
    prices = store.freeze(
        result().model_copy(
            update={
                "records_json": canonical(rows),
                "raw_records_json": canonical(rows),
                "units": (("close", "CNY/share"),),
            }
        )
    )
    days = [{"instrument_id": ID, "date": r["date"], "is_session": True} for r in rows]
    calendar = store.freeze(
        result(req=request(dataset="calendar")).model_copy(
            update={"records_json": canonical(days), "raw_records_json": canonical(days)}
        )
    )
    return dict(
        instrument=ID,
        date_start="2024-06-01",
        date_end="2024-06-14",
        asof="2024-06-15T00:00:00Z",
        depth="quick",
        frozen_snapshot_ids=[prices.snapshot_id, calendar.snapshot_id],
    )


def test_start_query_unverified_locators_eventloop_and_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("QUANT_CALL_JOURNAL_ROOT", str(tmp_path / "calls"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        with app.state.inv_sessions() as session:
            assert (
                session.execute(text("select version_num from alembic_version")).scalar_one()
                == "20261010_10"
            )
        payload = freeze_inputs(app)
        payload["question"] = "全面分析平安银行的历史收益与估值"
        assert client.get("/api/quant/instruments?q=000001").json()[0]["instrument_id"] == ID
        assert (
            client.post("/api/quant/runs", json={**payload, "date_end": "2024-06-13"}).status_code
            == 422
        )
        response = client.post("/api/quant/runs", json=payload)
        assert response.status_code == 202, response.text
        started = response.json()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            tick = time.monotonic()
            assert client.get("/api/health").status_code == 200
            assert time.monotonic() - tick < 2
            response = client.get("/api/quant/runs/" + started["run_id"])
            assert response.status_code == 200, response.text
            detail = response.json()
            if detail["run"]["status"] == "COMPLETED":
                break
            time.sleep(0.2)
        assert detail["run"]["status"] == "COMPLETED", detail
        assert detail["plan"]["question"] == payload["question"]
        assert [node["stage"] for node in detail["process"]] == [
            "planner",
            "data",
            "analyst",
            "verifier",
            "writer",
        ]
        assert all(node["status"] == "COMPLETED" for node in detail["process"])
        assert detail["report_id"] and detail["values"] and 1 <= len(detail["charts"]) <= 3
        assert all(
            v["status"] == "UNVERIFIED"
            and v["cell_hash"]
            and v["metric_path"].startswith("/values/")
            for v in detail["values"]
        )
        assert detail["evidence_count"] == detail["citation_count"]
        links = client.get("/api/reports/" + detail["report_id"] + "/citations").json()
        assert len(links) == detail["citation_count"]
        citation = client.get("/api/citations/" + links[0]["citation_id"])
        assert citation.status_code == 200, citation.text
        assert citation.json()["evidence"]["locator_type"] == "COMPUTATION_CELL"
        assert citation.json()["claim"]["validation_status"] == "UNVERIFIED"
        overview = client.get("/api/quant/overview").json()
        assert overview["mode"] == "historical_frozen" and not overview["indices"]
        stock = overview["stocks"][0]
        assert stock["run_id"] == started["run_id"]
        price = stock["quote"]
        assert price["value"] == "99" and price["status"] == "UNVERIFIED"
        assert price in detail["values"] and price["cell_hash"]
        assert (
            client.get("/api/quant/runs?status=COMPLETED&limit=1").json()[0]["run_id"]
            == started["run_id"]
        )
        assert client.post("/api/runs/" + started["run_id"] + "/cancel").status_code == 409
        assert (
            client.delete("/api/investigations/" + started["investigation_id"]).status_code == 200
        )
        assert client.get("/api/quant/runs/" + started["run_id"]).status_code == 404


def test_cancel_acquisition_persists_partial_report(tmp_path, monkeypatch):
    import asyncio

    async def wait(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr("marketpulse.quant.acquisition.acquire_inputs", wait)
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        app.state.quant_runtime.service.store.register(instrument())
        result = client.post(
            "/api/quant/runs",
            json={
                "instrument": ID,
                "date_start": "2024-06-01",
                "date_end": "2024-06-14",
                "asof": "2024-06-15T00:00:00Z",
            },
        ).json()
        time.sleep(0.1)
        assert client.delete("/api/investigations/" + result["investigation_id"]).status_code == 409
        assert client.post("/api/runs/" + result["run_id"] + "/cancel").status_code == 200
        detail = client.get("/api/quant/runs/" + result["run_id"]).json()
        assert detail["report_id"] and detail["run"]["status"] == "COMPLETED"
        assert not detail["values"]
        assert detail["process"][1]["status"] == "DEGRADED"
        assert detail["process"][-1]["status"] == "COMPLETED"
        assert detail["process"][2]["status"] == "NOT_RECORDED"
