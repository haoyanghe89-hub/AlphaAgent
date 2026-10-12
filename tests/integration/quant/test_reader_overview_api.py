"""Reader projections and exports never rewrite completed research evidence."""

import copy
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant.live.research_api import compat_router, router
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "research.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    ResearchRunRow.__table__.create(engine)
    repo = ResearchStore(
        sessionmaker(engine),
        LocalContentAddressedBlobStorage(tmp_path / "blobs"),
        tmp_path / "legacy",
    )
    dates = [(date(2025, 10, 9) + timedelta(days=i)).isoformat() for i in range(366)]
    d = dict(
        subject=dict(name="测试公司"),
        acquired_at="2026-10-10",
        sources=[],
        metrics=[],
        checks=[],
        risks=[],
        notes=[],
        dcf=dict(status="NOT_APPLICABLE"),
        simulation=dict(note="研究模拟"),
        financial={},
        valuation={},
        data_dates=dict(price=dates[-1]),
        kline=dict(x=dates, ohlc=[[70 + i / 100] * 4 for i in range(366)]),
    )
    run = "RUN-LIVE-" + "1" * 32
    ref = repo.blob(d)
    repo.put(
        dict(
            run_id=run,
            request={},
            status="COMPLETED",
            created_at="2026-10-10",
            result_ref=ref,
            result_sha256=ref.rsplit("/", 1)[1],
            calls=[],
        )
    )
    app = FastAPI()
    app.state.research_store = repo
    app.include_router(router)
    app.include_router(compat_router)
    with TestClient(app) as value:
        yield value, repo, run
    engine.dispose()


def test_old_report_projection_alias_export_and_immutability(client):
    api, repo, run = client
    original = copy.deepcopy(repo.get(run))
    response = api.get(f"/api/quant/research/runs/{run}/overview")
    assert response.status_code == 200
    overview = response.json()
    assert overview["price"]["position"] == "区间偏上"
    assert overview["input_result_ref"] == original["result_ref"]
    assert overview == api.get(f"/api/quant/live/runs/{run}/overview").json()
    text = api.get(f"/api/quant/research/runs/{run}/export").text
    assert "后续观察条件" in text and "分业务" in text
    assert "价格位置不等于估值" in text
    assert repo.get(run) == original
    assert api.get(f"/api/quant/research/runs/{run}").json()["data"] == original["data"]


def test_incomplete_run_has_no_reader_projection(client):
    api, repo, _ = client
    run = "RUN-LIVE-" + "2" * 32
    repo.put(dict(run_id=run, request={}, status="RUNNING", created_at="2026-10-10", calls=[]))
    assert api.get(f"/api/quant/research/runs/{run}/overview").status_code == 409


def test_integrity_check_runs_before_reader_calculation(client):
    api, repo, run = client
    raw = repo.get(run)["result_ref"].rsplit("/", 1)[1]
    blob = next(p for p in repo.blobs._root.rglob(raw + "*") if p.is_file())
    blob.write_bytes(b"{}")
    assert api.get(f"/api/quant/research/runs/{run}/overview").status_code == 409
