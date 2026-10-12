"""Offline synthetic accounting examples, never installed as real market inputs."""

from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

import pytest

from marketpulse.quant.backtest.accounting import replay_actions
from marketpulse.quant.backtest.ledger import Bar, Target
from marketpulse.quant.backtest.package import ActionEvent
from marketpulse.quant.backtest.selection import select_at
from marketpulse.quant.backtest.strategy import annual_returns, execute, performance
from marketpulse.quant.contracts import digest
from tests.unit.quant.test_backtest_package import fixture


def inputs():
    days = [date(2024, 5, d) for d in (6, 7, 8, 9, 10)]
    bars = [
        Bar(
            session=d,
            instrument="A",
            open="10",
            close="10",
            lot_size=1,
            buy_allowed=True,
            sell_allowed=True,
            suspended=False,
        )
        for d in days
    ]
    targets = [Target(session=days[1], known_on=days[0], weights=(("A", "1"),))]
    return dict(sessions=days, bars=bars, targets=targets, initial_cash="1000", cost_rate="0")


def action(kind, **kw):
    return ActionEvent(
        event_id=kind,
        instrument_id="A",
        kind=kind,
        known_at=datetime(2024, 5, 1, tzinfo=UTC),
        record_day=date(2024, 5, 7),
        ex_day=date(2024, 5, 8),
        settlement_day=date(2024, 5, 10),
        **kw,
    )


def test_cash_dividend_receivable_then_payment_no_spendable_cash():
    args = inputs()
    args["bars"] = [
        b.model_copy(update={"open": Decimal("9"), "close": Decimal("9")})
        if b.session >= date(2024, 5, 8)
        else b
        for b in args["bars"]
    ]
    out = replay_actions(**args, actions=[action("cash_dividend", cash_per_share="1")])
    assert out["daily"][2]["equity"] == "1000"
    assert out["daily"][2]["cash"] == "0" and out["daily"][2]["receivables"] == "100"
    assert out["daily"][-1]["cash"] == "100" and out["daily"][-1]["receivables"] == "0"
    assert out == replay_actions(**args, actions=[action("cash_dividend", cash_per_share="1")])


def test_split_locked_shares_and_delisting_settlement():
    args = inputs()
    args["bars"] = [
        b.model_copy(update={"open": Decimal("5"), "close": Decimal("5")})
        if b.session >= date(2024, 5, 8)
        else b
        for b in args["bars"]
    ]
    out = replay_actions(**args, actions=[action("share_distribution", shares_per_share="1")])
    assert out["daily"][2]["holdings"]["A"] == 100
    assert out["daily"][2]["equity"] == "1000"
    assert out["daily"][-1]["holdings"]["A"] == 200
    args = inputs()
    args["bars"] = [b for b in args["bars"] if b.session < date(2024, 5, 8)]
    out = replay_actions(**args, actions=[action("delisting", cash_per_share="9")])
    assert out["daily"][2]["holdings"] == {} and out["daily"][2]["equity"] == "900"
    assert out["daily"][-1]["cash"] == "900"


def test_rights_subscription_requires_cash_and_explicit_fraction_terms():
    with pytest.raises(ValueError, match="lacks cash"):
        replay_actions(
            **inputs(),
            actions=[action("rights_issue", shares_per_share=".1", subscription_price="5")],
        )
    args = inputs()
    args["targets"] = [args["targets"][0].model_copy(update={"weights": (("A", Decimal(".5")),)})]
    out = replay_actions(
        **args, actions=[action("rights_issue", shares_per_share=".1", subscription_price="5")]
    )
    assert out["daily"][-1]["cash"] == "475" and out["daily"][-1]["holdings"]["A"] == 55
    with pytest.raises(ValueError, match="fractional"):
        replay_actions(**args, actions=[action("share_distribution", shares_per_share=".001")])


def test_rejected_order_no_implicit_retry_and_empty_target_cash():
    args = inputs()
    args["bars"] = [
        b.model_copy(update={"buy_allowed": False}) if b.session == date(2024, 5, 7) else b
        for b in args["bars"]
    ]
    out = replay_actions(**args, actions=[])
    assert out["orders"][0]["status"] == "REJECTED"
    assert all(d["cash"] == "1000" and d["holdings"] == {} for d in out["daily"])


def test_performance_hand_computed_drawdown_return_and_explicit_risk_free():
    days = [date(2023, 12, 29), date(2024, 1, 2), date(2024, 1, 3)]
    m = performance(days, ["1", "1.1", ".99"], ".02")
    assert Decimal(m["total_ret"]) == -1 and Decimal(m["max_dd"]) == -10
    assert Decimal(m["vol"]) == pytest.approx(
        Decimal("224.499443206436483133345923389"), rel=Decimal("1e-20")
    )
    assert performance(days, ["1", "1", "1"], "0")["sharpe"] is None
    with pytest.raises(ValueError):
        performance(days, ["1", "NaN", "1"], "0")


def test_admission_before_strategy_costs_empty_selection_and_reproducibility():
    package, reviews, blobs = fixture()
    kw = dict(initial_cash="100000", risk_free_annual=".02", cost_pct_per_side=".13")
    out = execute(package, reviews, {}, blobs.get, **kw)
    assert out["execution_status"] == "COMPLETED"
    assert out["nav"]["strategy"][0] == "1"
    assert out["ledgers"]["strategy"]["orders"][0]["status"] == "FILLED"
    assert len({x["input_hash"] for x in out["ledgers"].values()}) == 3
    assert out["metric_status"] == "UNVERIFIED"
    with localcontext() as ctx:
        ctx.prec = 8
        assert out == execute(package, reviews, {}, blobs.get, **kw)
    blocked = execute(package, (), {}, blobs.get, **kw)
    assert blocked["execution_status"] == "BLOCKED" and "nav" not in blocked


def test_annual_includes_first_new_year_move_and_empty_selection_stays_cash():
    dates = [date(2023, 12, 28), date(2023, 12, 29), date(2024, 1, 2), date(2024, 12, 31)]
    nav = dict(
        dates=list(map(str, dates)),
        strategy=["1", "1.1", "1.21", "1.32"],
        benchmark=["1", "1", ".9", ".99"],
    )
    annual = annual_returns(dates, nav, [dict(holdings={}) for _ in dates])
    assert Decimal(annual[0]["strategy"]) == 10
    assert Decimal(annual[1]["strategy"]) == 20
    assert Decimal(annual[1]["benchmark"]) == -1
    assert annual[1]["baseline_date"] == "2023-12-29"
    package, reviews, blobs = fixture()
    period = package.annual[0]
    selected = select_at(period.candidates, period.cutoff, {"pe_max": "1"})
    # The isolated fixture's internal registry must bind the changed selection;
    # production cannot reuse a review for different selection inputs.
    reviews = tuple(
        r.model_copy(
            update={
                "payload_hash": digest(
                    dict(
                        instrument_id=period.candidates[0].instrument_id,
                        cutoff=period.cutoff.isoformat(),
                        metric=r.domain.removeprefix("metric_"),
                        selection_hash=selected["output_hash"],
                    )
                )
            }
        )
        if r.domain.startswith("metric_")
        else r
        for r in reviews
    )
    result = execute(
        package,
        reviews,
        {"pe_max": "1"},
        blobs.get,
        initial_cash="100000",
        risk_free_annual=".02",
        cost_pct_per_side=".13",
    )
    assert result["execution_status"] == "COMPLETED"
    assert all(v == "1" for v in result["nav"]["strategy"])
    assert result["trades"][0]["stocks"] == [] and result["ledgers"]["strategy"]["orders"] == []


def test_pending_odd_shares_cannot_create_invalid_buy_or_sell_lots():
    args = inputs()
    args["bars"] = [b.model_copy(update={"lot_size": 10}) for b in args["bars"]]
    args["targets"].append(
        Target(session=date(2024, 5, 9), known_on=date(2024, 5, 8), weights=(("A", "1"),))
    )
    out = replay_actions(**args, actions=[action("share_distribution", shares_per_share=".01")])
    assert all(o["quantity"] % 10 == 0 for o in out["orders"])
