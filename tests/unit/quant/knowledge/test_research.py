import asyncio
import io
import json
import time
import zipfile
from decimal import Decimal
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge.research_api import router
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run
from marketpulse.quant.knowledge.research_store import InvestmentStore
from marketpulse.quant.knowledge.routing import RULE_IDS
from marketpulse.quant.live.store import ResearchStore

from .test_features import market_packet


def knowledge():
    return dict(
        rules=[
            dict(
                rule_id=r,
                version="a" * 64,
                source_families=["SYNTHETIC"],
                rule=dict(title="合成测试规则"),
            )
            for r in RULE_IDS
        ],
        documents=[],
        fragments=[
            dict(
                rule_id=r,
                title="合成测试方法",
                system_rule="测试条件须核查真实输入，不代表投资效果。",
                adaptation="合成测试改写，不代表作者原意。",
                counterexamples=["会计利润可能不能兑现为现金。"],
                quote="SYNTHETIC SOURCE ONLY",
            )
            for r in RULE_IDS
        ],
    )


class SyntheticPort:
    def __init__(self, fail_writer=False, reject_review=False):
        self.roles = []
        self.fail_writer = fail_writer
        self.reject_review = reject_review
        self.contexts = []

    async def generate(self, request):
        schema = request.response_model
        role = {
            agents.Plan: "Planner",
            agents.Analysis: "Analyst",
            agents.Review: "Verifier",
            agents.ReviewChecks: "Verifier",
            agents.DirectionCheck: "Verifier",
            agents.Writing: "Writer",
        }[schema]
        self.roles.append(role)
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        self.contexts.append(context)
        if role == "Writer" and self.fail_writer:
            raise ValueError("synthetic provider failure")
        if role == "Planner":
            value = dict(
                selections=[
                    dict(
                        security_id=s["security_id"],
                        route_id=s["eligible_routes"][0],
                        rationale="测试：当前输入适合趋势条件观察。",
                        focus=["核查价格与量能条件。"],
                    )
                    for s in context["stocks"]
                ]
            )
        elif role == "Analyst":
            opinions = []
            for s in context["stocks"]:
                facts = [
                    f["evidence_id"]
                    for f in context["evidence"]
                    if f.get("security_id") == s["security_id"]
                    or f["evidence_id"].startswith(
                        (
                            "F-" + s["security_id"].replace(".", "") + "-",
                            "T-" + s["security_id"] + "-",
                            "X-" + s["security_id"] + "-",
                        )
                    )
                ]
                opinions.append(
                    dict(
                        security_id=s["security_id"],
                        direction="UNFAVORABLE",
                        summary="倾向看空，测试情景需等待确认。",
                        claims=[
                            dict(
                                claim_id="C1",
                                text="合成测试：价格条件仍需等待确认。",
                                evidence_ids=[facts[0]],
                            ),
                            dict(
                                claim_id="C2",
                                text="合成测试：市场条件尚有不确定性。",
                                evidence_ids=[facts[1]],
                            ),
                        ],
                        opposing=[
                            dict(
                                claim_id="C3",
                                text="合成测试：价格仍可能出现反弹。",
                                evidence_ids=[facts[0]],
                            )
                        ],
                        direction_claim_ids=["C1"],
                        method_rule_ids=[RULE_IDS[0]],
                        invalidation=["若价格条件转强则复核方向。"],
                        next_checks=["核对后续价格变化。"],
                    )
                )
            value = dict(opinions=opinions)
        elif role == "Verifier":
            value = dict(
                reviews=[
                    dict(
                        security_id=o["security_id"],
                        verdict="APPROVE",
                        decisions=[
                            dict(
                                claim_id=c["claim_id"],
                                verdict="APPROVE",
                                reason="合成测试确认字段绑定。",
                            )
                            for c in o["claims"] + o["opposing"]
                        ],
                        direction_supported=True,
                        conditions_respected=True,
                        reason="合成测试仅验证流程，不代表真实投资判断。",
                    )
                    for o in context["proposal"]["opinions"]
                ]
            )
            if schema is agents.DirectionCheck:
                value = dict(
                    reviews=[
                        dict(
                            security_id=o["security_id"],
                            decision="SUPPORTED",
                            reason="合成测试确认有限依据下的方向取舍。",
                        )
                        for o in context["proposal"]["opinions"]
                    ]
                )
        else:
            value = context
        if role == "Verifier" and self.reject_review:
            for item in value["reviews"]:
                item.update(verdict="REJECT", direction_supported=False)
        if schema is agents.ReviewChecks:
            for item in value["reviews"]:
                item.pop("verdict", None)
        return SimpleNamespace(
            output=schema.model_validate(value),
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="synthetic-response",
        )


@pytest.fixture
def runtime(rules, monkeypatch):
    base = ResearchStore(rules.sessions, rules.library.blobs, Path("unused-synthetic"))
    repo = InvestmentStore(base)
    packet = market_packet()
    packet.update(captured_at="2026-10-16T00:00:00+00:00", sources=[])
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.research_service.knowledge_snapshot", lambda *_: knowledge()
    )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.research_service.verify_trading_rules", lambda *_: []
    )
    return repo, packet, SimpleNamespace(model="SYNTHETIC", model_read_timeout_seconds=5)


def test_contract_keeps_old_periods_separate_and_defaults_sessions():
    assert (
        InvestmentRequest(
            request_key=uuid4(), entities=["000333"], horizon="MEDIUM"
        ).horizon_sessions
        == 60
    )
    with pytest.raises(ValueError):
        InvestmentRequest(
            request_key=uuid4(), entities=["000333"], horizon="SWING", horizon_sessions=60
        )


def test_four_roles_private_profile_stays_local_and_complete_run_is_immutable(runtime):
    repo, packet, settings = runtime
    port = SyntheticPort()
    request = InvestmentRequest(
        request_key=uuid4(),
        entities=["000333"],
        profile=dict(capital="987654.32", available_cash="987654.32"),
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.roles == ["Planner", "Analyst", "Verifier", "Writer"]
    assert "987654.32" not in json.dumps(port.contexts)
    assert all(
        "profile" not in c and "holdings" not in c and "portfolio" not in c for c in port.contexts
    )
    assert not result["data"]["privacy"]["profile_sent_to_model"]
    assert result["data"]["candidates"][0]["action"] == "倾向看空，暂不安排新增仓位"
    assert Decimal(result["data"]["portfolio"]["reserved_cash"]) == 0
    assert "看空" in result["data"]["portfolio"]["plans"][0]["reason"]
    assert repo.base.list() == []
    assert repo.create(request.model_dump(mode="json"))[1] is False
    changed = dict(result, title="overwrite")
    with pytest.raises(ValueError):
        repo.save(changed)


def test_failed_writer_resumes_without_refetch_or_repeating_approved_roles(runtime):
    repo, packet, settings = runtime
    port = SyntheticPort(fail_writer=True)
    request = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    state, _ = repo.create(request.model_dump(mode="json"))
    first = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert first["status"] == "INTERRUPTED" and "result_ref" not in first
    # Simulate a restart between persisting the successful call and its step index.
    first["model_steps"].pop("Analyst-0")
    repo.save(first)
    fixed = SyntheticPort()

    def forbidden(*_):
        raise AssertionError("must reuse frozen inputs")

    final = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=fixed, collector=forbidden)
    )
    assert final["status"] == "COMPLETED", final.get("error")
    assert fixed.roles == ["Writer"]
    assert any(c["status"] == "FAILED" for c in final["calls"])


def test_rejected_explanations_can_continue_with_new_feedback_round(runtime):
    repo, packet, settings = runtime
    request = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    state, _ = repo.create(request.model_dump(mode="json"))
    rejected = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=SyntheticPort(reject_review=True),
            collector=lambda *_: packet,
        )
    )
    assert rejected["status"] == "INTERRUPTED" and rejected["review_round"] == 3
    port = SyntheticPort()
    resumed = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert resumed["status"] == "COMPLETED", resumed.get("error")
    assert port.roles == ["Verifier", "Writer"]
    assert resumed["approved_analysis_step"] == "Analyst-2"
    assert resumed["direction_review_recovery"]["previous_review_step"] == "Verifier-2"


def test_api_idempotence_local_guard_and_self_contained_export(runtime, monkeypatch):
    repo, packet, settings = runtime
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = repo.base
    app.state.knowledge_rules = None
    app.state.settings = settings
    app.state.research_jobs = {}
    app.state.investment_research_port = SyntheticPort()
    app.state.investment_research_collector = lambda *_: packet
    definition = InvestmentRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    with TestClient(app) as client:
        first = client.post("/api/quant/investment-research/runs", json=definition)
        assert first.status_code == 202
        url = "/api/quant/investment-research/runs/" + first.json()["run_id"]
        for _ in range(100):
            result = client.get(url)
            if result.json()["status"] != "RUNNING":
                break
            time.sleep(0.01)
        assert result.json()["status"] == "COMPLETED", result.json().get("error")
        assert (
            client.post("/api/quant/investment-research/runs", json=definition).json()["result_ref"]
            == result.json()["result_ref"]
        )
        assert (
            client.post(
                "/api/quant/investment-research/runs",
                json=definition,
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/quant/investment-research/runs", json=dict(definition, horizon_sessions=30)
            ).status_code
            == 409
        )
        assert client.get(url + "/sources/" + "0" * 64).status_code == 404
        audit = client.get(url + "/audit").json()
        assert len(audit["model_calls"]) == 4
        response = client.get(url + "/export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert "record.json" in bundle.namelist()
            assert len([n for n in bundle.namelist() if n.startswith("blobs/")]) > 4
        # UI fixture only: never register synthetic data in the user's real database.
        fixture = Path(".phase3-quant-o/investment-knowledge-20261016-04/synthetic-ui-fixture.json")
        fixture.write_text(json.dumps(result.json(), ensure_ascii=False), encoding="utf8")
        monkeypatch.setattr(
            "marketpulse.quant.knowledge.research_service.knowledge_snapshot",
            lambda *_: (_ for _ in ()).throw(AssertionError("must use fixed versions")),
        )
        assert client.get(url).status_code == 200


def test_unapproved_route_cross_stock_citation_and_writer_rewrite_rejected(runtime):
    repo, packet, settings = runtime
    port = SyntheticPort()
    request = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    state, _ = repo.create(request.model_dump(mode="json"))
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    context = repo.value(result["public_context_ref"])
    plan = agents.Plan(
        selections=[
            dict(
                security_id="000333.SZ",
                route_id="QUALITY_GROWTH",
                rationale="合成测试错误路线选择。",
                focus=["核查现金回收。"],
            )
        ]
    )
    with pytest.raises(ValueError, match="未准入"):
        agents.guard_plan(plan, context)
    original = result["data"]["research_opinions"]
    wrong = json.loads(json.dumps(original))
    wrong[0]["claims"][0]["evidence_ids"] = ["F-OTHER-close"]
    with pytest.raises(ValueError, match="引用"):
        agents.guard_analysis(agents.Analysis(opinions=wrong), context)
    with pytest.raises(ValueError, match="未批准"):
        agents.guard_writing(
            agents.Writing(
                approved_hash="a" * 64,
                items=[
                    dict(
                        security_id="000333.SZ",
                        direction="FAVORABLE",
                        summary="倾向看多，改写摘要。",
                    )
                ],
            ),
            dict(opinions=original),
        )
