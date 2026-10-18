# ruff: noqa: F811
import asyncio
import copy
from datetime import date, timedelta
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from marketpulse.quant.knowledge import advisor_quotes, observations
from marketpulse.quant.knowledge.portfolio_api import router
from marketpulse.quant.knowledge.portfolio_contracts import PortfolioRequest
from marketpulse.quant.knowledge.portfolio_engine import build
from marketpulse.quant.knowledge.portfolio_risk import correlations, volatility
from marketpulse.quant.knowledge.portfolio_service import run
from marketpulse.quant.knowledge.portfolio_store import PortfolioStore
from marketpulse.quant.knowledge.research_store import InvestmentStore

from .test_advisor import TRADING, report
from .test_context import snapshot
from .test_observations import archived_calendar
from .test_research import SyntheticPort, runtime  # noqa: F401


class PortfolioPort(SyntheticPort):
    async def generate(self, model_request):
        from marketpulse.quant.knowledge import research_agents as agents

        result = await super().generate(model_request)
        if model_request.response_model is agents.Analysis:
            output = result.output.model_dump(mode="json")
            context = self.contexts[-1]
            for o in output["opinions"]:
                sid = o["security_id"]
                for kind in ("DAILY_FLOW", "TECHNICAL", "POLICY_CLAUSE"):
                    fact = next(
                        (
                            f
                            for f in context["evidence"]
                            if f.get("kind") == kind
                            and f["evidence_id"].startswith(("X-" + sid + "-", "T-" + sid + "-"))
                        ),
                        None,
                    )
                    if fact:
                        claim = o["opposing"][0] if kind == "POLICY_CLAUSE" else o["claims"][0]
                        claim["evidence_ids"].append(fact["evidence_id"])
                        claim["text"] = "合成测试：资料仍需保留反证，公司资格尚未核定。"
            result.output = agents.Analysis.model_validate(output)
        return result


def request(**changes):
    default = dict(
        request_key=uuid4(),
        all_holdings_declared=True,
        input_origin="SYNTHETIC_ACCEPTANCE",
        available_cash="100000",
        assets=[
            dict(entity=s, cost_price="90", shares=100, sellable_shares=100)
            for s in ("000333", "000651")
        ],
        commission_rate="0",
        minimum_commission="0",
        other_sell_cost_rate="0",
        other_buy_cost_rate="0",
    )
    return PortfolioRequest(**(default | changes))


def chart(sign=1):
    start = date(2026, 1, 1)
    price = Decimal(100)
    rows = []
    for i in range(80):
        d = start + timedelta(days=i)
        if d.weekday() > 4:
            continue
        price *= 1 + sign * Decimal(((i * 7) % 11) - 5) / 1000
        rows.append(dict(date=d.strftime("%Y%m%d"), close=str(price)))
    # 100 rows yield at least 61 weekdays.
    for i in range(80, 100):
        d = start + timedelta(days=i)
        if d.weekday() < 5:
            price *= 1 + sign * Decimal(((i * 7) % 11) - 5) / 1000
            rows.append(dict(date=d.strftime("%Y%m%d"), close=str(price)))
    return rows


def compute(
    req=None,
    *,
    ready=False,
    independent=False,
    bearish=False,
    missing=False,
    quote_sources=False,
    reverse_quotes=False,
):
    req = req or request()
    base = report()
    data = dict(
        base, request=dict(entities=[a.entity for a in req.assets], horizon="SWING", profile=None)
    )
    data["candidates"] = []
    data["research_opinions"] = []
    quotes = {}
    trading = {}
    for i, a in enumerate(req.assets):
        c = copy.deepcopy(base["candidates"][0])
        sid = a.entity + ".SZ"
        c.update(
            security_id=sid,
            name="SYNTHETIC-" + a.entity,
            industry_code="801110.SI" if i < 2 else "801080.SI",
            industry_name="合成行业" if i < 2 else "合成其他",
            chart=chart(-1 if independent and i else 1),
        )
        c["metrics"].update(
            average_turnover_20=dict(value="1000000000"),
            relative_return_20=dict(value=str(Decimal(i) / 100)),
        )
        c["routes"][0]["status"] = "CONDITIONS_MET" if ready else "WAIT_TRIGGER"
        if missing and i == 1:
            c["chart"].pop(-3)
        o = copy.deepcopy(base["research_opinions"][0])
        o.update(security_id=sid, direction="UNFAVORABLE" if bearish and i == 0 else "FAVORABLE")
        data["candidates"].append(c)
        data["research_opinions"].append(o)
        quotes[sid] = dict(
            security_id=sid,
            price="100",
            previous_close="99",
            quoted_at="2026-10-16T15:00:00+08:00",
            kind="OBSERVED_QUOTE",
            trading_status="TRADED",
            source=dict(blob_ref="blob://sha256/" + str(i) * 64) if quote_sources else None,
        )
        trading[sid] = TRADING
    study = dict(run_id="RUN-IR-SYNTHETIC", result_sha256="a" * 64, data=data)
    if reverse_quotes:
        quotes = dict(reversed(list(quotes.items())))
    return build(req, [study], quotes, trading, [r["date"] for r in chart()])


def test_quote_map_order_does_not_change_archived_result():
    req = request()
    assert compute(req, quote_sources=True) == compute(req, quote_sources=True, reverse_quotes=True)


def test_contract_requires_complete_cash_account_and_consistent_quantities():
    for values in (
        dict(all_holdings_declared=False),
        dict(available_cash="NaN"),
        dict(reserve_cash="100001"),
        dict(trend_allocation=".6", quality_allocation=".6"),
        dict(assets=[dict(entity="000333", cost_price="90", shares=100, sellable_shares=101)]),
    ):
        with pytest.raises(ValidationError):
            request(**values)


def test_returns_correlations_covariance_and_fixed_weight_volatility():
    a = chart()
    b = chart(-1)
    days = [p["date"] for p in a]
    risk = correlations(dict(A=a, B=a, C=b), days, Decimal(".75"))
    assert risk["sample_count"] == 60
    assert Decimal(risk["matrix"][0][1]) > Decimal(".999999")
    assert Decimal(risk["matrix"][0][2]) < Decimal("-.999999")
    assert risk["groups"] == [["A", "B"]]
    assert Decimal(volatility(risk, dict(A=Decimal(".5"), B=Decimal(".5")))) > 0
    # These opposite daily returns have small fixed-mix historical variance, no future guarantee.
    assert Decimal(volatility(risk, dict(A=Decimal(".5"), C=Decimal(".5")))) < Decimal(".00001")


def test_missing_day_is_not_removed_and_zero_variance_is_not_zero_correlation():
    a = chart()
    b = chart()
    b.pop(-3)
    assert (
        correlations(dict(A=a, B=b), [p["date"] for p in a], Decimal(".75"))["status"] == "MISSING"
    )
    flat = [dict(p, close="100") for p in a]
    risk = correlations(dict(A=a, B=flat), [p["date"] for p in a], Decimal(".75"))
    assert risk["matrix"][0][1] is None and risk["matrix"][1][1] is None


def test_combined_caps_reduce_once_per_stock_and_keep_account_conservation():
    req = request(
        available_cash="0",
        assets=[
            dict(entity=s, cost_price="90", shares=1000, sellable_shares=1000)
            for s in ("000333", "000651")
        ],
    )
    result = compute(req)
    assert result["initial_breaches"] and not result["unresolved"]
    assert sum(r["sell_shares"] for r in result["changes"]) <= 2000
    assert all(r["sell_shares"] <= r["sellable_shares"] for r in result["changes"])
    assert Decimal(result["after"]["cash"]) + Decimal(result["after"]["invested"]) == Decimal(
        result["after"]["total_assets"]
    )
    assert all(Decimal(i["weight"]) <= req.industry_cap for i in result["after"]["industries"])
    assert (
        Decimal(result["after"]["risk_to_reference"])
        <= Decimal(result["after"]["total_assets"]) * req.total_risk
    )


def test_locked_shares_leave_unresolved_risk_and_prevent_all_additions():
    req = request(
        available_cash="0",
        assets=[
            dict(entity=s, cost_price="90", shares=1000, sellable_shares=0, bought_today=1000)
            for s in ("000333", "000651")
        ],
    )
    result = compute(req, ready=True)
    assert result["unresolved"]
    assert all(r["sell_shares"] == r["buy_shares"] == 0 for r in result["changes"])


def test_additions_need_route_confirmation_and_every_account_limit():
    req = request(
        single_cap=".4",
        industry_cap=".8",
        correlated_cap=".8",
        equity_cap=".8",
        trend_allocation=".8",
        quality_allocation="0",
        risk_per_trade=".04",
        total_risk=".08",
    )
    wait = compute(req, ready=False)
    assert all(r["buy_shares"] == 0 for r in wait["changes"])
    ready = compute(req, ready=True)
    assert any(r["buy_shares"] for r in ready["changes"])
    assert Decimal(ready["after"]["equity_weight"]) <= req.equity_cap
    assert Decimal(ready["after"]["cash"]) >= req.reserve_cash
    missing = compute(req, ready=True, missing=True)
    assert all(r["buy_shares"] == 0 for r in missing["changes"])


def test_fees_reduce_net_asset_denominator_and_cash_before_projected_buying():
    req = request(
        available_cash="0",
        commission_rate=".0003",
        minimum_commission="5",
        other_sell_cost_rate=".001",
        assets=[
            dict(entity=s, cost_price="90", shares=1000, sellable_shares=1000)
            for s in ("000333", "000651")
        ],
    )
    result = compute(req, ready=True)
    assert Decimal(result["after"]["total_assets"]) < Decimal(result["before"]["total_assets"])
    assert Decimal(result["after"]["total_assets"]) + Decimal(result["after"]["costs"]) == Decimal(
        result["before"]["total_assets"]
    )
    assert not result["unresolved"]
    assert all(not (r["sell_shares"] and r["buy_shares"]) for r in result["changes"])


@pytest.fixture
def portfolio(runtime, monkeypatch):
    public, packet, settings = runtime
    stock = copy.deepcopy(packet["stocks"][0])
    stock["ts_code"] = "000651.SZ"
    stock["name"] = "SYNTHETIC-B"
    for row in stock["daily"] + stock["adjustments"]:
        row["ts_code"] = "000651.SZ"
    packet["stocks"].append(stock)
    monkeypatch.setattr(
        observations,
        "calendars",
        lambda client, initial, spec: archived_calendar(public, initial, spec),
    )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.portfolio_service.sell_rules", lambda *_: TRADING
    )
    monkeypatch.setattr(
        "marketpulse.quant.knowledge.portfolio_store.sell_rules", lambda *_: TRADING
    )

    def context(*_):
        value = snapshot(public, packet)
        value["entries"].extend(
            dict(security_id=s["ts_code"], entries=[]) for s in packet["stocks"][1:]
        )
        return value

    options = dict(collector=lambda *_: packet, context_collector=context)
    return PortfolioStore(public.base), settings, options


def completed(portfolio, req=None, port=None):
    repo, settings, options = portfolio
    state, _ = repo.create((req or request()).model_dump(mode="json"))
    return asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port or PortfolioPort()),
            quote_collector=lambda client, data: advisor_quotes.fallback(data),
        )
    )


def test_public_research_privacy_immutable_turns_and_budgeted_followup(portfolio):
    repo, _, _ = portfolio
    port = PortfolioPort()
    first = completed(portfolio, port=port)
    assert first["status"] == "COMPLETED", first.get("error")
    assert port.roles == ["Planner", "Analyst", "Verifier", "Writer"]
    assert all(
        "available_cash" not in c and "assets" not in c and "position" not in c
        for c in port.contexts
    )
    second = completed(
        portfolio, request(previous_run_id=first["run_id"], available_cash="99999.37")
    )
    assert second["status"] == "COMPLETED", second.get("error")
    assert first["research_groups"] == second["research_groups"]
    assert len(repo.conversation(second["run_id"])) == 2
    assert repo.create(first["request"])[1] is False
    with pytest.raises(ValueError):
        repo.save(dict(first, title="overwrite"))
    assert repo.base.list() == []


def test_failed_role_resumes_with_same_research_and_spent_budget(portfolio):
    repo, settings, options = portfolio
    first = completed(portfolio, port=PortfolioPort(fail_writer=True))
    assert first["status"] == "INTERRUPTED"
    ids = first["research_groups"]
    old = InvestmentStore(repo.base).get(ids[0]["run_id"], with_data=False)
    port = PortfolioPort()
    final = asyncio.run(
        run(
            repo,
            None,
            first["run_id"],
            Event(),
            settings,
            research_options=dict(options, port=port),
            quote_collector=lambda _, r: advisor_quotes.fallback(r),
        )
    )
    assert final["status"] == "COMPLETED", final.get("error")
    assert final["research_groups"] == ids and port.roles == ["Writer"]
    assert (
        InvestmentStore(repo.base).get(ids[0]["run_id"], with_data=False)["charged_tokens"]
        > old["charged_tokens"]
    )


def test_account_api_history_export_and_same_origin_guard(portfolio):
    repo, settings, _ = portfolio
    first = completed(portfolio)
    assert first["status"] == "COMPLETED", first.get("error")
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = repo.base
    app.state.settings = settings
    app.state.research_jobs = {}
    with TestClient(app) as client:
        path = "/api/quant/portfolio-advisor/turns/" + first["run_id"]
        assert client.get(path).status_code == 200
        assert client.get(path).headers["cache-control"] == "no-store"
        assert client.get(path + "/export").json()["turns"][0]["result_ref"] == first["result_ref"]
        assert (
            client.post("/api/quant/portfolio-advisor/turns", json=first["request"]).status_code
            == 202
        )
        assert (
            client.post(path + "/resume", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
