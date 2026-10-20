"""Synthetic active-research boundary tests; no user archives or network."""

import copy
from datetime import UTC, datetime

import pytest

from marketpulse.quant.knowledge import active_research as active


def basis():
    rules = []
    for key in ("KR-AVOID-RUIN", "KR-PROFIT-CASH-BRIDGE", "KR-TREND-MARKET"):
        rules.append(
            dict(
                rule_id=key,
                version="a" * 64,
                rule=dict(
                    title=key,
                    system_rule="核对证据，缺项时保留判断边界。",
                    horizons=["MEDIUM"],
                    required_inputs=["财务附注"],
                    conditions=["主体同窗可比。"],
                    counterexamples=["季节性可能导致差异。"],
                    forbidden_inferences=["不能保证收益。"],
                    keywords=["现金流"],
                    execution_status="REFERENCE_ONLY",
                    validation_status="UNVALIDATED",
                ),
            )
        )
    context = dict(
        horizon="MEDIUM",
        market_date="20261016",
        cutoff="2026-10-18T00:00:00+00:00",
        stocks=[dict(security_id="000333.SZ", name="合成测试", industry="家电")],
        methods=[],
        evidence=[
            dict(
                evidence_id="F-000333SZ-close",
                security_id="000333.SZ",
                value="50",
                unit="CNY/share",
                metric_key="close",
                window={},
            )
        ],
        limitations=[],
    )
    return context, dict(rules=rules)


def proposal():
    return active.ResearchPlan(
        selections=[
            dict(
                security_id="000333.SZ",
                method_rule_ids=["KR-PROFIT-CASH-BRIDGE"],
                hypothesis="核对增长是否伴随真实现金回收。",
                countercheck="若应收占款能够解释差异则修正风险判断。",
            )
        ],
        tools=[
            dict(tool="VALUATION_SNAPSHOT", security_id="000333.SZ", query="核对估值与价格预期。")
        ],
    )


def test_plan_refuses_unknown_stock_method_and_duplicate_tool():
    context, knowledge = basis()
    active.guard(proposal(), context, knowledge)
    for mutation in ("stock", "method", "duplicate"):
        value = proposal().model_dump(mode="json")
        if mutation == "stock":
            value["tools"][0]["security_id"] = "600036.SH"
        elif mutation == "method":
            value["selections"][0]["method_rule_ids"] = ["KR-UNKNOWN"]
        else:
            value["tools"] *= 2
        with pytest.raises(ValueError):
            active.guard(active.ResearchPlan.model_validate(value), context, knowledge)


def test_selection_preserves_conditions_and_mandatory_risk_without_mutating_base():
    context, knowledge = basis()
    original = copy.deepcopy(context)
    result = active.apply(context, knowledge, proposal(), [])
    assert context == original
    ids = {m["rule_id"] for m in result["methods"]}
    assert "KR-AVOID-RUIN" in ids and "KR-PROFIT-CASH-BRIDGE" in ids
    method = next(m for m in result["methods"] if m["rule_id"] == "KR-PROFIT-CASH-BRIDGE")
    assert method["required_inputs"] and method["conditions"] and method["forbidden_inferences"]
    assert result["research_selection"][0]["method_rule_ids"] == ["KR-PROFIT-CASH-BRIDGE"]


def test_plan_horizon_does_not_promote_long_methods_to_tomorrow_signals():
    context, knowledge = basis()
    context["horizon"] = "NEXT_SESSION"
    prompt = active.planning_context(context, knowledge, [])
    assert prompt["method_scope"] == "RISK_AND_INTERPRETATION_ONLY"
    assert prompt["tools"] == ["VALUATION_SNAPSHOT", "EVIDENCE_SEARCH"]
    assert "profile" not in prompt and "holdings" not in prompt
    active.guard(proposal(), context, knowledge)


def test_new_planning_contract_filters_horizon_without_changing_sealed_old_contract():
    context, knowledge = basis()
    knowledge["rules"][0]["rule"]["horizons"] = ["SWING"]
    old = active.planning_context(context, knowledge, [], contract_version=2)
    new = active.planning_context(context, knowledge, [], contract_version=3)
    assert len(old["methods"]) == 3 and len(new["methods"]) == 2
    assert all(context["horizon"] in m["horizons"] for m in new["methods"])
    assert old["selection_contract"] == new["selection_contract"]
    assert len(knowledge["rules"]) == 3


def test_literal_guard_feedback_names_the_failed_word_without_weakening_checks():
    from marketpulse.quant.knowledge import research_agents as agents

    with pytest.raises(ValueError, match="概率；否定句"):
        agents.qualitative("历史相对表现不是预测概率。")
    config = dict(prompt_version=agents.CHECKS_PROMPT_VERSION, agentic_research_v1=True)
    old = agents.role_prompts(config)
    new = agents.role_prompts(dict(config, literal_text_contract_v1=True))
    assert "正文受字面门禁检查" not in old["Analyst"]
    assert "正文受字面门禁检查" in new["Analyst"]
    assert old == agents.role_prompts(config)


def test_evidence_search_is_stock_bound_and_manifest_replay_is_exact():
    context, knowledge = basis()
    context["evidence"] += [
        dict(
            evidence_id="F-600036SH-close",
            security_id="600036.SH",
            text="价格",
            value="40",
            unit="CNY/share",
            metric_key="close",
            window={},
        )
    ]
    request = active.ToolRequest(tool="EVIDENCE_SEARCH", security_id="000333.SZ", query="价格")
    result = active.search_evidence(context, request)
    assert all(e["security_id"] == "000333.SZ" for e in result["facts"])
    assert active.manifest_hash(proposal(), [result]) != active.manifest_hash(proposal(), [])
    assert datetime.now(UTC).tzinfo is not None


def test_model_view_retains_facts_once_and_keeps_raw_provenance_in_archive():
    context, knowledge = basis()
    tool = active.ToolRequest(tool="EVIDENCE_SEARCH", security_id="000333.SZ", query="价格")
    result = active.apply(context, knowledge, proposal(), [active.search_evidence(context, tool)])
    original = copy.deepcopy(result)
    view = active.model_view(result)
    assert result == original
    assert view["evidence"] == result["evidence"]
    assert view["methods"] == result["methods"]
    assert "facts" not in view["tool_results"][0]
    assert view["tool_results"][0]["status"] == result["tool_results"][0]["status"]
