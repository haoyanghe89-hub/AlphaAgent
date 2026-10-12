"""Hand-calculated synthetic fixtures, never used for page acceptance data."""

from datetime import date
from decimal import Decimal, localcontext

import pytest

from marketpulse.quant.backtest.ledger import Bar, Target, replay

DAYS = (date(2024, 5, 6), date(2024, 5, 7))


def bars(**changes):
    changes = {"buy_allowed": True, "sell_allowed": True, "suspended": False, **changes}
    return tuple(
        Bar(session=day, instrument="A", open=price, close=price, lot_size=10, **changes)
        for day, price in zip(DAYS, ("10", "11"), strict=True)
    )


def target(day, weights):
    return Target(session=day, known_on=date(2024, 5, 5), weights=weights)


def run(prices=None, targets=None, cost="0.01"):
    return replay(
        sessions=DAYS,
        bars=bars() if prices is None else prices,
        targets=(target(DAYS[0], (("A", "1"),)), target(DAYS[1], ()))
        if targets is None
        else targets,
        initial_cash=Decimal("1000"),
        cost_rate=Decimal(cost),
        no_corporate_actions=True,
    )


def test_hand_calculated_cash_fees_and_holdings_conserve():
    output = run()
    first, last = output["daily"]
    assert first["cash"] == "91" and first["equity"] == "991"
    assert first["holdings"] == {"A": 90}
    assert last["cash"] == "1071.1" and last["equity"] == "1071.1"
    assert last["holdings"] == {}
    assert [t["fee"] for t in output["orders"]] == ["9", "9.9"]
    assert output["initial_equity"] == "1000" and output["initial_nav"] == "1"
    assert last["nav"] == "1.0711"
    assert output["status"] == "UNVERIFIED"


def test_direction_limits_reject_sell_and_keep_actual_position():
    prices = list(bars())
    prices[1] = prices[1].model_copy(update={"sell_allowed": False})
    out = run(prices)
    assert out["daily"][-1]["holdings"] == {"A": 90}
    assert out["daily"][-1]["cash"] == "91"
    assert out["orders"][-1]["status"] == "REJECTED"
    assert out["orders"][-1]["reason"] == "sell_restricted"
    prices[0] = prices[0].model_copy(update={"buy_allowed": False})
    assert run(prices)["daily"][-1]["cash"] == "1000"


def test_suspension_empty_selection_and_missing_mark_price():
    assert run(bars(suspended=True))["daily"][-1]["cash"] == "1000"
    assert run(targets=(target(DAYS[0], ()),))["orders"] == []
    with pytest.raises(ValueError, match="missing raw bar"):
        run(bars()[:1])
    with pytest.raises(ValueError):
        Bar(session=DAYS[0], instrument="A", open="10", close=None, lot_size=100)
    with pytest.raises(ValueError):
        Bar(session=DAYS[0], instrument="A", open="10", close="10", lot_size=100, buy_allowed=None)


def test_signal_cannot_use_execution_day_and_duplicate_orders_rejected():
    with pytest.raises(ValueError, match="before execution"):
        Target(session=DAYS[0], known_on=DAYS[0], weights=(("A", "1"),))
    with pytest.raises(ValueError, match="duplicate"):
        run(targets=(target(DAYS[0], (("A", "1"),)), target(DAYS[0], ())))
    with pytest.raises(ValueError):
        target(DAYS[0], (("A", ".5"), ("A", ".5")))
    with pytest.raises(ValueError):
        target(DAYS[0], (("A", "1.1"),))
    with pytest.raises(ValueError, match="duplicate"):
        run(bars() + bars())


def test_cash_no_leverage_cost_replay_and_decimal_context_independence():
    paid = run()
    free = run(cost="0")
    assert free["daily"][-1]["equity"] == "1100"
    assert paid["input_hash"] != free["input_hash"]
    with localcontext() as context:
        context.prec = 4
        assert run() == paid
    assert run() == paid
    assert all(Decimal(d["cash"]) >= 0 for d in paid["daily"])


def test_kernel_refuses_unconfirmed_corporate_actions_and_unordered_calendar():
    with pytest.raises(ValueError, match="corporate action"):
        replay(
            sessions=DAYS,
            bars=bars(),
            targets=(),
            initial_cash=Decimal("1000"),
            cost_rate=Decimal("0"),
            no_corporate_actions=False,
        )
    with pytest.raises(ValueError, match="ordered"):
        replay(
            sessions=DAYS[::-1],
            bars=bars(),
            targets=(),
            initial_cash=Decimal("1000"),
            cost_rate=Decimal("0"),
            no_corporate_actions=True,
        )
