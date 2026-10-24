import asyncio
import json
from threading import Event
from uuid import uuid4

import pytest

from marketpulse.quant.knowledge import numeric_grounding as numbers
from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge.research_context import analyst_context
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_research import SyntheticPort
from .test_research import runtime as research_fixture


@pytest.fixture
def research_runtime(rules, monkeypatch):
    return research_fixture.__wrapped__(rules, monkeypatch)


def context():
    sid = "002415.SZ"
    return dict(
        numeric_grounding=numbers.VERSION,
        stocks=[
            dict(
                security_id=sid,
                routes=[
                    dict(
                        conditions=[dict(label="股价站上20日和60日均线")],
                        invalidation="跌破20日低点时重新评估；趋势失效不能改成长线补仓理由。",
                    )
                ],
            )
        ],
        methods=[dict(rule_id="R1")],
        evidence=[
            dict(
                evidence_id="close",
                security_id=sid,
                label="收盘价",
                metric_key="close",
                value="32.42",
                unit="CNY/share",
                window=dict(end="20261022"),
            ),
            dict(
                evidence_id="relative",
                security_id=sid,
                label="相对沪深三百收益率差",
                metric_key="relative_return_20",
                value="0.0933",
                unit="ratio",
                window=dict(return_sessions=20, benchmark_id="000300.SH"),
            ),
            dict(
                evidence_id="turnover",
                security_id=sid,
                label="近五日与二十日平均成交额之比",
                metric_key="turnover_ratio_5_20",
                value="1.15",
                unit="ratio",
                window={},
            ),
            dict(
                evidence_id="ma",
                security_id=sid,
                label="六十日均线",
                metric_key="ma_60",
                value="34.38",
                unit="CNY/share",
                window={},
            ),
            dict(
                evidence_id="ma20",
                security_id=sid,
                label="二十日均线",
                metric_key="ma_20",
                value="32.94",
                unit="CNY/share",
                window={},
            ),
            dict(
                evidence_id="other-stock",
                security_id="600036.SH",
                label="收盘价",
                metric_key="close",
                value="40",
                unit="CNY/share",
                window={},
            ),
        ],
    )


def analysis(text="收盘价32.42元，仍需核对趋势条件。", ids=("close",)):
    return agents.Analysis(
        opinions=[
            dict(
                security_id="002415.SZ",
                direction="FAVORABLE",
                summary="倾向看多，但条件仍待确认。",
                claims=[
                    dict(claim_id="C1", text=text, evidence_ids=ids),
                    dict(claim_id="C2", text="相对表现仍有观察价值。", evidence_ids=["relative"]),
                ],
                opposing=[dict(claim_id="C3", text="市场方向仍有不确定性。", evidence_ids=["ma"])],
                direction_claim_ids=["C1"],
                method_rule_ids=["R1"],
                invalidation=["跌破20日低点时重新评估，不能改为长线补仓理由。"],
                next_checks=["继续核查趋势条件。"],
            )
        ]
    )


@pytest.mark.parametrize(
    "text,ids",
    [
        ("收盘价32.42元，仍需核对趋势条件。", ["close"]),
        ("相对沪深三百收益率差9.33%，不能保证未来延续。", ["relative"]),
        ("近20日相对沪深300表现偏强，近5日成交额强度高于20日均值。", ["relative", "turnover"]),
        ("股价低于20日与60日均线且未突破前高，趋势入场条件尚未满足。", ["ma20", "ma"]),
    ],
)
def test_cited_fact_value_unit_and_window_are_accepted(text, ids):
    agents.guard_analysis(analysis(text, ids), context())


@pytest.mark.parametrize(
    "text,ids",
    [
        ("收盘价33.42元，仍需核对条件。", ["close"]),
        ("收盘价32.42%，仍需核对条件。", ["close"]),
        ("收盘价32.42万元，仍需核对条件。", ["close"]),
        ("收盘价32.42元/吨，仍需核对条件。", ["close"]),
        ("收盘价-32.42元，仍需核对条件。", ["close"]),
        ("RSI32.42，仍需核对条件。", ["close"]),
        ("收盘价32.42元，仍需核对条件。", ["ma"]),
        ("相对沪深三百收益率差9.34%，仍需核对。", ["relative"]),
        ("近25日相对沪深300表现偏强。", ["relative"]),
        ("近5日相对沪深300表现偏强。", ["relative"]),
        ("目标价设为收盘价32.42元。", ["close"]),
        ("收盘价32.42元，上涨概率很高。", ["close"]),
        ("收盘价32.42元，可以立即买入。", ["close"]),
    ],
)
def test_unsupported_or_repurposed_numbers_remain_blocked(text, ids):
    with pytest.raises(ValueError):
        agents.guard_analysis(analysis(text, ids), context())


def test_cross_stock_reference_is_rejected_before_number_matching():
    with pytest.raises(ValueError, match="跨证券"):
        agents.guard_analysis(analysis("收盘价40元，仍需核对条件。", ["other-stock"]), context())


def test_legacy_policy_and_prompt_bytes_are_unchanged():
    legacy = context()
    legacy.pop("numeric_grounding")
    with pytest.raises(ValueError, match="正文含未绑定数字"):
        agents.guard_analysis(analysis(), legacy)
    config = dict(prompt_version=agents.CHECKS_PROMPT_VERSION)
    before = agents.role_prompts(config)
    revised = agents.role_prompts(dict(config, numeric_grounding=numbers.VERSION))
    assert before == agents.role_prompts(config)
    assert before["Planner"] == revised["Planner"]
    assert "numeric_quotes" in revised["Analyst"]
    assert "正文只写定性判断，不出现阿拉伯数字" not in revised["Analyst"]
    assert (
        "简洁定性中文，不写数字"
        not in agents.role_prompts(
            dict(
                config,
                execution_contract="investment-structured-v2",
                numeric_grounding=numbers.VERSION,
            )
        )["Analyst"]
    )


def test_actual_role_input_contains_server_generated_quotes_before_window_packing():
    original = context()
    packed = analyst_context(original, agents.TECHNICAL_PROMPT_VERSION)
    assert "收盘价32.42元" in packed["numeric_quotes"]["close"]
    assert "沪深300" in packed["numeric_quotes"]["relative"]
    assert "numeric_quotes" not in original


def test_summary_cannot_borrow_an_uncited_value_and_route_window_cannot_change():
    value = analysis().model_dump(mode="json")
    value["opinions"][0]["summary"] = "倾向看多，未引用的收盘价40元不能借用。"
    with pytest.raises(ValueError):
        agents.guard_analysis(agents.Analysis.model_validate(value), context())
    value = analysis().model_dump(mode="json")
    value["opinions"][0]["invalidation"] = ["跌破25日低点时重新评估。"]
    with pytest.raises(ValueError):
        agents.guard_analysis(agents.Analysis.model_validate(value), context())


def test_unknown_grounding_version_is_rejected():
    changed = dict(context(), numeric_grounding="unknown")
    with pytest.raises(ValueError, match="未知数字引用校验版本"):
        agents.guard_analysis(analysis(), changed)


def test_funding_windows_and_negative_amount_keep_their_source_meaning():
    fact = dict(
        evidence_id="X-002415.SZ-daily-5",
        label="5个交易日大单净额",
        value="-20.00",
        unit="CNY",
        window="20261016—20261022",
    )
    quotes = numbers.quotes(fact)
    agents.qualitative("近5日资金代理为流出；5个交易日大单净额-20元。", numeric_quotes=quotes)
    with pytest.raises(ValueError):
        agents.qualitative("5个交易日大单净额20元，不能反转正负。", numeric_quotes=quotes)


def test_new_task_uses_grounded_numbers_and_replays_without_modifying_history(research_runtime):
    class NumericPort(SyntheticPort):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is agents.Analysis:
                context = json.loads(request.messages[-1].content)
                value = response.output.model_dump(mode="json")
                for opinion in value["opinions"]:
                    fact = next(
                        f
                        for f in context["evidence"]
                        if f["security_id"] == opinion["security_id"]
                        and f.get("metric_key") == "close"
                    )
                    quote = next(
                        q
                        for q in context["numeric_quotes"][fact["evidence_id"]]
                        if q.startswith("收盘价")
                    )
                    opinion["claims"][0].update(
                        text=quote + "，仅为合成流程测试。", evidence_ids=[fact["evidence_id"]]
                    )
                response.output = agents.Analysis.model_validate(value)
            return response

    repo, packet, settings = research_runtime
    request = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    state, _ = repo.create(request.model_dump(mode="json"))
    port = NumericPort()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["config"]["numeric_grounding"] == numbers.VERSION
    assert port.roles == ["Planner", "Analyst", "Verifier", "Writer"]
    before = repo.blob(result)
    assert repo.get(state["run_id"]) == result
    assert repo.blob(repo.get(state["run_id"])) == before
    changed = dict(
        result, config={k: v for k, v in result["config"].items() if k != "numeric_grounding"}
    )
    with pytest.raises(ValueError, match="数字引用校验版本"):
        repo.verify(changed)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1E+999999"])
def test_invalid_values_do_not_create_numeric_quotations(value):
    fact = dict(context()["evidence"][0], value=value)
    assert not any("收盘价" in q for q in numbers.quotes(fact))


def test_percentage_display_has_deterministic_decimal_precision():
    fact = dict(context()["evidence"][1], value="0.093349999999999999999999999999999")
    assert "相对沪深三百收益率差9.33%" in numbers.quotes(fact)
