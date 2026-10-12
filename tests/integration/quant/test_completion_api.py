"""Full delivery contracts over isolated synthetic source packages and fake model branches."""

import copy
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant import completion
from marketpulse.quant.api import router as legacy_quant_router
from marketpulse.quant.backtest.catalog import PackageCatalog
from marketpulse.quant.contracts import canonical
from marketpulse.quant.live.research_api import router as research_router
from marketpulse.quant.live.store import ResearchRunRow, ResearchStore
from tests.unit.quant.test_backtest_package import fixture


@pytest.fixture
def api(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "db.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    ResearchRunRow.__table__.create(engine)
    repo = ResearchStore(
        sessionmaker(engine),
        LocalContentAddressedBlobStorage(tmp_path / "blobs"),
        tmp_path / "legacy",
    )
    app = FastAPI()
    app.state.research_store = repo
    app.state.research_jobs = {}
    app.state.settings = Settings()
    app.state.research_model_port = object()
    app.state.strategy_catalog = PackageCatalog(tmp_path / "catalog")
    app.include_router(legacy_quant_router)
    app.include_router(completion.router)
    app.include_router(research_router)
    with TestClient(app) as client:
        yield client, repo, app
    engine.dispose()


def wait(client, run):
    for _ in range(2000):
        response = client.get("/api/quant/deliveries/" + run)
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] != "RUNNING":
            return state
        time.sleep(0.01)
    pytest.fail("delivery timed out")


def install(app, name="synthetic-test-only", reviews=True):
    p, r, blobs = fixture()
    root = app.state.strategy_catalog.directory(name)
    (root / "objects").mkdir(parents=True)
    (root / "package.json").write_text(p.model_dump_json(), encoding="utf-8")
    (root / "reviews.json").write_text(
        canonical([v.model_dump(mode="json") for v in r] if reviews else []), encoding="utf-8"
    )
    for sha, raw in blobs.items():
        (root / "objects" / sha).write_bytes(raw)
    return root, p


def test_no_package_is_persistent_blocked_not_successful_returns(api):
    client, repo, _ = api
    request = dict(request_key=str(uuid4()), risk_free_annual_pct="2")
    response = client.post("/api/quant/strategies", json=request)
    assert response.status_code == 202
    run = response.json()["run_id"]
    result = wait(client, run)
    assert result["status"] == "COMPLETED" and result["data"]["execution_status"] == "BLOCKED"
    assert "nav" not in result["data"] and "metrics" not in result["data"]
    assert client.post("/api/quant/strategies", json=request).json()["run_id"] == run
    assert client.post("/api/quant/strategies", json=dict(request, roe_min="18")).status_code == 409
    assert client.post("/api/quant/strategies", json=dict(request, strict=True)).status_code == 422
    assert (
        client.post("/api/quant/strategies", json={"request_key": str(uuid4())}).status_code == 422
    )
    assert (
        json.loads(client.get("/api/quant/deliveries/" + run + "/export").text)["data"]
        == result["data"]
    )
    repo.interrupt_abandoned()
    assert repo.get(run)["status"] == "COMPLETED"
    assert any(r["run_id"] == run for r in client.get("/api/quant/research/runs").json())


def test_installed_package_runs_fixed_isolated_worker_and_freezes_sources(api):
    client, repo, app = api
    root, p = install(app)
    request = dict(request_key=str(uuid4()), package_id=root.name, risk_free_annual_pct="2")
    response = client.post("/api/quant/strategies", json=request)
    assert response.status_code == 202, response.text
    state = wait(client, response.json()["run_id"])
    assert state["status"] == "COMPLETED", state.get("error")
    result = state["data"]
    assert result["execution_status"] == "COMPLETED"
    assert len(result["nav"]["dates"]) == len(p.sessions)
    assert result["trades"][0]["stocks"]
    assert result["ledgers"]["strategy"]["orders"][0]["status"] == "FILLED"
    assert result["metric_status"] == "UNVERIFIED"
    assert result["sources"] and repo.value(result["package_ref"]) == p.model_dump(mode="json")
    before = copy.deepcopy(state)
    (root / "reviews.json").write_text("[]", encoding="utf-8")
    proof = client.post("/api/quant/deliveries/" + state["run_id"] + "/reproduce")
    assert proof.status_code == 200, proof.text
    assert proof.json()["status"] == "MATCH"
    assert client.get("/api/quant/deliveries/" + state["run_id"]).json()["data"] == before["data"]
    ref = result["sources"][0]["blob_ref"].rsplit("/", 1)[1]
    blob = next(p for p in repo.blobs._root.rglob(ref + "*") if p.is_file())
    blob.write_bytes(b"tampered")
    assert client.get("/api/quant/deliveries/" + state["run_id"]).status_code == 409


def test_self_assertions_do_not_admit_and_catalog_paths_restricted(api):
    client, _, app = api
    root, _ = install(app, reviews=False)
    response = client.post(
        "/api/quant/strategies",
        json=dict(request_key=str(uuid4()), package_id=root.name, risk_free_annual_pct=0),
    )
    state = wait(client, response.json()["run_id"])
    assert state["status"] == "COMPLETED", state.get("error")
    assert state["data"]["execution_status"] == "BLOCKED" and "nav" not in state["data"]
    assert (
        client.post(
            "/api/quant/strategies",
            json=dict(request_key=str(uuid4()), package_id="../x", risk_free_annual_pct=0),
        ).status_code
        == 422
    )
    assert client.get("/api/quant/strategies/schema").json()["layout"]
    assert len(client.get("/api/quant/delivery").json()["data_slots"]) >= 4


def research_data(code):
    return dict(
        subject=dict(code=code, name="测试" + code, company_type="4", industry="测试行业"),
        data_dates=dict(financial="20240630", price="2024-08-01", valuation="2024-08-01"),
        acquired_at="2024-08-02",
        revenue_basis="营业收入",
        metrics=[
            dict(name="营收累计同比", value=5 if code == "000333" else 3, unit="%", source="lrb")
        ],
        calculation_inputs=dict(
            prices=[dict(date="2024-07-31", close=10), dict(date="2024-08-01", close=11)]
        ),
        agent_report=dict(claims=[dict(text="仅解释测试事实", evidence_ids=["F0"])]),
        sources=[],
    )


def test_comparison_partial_failure_resume_keeps_completed_branch(api, monkeypatch):
    client, repo, _ = api
    calls = []
    fail = [True]

    def resolve(query, save):
        return dict(code=query, name="测试" + query, company_type="4", industry="测试行业"), []

    class Workflow:
        def __init__(self, *args):
            pass

        async def run(self, run):
            state = repo.get(run, with_data=False)
            code = state["request"]["code"]
            calls.append(code)
            if code == "000651" and fail[0]:
                fail[0] = False
                state.update(status="FAILED", error="synthetic provider failure")
            else:
                state.update(
                    status="COMPLETED",
                    result_ref=repo.blob(research_data(code)),
                    subject=research_data(code)["subject"],
                )
            repo.put(state)

    monkeypatch.setattr(completion, "resolve", resolve)
    monkeypatch.setattr(completion, "ResearchWorkflow", Workflow)
    payload = dict(request_key=str(uuid4()), entities=["000333", "000651"])
    response = client.post("/api/quant/research/comparisons", json=payload)
    assert response.status_code == 202
    run = response.json()["run_id"]
    state = wait(client, run)
    assert state["status"] == "FAILED" and len(state["children"]) == 2
    first = copy.deepcopy(repo.get(state["children"][0]))
    assert client.post("/api/quant/deliveries/" + run + "/resume").status_code == 202
    state = wait(client, run)
    assert state["status"] == "COMPLETED", state.get("error")
    assert calls == ["000333", "000651", "000651"]
    assert repo.get(state["children"][0]) == first
    assert state["data"]["rows"][0]["difference"] == 2
    assert state["data"]["chart"]["series"][0]["data"] == [1, 1.1]
    assert client.post("/api/quant/research/comparisons", json=payload).json()["run_id"] == run
    assert (
        client.post(
            "/api/quant/research/comparisons", json=dict(payload, entities=["000333", "600519"])
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/quant/research/comparisons", json=dict(payload, entities=["000333", "000333"])
        ).status_code
        == 422
    )
    assert "child_result_refs" in client.get("/api/quant/deliveries/" + run + "/export").text


def test_comparison_period_industry_and_interior_gap_not_silently_aligned():
    left, right = research_data("000333"), research_data("000651")
    states = [
        dict(run_id="A", data=left, result_ref="a"),
        dict(run_id="B", data=right, result_ref="b"),
    ]
    right["data_dates"]["financial"] = "20240331"
    result = completion.compare_data(states, datetime.now(UTC).isoformat())
    assert result["rows"][0]["alignment"] == "METHOD_DIFFERENCE"
    assert result["rows"][0]["difference"] is None
    left["calculation_inputs"]["prices"].insert(1, dict(date="2024-07-31-extra", close=10))
    assert completion.compare_data(states, "date")["chart"]["x"] == []


def test_new_endpoints_do_not_shadow_existing_quant_routes(api):
    _, _, app = api
    seen = set()
    for source in (legacy_quant_router, completion.router, research_router):
        for route in source.routes:
            for method in getattr(route, "methods", ()):
                key = method, route.path
                assert key not in seen, key
                seen.add(key)
    assert ("POST", "/api/quant/comparisons") in seen
    assert ("POST", "/api/quant/research/comparisons") in seen
