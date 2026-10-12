"""Cash-only raw-price execution kernel, not a financial selection strategy.

The caller must independently verify the absence of corporate actions. This
kernel is deliberately unavailable to the public API until data admission and
corporate-action accounting are implemented. Unit fixtures are synthetic.
"""

from datetime import date
from decimal import ROUND_FLOOR, Decimal, localcontext

from pydantic import Field, StrictBool, model_validator

from ..contracts import digest
from ..domain import ExactDecimal, FrozenModel, decimal_text


class Bar(FrozenModel):
    session: date
    instrument: str = Field(min_length=1, max_length=100)
    open: ExactDecimal = Field(gt=0)
    close: ExactDecimal = Field(gt=0)
    lot_size: int = Field(ge=1, le=10000)
    buy_allowed: StrictBool
    sell_allowed: StrictBool
    suspended: StrictBool


class Target(FrozenModel):
    session: date
    known_on: date
    weights: tuple[tuple[str, ExactDecimal], ...]

    @model_validator(mode="after")
    def eligible(self):
        if self.known_on >= self.session:
            raise ValueError("signal must be known before execution day")
        names = [name for name, _ in self.weights]
        if len(set(names)) != len(names) or any(not n for n in names):
            raise ValueError("duplicate/empty target instrument")
        with localcontext() as context:
            context.prec = 40
            if (
                any(w < 0 or w > 1 for _, w in self.weights)
                or sum((w for _, w in self.weights), Decimal(0)) > 1
            ):
                raise ValueError("long-only target weights must sum to <=1")
        return self


def replay(*, sessions, bars, targets, initial_cash, cost_rate, no_corporate_actions):
    """One rebalance per session, sells before buys, no leverage or queued fills.

    cost_rate is an explicit all-in scenario cost charged on each executed side;
    it does not claim to reconstruct historical commissions/tax. Unknown bars
    fail closed. Direction restrictions are supplied by reviewed historical
    inputs, never inferred from adjusted prices or today's board rules.
    """
    with localcontext() as context:
        context.prec = 40
        initial_cash, cost_rate = Decimal(initial_cash), Decimal(cost_rate)
        if (
            not initial_cash.is_finite()
            or initial_cash <= 0
            or not cost_rate.is_finite()
            or not 0 <= cost_rate <= Decimal("0.01")
        ):
            raise ValueError("positive cash and bounded explicit cost required")
        if no_corporate_actions is not True:
            raise ValueError("corporate action absence must be independently confirmed")
        if not sessions or tuple(sessions) != tuple(sorted(set(sessions))):
            raise ValueError("ordered unique sessions required")
        if len(sessions) > 2200 or len(bars) > 250000 or len(targets) > 100:
            raise ValueError("bounded execution inputs required")
        quotes = {(b.session, b.instrument): b for b in bars}
        signals = {t.session: t for t in targets}
        if len(quotes) != len(bars) or len(signals) != len(targets):
            raise ValueError("duplicate bars or rebalance signals")
        if any(b.session not in sessions for b in bars) or any(
            t.session not in sessions for t in targets
        ):
            raise ValueError("input outside verified sessions")
        cash, holdings, bought_on = initial_cash, {}, {}
        daily, orders = [], []

        def quote(day, name):
            result = quotes.get((day, name))
            if result is None:
                raise ValueError("missing raw bar for held/target instrument")
            return result

        def order(day, name, side, quantity):
            nonlocal cash
            bar = quote(day, name)
            reason = (
                "suspended"
                if bar.suspended
                else "buy_restricted"
                if side == "BUY" and not bar.buy_allowed
                else "sell_restricted"
                if side == "SELL" and not bar.sell_allowed
                else "t_plus_one"
                if side == "SELL" and bought_on.get(name) == day
                else None
            )
            gross, fee = bar.open * quantity, bar.open * quantity * cost_rate
            if side == "BUY" and gross + fee > cash:
                raise ValueError("cash conservation failure")
            if side == "SELL" and quantity > holdings.get(name, 0):
                raise ValueError("position conservation failure")
            if not reason:
                cash += gross - fee if side == "SELL" else -gross - fee
                holdings[name] = holdings.get(name, 0) + (quantity if side == "BUY" else -quantity)
                if not holdings[name]:
                    holdings.pop(name)
                if side == "BUY":
                    bought_on[name] = day
            orders.append(
                dict(
                    session=day.isoformat(),
                    instrument=name,
                    side=side,
                    quantity=quantity,
                    price=decimal_text(bar.open),
                    gross=decimal_text(gross) if not reason else "0",
                    fee=decimal_text(fee) if not reason else "0",
                    status="REJECTED" if reason else "FILLED",
                    reason=reason,
                    cash_after=decimal_text(cash),
                    holdings_after=dict(sorted(holdings.items())),
                )
            )

        for day in sessions:
            signal = signals.get(day)
            if signal is not None:
                weights = dict(signal.weights)
                names = sorted(set(holdings) | set(weights))
                equity = cash + sum(
                    (quantity * quote(day, name).open for name, quantity in holdings.items()),
                    Decimal(0),
                )
                desired = {}
                for name in names:
                    bar = quote(day, name)
                    units = equity * weights.get(name, Decimal(0)) / bar.open / bar.lot_size
                    desired[name] = (
                        int(units.to_integral_value(rounding=ROUND_FLOOR)) * bar.lot_size
                    )
                for name in names:
                    quantity = holdings.get(name, 0) - desired[name]
                    if quantity > 0:
                        order(day, name, "SELL", quantity)
                for name in names:
                    quantity = desired[name] - holdings.get(name, 0)
                    if quantity <= 0:
                        continue
                    bar = quote(day, name)
                    affordable = (
                        int(
                            (cash / (bar.open * (1 + cost_rate)) / bar.lot_size).to_integral_value(
                                rounding=ROUND_FLOOR
                            )
                        )
                        * bar.lot_size
                    )
                    quantity = min(quantity, affordable)
                    if quantity:
                        order(day, name, "BUY", quantity)
                    else:
                        orders.append(
                            dict(
                                session=day.isoformat(),
                                instrument=name,
                                side="BUY",
                                quantity=0,
                                status="REJECTED",
                                reason="insufficient_cash",
                                gross="0",
                                fee="0",
                                cash_after=decimal_text(cash),
                                holdings_after=dict(sorted(holdings.items())),
                            )
                        )
            equity = cash + sum(
                (quantity * quote(day, name).close for name, quantity in holdings.items()),
                Decimal(0),
            )
            if cash < 0 or any(q <= 0 for q in holdings.values()):
                raise ValueError("ledger conservation failure")
            daily.append(
                dict(
                    session=day.isoformat(),
                    cash=decimal_text(cash),
                    holdings=dict(sorted(holdings.items())),
                    equity=decimal_text(equity),
                    nav=decimal_text(equity / initial_cash),
                )
            )
        payload = dict(
            schema_version="cash-ledger-v1",
            status="UNVERIFIED",
            initial_equity=decimal_text(initial_cash),
            initial_nav="1",
            daily=daily,
            orders=orders,
        )
        payload["input_hash"] = digest(
            dict(
                schema_version="cash-ledger-v1",
                sessions=[s.isoformat() for s in sessions],
                bars=[
                    b.model_dump(mode="json")
                    for b in sorted(bars, key=lambda b: (b.session, b.instrument))
                ],
                targets=[
                    t.model_dump(mode="json") for t in sorted(targets, key=lambda t: t.session)
                ],
                initial_cash=decimal_text(initial_cash),
                cost_rate=decimal_text(cost_rate),
                no_corporate_actions=True,
            )
        )
        payload["output_hash"] = digest(payload)
        return payload
