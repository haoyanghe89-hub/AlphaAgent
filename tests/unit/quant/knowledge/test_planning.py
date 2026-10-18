from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.knowledge.planning_api import router
from marketpulse.quant.knowledge.planning_contracts import PlanRequest
from marketpulse.quant.knowledge.planning_service import execute
from marketpulse.quant.knowledge.planning_store import PlanningStore
from marketpulse.quant.knowledge.routing import RULE_IDS
from marketpulse.quant.live.store import ResearchStore

from .test_features import market_packet


def test_saved_run_idempotence_immutable_get_and_local_api(rules, monkeypatch):
    base = ResearchStore(rules.sessions, rules.library.blobs, Path("unused-synthetic"))
    repository = PlanningStore(base)
    current = [
        dict(
            rule_id=r,
            version="a" * 64,
            source_families=["SYNTHETIC"],
            rule=dict(title="合成规则", execution_status="REFERENCE_ONLY"),
        )
        for r in RULE_IDS
    ]
    fake_rules = SimpleNamespace(current=lambda _: current)
    packet = market_packet()
    packet.update(captured_at="2026-10-16T00:00:00+00:00", sources=[])
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.planning_service.verify_trading_rules", lambda *_: []
    )
    request = PlanRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    state, fresh = repository.create(request)
    assert fresh
    assert not repository.create(request)[1]
    execute(
        repository,
        fake_rules,
        state["run_id"],
        SimpleNamespace(is_set=lambda: False),
        collector=lambda *_: packet,
    )
    result = repository.get(state["run_id"])
    assert result["status"] == "COMPLETED"
    assert result["data"]["portfolio"]["status"] == "NO_PROFILE"
    assert base.list() == []
    old = dict(result)
    old["title"] = "changed"
    import pytest

    with pytest.raises(ValueError, match="cannot be overwritten"):
        repository.save(old)
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = base
    app.state.knowledge_rules = fake_rules
    app.state.research_jobs = {}
    with TestClient(app) as client:
        assert client.get("/api/quant/investment-plans/" + state["run_id"]).status_code == 200
        assert client.get("/api/quant/investment-plans/RUN-IP-" + "0" * 32).status_code == 404
        assert (
            client.post("/api/quant/investment-plans", json=request).json()["run_id"]
            == state["run_id"]
        )
        assert (
            client.post(
                "/api/quant/investment-plans",
                json=request,
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert client.post("/api/quant/knowledge/supplements", json={}).status_code == 405
        modified = dict(request, entities=["600519"])
        assert client.post("/api/quant/investment-plans", json=modified).status_code == 409
