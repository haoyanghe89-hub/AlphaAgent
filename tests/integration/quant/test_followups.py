"""Versioned quant followups use immutable, synthetic inputs, never live market data."""

import time
from uuid import uuid4

from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_api_o import freeze_inputs


def finish(client, run_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = client.get("/api/quant/runs/" + run_id)
        assert response.status_code == 200, response.text
        item = response.json()
        if item["run"]["status"] == "COMPLETED":
            return item
        time.sleep(0.2)
    raise AssertionError(item)


def test_followup_version_pins_idempotency_and_parent_immutability(tmp_path, monkeypatch):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("QUANT_CALL_JOURNAL_ROOT", str(tmp_path / "calls"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        payload = {**freeze_inputs(app), "question": "全面分析平安银行"}
        root = client.post("/api/quant/runs", json=payload).json()
        parent = finish(client, root["run_id"])
        original = client.get("/api/reports/" + parent["report_id"]).content
        citations = client.get("/api/reports/" + parent["report_id"] + "/citations").content
        url = "/api/quant/runs/" + root["run_id"] + "/followups"
        for question in (
            "现金流再细看",
            "增长率按12%重算",
            "对比贵州茅台",
            "近三年收益",
            "无风险利率按3%重算",
        ):
            rejected = client.post(url, json={"question": question, "request_key": str(uuid4())})
            assert rejected.status_code == 422, rejected.text
        missing_basis = client.post(
            url, json={"question": "按前复权重算", "request_key": str(uuid4())}
        )
        assert missing_basis.status_code == 422
        request = {"question": "无风险利率按0重算", "request_key": str(uuid4())}
        response = client.post(url, json=request)
        assert response.status_code == 202, response.text
        child = response.json()
        assert child["investigation_id"] == root["investigation_id"]
        assert child["run_id"] != root["run_id"]
        assert client.post(url, json=request).json()["run_id"] == child["run_id"]

        assert (
            client.post(
                "/api/quant/runs/" + child["run_id"] + "/followups", json=request
            ).status_code
            == 409
        )

        import asyncio

        finish(client, child["run_id"])

        async def wait(**kwargs):
            await asyncio.Event().wait()

        original_execute = app.state.quant_runtime.runner._execute_quant
        monkeypatch.setattr(app.state.quant_runtime.runner, "_execute_quant", wait)
        pending = client.post(
            url, json={"question": "重新核对收益与风险", "request_key": str(uuid4())}
        )
        assert pending.status_code == 202, pending.text
        active = pending.json()["run_id"]
        assert (
            client.post(
                url, json={"question": "重新核对收益与风险", "request_key": str(uuid4())}
            ).status_code
            == 409
        )
        assert client.post("/api/runs/" + active + "/cancel").status_code == 200
        finish(client, active)
        monkeypatch.setattr(app.state.quant_runtime.runner, "_execute_quant", original_execute)
        retry = client.post(
            url, json={"question": "重新核对收益与风险", "request_key": str(uuid4())}
        )
        assert retry.status_code == 202, retry.text
        recovered = finish(client, retry.json()["run_id"])
        assert len(recovered["versions"]) == 4
        assert client.get("/api/reports/" + parent["report_id"]).content == original
        conflict = client.post(url, json={**request, "question": "重新核对收益与风险"})
        assert conflict.status_code == 409
        second = finish(client, child["run_id"])
        assert second["parent_run_id"] == root["run_id"]
        assert second["input_snapshot_ids"] == parent["input_snapshot_ids"]
        assert second["plan"]["date_start"] == parent["plan"]["date_start"]
        assert second["plan"]["asof"] == parent["plan"]["asof"]
        assert second["methodology"]["explicit_zero_risk_free"] is True
        report = client.get("/api/reports/" + second["report_id"]).json()
        assert report["report"]["version"] == 2
        assert "无风险利率按0重算" in str(report)
        assert [v["version"] for v in second["versions"][:2]] == [1, 2]
        assert client.get("/api/reports/" + parent["report_id"]).content == original
        assert client.get("/api/reports/" + parent["report_id"] + "/citations").content == citations
        restored = client.get("/api/quant/runs/" + root["run_id"]).json()
        assert [v["citation_id"] for v in restored["values"]] == [
            v["citation_id"] for v in parent["values"]
        ]
        assert client.post(url, json=request).json()["run_id"] == child["run_id"]


def test_followup_rejects_unfinished_and_partial_parents(tmp_path, monkeypatch):
    import asyncio

    async def wait(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr("marketpulse.quant.acquisition.acquire_inputs", wait)
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        inputs = freeze_inputs(app)
        inputs.pop("frozen_snapshot_ids")
        root = client.post("/api/quant/runs", json=inputs).json()
        request = {"question": "重新核对收益与风险", "request_key": str(uuid4())}
        url = "/api/quant/runs/" + root["run_id"] + "/followups"
        assert client.post(url, json=request).status_code == 409
        assert client.post("/api/runs/" + root["run_id"] + "/cancel").status_code == 200
        assert client.post(url, json=request).status_code == 422
