# ruff: noqa: F811
import asyncio
from decimal import Decimal
from threading import Event

import pytest

from marketpulse.quant.knowledge.portfolio_service import run
from marketpulse.quant.knowledge.research_store import InvestmentStore

from .test_portfolio import PortfolioPort, completed, compute, portfolio, request  # noqa: F401
from .test_research import runtime  # noqa: F401


def test_compact_public_context_preserves_facts_and_uses_fixed_execution_rules():
    from marketpulse.quant.knowledge import research_agents as agents
    from marketpulse.quant.knowledge.research_context import (
        analysis_plan,
        analyst_context,
        review_context,
    )

    context = dict(
        stocks=[dict(security_id="S", routes=[dict(route_id="TREND_SWING")])],
        evidence=[
            dict(
                evidence_id="T-S",
                security_id="S",
                label="动量",
                metric_key="macd",
                value="0.25",
                unit="ratio",
                window=dict(start="20260101", end="20260130"),
            )
        ],
        methods=[
            dict(
                rule_id="KR-TREND-MARKET",
                title="原文题名",
                quote="已存原文",
                counterexamples=["反例"],
                system_rule="固定系统规则",
            )
        ],
        technical_guidance="技术辅助说明",
        cutoff="20260130",
        horizon="SWING",
        horizon_sessions=20,
        market_date="20260130",
        protocol="public-investment-context-v3",
        limitations=["局限保留"],
    )
    plan = dict(
        selections=[
            dict(security_id="S", route_id="TREND_SWING", rationale="封存理由", focus=["封存重点"])
        ]
    )
    normal = analyst_context(context, agents.TECHNICAL_PROMPT_VERSION, plan, scoped=True)
    compact = analyst_context(
        context, agents.TECHNICAL_PROMPT_VERSION, plan, scoped=True, compact=True
    )
    assert compact["evidence"] == normal["evidence"]
    assert compact["windows"] == normal["windows"]
    assert compact["stocks"] == normal["stocks"]
    assert compact["limitations"] == normal["limitations"]
    assert compact["methods"] == [dict(rule_id="KR-TREND-MARKET", system_rule="固定系统规则")]
    assert analysis_plan(plan, True)["selections"][0] == dict(
        security_id="S", route_id="TREND_SWING"
    )
    analysis = dict(
        opinions=[
            dict(
                claims=[dict(evidence_ids=["T-S"])],
                opposing=[],
                method_rule_ids=["KR-TREND-MARKET"],
            )
        ]
    )
    before = review_context(context, plan, analysis, 0, compact=True)
    after = review_context(context, plan, analysis, 0, compact=True, portfolio_compact=True)
    assert all(
        after[k] == before[k] for k in ("evidence", "windows", "stocks", "proposal", "limitations")
    )
    config = dict(prompt_version=agents.TECHNICAL_PROMPT_VERSION)
    assert agents.role_prompts(config) == agents.prompts(config["prompt_version"])
    assert "T技术证据" in agents.role_prompts(dict(config, portfolio_compact_v1=True))["Analyst"]


def test_remaining_budget_can_reduce_output_allowance_without_dropping_context(runtime):
    from types import SimpleNamespace
    from uuid import uuid4

    from marketpulse.quant.contracts import canonical
    from marketpulse.quant.decision.next_session.agents import DailyRoles
    from marketpulse.quant.knowledge import research_agents as agents
    from marketpulse.quant.knowledge.research_contracts import InvestmentRequest

    repo, _, settings = runtime
    state, _ = repo.create(
        InvestmentRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    )
    context = {
        "approved_hash": "a" * 64,
        "items": [
            {
                "security_id": "000333.SZ",
                "direction": "FAVORABLE",
                "summary": "倾向看多，合成预算测试。",
            }
        ],
    }
    prompt = agents.PROMPTS["Writer"]
    input_bound = len(
        (canonical(context) + prompt + canonical(agents.Writing.model_json_schema())).encode("utf8")
    )
    state["charged_tokens"] = 60000 - input_bound - 700
    repo.save(state)

    class Port:
        async def generate(self, req):
            assert req.max_output_tokens == 700
            assert req.messages[-1].content == canonical(context)
            return SimpleNamespace(
                output=agents.Writing.model_validate(context),
                usage=SimpleNamespace(input_tokens=100, output_tokens=100),
                provider="SYNTHETIC",
                model="SYNTHETIC",
                response_id="SYNTHETIC",
            )

    async def call():
        async with DailyRoles(
            repo,
            state,
            settings,
            lambda: repo.save(state),
            lambda: None,
            port=Port(),
            prompts=agents.PROMPTS,
            output_tokens=1200,
            min_output_tokens=500,
            reserve_limit=20000,
        ) as roles:
            return await roles.call("Writer", agents.Writing, context)

    asyncio.run(call())
    assert state["charged_tokens"] <= 60000
    assert state["calls"][-1]["reserved_tokens"] == input_bound + 700


def test_rule_extraction_reuses_only_identical_bytes_and_keeps_source_reads(monkeypatch):
    from marketpulse.quant.knowledge import advisor_engine

    calls, reads = [], []

    def extract(raw):
        calls.append(raw)
        return raw.decode()

    monkeypatch.setattr(advisor_engine, "rule_text", extract)
    text = (
        "3.1.4投资者买入的证券交收前不得卖出。3.3.8每次100股整数倍不足100股一次性卖出。3.3.9下一条"
    )
    report = dict(
        trading_rules=[
            dict(exchange="SZSE", source=dict(blob_ref="fixed"), effective_date="20260706")
        ]
    )

    def raw_bytes(ref):
        reads.append(ref)
        return text.encode()

    advisor_engine.sell_rules(report, raw_bytes, "000333.SZ")
    advisor_engine.sell_rules(report, raw_bytes, "000651.SZ")
    assert reads == ["fixed", "fixed"]
    assert calls == [text.encode()]
    with pytest.raises(ValueError, match="交收条款"):
        advisor_engine.sell_rules(
            report, lambda _: text.replace("一次性", "分别").encode(), "000651.SZ"
        )
    assert len(calls) == 2


def test_eight_assets_use_two_groups_and_share_one_account_budget(runtime, monkeypatch):
    import copy

    from marketpulse.quant.knowledge import advisor_quotes, context, observations
    from marketpulse.quant.knowledge.portfolio_store import PortfolioStore

    from .test_advisor import TRADING
    from .test_observations import archived_calendar

    public, packet, settings = runtime
    ids = ["000333", "000651", "000725", "000001", "000002", "000063", "000100", "000166"]
    stocks = []
    for code in ids:
        stock = copy.deepcopy(packet["stocks"][0])
        stock["ts_code"] = code + ".SZ"
        stock["name"] = "SYNTHETIC-" + code
        for row in stock["daily"] + stock["adjustments"]:
            row["ts_code"] = stock["ts_code"]
        stocks.append(stock)

    def collector(client, entities, stamp):
        return dict(
            packet, stocks=[next(s for s in stocks if s["ts_code"][:6] == e) for e in entities]
        )

    def external(repo, client, p):
        return dict(
            version=context.VERSION,
            market_date=p["market_date"],
            captured_at="2026-10-16T10:00:00+00:00",
            entries=[dict(security_id=s["ts_code"], entries=[]) for s in p["stocks"]],
            gaps=[],
        )

    monkeypatch.setattr(
        observations, "calendars", lambda client, p, spec: archived_calendar(public, p, spec)
    )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.portfolio_service.sell_rules", lambda *_: TRADING
    )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.portfolio_store.sell_rules", lambda *_: TRADING
    )
    repo = PortfolioStore(public.base)
    port = PortfolioPort()
    state, _ = repo.create(
        request(
            assets=[dict(entity=s, cost_price="90", shares=100, sellable_shares=100) for s in ids]
        ).model_dump(mode="json")
    )
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            research_options=dict(port=port, collector=collector, context_collector=external),
            quote_collector=lambda _, r: advisor_quotes.fallback(r),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    studies = [
        InvestmentStore(repo.base).get(g["run_id"], with_data=False)
        for g in result["research_groups"]
    ]
    assert len(studies) == 2 and len(result["data"]["changes"]) == 8
    assert sum(s["model_calls"] for s in studies) == 8
    assert studies[1]["config"]["budget"]["max_tokens"] == 60000
    assert studies[1]["config"]["budget"]["max_model_calls"] == 24
    assert result["budget"]["max_tokens"] == 120000
    assert result["budget"]["max_model_calls"] == 48


def test_correlated_group_limits_even_without_industry_or_single_breaches():
    req = request(
        available_cash="0",
        single_cap=".5",
        industry_cap="1",
        equity_cap="1",
        correlated_cap=".35",
        risk_per_trade=".05",
        total_risk=".1",
        trend_allocation="1",
        quality_allocation="0",
        assets=[
            dict(entity=s, cost_price="90", shares=500, sellable_shares=500)
            for s in ("000333", "000651")
        ],
    )
    linked = compute(req)
    assert linked["correlated_groups"]
    assert Decimal(linked["correlated_groups"][0]["after_weight"]) <= req.correlated_cap
    opposite = compute(req, independent=True)
    assert not opposite["correlated_groups"]
    assert not any(r["sell_shares"] for r in opposite["changes"])


def test_rotation_uses_net_sale_proceeds_conditionally_without_double_trading():
    req = request(
        available_cash="0",
        assets=[
            dict(entity=s, cost_price="90", shares=1000, sellable_shares=1000)
            for s in ("000333", "000651")
        ]
        + [dict(entity="000725", shares=0, sellable_shares=0)],
    )
    result = compute(req, ready=True)
    assert any(r["sell_shares"] for r in result["changes"])
    assert result["changes"][2]["buy_shares"] > 0
    assert Decimal(result["before"]["cash"]) == 0 and Decimal(result["after"]["cash"]) >= 0
    assert "减仓实际成交" in result["execution"][1]


def test_huge_positions_respect_daily_liquidity_and_leave_unresolved_risk():
    req = request(
        available_cash="0",
        liquidity_cap=".00001",
        assets=[
            dict(entity=s, cost_price="90", shares=1000000, sellable_shares=1000000)
            for s in ("000333", "000651")
        ],
    )
    result = compute(req, ready=True)
    assert result["unresolved"]
    assert all(r["sell_shares"] <= r["sell_limit"] == 100 for r in result["changes"])
    assert not any(r["buy_shares"] for r in result["changes"])


def test_spent_account_budget_is_not_reset_on_resume(portfolio):
    repo, settings, options = portfolio
    first = completed(portfolio, port=PortfolioPort(fail_writer=True))
    assert first["status"] == "INTERRUPTED"
    public = InvestmentStore(repo.base)
    rid = first["research_groups"][0]["run_id"]
    study = public.get(rid, with_data=False)
    study["charged_tokens"] = 60000
    public.save(study)
    port = PortfolioPort()
    final = asyncio.run(
        run(
            repo,
            None,
            first["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port),
        )
    )
    assert final["status"] == "INTERRUPTED" and "共享预算" in final["error"]
    assert public.get(rid, with_data=False)["charged_tokens"] == 60000
    assert not port.roles


def test_changed_assets_require_new_research_and_existing_route_stays_fixed(portfolio):
    repo, _, _ = portfolio
    first = completed(portfolio)
    with pytest.raises(ValueError, match="更新公开资料"):
        repo.create(
            request(
                previous_run_id=first["run_id"],
                assets=[
                    dict(entity=s, cost_price="90", shares=100, sellable_shares=100)
                    for s in ("000333", "000725")
                ],
            ).model_dump(mode="json")
        )
    with pytest.raises(ValueError, match="原周期"):
        repo.create(
            request(
                previous_run_id=first["run_id"],
                assets=[
                    dict(
                        entity=s, cost_price="90", shares=100, sellable_shares=100, horizon="MEDIUM"
                    )
                    for s in ("000333", "000651")
                ],
            ).model_dump(mode="json")
        )
