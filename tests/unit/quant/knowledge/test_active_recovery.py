# ruff: noqa: F811
# Imported pytest fixtures intentionally share names with test parameters.
"""Public-only, bounded enhanced workflow edges with synthetic model and HTTP fixtures."""

import asyncio
import copy
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests

from marketpulse.quant.decision import tushare
from marketpulse.quant.decision.tools import SourceUnavailable
from marketpulse.quant.knowledge import active_research as active
from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_research import SyntheticPort, knowledge, runtime  # noqa: F401


@pytest.fixture
def enhanced(runtime, monkeypatch):
    repo, packet, settings = runtime
    snapshot = knowledge()
    for item in snapshot["rules"]:
        item["rule"].update(
            system_rule="合成测试：核对实际证据并保留适用条件。",
            horizons=["SWING", "MEDIUM"],
            required_inputs=["行情"],
            conditions=["同一截止时点。"],
            counterexamples=["趋势可能反复。"],
            forbidden_inferences=["不能保证收益。"],
            execution_status="REFERENCE_ONLY",
            validation_status="UNVALIDATED",
        )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.research_service.knowledge_snapshot",
        lambda *args, **kwargs: snapshot,
    )
    request = InvestmentRequest(
        request_key=uuid4(),
        entities=["000333"],
        enhanced_research=True,
        profile=dict(
            capital="987654.32",
            available_cash="900000",
            holdings=[
                dict(
                    security_id="600036.SH",
                    route_id="QUALITY_GROWTH",
                    industry_code="801780.SI",
                    market_value="87654.32",
                )
            ],
        ),
    )
    state, _ = repo.create(request.model_dump(mode="json"))
    return repo, packet, settings, state


class EdgePort(SyntheticPort):
    def __init__(self, *, two_rounds=False, valuation=False, fail_analysis=False):
        super().__init__()
        self.two_rounds, self.valuation, self.fail_analysis = two_rounds, valuation, fail_analysis

    async def generate(self, request):
        if request.response_model is not active.ResearchPlan:
            if self.fail_analysis and request.response_model is agents.Analysis:
                from marketpulse.investigation.recording.errors import InvalidProviderResponseError

                raise InvalidProviderResponseError(
                    "synthetic interruption", input_tokens=5, output_tokens=5
                )
            return await super().generate(request)
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        self.roles.append("Researcher")
        self.contexts.append(context)
        assert request.thinking_enabled and request.reasoning_effort == "low"
        sid = context["stocks"][0]["security_id"]
        second = bool(context["tool_results"])
        plan = active.ResearchPlan(
            selections=[
                dict(
                    security_id=sid,
                    method_rule_ids=["KR-TREND-MARKET"],
                    hypothesis="合成测试：核对市场条件而不预设事实。",
                    countercheck="合成测试：检查相反证据及其适用窗口。",
                )
            ],
            tools=[
                dict(
                    tool="VALUATION_SNAPSHOT" if self.valuation else "EVIDENCE_SEARCH",
                    security_id=sid,
                    query="成交金额" if second else "收盘价",
                )
            ],
            ready=not self.two_rounds or second,
        )
        return SimpleNamespace(
            output=plan,
            usage=SimpleNamespace(input_tokens=100, output_tokens=100),
            provider="SYNTHETIC",
            model="SYNTHETIC",
            response_id="synthetic-edge",
        )


def run_state(enhanced, port, *, frozen=False):
    repo, packet, settings, state = enhanced

    def collector(*_):
        if frozen:
            raise AssertionError("recovery must retain its frozen inputs")
        return packet

    return asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=collector)
    )


def test_two_rounds_have_distinct_tools_bounded_cost_and_no_private_profile(enhanced):
    repo, _, _, _ = enhanced
    port = EdgePort(two_rounds=True)
    result = run_state(enhanced, port)
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.roles == ["Researcher", "Researcher", "Planner", "Analyst", "Verifier"]
    assert result["model_calls"] == 5 and result["charged_tokens"] == result["tokens"] == 1000
    manifest = repo.value(result["active_manifest_ref"])
    assert [r["results_before"] for r in manifest["rounds"]] == [0, 1]
    assert [r["round"] for r in manifest["results"]] == [0, 1]
    assert len({r["request_key"] for r in manifest["results"]}) == 2
    assert len(port.contexts[1]["tool_results"]) == 1
    assert result["model_steps"]["Writer"]["output_kind"] == "deterministic_publication"
    encoded = json.dumps(port.contexts)
    assert all(v not in encoded for v in ["987654.32", "900000", "87654.32", "600036.SH"])
    assert not result["data"]["privacy"]["profile_sent_to_model"]
    repo.verify(result)


@pytest.mark.parametrize("drop_round_checkpoint", [False, True])
@pytest.mark.parametrize("two_rounds", [False, True])
def test_completed_research_recovers_without_refetch_or_double_charge(
    enhanced, drop_round_checkpoint, two_rounds
):
    repo, _, _, _ = enhanced
    first = run_state(enhanced, EdgePort(fail_analysis=True, two_rounds=two_rounds))
    assert first["status"] == "INTERRUPTED"
    expected = 2 if two_rounds else 1
    assert len([c for c in first["calls"] if c.get("role") == "Researcher"]) == expected
    previous_calls, previous_charge = first["model_calls"], first["charged_tokens"]
    if drop_round_checkpoint:
        # Process restart after the provider call saved, before the journal checkpoint saved.
        first.pop("active_manifest_ref")
        journal = first["active_research"]
        prior = [r for r in journal["results"] if two_rounds and r["round"] == 0]
        first["active_research"] = dict(
            journal,
            rounds=journal["rounds"][:1] if two_rounds else [],
            results=prior,
            tool_keys=[r["request_key"] for r in prior],
        )
        repo.save(first)
    fixed = EdgePort(two_rounds=two_rounds)
    result = run_state(enhanced, fixed, frozen=True)
    assert result["status"] == "COMPLETED", result.get("error")
    assert fixed.roles == ["Analyst", "Verifier"]
    assert result["model_calls"] == previous_calls + 2
    assert result["charged_tokens"] == previous_charge + 400
    assert len([c for c in result["calls"] if c.get("role") == "Researcher"]) == expected
    repo.verify(result)


def failed_valuation(monkeypatch):
    monkeypatch.setattr(tushare, "cached", lambda *_: None)
    monkeypatch.setattr(tushare, "load_token", lambda: "synthetic-edge-token")

    def unavailable(*args, **kwargs):
        raise requests.ConnectTimeout("synthetic upstream timeout")

    monkeypatch.setattr(requests, "request", unavailable)


def test_optional_valuation_transport_failure_has_real_failed_calls_and_can_finish(
    enhanced, monkeypatch
):
    repo, _, _, _ = enhanced
    failed_valuation(monkeypatch)
    result = run_state(enhanced, EdgePort(valuation=True))
    assert result["status"] == "COMPLETED", result.get("error")
    manifest = repo.value(result["active_manifest_ref"])
    value = manifest["results"][0]
    assert value["status"] == "UNAVAILABLE" and value["facts"] == []
    assert len(value["failed_call_indices"]) == 2
    for index in value["failed_call_indices"]:
        call = result["calls"][index]
        assert call["status"] == "FAILED" and call["error"] == "ConnectTimeout"
        definition = repo.value(call["request_ref"])
        assert definition["data"]["api_name"] == "daily_basic"
        assert "token" not in definition["data"]
    repo.verify(result)
    changed = copy.deepcopy(result)
    changed["calls"][value["failed_call_indices"][0]]["status"] = "COMPLETED"
    with pytest.raises(ValueError, match="真实调用"):
        repo.verify(changed)


@pytest.mark.parametrize("mismatch", [False, True])
def test_unrecorded_or_wrong_identity_valuation_cannot_be_downgraded(
    enhanced, monkeypatch, mismatch
):
    def invalid(*args):
        if mismatch:
            raise ValueError("估值原文证券、基准日或收盘价不一致")
        raise SourceUnavailable("预算已用尽，没有真实来源失败记录")

    monkeypatch.setattr("marketpulse.quant.knowledge.research_tools.capture_valuation", invalid)
    result = run_state(enhanced, EdgePort(valuation=True))
    assert result["status"] == "INTERRUPTED" and "active_manifest_ref" not in result
    assert "result_ref" not in result
    assert result["active_research"]["results"] == []
    assert "不一致" in result["error"] if mismatch else "真实来源失败记录" in result["error"]


def test_available_valuation_enters_bound_evidence_and_rejects_request_tampering(
    enhanced, monkeypatch
):
    repo, packet, _, _ = enhanced
    close = packet["stocks"][0]["daily"][-1]["close"]
    fields = tushare.FIELDS["daily_basic"].split(",")
    raw = json.dumps(
        dict(
            code=0,
            data=dict(
                fields=fields,
                items=[
                    [
                        "000333.SZ",
                        packet["market_date"],
                        close,
                        "15.25",
                        "2.50",
                        "1234567.89",
                        "1000000.25",
                        "17028.5226",
                    ]
                ],
            ),
        )
    ).encode()

    class Response:
        url, is_redirect = tushare.URL + "/", False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, *_):
            yield raw

    monkeypatch.setattr("requests.request", lambda *args, **kwargs: Response())
    monkeypatch.setattr(tushare, "cached", lambda *_: None)
    monkeypatch.setattr(tushare, "remember", lambda *_: None)
    monkeypatch.setattr(tushare, "load_token", lambda: "synthetic-edge-token")
    result = run_state(enhanced, EdgePort(valuation=True))
    assert result["status"] == "COMPLETED", result.get("error")
    context = repo.value(result["public_context_ref"])
    facts = [f for f in context["evidence"] if f["evidence_id"].startswith("R-")]
    assert len(facts) == 4
    assert next(f for f in facts if f["metric_key"] == "total_mv")["unit"] == "万元"
    manifest = repo.value(result["active_manifest_ref"])
    source = manifest["results"][0]["source"]
    assert source in result["data"]["sources"]
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from marketpulse.quant.knowledge.research_api import router

    app = FastAPI()
    app.state.research_store = repo.base
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get(
            "/api/quant/investment-research/runs/"
            + result["run_id"]
            + "/sources/"
            + source["sha256"]
        )
    assert response.status_code == 200 and response.content == raw
    assert source["blob_ref"] in result["source_refs"]
    assert any(c["status"] == "COMPLETED" and c.get("source") == source for c in result["calls"])
    definition = repo.value(source["request_ref"])
    definition["data"]["params"]["ts_code"] = "600036.SH"
    source["request_ref"] = repo.blob(definition)
    changed = dict(result, active_manifest_ref=repo.blob(manifest))
    with pytest.raises(ValueError, match="受控证券"):
        repo.verify(changed)


def test_second_round_cannot_repeat_same_valuation_tool_with_different_query(enhanced, monkeypatch):
    failed_valuation(monkeypatch)
    result = run_state(enhanced, EdgePort(two_rounds=True, valuation=True))
    assert result["status"] == "INTERRUPTED" and "跨轮重复" in result["error"]
    assert len(result["active_research"]["results"]) == 1
    # The first upstream read had two bounded failures; no second read was attempted.
    assert len([c for c in result["calls"] if c.get("status") == "FAILED"]) == 2


@pytest.mark.parametrize("mutation", ["request", "call", "config", "manifest", "time"])
def test_reasoning_and_manifest_changes_cannot_pass_review(enhanced, mutation):
    repo, _, _, _ = enhanced
    result = run_state(enhanced, EdgePort())
    assert result["status"] == "COMPLETED", result.get("error")
    changed = copy.deepcopy(result)
    manifest = repo.value(result["active_manifest_ref"])
    checkpoint = manifest["rounds"][0]
    index = checkpoint["call_index"]
    if mutation == "request":
        request = repo.value(checkpoint["request_ref"])
        request["thinking_enabled"] = False
        ref = repo.blob(request)
        checkpoint["request_ref"] = ref
        changed["calls"][index]["request_ref"] = ref
    elif mutation == "call":
        changed["calls"][index]["reasoning_tokens"] = 0
    elif mutation == "config":
        changed["config"]["reasoning_profile"]["roles"]["Researcher"]["reasoning_effort"] = "high"
    elif mutation == "manifest":
        manifest["final_plan"]["selections"][0]["hypothesis"] = "篡改后未经模型生成的研究假设。"
    else:
        changed["calls"][index]["finished_at"] = "2099-01-01T00:00:00+00:00"
    changed["active_manifest_ref"] = repo.blob(manifest)
    with pytest.raises(ValueError):
        repo.verify(changed)


def test_manifest_round_input_count_cannot_be_rewritten(enhanced):
    repo, _, _, _ = enhanced
    result = run_state(enhanced, EdgePort(two_rounds=True))
    assert result["status"] == "COMPLETED", result.get("error")
    manifest = repo.value(result["active_manifest_ref"])
    manifest["rounds"][1]["results_before"] = 999
    changed = dict(result, active_manifest_ref=repo.blob(manifest))
    with pytest.raises(ValueError):
        repo.verify(changed)


def test_disabled_flag_retains_original_serialized_request_and_original_workflow(runtime):
    repo, packet, settings = runtime
    old = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    explicit = InvestmentRequest(**old.model_dump(mode="json"), enhanced_research=False)
    assert old.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "enhanced_research" not in json.loads(explicit.model_dump_json())
    assert "enhanced_research" not in explicit.calculation_request()
    state, _ = repo.create(explicit.model_dump(mode="json"))
    port = SyntheticPort()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.roles == ["Planner", "Analyst", "Verifier", "Writer"]
    assert "active_manifest_ref" not in result
    assert "reasoning_profile" not in result["config"]
