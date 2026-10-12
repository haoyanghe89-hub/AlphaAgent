"""Lifecycle workflow integration over isolated synthetic exchange files."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from marketpulse.investigation.server import create_app
from marketpulse.quant.backtest.lifecycles import endpoint, extract_lifecycles
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_backtest_benchmark_api import history
from tests.integration.quant.test_history_acquisition_api import completed, packet
from tests.unit.quant.test_backtest_lifecycles import document


def setup_sources(monkeypatch, tmp_path, mode="complete"):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    async def fetch(query, **kwargs):
        return packet(query)

    calls = []

    async def official(query, store):
        calls.append(query)
        if mode != "complete" and len(calls) > 1:
            if mode == "stop":
                await asyncio.sleep(30)
            raise TimeoutError("synthetic exchange unavailable")
        raw = document(query["table"])
        blob = store.blobs.put_bytes(raw)
        url, params, _ = endpoint(query)
        return dict(
            provider="sse_official" if query["table"].startswith("sh_") else "szse_official",
            query=query,
            source_url=url,
            params=params,
            **extract_lifecycles(raw, query),
            retrieved_at="2026-10-09T00:00:00Z",
            document_sha256=blob.ref.sha256,
            document_size=len(raw),
        )

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_lifecycles", official)


def test_lifecycle_uuid_export_parent_restart_and_raw_tamper(tmp_path, monkeypatch):
    setup_sources(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        original = client.get("/api/quant/backtests/" + parent).json()
        path = f"/api/quant/backtests/{parent}/lifecycles"
        body = dict(request_key=str(uuid4()))
        response = client.post(path, json=body)
        assert response.status_code == 202, response.text
        run = response.json()["run_id"]
        client.portal.call(completed, app, run)
        response = client.get("/api/quant/backtests/" + run)
        assert response.status_code == 200, response.text
        report = response.json()
        audit = report["lifecycle_audit"]
        assert audit["complete"] and len(audit["sources"]) == 5
        assert audit["check_counts"]["MATCH"] == 1
        assert audit["pit_level"] == "PARTIAL" and report["execution_status"] == "BLOCKED"
        assert report["historical_data"] == original["historical_data"]
        assert client.get("/api/quant/backtests/" + parent).json() == original
        assert client.post(path, json=body).json()["run_id"] == run
        assert client.post(path, json={**body, "url": "https://arbitrary"}).status_code == 422
        assert client.get(f"/api/quant/backtests/{run}/export").json() == report
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get("/api/quant/backtests/" + run).json() == report
        sha = audit["sources"][0]["document_sha256"]
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"tampered exchange input")
        assert client.get("/api/quant/backtests/" + run).status_code == 409
        assert client.get(f"/api/quant/backtests/{run}/export").status_code == 409


@pytest.mark.parametrize("mode", ["failure", "stop"])
def test_partial_official_lifecycles_keep_frozen_evidence(tmp_path, monkeypatch, mode):
    setup_sources(monkeypatch, tmp_path, mode)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        run = client.post(
            f"/api/quant/backtests/{parent}/lifecycles", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        if mode == "stop":
            assert client.post(f"/api/quant/backtests/{run}/stop").status_code == 200
        else:
            client.portal.call(completed, app, run)
        report = client.get("/api/quant/backtests/" + run).json()
        assert report["check_status"] == ("CANCELLED" if mode == "stop" else "FAILED")
        assert not report["lifecycle_audit"]["complete"]
        assert len(report["lifecycle_audit"]["sources"]) == 1
        assert report["execution_status"] == "BLOCKED" and "metrics" not in report
        assert all(
            a["official_observed_count"] is None and a["only_native_count"] is None
            for a in report["lifecycle_audit"]["annual"]
        )


def test_lifecycle_child_preserves_official_benchmark(tmp_path, monkeypatch):
    from tests.integration.quant.test_backtest_benchmark_api import sources

    sources(monkeypatch, tmp_path)
    setup_sources(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        benchmark = client.post(
            f"/api/quant/backtests/{parent}/benchmark", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(completed, app, benchmark)
        original = client.get("/api/quant/backtests/" + benchmark).json()
        run = client.post(
            f"/api/quant/backtests/{benchmark}/lifecycles", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        client.portal.call(completed, app, run)
        result = client.get("/api/quant/backtests/" + run).json()
        assert result["benchmark_audit"] == original["benchmark_audit"]
        assert result["historical_data"] == original["historical_data"]
        assert result["lifecycle_audit"]["complete"]
        assert client.get("/api/quant/backtests/" + benchmark).json() == original
