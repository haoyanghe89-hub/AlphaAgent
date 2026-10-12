"""Raw-price, long-only corporate-action ledger. No provider or model access."""

from collections import defaultdict
from decimal import ROUND_FLOOR, Decimal, localcontext
from zoneinfo import ZoneInfo

from ..contracts import digest
from ..domain import decimal_text


def replay_actions(*, sessions, bars, targets, actions, initial_cash, cost_rate):
    with localcontext() as ctx:
        ctx.prec = 40
        return _replay(sessions, bars, targets, actions, Decimal(initial_cash), Decimal(cost_rate))


def _replay(sessions, bars, targets, actions, initial_cash, cost_rate):
    if not initial_cash.is_finite() or initial_cash <= 0 or not cost_rate.is_finite():
        raise ValueError("finite positive cash required")
    if not 0 <= cost_rate <= Decimal(".01"):
        raise ValueError("cost rate outside scenario bound")
    if not sessions or tuple(sessions) != tuple(sorted(set(sessions))):
        raise ValueError("ordered unique sessions required")
    if len(sessions) > 2200 or len(bars) > 250000 or len(targets) > 100 or len(actions) > 20000:
        raise ValueError("bounded ledger required")
    quotes = {(b.session, b.instrument): b for b in bars}
    signals = {t.session: t for t in targets}
    if len(quotes) != len(bars) or len(signals) != len(targets):
        raise ValueError("duplicate quote or target")
    if any(b.session not in sessions for b in bars) or any(
        t.session not in sessions for t in targets
    ):
        raise ValueError("input outside calendar")
    if len({a.event_id for a in actions}) != len(actions):
        raise ValueError("duplicate action")
    for a in actions:
        if (
            a.ex_day not in sessions
            or not a.settlement_day
            or a.settlement_day < a.ex_day
            or a.known_at.astimezone(ZoneInfo("Asia/Shanghai")).date() > a.ex_day
        ):
            raise ValueError("unusable action date/terms")
        if a.kind != "delisting" and (not a.record_day or a.record_day >= a.ex_day):
            raise ValueError("record close must precede ex date")
        if a.record_day and a.record_day >= sessions[0] and a.record_day not in sessions:
            raise ValueError("record day must have a verified closing session")
        if a.kind in {"cash_dividend", "delisting"} and a.cash_per_share is None:
            raise ValueError("cash settlement terms missing")
        if a.kind in {"share_distribution", "rights_issue"} and a.shares_per_share is None:
            raise ValueError("share settlement terms missing")
        if a.kind == "rights_issue" and a.subscription_price is None:
            raise ValueError("subscription terms missing")
    cash, holdings, entitlements, pending = initial_cash, {}, {}, []
    daily, orders, events = [], [], []
    record_dates = defaultdict(list)
    ex_dates = defaultdict(list)
    for a in actions:
        record_dates[a.record_day].append(a)
        ex_dates[a.ex_day].append(a)

    def quote(day, name):
        if (day, name) not in quotes:
            raise ValueError("missing raw mark/execution quote")
        return quotes[day, name]

    def fill(day, name, side, qty):
        nonlocal cash
        bar = quote(day, name)
        reason = (
            "suspended"
            if bar.suspended
            else "buy_restricted"
            if side == "BUY" and not bar.buy_allowed
            else "sell_restricted"
            if side == "SELL" and not bar.sell_allowed
            else None
        )
        gross, fee = bar.open * qty, bar.open * qty * cost_rate
        if not reason:
            if side == "BUY":
                if gross + fee > cash:
                    raise ValueError("cash conservation failure")
                cash -= gross + fee
                holdings[name] = holdings.get(name, 0) + qty
            else:
                if qty > holdings.get(name, 0):
                    raise ValueError("position conservation failure")
                cash += gross - fee
                holdings[name] -= qty
                if not holdings[name]:
                    holdings.pop(name)
        orders.append(
            dict(
                session=str(day),
                instrument=name,
                side=side,
                quantity=qty,
                price=decimal_text(bar.open),
                gross=decimal_text(gross) if not reason else "0",
                fee=decimal_text(fee) if not reason else "0",
                reason=reason,
                status="REJECTED" if reason else "FILLED",
                cash_after=decimal_text(cash),
                holdings_after=dict(sorted(holdings.items())),
            )
        )

    def settle(day):
        nonlocal cash
        for item in pending[:]:
            if item["day"] <= day:
                cash += item["cash"]
                if item["shares"]:
                    name = item["instrument"]
                    holdings[name] = holdings.get(name, 0) + item["shares"]
                events.append(
                    dict(
                        session=str(day),
                        event_id=item["id"],
                        phase="SETTLED",
                        cash=decimal_text(item["cash"]),
                        shares=item["shares"],
                    )
                )
                pending.remove(item)

    for day in sessions:
        settle(day)
        for a in sorted(ex_dates[day], key=lambda a: a.event_id):
            n = (
                holdings.get(a.instrument_id, 0)
                if a.kind == "delisting"
                else entitlements.get(a.event_id, 0)
            )
            amount, shares = Decimal(0), 0
            if a.kind in {"cash_dividend", "delisting"}:
                amount = n * a.cash_per_share
            else:
                exact = n * a.shares_per_share
                if exact != exact.to_integral_value():
                    raise ValueError(
                        "fractional shares require explicit audited cash-in-lieu terms"
                    )
                shares = int(exact)
                if a.kind == "rights_issue":
                    debit = shares * a.subscription_price
                    if debit > cash:
                        raise ValueError(
                            "rights subscription lacks cash; no implicit financing or waiver"
                        )
                    cash -= debit
            if a.kind == "delisting":
                holdings.pop(a.instrument_id, None)
                if any(p["instrument"] == a.instrument_id and p["shares"] for p in pending):
                    raise ValueError("delisting with unsettled shares requires settlement terms")
            if n:
                pending.append(
                    dict(
                        id=a.event_id,
                        instrument=a.instrument_id,
                        day=a.settlement_day,
                        cash=amount,
                        shares=shares,
                    )
                )
                events.append(
                    dict(
                        session=str(day),
                        event_id=a.event_id,
                        phase="EX_DATE",
                        entitlement=n,
                        receivable=decimal_text(amount),
                        locked_shares=shares,
                    )
                )
        settle(day)
        signal = signals.get(day)
        if signal:
            weights = dict(signal.weights)
            names = sorted(set(holdings) | set(weights))
            locked = sum(
                p["cash"] + (p["shares"] * quote(day, p["instrument"]).open if p["shares"] else 0)
                for p in pending
            )
            # Receivables are NAV assets, never spendable buying power.
            equity = cash + locked + sum(q * quote(day, n).open for n, q in holdings.items())
            desired = {
                n: int(
                    (
                        equity
                        * weights.get(n, Decimal(0))
                        / quote(day, n).open
                        / quote(day, n).lot_size
                    ).to_integral_value(rounding=ROUND_FLOOR)
                )
                * quote(day, n).lot_size
                for n in names
            }
            for n in names:
                locked_shares = sum(p["shares"] for p in pending if p["instrument"] == n)
                desired[n] = max(0, desired[n] - locked_shares)
            for n in names:
                qty = holdings.get(n, 0) - desired[n]
                if qty > 0:
                    lot = quote(day, n).lot_size
                    odd = holdings[n] % lot
                    # A residual odd holding may be sold together; never create
                    # a new fractional lot by selling a partial regular lot.
                    qty = qty // lot * lot + (odd if odd and qty % lot >= odd else 0)
                    if qty:
                        fill(day, n, "SELL", qty)
            for n in names:
                qty = desired[n] - holdings.get(n, 0)
                if qty > 0:
                    b = quote(day, n)
                    qty = qty // b.lot_size * b.lot_size
                    if qty == 0:
                        continue
                    affordable = (
                        int(
                            (cash / (b.open * (1 + cost_rate)) / b.lot_size).to_integral_value(
                                rounding=ROUND_FLOOR
                            )
                        )
                        * b.lot_size
                    )
                    if affordable:
                        fill(day, n, "BUY", min(qty, affordable))
                    else:
                        orders.append(
                            dict(
                                session=str(day),
                                instrument=n,
                                side="BUY",
                                quantity=0,
                                status="REJECTED",
                                reason="insufficient_cash",
                                gross="0",
                                fee="0",
                            )
                        )
        for a in record_dates[day]:
            if any(p["instrument"] == a.instrument_id and p["shares"] for p in pending):
                raise ValueError(
                    "overlapping share entitlements require explicit registration terms"
                )
            entitlements[a.event_id] = holdings.get(a.instrument_id, 0)
        receivables = sum(
            (
                p["cash"] + (p["shares"] * quote(day, p["instrument"]).close if p["shares"] else 0)
                for p in pending
            ),
            Decimal(0),
        )
        equity = cash + receivables + sum(q * quote(day, n).close for n, q in holdings.items())
        if cash < 0 or equity <= 0 or any(q <= 0 for q in holdings.values()):
            raise ValueError("invalid ledger state")
        daily.append(
            dict(
                session=str(day),
                cash=decimal_text(cash),
                holdings=dict(sorted(holdings.items())),
                receivables=decimal_text(receivables),
                equity=decimal_text(equity),
                nav=decimal_text(equity / initial_cash),
            )
        )
    result = dict(
        schema_version="corporate-action-ledger-v1",
        initial_equity=decimal_text(initial_cash),
        initial_nav="1",
        daily=daily,
        orders=orders,
        events=events,
        pending=[dict(p, day=str(p["day"]), cash=decimal_text(p["cash"])) for p in pending],
    )
    result["input_hash"] = digest(
        dict(
            sessions=list(map(str, sessions)),
            bars=[
                b.model_dump(mode="json")
                for b in sorted(bars, key=lambda b: (b.session, b.instrument))
            ],
            targets=[t.model_dump(mode="json") for t in sorted(targets, key=lambda t: t.session)],
            actions=[a.model_dump(mode="json") for a in sorted(actions, key=lambda a: a.event_id)],
            initial_cash=decimal_text(initial_cash),
            cost_rate=decimal_text(cost_rate),
        )
    )
    result["output_hash"] = digest(result)
    return result
