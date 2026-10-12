"""Real DB checks; synthetic market fixtures are confined to this test DB."""

from uuid import uuid4

from fastapi.testclient import TestClient

from marketpulse.investigation.persistence.models import AuditEventRow
from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_api_o import freeze_inputs


def test_real_check_retry_restore_history_export_and_tamper(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        freeze_inputs(app)
        payload = dict(
            pe_max=30,
            roe_min=15,
            rev_yoy_min=None,
            years=5,
            cost_pct_per_side="0.13",
            request_key=str(uuid4()),
        )
        first = client.post("/api/quant/backtests", json=payload)
        assert first.status_code == 201, first.text
        run_id = first.json()["run_id"]
        assert client.post("/api/quant/backtests", json=payload).json()["run_id"] == run_id
        assert client.post("/api/quant/backtests", json={**payload, "years": 3}).status_code == 409
        result = client.get("/api/quant/backtests/" + run_id).json()
        assert result["execution_status"] == "BLOCKED"
        assert result["check_status"] == "COMPLETED"
        assert result["data_check"]["instrument_count"] == 1
        assert len(result["data_check"]["snapshots"]) > 0
        assert "metrics" not in result and "nav" not in result
        assert all(c["status"] != "MATCH" for c in result["data_check"]["checks"])
        assert result["process"][2]["status"] == "NOT_EXECUTED"
        client.portal.call(app.state.recovery_watchdog.tick)
        with app.state.quant_runtime.service.sessions() as session:
            from sqlalchemy import select

            assert not session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.run_id == run_id, AuditEventRow.event_type == "OPS_ALERT"
                )
            ).all()
        resume = client.post("/api/runs/" + run_id + "/resume", json={"expected_state_version": 1})
        assert resume.status_code == 409
        assert resume.json()["detail"]["code"] == "BACKTEST_INPUTS_UNAVAILABLE"
        assert client.get("/api/quant/backtests/" + run_id + "/export").status_code == 200
        history = client.get("/api/quant/runs").json()
        assert history[0]["scenario"] == "backtest"
        assert history[0]["execution_status"] == "BLOCKED"
        assert history[0]["status"] == "FAILED"
        assert history[0]["check_status"] == "COMPLETED"
        # Future catalog changes cannot rewrite the frozen result.
        from tests.unit.quant.test_contracts_data import instrument

        second = instrument()
        app.state.quant_runtime.service.store.register(
            second.model_copy(
                update={
                    "instrument_id": "ins_second_catalog_001",
                    "name": "fixture added later",
                    "aliases": (second.aliases[0].model_copy(update={"code": "000002"}),),
                }
            )
        )
        assert client.get("/api/quant/backtests/" + run_id).json() == result
        import pytest
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError, match="append-only"):
            with app.state.quant_runtime.service.sessions.begin() as session:
                row = session.get(AuditEventRow, "QBT-" + run_id)
                row.metadata_payload = {**row.metadata_payload, "sha256": "0" * 64}
        with app.state.quant_runtime.service.sessions() as session:
            row = session.get(AuditEventRow, "QBT-" + run_id)
            sha = row.metadata_payload["sha256"]
        blob = next((tmp_path / "blobs").rglob(sha))
        blob.write_bytes(b"corrupted frozen check")
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409
        assert client.get("/api/quant/backtests/" + run_id + "/export").status_code == 409
        history = client.get("/api/quant/runs").json()
        assert history[0]["check_status"] == "INTEGRITY_FAILED"
    # A fresh app instance recovers persisted idempotency; no in-memory task is required.
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.post("/api/quant/backtests", json=payload).status_code == 201
        assert client.get("/api/quant/backtests/" + run_id).status_code == 409


def test_bounds_reject_and_empty_database_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        payload = dict(request_key=str(uuid4()), years=5, pe_max=30, roe_min=15)
        for change in (
            {"years": 9},
            {"years": 2},
            {"cost_pct_per_side": -1},
            {"pe_max": "NaN"},
            {"roe_min": "Infinity"},
            {"unexpected": 1},
        ):
            assert (
                client.post("/api/quant/backtests", json={**payload, **change}).status_code == 422
            )
        response = client.post("/api/quant/backtests", json=payload)
        assert response.status_code == 201, response.text
        result = response.json()
        saved = client.get("/api/quant/backtests/" + result["run_id"]).json()
        assert saved["data_check"]["instrument_count"] == 0
        assert saved["data_check"]["snapshots"] == []
        assert saved["data_check"]["checks"][0]["status"] == "UNAVAILABLE"
