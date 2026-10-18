from copy import deepcopy
from decimal import Decimal

import pytest

from marketpulse.quant.knowledge.planning_contracts import Profile
from marketpulse.quant.knowledge.playbook import allocate_plans, trading_unit


def candidate(code="000333.SZ", industry="801110.SI", route="TREND_SWING"):
    return dict(
        status="COMPUTED",
        security_id=code,
        industry_code=industry,
        selected_route=route,
        metrics=dict(average_turnover_20=dict(value="1000000000")),
        routes=[
            dict(
                route_id=route,
                budget_eligible=True,
                entry_reference="100",
                risk_reference="95",
                status="WAIT_TRIGGER",
            )
        ],
    )


def test_multiple_routes_and_stocks_share_cash_and_industry_caps():
    profile = Profile(capital=50000, available_cash=50000, single_cap=".3", industry_cap=".5")
    result = allocate_plans(
        [candidate(), candidate("000651.SZ", route="QUALITY_GROWTH"), candidate("600519.SH")],
        profile,
    )
    assert sum(Decimal(p["reserved_cash"]) for p in result["plans"]) <= 50000
    assert sum(Decimal(p["estimated_value"]) for p in result["plans"]) <= 25000
    assert all(p["conditional"] for p in result["plans"])


def test_no_profile_no_amount_and_unknown_holdings_cannot_be_ignored():
    assert allocate_plans([candidate()], None)["plans"] == []
    with pytest.raises(ValueError, match="现金加持仓"):
        Profile(capital=50000, available_cash=40000)


def test_reserve_existing_exposure_and_lot_minimum():
    profile = Profile(
        capital=50000,
        available_cash=45000,
        reserve_cash=45000,
        holdings=[
            dict(
                security_id="000333.SZ",
                route_id="TREND_SWING",
                industry_code="801110.SI",
                market_value=5000,
            )
        ],
    )
    result = allocate_plans([candidate()], profile)
    assert result["plans"][0]["shares"] == 0
    assert Decimal(result["reserved_cash"]) == 0
    assert trading_unit("688981.SH")["minimum"] == 200
    assert trading_unit("688981.SH")["increment"] == 1
    assert trading_unit("300750.SZ")["maximum"] == 300000


def test_small_account_cannot_buy_one_star_lot_and_liquidity_is_binding():
    p = Profile(capital=50000, available_cash=50000)
    assert allocate_plans([candidate("688981.SH")], p)["plans"][0]["shares"] == 0
    row = deepcopy(candidate())
    row["metrics"]["average_turnover_20"]["value"] = "1000000"
    assert allocate_plans([row], p)["plans"][0]["shares"] == 0


def test_total_new_risk_is_shared_and_includes_round_trip_costs():
    p = Profile(capital=500000, available_cash=500000, total_new_risk=".005")
    result = allocate_plans([candidate(), candidate("600519.SH", "801120.SI")], p)
    assert sum(Decimal(r["estimated_risk"]) for r in result["plans"]) <= 2500
    assert result["plans"][0]["stress_scenarios"][1]["assumed_price_drop"] == "0.2"


def test_profile_default_serialization_is_stable_after_reload():
    p = Profile(capital="50000", available_cash="50000")
    assert p.model_dump(mode="json") == Profile.model_validate(
        p.model_dump(mode="json")
    ).model_dump(mode="json")
