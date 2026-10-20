"""Synthetic-only integration of public active research and private local advice."""

# ruff: noqa: F811
# Imported pytest fixtures intentionally share names with test parameters.

import asyncio
import copy
import json
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from marketpulse.investigation.recording.errors import InvalidProviderResponseError
from marketpulse.quant.knowledge import active_research, advisor_quotes, research_agents
from marketpulse.quant.knowledge.advisor_service import run as holding_run
from marketpulse.quant.knowledge.advisor_store import AdvisorStore
from marketpulse.quant.knowledge.portfolio_service import run as account_run
from marketpulse.quant.knowledge.portfolio_store import PortfolioStore
from marketpulse.quant.knowledge.research_store import InvestmentStore
from marketpulse.quant.live.store import ResearchRunRow

from .test_advisor import advisor  # noqa: F401
from .test_advisor import request as holding_request
from .test_portfolio import PortfolioPort, portfolio  # noqa: F401
from .test_portfolio import request as account_request
from .test_research import knowledge, runtime  # noqa: F401

METHOD = "KR-SYNTHETIC-ACTIVE-ONLY"
BOUNDARIES = dict(
    required_inputs=["合成测试：本次行情和反面证据。"],
    conditions=["合成测试：必须核对同一截止时点。"],
    counterexamples=["合成测试：反弹不能证明趋势反转。"],
    forbidden_inferences=["合成测试：不能以方法代替股票事实。"],
)


@pytest.fixture
def enhanced_knowledge(runtime, monkeypatch):
    snapshot = knowledge()
    for item in snapshot["rules"]:
        item["rule"].update(
            system_rule="合成测试：核对实际证据与方法边界。",
            horizons=["SWING", "MEDIUM"],
            execution_status="REFERENCE_ONLY",
            validation_status="UNVALIDATED",
            **copy.deepcopy(BOUNDARIES),
        )
    custom = copy.deepcopy(snapshot["rules"][0])
    custom["rule_id"] = METHOD
    snapshot["rules"].append(custom)
    fragment = copy.deepcopy(snapshot["fragments"][0])
    fragment["rule_id"] = METHOD
    snapshot["fragments"].append(fragment)
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.research_service.knowledge_snapshot",
        lambda *args, **kwargs: copy.deepcopy(snapshot),
    )


class EnhancedPort(PortfolioPort):
    """No network, investment judgement or real-money account input in this fixture."""

    def __init__(self, *, fail_analysis=False):
        super().__init__()
        self.fail_analysis = fail_analysis

    async def generate(self, request):
        context = json.loads(request.messages[-1].content.split("\n上次检查失败：")[0])
        if request.response_model is active_research.ResearchPlan:
            self.roles.append("Researcher")
            self.contexts.append(context)
            assert request.thinking_enabled and request.reasoning_effort == "low"
            result = active_research.ResearchPlan(
                selections=[
                    dict(
                        security_id=s["security_id"],
                        method_rule_ids=[METHOD],
                        hypothesis="合成测试：检查价格变化能否支持方向。",
                        countercheck="合成测试：核查相反证据和失效条件。",
                    )
                    for s in context["stocks"]
                ],
                tools=[
                    dict(tool="EVIDENCE_SEARCH", security_id=s["security_id"], query="收盘价")
                    for s in context["stocks"][:2]
                ],
            )
            return SimpleNamespace(
                output=result,
                usage=SimpleNamespace(input_tokens=100, output_tokens=100),
                provider="SYNTHETIC",
                model="SYNTHETIC",
                response_id="synthetic-enhanced-advisor",
            )
        if self.fail_analysis and request.response_model is research_agents.Analysis:
            self.roles.append("Analyst")
            self.contexts.append(context)
            raise InvalidProviderResponseError(
                "synthetic interruption during analysis", input_tokens=7, output_tokens=11
            )
        result = await super().generate(request)
        if request.response_model is research_agents.Analysis:
            value = result.output.model_dump(mode="json")
            for opinion in value["opinions"]:
                opinion["method_rule_ids"] = [METHOD]
            result.output = research_agents.Analysis.model_validate(value)
        return result


def execute(fixture, kind, request, port):
    repo, settings, options = fixture
    state, _ = repo.create(request.model_dump(mode="json"))
    return asyncio.run(
        (holding_run if kind == "holding" else account_run)(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port),
            quote_collector=lambda _, report: advisor_quotes.fallback(report),
        )
    )


def requests_for(kind, **changes):
    return (holding_request if kind == "holding" else account_request)(**changes)


def child_ids(state, kind):
    return (
        [state["research_run_id"]]
        if kind == "holding"
        else [g["run_id"] for g in state["research_groups"]]
    )


def assert_private_boundaries(repo, state, port, private_values):
    serialized = json.dumps(port.contexts, ensure_ascii=False)
    for value in private_values:
        assert value not in serialized
    for child in child_ids(
        state, "holding" if state["run_id"].startswith("RUN-HA-") else "account"
    ):
        public = InvestmentStore(repo.base).get(child)
        assert public["request"]["profile"] is None
        assert public["request"]["enhanced_research"] is True
        assert public["active_manifest_ref"]
        assert public["charged_tokens"] <= 60000 and public["model_calls"] <= 24
        for call in public["calls"]:
            if call.get("kind") == "model":
                payload = json.dumps(repo.value(call["request_ref"]), ensure_ascii=False)
                for value in private_values:
                    assert value not in payload
    for role in ("Analyst", "Verifier"):
        for actual_role, context in zip(port.roles, port.contexts, strict=True):
            if actual_role != role:
                continue
            method = next(m for m in context["methods"] if m["rule_id"] == METHOD)
            for key, value in BOUNDARIES.items():
                assert method[key] == value


@pytest.mark.parametrize("kind", ["holding", "account"])
def test_legacy_false_mode_serializes_exactly_like_omitted_mode(kind):
    request = requests_for(kind)
    original = request.model_dump(mode="json")
    assert "enhanced_research" not in original
    explicit = type(request).model_validate(dict(original, enhanced_research=False))
    assert explicit.model_dump(mode="json") == original
    assert explicit == request


def test_holding_enhancement_uses_knowledge_and_keeps_private_inputs_local(
    advisor, enhanced_knowledge
):
    request = holding_request(
        enhanced_research=True,
        position=dict(cost_price="83.719", total_assets="8765432.19", shares=1379),
    ).model_copy(update=dict(question="PRIVATE-HOLDING-QUESTION-6917 到多少可以卖？"))
    port = EnhancedPort()
    first = execute(advisor, "holding", request, port)
    assert first["status"] == "COMPLETED", first.get("error")
    assert port.roles == ["Researcher", "Planner", "Analyst", "Verifier"]
    assert_private_boundaries(
        advisor[0], first, port, ["83.719", "8765432.19", "1379", "PRIVATE-HOLDING-QUESTION-6917"]
    )
    followup = EnhancedPort()
    second = execute(
        advisor,
        "holding",
        holding_request(
            enhanced_research=True, previous_run_id=first["run_id"], scenario_price="85.713"
        ),
        followup,
    )
    assert second["status"] == "COMPLETED", second.get("error")
    assert followup.roles == []
    assert second["research_run_id"] == first["research_run_id"]
    assert second["data"]["research_sha256"] == first["data"]["research_sha256"]
    assert AdvisorStore(advisor[0].base).get(second["run_id"])["result_ref"] == second["result_ref"]
    assert len(AdvisorStore(advisor[0].base).conversation(second["run_id"])) == 2


@pytest.mark.parametrize("count", [2, 4])
def test_enhanced_account_covers_all_stocks_and_reuses_public_studies_on_followup(
    portfolio, runtime, enhanced_knowledge, count
):
    repo, _, _ = portfolio
    _, packet, _ = runtime
    symbols = ["000333", "000651", "000002", "000001"][:count]
    for symbol in symbols[2:]:
        stock = copy.deepcopy(packet["stocks"][0])
        stock.update(ts_code=symbol + ".SZ", name="SYNTHETIC-" + symbol)
        for row in stock["daily"] + stock["adjustments"]:
            row["ts_code"] = stock["ts_code"]
        packet["stocks"].append(stock)
    request = account_request(
        enhanced_research=True,
        available_cash="987654.31",
        question="PRIVATE-ACCOUNT-QUESTION-7219 应如何调仓？",
        assets=[
            dict(entity=s, cost_price="83.719", shares=1379, sellable_shares=1300) for s in symbols
        ],
    )
    port = EnhancedPort()
    first = execute(portfolio, "account", request, port)
    assert first["status"] == "COMPLETED", first.get("error")
    assert port.roles == ["Researcher", "Planner", "Analyst", "Verifier"]
    assert len(first["research_groups"]) == 1
    child = InvestmentStore(repo.base).get(first["research_groups"][0]["run_id"])
    expected = {s + ".SZ" for s in symbols}
    assert {o["security_id"] for o in child["data"]["research_opinions"]} == expected
    manifest = repo.value(child["active_manifest_ref"])
    assert {s["security_id"] for s in manifest["final_plan"]["selections"]} == expected
    assert first["budget"] == dict(max_model_calls=24, max_tokens=60000, max_seconds=900)
    assert_private_boundaries(
        repo, first, port, ["83.719", "987654.31", "1379", "PRIVATE-ACCOUNT-QUESTION-7219"]
    )
    followup = EnhancedPort()
    second_request = account_request(
        enhanced_research=True,
        previous_run_id=first["run_id"],
        assets=request.model_dump(mode="json")["assets"],
    )
    second = execute(portfolio, "account", second_request, followup)
    assert second["status"] == "COMPLETED", second.get("error")
    assert followup.roles == [] and second["research_groups"] == first["research_groups"]
    assert PortfolioStore(repo.base).get(second["run_id"])["result_ref"] == second["result_ref"]
    assert len(PortfolioStore(repo.base).conversation(second["run_id"])) == 2


@pytest.mark.parametrize("kind", ["holding", "account"])
def test_legacy_to_enhanced_requires_refresh_and_keeps_old_history(
    request, enhanced_knowledge, kind
):
    fixture = request.getfixturevalue("advisor" if kind == "holding" else "portfolio")
    first = execute(fixture, kind, requests_for(kind), PortfolioPort())
    assert first["status"] == "COMPLETED", first.get("error")
    old_ref = first["result_ref"]
    next_request = requests_for(kind, previous_run_id=first["run_id"], enhanced_research=True)
    with pytest.raises(ValueError, match="更新公开资料"):
        fixture[0].create(next_request.model_dump(mode="json"))
    port = EnhancedPort()
    second = execute(
        fixture, kind, next_request.model_copy(update=dict(refresh_research=True)), port
    )
    assert second["status"] == "COMPLETED", second.get("error")
    assert child_ids(first, kind) != child_ids(second, kind)
    fresh = (AdvisorStore if kind == "holding" else PortfolioStore)(fixture[0].base)
    turns = fresh.conversation(second["run_id"])
    assert len(turns) == 2 and turns[0]["result_ref"] == old_ref
    assert "enhanced_research" not in turns[0]["request"]
    assert port.roles == ["Researcher", "Planner", "Analyst", "Verifier"]


@pytest.mark.parametrize("kind", ["holding", "account"])
def test_reader_rejects_parent_and_child_enhancement_mode_mismatch(
    request, enhanced_knowledge, kind
):
    fixture = request.getfixturevalue("advisor" if kind == "holding" else "portfolio")
    first = execute(fixture, kind, requests_for(kind, enhanced_research=True), EnhancedPort())
    assert first["status"] == "COMPLETED", first.get("error")
    repo = fixture[0]
    child_id = child_ids(first, kind)[0]
    with repo.base.sessions.begin() as session:
        row = session.scalar(select(ResearchRunRow).where(ResearchRunRow.run_id == child_id))
        changed = copy.deepcopy(row.payload)
        changed["request"].pop("enhanced_research")
        row.payload = changed
    # Avoid a previous HTTP request's read cache when checking persistent bindings.
    fresh = (AdvisorStore if kind == "holding" else PortfolioStore)(repo.base)
    with pytest.raises(ValueError):
        fresh.get(first["run_id"])


@pytest.mark.parametrize("kind", ["holding", "account"])
def test_enhanced_resume_reuses_research_manifest_and_retains_spent_budget(
    request, enhanced_knowledge, kind
):
    fixture = request.getfixturevalue("advisor" if kind == "holding" else "portfolio")
    first_request = requests_for(kind, enhanced_research=True)
    first = execute(fixture, kind, first_request, EnhancedPort(fail_analysis=True))
    assert first["status"] == "INTERRUPTED"
    repo = fixture[0]
    child_id = child_ids(first, kind)[0]
    before = InvestmentStore(repo.base).get(child_id, with_data=False)
    assert before["stage"] == "ANALYST" and before["active_manifest_ref"]
    manifest = before["active_manifest_ref"]
    port = EnhancedPort()
    final = execute(fixture, kind, first_request, port)
    assert final["status"] == "COMPLETED", final.get("error")
    after = InvestmentStore(repo.base).get(child_id)
    assert child_ids(first, kind) == child_ids(final, kind)
    assert after["active_manifest_ref"] == manifest
    assert port.roles == ["Analyst", "Verifier"]
    assert after["model_calls"] == before["model_calls"] + 2
    assert after["charged_tokens"] == before["charged_tokens"] + 400
    assert after["config"]["budget"] == before["config"]["budget"]


@pytest.mark.parametrize("kind", ["holding", "account"])
def test_researcher_guard_failures_receive_bounded_automatic_continuation(
    request, enhanced_knowledge, kind
):
    class CorrectingResearcher(EnhancedPort):
        async def generate(self, model_request):
            result = await super().generate(model_request)
            if (
                model_request.response_model is active_research.ResearchPlan
                and self.roles.count("Researcher") <= 2
            ):
                value = result.output.model_dump(mode="json")
                value["selections"][0]["method_rule_ids"] = ["KR-SYNTHETIC-NOT-PUBLISHED"]
                result.output = active_research.ResearchPlan.model_validate(value)
            return result

    fixture = request.getfixturevalue("advisor" if kind == "holding" else "portfolio")
    port = CorrectingResearcher()
    state = execute(fixture, kind, requests_for(kind, enhanced_research=True), port)
    assert state["status"] == "COMPLETED", state.get("error")
    child_id = child_ids(state, kind)[0]
    child = InvestmentStore(fixture[0].base).get(child_id)
    assert port.roles == ["Researcher"] * 3 + ["Planner", "Analyst", "Verifier"]
    assert child["model_calls"] == 6 and child["charged_tokens"] == 1200
    continuations = state["automatic_continuations"]
    assert (continuations if kind == "holding" else continuations[child_id]) == 1
