"""Synthetic enhanced market research; never a real market recommendation."""

import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_decision_api import setup as setup  # noqa: F401
from test_market_history import dated_fixture

from marketpulse.quant.contracts import canonical, digest
from marketpulse.quant.decision import agents, market_scan
from marketpulse.quant.decision import market_enhanced as enhanced
from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.live.store import ResearchRunRow

PRIVATE_QUESTION = "合成私密提问，不应进入任何公开模型消息。"


def knowledge():
    return dict(
        rules=[
            dict(
                rule_id=rule_id,
                version="a" * 64,
                rule=dict(
                    title="合成研究方法",
                    horizons=["MEDIUM"],
                    system_rule="先看同窗证据，再保留反证与局限。",
                    required_inputs=["共同观察窗口与固定行业成员。"],
                    conditions=["只在本次覆盖范围内比较。"],
                    counterexamples=["少数成员成交集中可能削弱整体解释。"],
                    forbidden_inferences=["成交活跃不能证明主力资金流入。"],
                ),
            )
            for rule_id in (
                "KR-AVOID-RUIN",
                "KR-EXPECTATION-EXIT",
                "KR-MARKET-BREADTH",
                "KR-UNSELECTED",
            )
        ],
        documents=[],
        fragments=[],
    )


def request(**changes):
    values = dict(
        request_key=uuid4(),
        scope="market",
        entity="设备与制造观察范围",
        research_mode="market_scan",
        question=PRIVATE_QUESTION,
        enhanced_research=True,
    )
    values.update(changes)
    return ResearchRequest(**values).model_dump(mode="json")


class CountedFailure(RuntimeError):
    input_tokens = 30
    output_tokens = 10


class MarketPort:
    def __init__(self, *, reject_once=False, analyst_failures=0):
        self.requests = []
        self.reject_once = reject_once
        self.analyst_failures = analyst_failures
        self.reviews = 0

    async def generate(self, definition):
        self.requests.append(definition)
        context = json.loads(definition.messages[-1].content.split("\n上次检查失败：")[0])
        schema = definition.response_model
        if schema is enhanced.ResearchPlan:
            output = schema(
                selections=[
                    dict(
                        industry_id=row["industry_id"],
                        method_rule_ids=["KR-MARKET-BREADTH"],
                        hypothesis="关注是否伴随上涨广度扩散，仍需观察。",
                        countercheck="若少数成员成交占比集中，应收缩整体解释。",
                    )
                    for row in context["industries"]
                ],
                tools=[
                    dict(
                        tool=tool,
                        industry_id=context["industries"][i]["industry_id"],
                        query="核对成交关注与上涨广度。",
                    )
                    for i, tool in enumerate(("EVIDENCE_SEARCH", "SECTOR_COMPARISON"))
                ],
            )
        elif schema is agents.Plan:
            output = schema(focus_questions=["行业关注与上涨广度是否一致？"])
        elif schema is enhanced.Conclusion:
            if self.analyst_failures:
                self.analyst_failures -= 1
                raise CountedFailure("synthetic analyst interruption")
            output = schema(
                summary="设备行业成交关注居前，下一步应同时核对广度和集中度。",
                reasons=[
                    dict(
                        claim_id="C-SHARE",
                        text="设备行业在固定范围内的成交关注居前。",
                        evidence_ids=[context["evidence"][0]["evidence_id"]],
                    ),
                    dict(
                        claim_id="C-WINDOW",
                        text="共同窗口内的行业变化需要结合成员价格观察。",
                        evidence_ids=[context["evidence"][2]["evidence_id"]],
                    ),
                ],
                opposing=[
                    dict(
                        claim_id="C-LIMIT",
                        text="仅覆盖设备与制造，不能代表全市场行业表现。",
                        evidence_ids=["ME-COVERAGE"],
                    )
                ],
                conditions=["若广度与关注背离，应重新评估行业解释。"],
                method_rule_ids=["KR-MARKET-BREADTH", "KR-AVOID-RUIN"],
            )
        elif schema is enhanced.Review:
            self.reviews += 1
            reject = self.reject_once and self.reviews == 1
            output = schema(
                decisions=[
                    dict(
                        claim_id=row["claim_id"],
                        verdict="REJECT" if reject else "APPROVE",
                        reason="需要重核定性解释与观察范围。" if reject else "引用与定性解释一致。",
                    )
                    for row in context["proposal"]["reasons"] + context["proposal"]["opposing"]
                ],
                summary_supported=not reject,
                conditions_respected=True,
                reason="需要缩小摘要的覆盖解释。" if reject else "摘要与获准事实及范围一致。",
            )
        else:
            pytest.fail("unexpected schema: " + str(schema))
        return SimpleNamespace(
            output=output,
            provider="synthetic",
            model=definition.model_hint,
            response_id="synthetic-" + str(len(self.requests)),
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=1 if definition.max_output_tokens == 1 else 100,
            ),
        )


@pytest.fixture
def environment(setup, monkeypatch):
    app, _ = setup
    monkeypatch.setattr(enhanced, "published_knowledge", lambda *a, **kw: knowledge())
    app.state.settings.model_read_timeout_seconds = 30
    app.state.settings.reasoning_effort = "high"
    repo = app.state.decision_store
    collections = []

    def collector(repo, state, stop, save, step):
        collections.append(state["run_id"])
        snapshot = dated_fixture(repo)
        state["calls"].extend(
            dict(kind="source", status="COMPLETED", source=source) for source in snapshot["sources"]
        )
        save()
        return snapshot

    return app, repo, collector, collections


def execute(environment, port=None, definition=None):
    app, repo, collector, _ = environment
    state, fresh = repo.create(definition or request(), "synthetic-enhanced-market")
    assert fresh
    asyncio.run(
        market_scan.run(
            repo,
            state["run_id"],
            threading.Event(),
            app.state.settings,
            collector=collector,
            port=port or MarketPort(),
        )
    )
    return repo.get(state["run_id"])


def test_flag_preserves_legacy_serialization_and_rejects_unsupported_modes():
    old = request(enhanced_research=False)
    assert "enhanced_research" not in old
    assert ResearchRequest.model_validate(old).model_dump(mode="json") == old
    assert request()["enhanced_research"] is True
    for mode in ("evidence", "medium_term"):
        with pytest.raises(ValidationError, match="增强市场研究"):
            request(scope="single", entity="中微公司", research_mode=mode)


def test_enhanced_market_seals_actual_roles_tools_methods_and_unchanged_scope(environment):
    _, repo, _, collections = environment
    port = MarketPort()
    result = execute(environment, port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert len(collections) == 1
    data = result["data"]
    block = data["enhanced_research"]
    model = [c for c in result["calls"] if c.get("kind") == "model"]
    completed = [c for c in model if c["status"] == "COMPLETED"]
    assert [c["role"] for c in completed] == ["Researcher", "Planner", "Analyst", "Verifier"]
    assert all(c["role"] != "Writer" for c in model)
    assert result["model_calls"] == len(model) >= 4
    assert result["charged_tokens"] == sum(c["charged_tokens"] for c in model) <= 60000
    assert all(c["thinking_enabled"] for c in model)
    assert {m["rule_id"] for m in block["methods"]} == {
        "KR-AVOID-RUIN",
        "KR-EXPECTATION-EXIT",
        "KR-MARKET-BREADTH",
    }
    assert all(m["conditions"] and m["forbidden_inferences"] for m in block["methods"])
    assert [t["request"]["tool"] for t in block["tool_results"]] == [
        "EVIDENCE_SEARCH",
        "SECTOR_COMPARISON",
    ]
    assert all(t["evidence_ids"] for t in block["tool_results"])
    for call in model:
        assert PRIVATE_QUESTION not in canonical(repo.value(call["request_ref"]))
    original = market_scan.rebuild(repo, repo.value(data["market_snapshot_ref"]))
    assert {
        k: v for k, v in data.items() if k not in {"market_snapshot_ref", "enhanced_research"}
    } == original
    assert data["coverage"]["full_market_share_status"] == "UNAVAILABLE"
    assert data["metrics"][0]["mean_20_share"] == "0.75"
    days = repo.value(data["market_snapshot_ref"])["sessions"]
    facts = {
        f["locator"]["path"].rsplit("/", 1)[-1]: f for f in block["evidence"] if f.get("window")
    }
    assert facts["breadth"]["window"] == dict(start=days[-1], end=days[-1], sessions=1)
    assert facts["last_turnover"]["window"]["sessions"] == 1
    assert facts["top3_turnover_share"]["window"]["start"] == days[-1]
    assert facts["mean_20_share"]["window"]["start"] == days[-20]
    assert facts["median_member_return_20"]["window"]["start"] == days[-21]
    assert facts["change_5_share"]["window"]["current"] == days[-5:]
    assert facts["change_5_share"]["window"]["previous"] == days[-10:-5]
    enhanced.verify_result(repo, data)
    assert repo.get(result["run_id"])["result_sha256"] == digest(data)
    for changes in ({"model_calls": result["model_calls"] + 1}, {"elapsed_seconds": 901}):
        changed = dict(result, **changes)
        with pytest.raises(ValueError):
            enhanced.verify_state(repo, changed)


def test_legacy_market_keeps_zero_model_calls_and_original_report(environment):
    _, repo, _, _ = environment
    port = MarketPort()
    result = execute(environment, port, request(enhanced_research=False))
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["model_calls"] == 0 and not port.requests
    assert "enhanced_research" not in result["data"]
    assert "enhanced_research" not in result["request"]
    data = result["data"]
    assert {k: v for k, v in data.items() if k != "market_snapshot_ref"} == market_scan.rebuild(
        repo, repo.value(data["market_snapshot_ref"])
    )


def test_rejected_review_repairs_with_frozen_facts_and_prior_feedback(environment):
    _, repo, _, _ = environment
    result = execute(environment, MarketPort(reject_once=True))
    assert result["status"] == "COMPLETED", result.get("error")
    block = result["data"]["enhanced_research"]
    manifest = repo.value(block["manifest_ref"])
    assert manifest["approved_round"] == 1
    analysts = [
        repo.value(c["request_ref"])["context"]
        for c in result["calls"]
        if c.get("role") == "Analyst" and c["status"] == "COMPLETED"
    ]
    assert len(analysts) == 2
    assert analysts[0]["feedback"] == []
    assert analysts[1]["feedback"][0]["decisions"][0]["verdict"] == "REJECT"
    assert {k: v for k, v in analysts[0].items() if k != "feedback"} == {
        k: v for k, v in analysts[1].items() if k != "feedback"
    }
    assert all(d["verdict"] == "APPROVE" for d in manifest["review"]["decisions"])
    enhanced.verify_result(repo, result["data"])


def test_resume_after_analyst_failure_reuses_research_and_planning(environment):
    app, repo, collector, collections = environment
    port = MarketPort(analyst_failures=2)
    failed = execute(environment, port)
    assert failed["status"] == "FAILED" and not failed.get("result_ref")
    assert failed["stage"] == "ANALYST"
    old_calls = copy.deepcopy(failed["calls"])
    old_tokens = failed["charged_tokens"]
    failed.update(status="RUNNING", error=None)
    repo.save(failed)
    asyncio.run(
        market_scan.run(
            repo,
            failed["run_id"],
            threading.Event(),
            app.state.settings,
            collector=collector,
            port=port,
        )
    )
    recovered = repo.get(failed["run_id"])
    assert recovered["status"] == "COMPLETED", recovered.get("error")
    assert recovered["calls"][: len(old_calls)] == old_calls
    assert recovered["charged_tokens"] > old_tokens
    assert len(collections) == 1
    assert sum(c.get("role") == "Researcher" for c in recovered["calls"]) == 1
    assert sum(c.get("role") == "Planner" for c in recovered["calls"]) == 1
    assert recovered["market_snapshot_ref"] == failed["market_snapshot_ref"]
    assert recovered["enhanced_knowledge_ref"] == failed["enhanced_knowledge_ref"]


def test_enhanced_market_cannot_reset_or_exceed_original_call_budget(environment):
    app, repo, collector, collections = environment
    port = MarketPort()
    result = execute(environment, port, request(budget_profile=dict(max_model_calls=1)))
    assert result["status"] == "FAILED" and not result.get("result_ref")
    assert result["model_calls"] == 1
    original = copy.deepcopy(result["calls"])
    charged = result["charged_tokens"]
    result.update(status="RUNNING", error=None)
    repo.save(result)
    asyncio.run(
        market_scan.run(
            repo,
            result["run_id"],
            threading.Event(),
            app.state.settings,
            collector=collector,
            port=port,
        )
    )
    final = repo.get(result["run_id"])
    assert final["status"] == "FAILED" and not final.get("result_ref")
    assert final["model_calls"] == 1
    assert final["charged_tokens"] == charged
    assert final["calls"] == original and len(collections) == 1
    assert final["config"]["budget"] == result["request"]["budget_profile"]


@pytest.mark.parametrize(
    "mutation",
    ["profile", "model_context", "manifest", "planned_tools", "methods", "facts", "knowledge"],
)
def test_enhanced_market_rejects_changed_actual_inputs_and_publication(environment, mutation):
    _, repo, _, _ = environment
    result = execute(environment)
    assert result["status"] == "COMPLETED", result.get("error")
    data = copy.deepcopy(result["data"])
    block = data["enhanced_research"]
    if mutation in {"profile", "model_context"}:
        frozen = repo.value(block["research_state_ref"])
        call = next(c for c in frozen["calls"] if c.get("role") == "Researcher")
        definition = repo.value(call["request_ref"])
        if mutation == "profile":
            definition["thinking_enabled"] = False
        else:
            definition["context"]["coverage"]["complete_union"] = False
        call["request_ref"] = repo.blob(definition)
        block["research_state_ref"] = repo.blob(frozen)
    elif mutation == "manifest":
        manifest = repo.value(block["manifest_ref"])
        manifest["tool_results"][0]["evidence_ids"] = ["UNKNOWN"]
        block["manifest_ref"] = repo.blob(manifest)
    elif mutation == "planned_tools":
        manifest = repo.value(block["manifest_ref"])
        manifest["plan"]["tools"][0]["query"] = "核查另一项成交解释。"
        plan = enhanced.ResearchPlan.model_validate(manifest["plan"])
        manifest["tool_results"] = [
            enhanced.execute_tool(tool, enhanced.public_context(data)) for tool in plan.tools
        ]
        block["tool_results"] = copy.deepcopy(manifest["tool_results"])
        block["manifest_ref"] = repo.blob(manifest)
    elif mutation == "methods":
        block["methods"][0]["conditions"] = []
    elif mutation == "facts":
        data["metrics"][0]["mean_20_share"] = "0.99"
    elif mutation == "knowledge":
        frozen = repo.value(block["knowledge_ref"])
        frozen["rules"][0]["version"] = "b" * 64
        block["knowledge_ref"] = repo.blob(frozen)
    with pytest.raises(ValueError):
        market_scan.verify(repo, data)


def test_api_restores_exports_evidence_and_hides_tampered_enhanced_result(environment):
    app, repo, _, _ = environment
    result = execute(environment)
    assert result["status"] == "COMPLETED", result.get("error")
    root = "/api/quant/decision/runs/" + result["run_id"]
    evidence = result["data"]["enhanced_research"]["conclusion"]["reasons"][0]["evidence_ids"][0]
    with TestClient(app) as client:
        assert client.get(root).json() == result
        exported = client.get(root + "/export").json()
        audit = exported.pop("enhancement_audit")
        assert exported == result
        assert audit["knowledge"] == knowledge()
        assert audit["manifest"] == repo.value(result["data"]["enhanced_research"]["manifest_ref"])
        assert len(audit["model_calls"]) == result["model_calls"]
        assert all(row["request"] and row["response"] for row in audit["model_calls"])
        citation = client.get(root + "/evidence/" + evidence)
        assert citation.status_code == 200
        assert citation.json()["evidence"]["evidence_id"] == evidence
        assert client.get(root + "/evidence/UNKNOWN").status_code == 404
        assert client.post("/api/quant/decision/runs", json=result["request"]).json() == result
        assert (
            len(
                [
                    r
                    for r in client.get("/api/quant/decision/runs").json()
                    if r["run_id"] == result["run_id"]
                ]
            )
            == 1
        )
        changed = copy.deepcopy(result)
        data = changed.pop("data")
        data["enhanced_research"]["methods"][0]["conditions"] = []
        changed["result_ref"] = repo.blob(data)
        changed["result_sha256"] = digest(data)
        with repo.base.sessions.begin() as session:
            session.get(ResearchRunRow, result["run_id"]).payload = changed
        assert client.get(root).status_code == 409
        assert client.get(root + "/export").status_code == 409
