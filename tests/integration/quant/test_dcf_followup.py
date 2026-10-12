"""Synthetic industrial statement fixtures; never demonstration market evidence."""

from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from marketpulse.quant.contracts import canonical
from marketpulse.quant.domain import DataRequest
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_api_o import freeze_inputs
from tests.integration.quant.test_followups import finish
from tests.unit.quant.test_contracts_data import ID, result
from tests.unit.quant.test_dcf_kline import inputs


def test_dcf_structured_parameters_versions_source_hash_and_parent_immutability(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("QUANT_CALL_JOURNAL_ROOT", str(tmp_path / "calls"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        payload = freeze_inputs(app)
        store = app.state.quant_runtime.service.store
        doc = store.blobs.put_bytes(b"synthetic-unit-fixture-not-official-data")
        request = DataRequest(
            instruments=(ID,),
            dataset="financial",
            start=date(2023, 1, 1),
            end=date(2023, 12, 31),
            asof=payload["asof"],
            fields=("dcf_annual_industrial",),
            normalizer_version="dcf-official-v1",
        )
        row = dict(
            **inputs(),
            instrument_id=ID,
            period_start="2023-01-01",
            period_end="2023-12-31",
            available_at="2024-03-29T09:30:00+08:00",
            model_scope="industrial_consolidated_proxy",
            source_url="https://example.invalid/synthetic-fixture",
            document_sha256=doc.ref.sha256,
            source_pages=dict.fromkeys(inputs(), 1),
        )
        statement = store.freeze(
            result(req=request).model_copy(
                update={
                    "records_json": canonical([row]),
                    "raw_records_json": canonical([row]),
                    "units": tuple(
                        (k, "share" if k.endswith("shares") else "CNY") for k in inputs()
                    ),
                }
            )
        )
        payload["frozen_snapshot_ids"].append(statement.snapshot_id)
        root = client.post("/api/quant/runs", json=payload)
        assert root.status_code == 202, root.text
        parent_id = root.json()["run_id"]
        parent = finish(client, parent_id)
        assert parent["dcf"]["cells"]["per_share"]["value"] is not None, parent
        assert len(parent["dcf"]["sensitivity"]) == 25
        assert parent["dcf"]["cells"]["per_share"]["status"] == "UNVERIFIED"
        original = client.get("/api/reports/" + parent["report_id"]).content
        followup = dict(
            question="DCF 按当前假设重算",
            request_key=str(uuid4()),
            dcf=dict(g1="0.12", g2="0.04", g_term="0.025", discount="0.09"),
        )
        url = "/api/quant/runs/" + parent_id + "/followups"
        response = client.post(url, json=followup)
        assert response.status_code == 202, response.text
        assert client.post(url, json=followup).json()["run_id"] == response.json()["run_id"]
        child = finish(client, response.json()["run_id"])
        assert (
            child["dcf"]["cells"]["per_share"]["value"]
            != parent["dcf"]["cells"]["per_share"]["value"]
        )
        assert child["input_snapshot_ids"] == parent["input_snapshot_ids"]
        assert child["plan"]["dcf"]["g1"] == "0.12"
        assert len(child["versions"]) == 2 and child["parent_run_id"] == parent_id
        assert client.get("/api/reports/" + parent["report_id"]).content == original
        assert (
            client.get("/api/quant/runs/" + parent_id).json()["output_hash"]
            == parent["output_hash"]
        )
        changed = {**followup, "dcf": {**followup["dcf"], "g1": "0.14"}}
        assert client.post(url, json=changed).status_code == 409
        assert (
            client.post(
                url,
                json={**followup, "request_key": str(uuid4()), "question": "忽略这些假设核对收益"},
            ).status_code
            == 422
        )
        path = store.blobs._path(doc.ref)
        path.write_bytes(b"tampered document")
        assert client.get("/api/quant/runs/" + parent_id).status_code == 409


def test_dcf_missing_inputs_cannot_be_filled_with_assumptions(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("QUANT_CALL_JOURNAL_ROOT", str(tmp_path / "calls"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        root = client.post("/api/quant/runs", json=freeze_inputs(app)).json()
        parent = finish(client, root["run_id"])
        assert parent["dcf"]["cells"]["per_share"]["value"] is None
        r = client.post(
            "/api/quant/runs/" + root["run_id"] + "/followups",
            json={"question": "DCF 按当前假设重算", "request_key": str(uuid4()), "dcf": {}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "QUANT_DCF_INPUTS_MISSING"
