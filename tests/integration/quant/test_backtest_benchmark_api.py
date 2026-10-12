"""Synthetic CSI responses only in isolated tests, never accepted as page evidence."""

import asyncio
import json
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.investigation.server import create_app
from marketpulse.quant.backtest.benchmark import URL, extract_index
from tests.integration.investigation.test_investigation_server import _settings
from tests.integration.quant.test_history_acquisition_api import completed, create_parent, packet


def sources(monkeypatch, tmp_path, mode="complete"):
    monkeypatch.setenv("INVESTIGATION_BLOB_ROOT", str(tmp_path / "blobs"))

    async def fetch(query, **kwargs):
        return packet(query)

    calls = []

    async def official(query, store):
        calls.append(query)
        if len(calls) > 1 and mode in {"failure", "stop"}:
            if mode == "stop":
                await asyncio.sleep(30)
            raise TimeoutError("synthetic provider failure")
        first, last = date.fromisoformat(query["start"]), date.fromisoformat(query["end"])
        rows = [
            dict(
                tradeDate=str(first + timedelta(days=i)).replace("-", ""),
                indexCode=query["code"],
                close=100 if query["code"] == "000300" else 150,
            )
            for i in range((last - first).days + 1)
            if (first + timedelta(days=i)).weekday() < 5
        ]
        document = json.dumps(dict(success=True, data=rows)).encode()
        blob = store.blobs.put_bytes(document)
        return dict(
            provider="csindex_official",
            query=query,
            rows=extract_index(document, query),
            retrieved_at="2026-10-09T00:00:00Z",
            document_sha256=blob.ref.sha256,
            document_size=len(document),
            source_url=URL,
        )

    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_query", fetch)
    monkeypatch.setattr("marketpulse.quant.backtest.acquisition.fetch_index", official)


def history(app, client):
    parent = create_parent(client)
    run = client.post(
        f"/api/quant/backtests/{parent}/history", json=dict(request_key=str(uuid4()))
    ).json()["run_id"]
    client.portal.call(completed, app, run)
    return run


def test_official_benchmark_uuid_parent_export_restart_and_raw_integrity(tmp_path, monkeypatch):
    sources(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        original = client.get("/api/quant/backtests/" + parent).json()
        body = dict(request_key=str(uuid4()))
        path = f"/api/quant/backtests/{parent}/benchmark"
        response = client.post(path, json=body)
        assert response.status_code == 202, response.text
        run = response.json()["run_id"]
        client.portal.call(completed, app, run)
        response = client.get("/api/quant/backtests/" + run)
        assert response.status_code == 200, response.text
        report = response.json()
        audit = report["benchmark_audit"]
        assert report["check_status"] == "COMPLETED" and audit["complete"]
        assert audit["check_counts"]["MATCH"] == audit["expected_session_count"]
        assert audit["series"][0]["independent_check"] == "MATCH"
        assert audit["series"][1]["independent_check"] == "UNAVAILABLE"
        assert all(s["coverage"] == "COVERED" for s in audit["series"])
        assert report["execution_status"] == "BLOCKED" and audit["metric_status"] == "UNVERIFIED"
        assert "nav" not in report and "metrics" not in report
        assert client.get("/api/quant/backtests/" + parent).json() == original
        assert client.post(path, json=body).json()["run_id"] == run
        assert client.post(path, json={**body, "code": "arbitrary"}).status_code == 422
        assert client.get(f"/api/quant/backtests/{run}/export").json() == report
    with TestClient(create_app(settings=_settings(tmp_path))) as client:
        assert client.get("/api/quant/backtests/" + run).json() == report
        sha = audit["sources"][0]["document_sha256"]
        next((tmp_path / "blobs").rglob(sha)).write_bytes(b"corrupted raw official response")
        assert client.get("/api/quant/backtests/" + run).status_code == 409
        assert client.get(f"/api/quant/backtests/{run}/export").status_code == 409


@pytest.mark.parametrize("mode", ["failure", "stop"])
def test_benchmark_partial_stop_or_failure_preserves_inputs_and_blocks(tmp_path, monkeypatch, mode):
    sources(monkeypatch, tmp_path, mode)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        run = client.post(
            f"/api/quant/backtests/{parent}/benchmark", json=dict(request_key=str(uuid4()))
        ).json()["run_id"]
        if mode == "stop":
            assert client.post(f"/api/quant/backtests/{run}/stop").status_code == 200
        else:
            client.portal.call(completed, app, run)
        report = client.get("/api/quant/backtests/" + run).json()
        assert report["check_status"] == ("CANCELLED" if mode == "stop" else "FAILED")
        assert len(report["benchmark_audit"]["sources"]) == 1
        assert report["benchmark_audit"]["complete"] is False
        assert report["execution_status"] == "BLOCKED"


def test_raw_extractor_refuses_duplicate_wrong_code_and_nonfinite():
    query = dict(kind="official_index", code="000300", start="2024-04-30", end="2024-05-06")
    for rows in (
        [dict(tradeDate="20240430", indexCode="000300", close=100)] * 2,
        [dict(tradeDate="20240430", indexCode="H00300", close=100)],
        [dict(tradeDate="20240430", indexCode="000300", close="NaN")],
    ):
        with pytest.raises(ValueError):
            extract_index(json.dumps(dict(success=True, data=rows)).encode(), query)


def test_parent_chain_reads_use_one_connection_and_still_verify_sources(tmp_path, monkeypatch):
    sources(monkeypatch, tmp_path)
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        parent = history(app, client)
        for _ in range(3):
            run = client.post(
                f"/api/quant/backtests/{parent}/benchmark", json=dict(request_key=str(uuid4()))
            ).json()["run_id"]
            client.portal.call(completed, app, run)
            parent = run
        report = client.get("/api/quant/backtests/" + run).json()
        runtime = app.state.quant_runtime
        engine = create_engine(
            runtime.service.sessions.kw["bind"].url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.1,
            connect_args={"check_same_thread": False},
        )
        sessions = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(runtime.service, "sessions", sessions)
        monkeypatch.setattr(runtime.service.store, "sessions", sessions)
        try:
            assert client.get("/api/quant/backtests/" + run).json() == report
            assert client.get(f"/api/quant/backtests/{run}/export").json() == report
            sha = report["benchmark_audit"]["sources"][0]["document_sha256"]
            next((tmp_path / "blobs").rglob(sha)).write_bytes(b"corrupted ancestor input")
            assert client.get("/api/quant/backtests/" + run).status_code == 409
        finally:
            engine.dispose()
