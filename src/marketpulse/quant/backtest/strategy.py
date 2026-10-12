"""Admitted historical package -> three independent ledgers and prototype report."""

import json
from decimal import Decimal, localcontext

from ..contracts import digest
from ..domain import decimal_text
from .accounting import replay_actions
from .ledger import Bar, Target
from .package import ActionEvent, DailyInput, DataPackage, Review, admit


def performance(dates, values, risk_free_annual, annual_sessions=252):
    """Percent output; sample daily std; geometric daily risk-free; ACT/365.25."""
    with localcontext() as ctx:
        ctx.prec = 40
        levels = [Decimal(v) for v in values]
        rf = Decimal(risk_free_annual)
        if (
            len(levels) != len(dates)
            or not levels
            or tuple(dates) != tuple(sorted(set(dates)))
            or any(not v.is_finite() or v <= 0 for v in levels)
        ):
            raise ValueError("positive finite dated levels required")
        if not rf.is_finite() or not Decimal("-.1") <= rf <= Decimal(".2"):
            raise ValueError("explicit bounded annual risk-free assumption required")
        returns = [b / a - 1 for a, b in zip(levels, levels[1:], strict=False)]
        elapsed = (dates[-1] - dates[0]).days
        total = levels[-1] / levels[0] - 1
        cagr = ((1 + total).ln() * Decimal("365.25") / elapsed).exp() - 1 if elapsed else None
        mean = sum(returns) / len(returns) if returns else None
        std = (
            (sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)).sqrt()
            if len(returns) > 1
            else None
        )
        daily_rf = ((1 + rf).ln() / annual_sessions).exp() - 1
        high, worst = levels[0], Decimal(0)
        for level in levels:
            high = max(high, level)
            worst = min(worst, level / high - 1)
        values = dict(
            total_ret=total * 100,
            cagr=cagr * 100 if cagr is not None else None,
            vol=std * Decimal(annual_sessions).sqrt() * 100 if std is not None else None,
            sharpe=(mean - daily_rf) / std * Decimal(annual_sessions).sqrt() if std else None,
            max_dd=worst * 100,
        )
        return {k: decimal_text(v) if v is not None else None for k, v in values.items()}


def execute(
    package, reviews, conditions, resolve, *, initial_cash, risk_free_annual, cost_pct_per_side
):
    with localcontext() as ctx:
        ctx.prec = 40
        return _execute(
            package, reviews, conditions, resolve, initial_cash, risk_free_annual, cost_pct_per_side
        )


def _execute(
    package, reviews, conditions, resolve, initial_cash, risk_free_annual, cost_pct_per_side
):
    package = DataPackage.model_validate(package)
    reviews = tuple(Review.model_validate(r) for r in reviews)
    admission = admit(package, reviews, conditions, resolve)
    result = dict(
        schema_version="backtest-strategy-v1",
        admission=admission,
        conditions=dict(conditions, cost_pct_per_side=str(cost_pct_per_side)),
        execution_status="BLOCKED",
        metric_status="UNVERIFIED",
        sources=[],
        notes=["历史样本存在选择与过拟合风险，不预示未来收益。"],
        meta=dict(
            date_start=str(package.start),
            date_end=str(package.end),
            period=f"{package.start} ~ {package.end}",
            universe="审核包所声明的沪深A股历史范围",
        ),
    )
    if admission["status"] != "READY_FOR_5_3":
        return result
    bars, actions = [], []
    # Validate the full universe above, materialize execution prices only for
    # securities selected in at least one period. Do not load unheld market bars.
    selected_ids = {
        c["instrument_id"]
        for a in admission["annual"]
        for c in a["selection"]["candidates"]
        if c["status"] == "ELIGIBLE"
    }
    for p in package.daily:
        if p.instrument_id not in selected_ids:
            continue
        for raw in json.loads(resolve(p.rows_hash)):
            r = DailyInput.model_validate(raw)
            bars.append(
                Bar(
                    session=r.session,
                    instrument=r.instrument_id,
                    open=r.open or r.close,
                    close=r.close,
                    lot_size=r.lot_size,
                    buy_allowed=r.buy_allowed,
                    sell_allowed=r.sell_allowed,
                    suspended=r.suspended,
                )
            )
    for p in package.actions:
        if p.instrument_id not in selected_ids:
            continue
        actions.extend(ActionEvent.model_validate(r) for r in json.loads(resolve(p.events_hash)))
    targets, trades = [], []
    quote_map = {(b.session, b.instrument): b for b in bars}
    for a, selection in zip(package.annual, admission["annual"], strict=True):
        chosen = [c for c in selection["selection"]["candidates"] if c["status"] == "ELIGIBLE"]
        n = len(chosen)
        targets.append(
            Target(
                session=a.execution_day,
                known_on=a.signal_day,
                weights=tuple(
                    (
                        c["instrument_id"],
                        (Decimal(1) / n).quantize(Decimal("1e-36"), rounding="ROUND_FLOOR"),
                    )
                    for c in chosen
                ),
            )
        )
        stocks = []
        for c in chosen:
            security = next(s for s in package.securities if s.instrument_id == c["instrument_id"])
            alias = next(
                x
                for x in security.aliases
                if x.valid_from <= a.signal_day
                and (x.valid_to is None or a.signal_day <= x.valid_to)
            )
            m = c["metrics"]
            stocks.append(
                dict(
                    code=alias.code,
                    name="名称未接入",
                    instrument_id=c["instrument_id"],
                    pe=m["pe"],
                    roe=decimal_text(Decimal(m["roe"]) * 100),
                    rev_yoy=decimal_text(Decimal(m["revenue_growth"]) * 100),
                    buy_price=decimal_text(quote_map[a.execution_day, c["instrument_id"]].open),
                    exec_day=str(a.execution_day),
                    source_hashes=c["source_hashes"],
                )
            )
        trades.append(
            dict(year=a.year, report_date=None, signal_day=str(a.signal_day), stocks=stocks)
        )
    rates = dict(
        strategy=Decimal(str(cost_pct_per_side)) / 100,
        strategy_low_cost=Decimal(0),
        strategy_high_cost=Decimal(".003"),
    )
    ledgers = {
        key: replay_actions(
            sessions=package.sessions,
            bars=bars,
            targets=targets,
            actions=actions,
            initial_cash=initial_cash,
            cost_rate=rate,
        )
        for key, rate in rates.items()
    }
    primary = ledgers["strategy"]
    # The first session is a pre-trade baseline. Reject packages starting at execution.
    if primary["daily"][0]["nav"] != "1":
        raise ValueError(
            "package must include an initial cash baseline before first signal execution"
        )
    benchmark = next(b for b in package.benchmarks if b.basis == "total_return_index")
    price_benchmark = next(b for b in package.benchmarks if b.basis == "price_index")
    levels = [v / benchmark.points[0][1] for _, v in benchmark.points]
    nav = dict(
        dates=list(map(str, package.sessions)),
        benchmark=list(map(decimal_text, levels)),
        benchmark_price=[
            decimal_text(v / price_benchmark.points[0][1]) for _, v in price_benchmark.points
        ],
    )
    nav.update({k: [r["nav"] for r in ledger["daily"]] for k, ledger in ledgers.items()})
    metrics = performance(package.sessions, nav["strategy"], risk_free_annual)
    bench_metrics = performance(package.sessions, nav["benchmark"], risk_free_annual)
    annual = annual_returns(package.sessions, nav, primary["daily"])
    result.update(
        execution_status="COMPLETED",
        check_status="COMPLETED",
        ledgers=ledgers,
        nav=nav,
        metrics=metrics,
        benchmark_metrics=bench_metrics,
        excess_metrics={
            k: decimal_text(Decimal(metrics[k]) - Decimal(bench_metrics[k]))
            if metrics[k] is not None and bench_metrics[k] is not None
            else None
            for k in metrics
        },
        annual=annual,
        trades=trades,
        win_years=sum(Decimal(a["excess"]) > 0 for a in annual),
        total_years=len(annual),
        assumptions=dict(
            initial_cash=str(initial_cash),
            risk_free_annual=str(risk_free_annual),
            annual_sessions=252,
            rights_policy="全额现金认购；资金不足／碎股条款不明时拒绝执行",
            dividend_policy="登记日收盘确权，除息日应收、支付日入现金，不自动再投；按输入现金额，未重建持有期差异税额",
        ),
        notes=result["notes"]
        + [
            "策略含现金分红及明确公司行为，基准为H00300全收益；000300价格指数另存，不混用。",
            "三种成本各自从初始现金重算；整数手数与拒单可使成本曲线次序变化，不强制单调。",
            "信号日15:00前可得版本选股，下一交易日开盘执行；停牌和方向限制保留拒单，不自动补单。",
            "CAGR按实际日数/365.25；波动为日收益样本标准差×√252；Sharpe使用显式年无风险假设。",
            "年度收益以前一年最后交易日为基点；初始年以起始现金基点计算。",
            "计算可复现及输入准入通过不等同策略指标已由外部独立引擎核验；指标保持UNVERIFIED。",
        ],
    )
    result["input_hash"] = digest(
        dict(
            package_hash=admission["package_hash"],
            review_hash=admission["review_hash"],
            conditions=conditions,
            initial_cash=str(initial_cash),
            risk_free_annual=str(risk_free_annual),
            cost_pct_per_side=str(cost_pct_per_side),
        )
    )
    result["output_hash"] = digest(result)
    return result


def annual_returns(dates, nav, daily):
    """Prior year closing baseline; initial range starts at initial equity."""
    annual = []
    for year in sorted({d.year for d in dates}):
        indices = [i for i, d in enumerate(dates) if d.year == year]
        first, last = max(0, indices[0] - 1), indices[-1]
        ret = (Decimal(nav["strategy"][last]) / Decimal(nav["strategy"][first]) - 1) * 100
        br = (Decimal(nav["benchmark"][last]) / Decimal(nav["benchmark"][first]) - 1) * 100
        annual.append(
            dict(
                year=year,
                strategy=decimal_text(ret),
                benchmark=decimal_text(br),
                excess=decimal_text(ret - br),
                n_stocks=len(daily[last]["holdings"]),
                baseline_date=nav["dates"][first],
                end_date=nav["dates"][last],
            )
        )
    return annual
