"""Synthetic bounded API fixture; real-market acceptance is run separately."""

import json
import time

from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from marketpulse.quant.contracts import canonical
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_api_o import freeze_inputs
from tests.unit.quant.test_contracts_data import ID, instrument


def two_inputs(app):
    first = freeze_inputs(app)
    store = app.state.quant_runtime.service.store
    second_id = "ins_test_entity_002"
    master = instrument()
    store.register(
        master.model_copy(
            update={
                "instrument_id": second_id,
                "name": "fixture B",
                "aliases": (master.aliases[0].model_copy(update={"code": "000002"}),),
            }
        )
    )
    ids = []
    for old_id in first["frozen_snapshot_ids"]:
        old = store.load(old_id).result
        rows = [{**r, "instrument_id": second_id} for r in json.loads(old.records_json)]
        ids.append(
            store.freeze(
                old.model_copy(
                    update={
                        "request": old.request.model_copy(update={"instruments": (second_id,)}),
                        "records_json": canonical(rows),
                        "raw_records_json": canonical(rows),
                    }
                )
            ).snapshot_id
        )
    second = {**first, "instrument": second_id, "frozen_snapshot_ids": ids}
    return first, second


def test_two_branches_composition_export_and_source_drift_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("QUANT_CALL_JOURNAL_ROOT", str(tmp_path / "calls"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        first, second = two_inputs(app)
        store = app.state.quant_runtime.service.store
        second_id = second["instrument"]
        payload = dict(question="fixture vs fixture B", subjects=[first, second])
        assert (
            client.post(
                "/api/quant/comparisons", json={**payload, "subjects": [first, first]}
            ).status_code
            == 422
        )
        response = client.post("/api/quant/comparisons", json=payload)
        assert response.status_code == 202, response.text
        parent = response.json()["run_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            response = client.get("/api/quant/runs/" + parent)
            assert response.status_code == 200, response.text
            data = response.json()
            if data["run"]["status"] == "COMPLETED":
                break
            time.sleep(0.2)
        assert data["run"]["status"] == "COMPLETED", data
        assert data["scenario"] == "compare" and data["report_id"]
        assert len(data["subjects"]) == 2 and len(data["comparison"]["dimensions"]) == 5
        assert all(n["status"] == "COMPLETED" for n in data["process"])
        assert all(
            c["status"] == "UNVERIFIED" for s in data["subjects"] for c in s["data"]["values"]
        )
        assert {s["subject"]["instrument_id"] for s in data["subjects"]} == {ID, second_id}
        assert client.get("/api/reports/" + data["report_id"]).status_code == 200
        assert client.get("/api/quant/runs").json()[0]["scenario"] == "compare"
        assert client.get("/api/quant/runs/" + parent + "/comparison-export").status_code == 200
        # Break a source locator: the parent must stop rendering both sides, no fallback.
        from sqlalchemy import select

        from marketpulse.investigation.domain.enums import ValidationStatus
        from marketpulse.investigation.persistence.models import ClaimRow

        with store.sessions.begin() as session:
            row = session.scalar(
                select(ClaimRow).where(ClaimRow.run_id == data["subjects"][0]["run_id"])
            )
            row.validation_status = ValidationStatus.VERIFIED
        assert client.get("/api/quant/runs/" + parent).status_code == 409
        assert client.get("/api/quant/runs/" + parent + "/comparison-export").status_code == 409


def test_cancelling_parent_stops_child_and_does_not_start_second_branch(tmp_path, monkeypatch):
    import asyncio

    started = []

    async def hold(**kwargs):
        started.append(kwargs["run_id"])
        await asyncio.Event().wait()

    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        first, second = two_inputs(app)
        monkeypatch.setattr(app.state.quant_runtime.runner, "_execute_quant", hold)
        parent = client.post(
            "/api/quant/comparisons",
            json=dict(question="fixture vs fixture B", subjects=[first, second]),
        ).json()["run_id"]
        deadline = time.monotonic() + 10
        while not started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(started) == 1
        assert client.post("/api/runs/" + parent + "/cancel").status_code == 200
        data = client.get("/api/quant/runs/" + parent).json()
        assert data["report_id"] and "comparison" not in data
        assert len(data["branches"]) == 1
        assert all(app.state.quant_runtime.runner.tasks[r].done() for r in [parent, *started])
        assert data["process"][1]["status"] == "DEGRADED"
