"""Decimal research features over sealed provider rows, never live model arithmetic."""

from decimal import Decimal, localcontext

VERSION = "investment-features-v1"


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("nonfinite numeric input")
    return result


def growth(current, previous):
    return (
        current / previous - 1
        if current is not None and previous is not None and previous > 0
        else None
    )


def ttm(values, period):
    if period not in values:
        return None
    if period.endswith("1231"):
        return values[period]
    prior = str(int(period[:4]) - 1)
    annual, same = prior + "1231", prior + period[4:]
    if annual not in values or same not in values:
        return None
    return values[period] + values[annual] - values[same]


def series(rows, security_id, cutoff):
    result = {}
    for row in rows:
        day = row["trade_date"]
        if row.get("ts_code") != security_id or day > cutoff or day in result:
            raise ValueError("mixed security, duplicate or future daily input")
        result[day] = row
    return result


def point(value, unit, formula, window, inputs, *, reason=None):
    return dict(
        value=str(value) if value is not None else None,
        unit=unit,
        formula=formula,
        window=window,
        inputs=inputs,
        status="COMPUTED" if value is not None else "MISSING",
        reason=reason if value is None else None,
    )


def statements(rows, security_id, cutoff, fields):
    """Unknown announcement time: use only dates strictly before market cutoff."""
    by_period = {}
    for index, row in enumerate(rows):
        dates = [str(row[k]) for k in ("ann_date", "f_ann_date") if row.get(k)]
        if (
            row.get("ts_code") != security_id
            or str(row.get("report_type")) != "1"
            or str(row.get("comp_type")) != "1"
            or not dates
            or max(dates) >= cutoff
            or str(row["end_date"]) > cutoff
        ):
            continue
        period = row["end_date"]
        if period[4:] not in ("0331", "0630", "0930", "1231"):
            continue
        values = {k: number(row[k]) if row.get(k) is not None else None for k in fields}
        key = max(dates)
        old = by_period.get(period)
        if old and old["available_date"] == key and old["values"] != values:
            raise ValueError("ambiguous same-vintage financial statement")
        if old is None or key > old["available_date"]:
            by_period[period] = dict(values=values, available_date=key, row_index=index)
    return by_period


def financial_features(stock, cutoff):
    if stock.get("financial_scope") == "FINANCIAL":
        return dict(scope="FINANCIAL_NOT_APPLICABLE", period=None, metrics={}, source_rows={})
    fields = dict(
        income=("total_revenue", "n_income", "n_income_attr_p"),
        cashflow=("n_cashflow_act",),
        balancesheet=("total_hldr_eqy_exc_min_int",),
    )
    records = {
        api: statements(stock.get("financials", {}).get(api, []), stock["ts_code"], cutoff, keys)
        for api, keys in fields.items()
    }
    common = set.intersection(*(set(rows) for rows in records.values()))
    if not common:
        return dict(scope="MISSING_CONSOLIDATED_INPUTS", period=None, metrics={}, source_rows={})
    period = max(common)
    previous = str(int(period[:4]) - 1) + period[4:]

    def values(api, key):
        return {
            p: r["values"][key] for p, r in records[api].items() if r["values"][key] is not None
        }

    revenue = values("income", "total_revenue")
    profit = values("income", "n_income")
    attributed = values("income", "n_income_attr_p")
    cashflow = values("cashflow", "n_cashflow_act")
    equity = values("balancesheet", "total_hldr_eqy_exc_min_int")
    rev, net, cfo = ttm(revenue, period), ttm(profit, period), ttm(cashflow, period)
    average_equity = (
        (equity[period] + equity[previous]) / 2 if period in equity and previous in equity else None
    )
    attr = ttm(attributed, period)
    data = dict(
        revenue_ttm=(rev, "CNY", "current_YTD+prior_annual-prior_same_YTD"),
        net_profit_ttm=(net, "CNY", "consolidated_profit_current_YTD+prior_annual-prior_same_YTD"),
        operating_cashflow_ttm=(cfo, "CNY", "CFO_current_YTD+prior_annual-prior_same_YTD"),
        revenue_growth_ttm=(
            growth(rev, ttm(revenue, previous)),
            "ratio",
            "current_TTM/prior_same_TTM-1;positive_base_only",
        ),
        profit_growth_ttm=(
            growth(net, ttm(profit, previous)),
            "ratio",
            "current_TTM/prior_same_TTM-1;positive_base_only",
        ),
        cash_conversion=(
            cfo / net if cfo is not None and net is not None and net > 0 else None,
            "ratio",
            "consolidated_CFO_TTM/consolidated_net_profit_TTM;positive_profit_only",
        ),
        roe_ttm=(
            attr / average_equity
            if attr is not None and average_equity is not None and average_equity > 0
            else None,
            "ratio",
            "attributable_profit_TTM/mean_attributable_equity_current_prior_same",
        ),
    )
    refs = {
        api: {
            p: dict(row_index=r["row_index"], available_date=r["available_date"])
            for p, r in rows.items()
        }
        for api, rows in records.items()
    }
    return dict(
        scope="NON_FINANCIAL_CONSOLIDATED",
        period=period,
        source_rows=refs,
        period_policy="LATEST_COMMON_PERIOD; newer partial statements are not substituted",
        metrics={
            k: point(
                v, unit, formula, period, list(fields), reason="missing_inputs_or_nonpositive_base"
            )
            for k, (v, unit, formula) in data.items()
        },
    )


def stock_features(packet, stock):
    cutoff, security_id = packet["market_date"], stock["ts_code"]
    exchange = "SSE" if security_id.endswith(".SH") else "SZSE"
    sessions = sorted(
        {
            r["cal_date"]
            for r in packet["calendars"][exchange]
            if str(r["is_open"]) == "1" and r["cal_date"] <= cutoff
        }
    )
    if len(sessions) < 61:
        raise ValueError("insufficient calendar sessions")
    daily = series(stock["daily"], security_id, cutoff)
    adjustments = series(stock["adjustments"], security_id, cutoff)
    benchmark = series(packet["benchmark"], packet["benchmark_id"], cutoff)
    for day in daily:
        if day not in adjustments or number(adjustments[day]["adj_factor"]) <= 0:
            raise ValueError("adjustment factor missing or invalid")
    if any(d not in daily or d not in benchmark for d in sessions[-61:]):
        raise ValueError("missing stock/benchmark session; no silent filling")
    factor = number(adjustments[cutoff]["adj_factor"])
    normalized = {}
    for day, row in daily.items():
        scale = number(adjustments[day]["adj_factor"]) / factor
        close, high, low = (number(row[k]) for k in ("close", "high", "low"))
        if not 0 < low <= close <= high or number(row["amount"]) < 0:
            raise ValueError("invalid price or turnover input")
        normalized[day] = dict(
            close=close * scale,
            high=high * scale,
            low=low * scale,
            amount=number(row["amount"]) * 1000,
        )
    rows = [normalized[d] for d in sessions[-61:]]
    closes = [r["close"] for r in rows]
    bench = [number(benchmark[d]["close"]) for d in sessions[-61:]]
    if any(v <= 0 for v in bench):
        raise ValueError("nonpositive benchmark price")
    ma20, ma60 = sum(closes[-20:]) / 20, sum(closes[-60:]) / 60
    ret = closes[-1] / closes[-21] - 1
    bench_ret = bench[-1] / bench[-21] - 1
    amount20 = sum(r["amount"] for r in rows[-20:]) / 20
    amount5 = sum(r["amount"] for r in rows[-5:]) / 5
    returns = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    mean = sum(returns) / 20
    volatility = (sum((r - mean) ** 2 for r in returns) / 19).sqrt() * Decimal(252).sqrt()
    window = dict(
        start=sessions[-21], end=cutoff, return_sessions=20, benchmark_id=packet["benchmark_id"]
    )
    inputs = ["daily", "adj_factor", "trade_cal", "index_daily"]
    rawmetrics = dict(
        close=(closes[-1], "CNY/share", "raw_close_at_last_factor_anchor"),
        ma_20=(ma20, "CNY/share", "mean(adjusted_close,last_20_sessions)"),
        ma_60=(ma60, "CNY/share", "mean(adjusted_close,last_60_sessions)"),
        return_20=(ret, "ratio", "adjusted_close[t]/adjusted_close[t-20]-1"),
        benchmark_return_20=(bench_ret, "ratio", "CSI300_close[t]/CSI300_close[t-20]-1"),
        relative_return_20=(ret - bench_ret, "ratio", "stock_return_20-CSI300_return_20"),
        annualized_volatility_20=(volatility, "ratio", "sample_std(simple_returns,20)*sqrt(252)"),
        turnover_ratio_5_20=(
            amount5 / amount20 if amount20 > 0 else None,
            "ratio",
            "mean(raw_CNY_turnover,5)/mean(raw_CNY_turnover,20)",
        ),
        average_turnover_20=(amount20, "CNY/session", "mean(provider_amount_thousand_CNY*1000,20)"),
        prior_high_20=(
            max(r["high"] for r in rows[-21:-1]),
            "CNY/share",
            "max(adjusted_high,prior_20_sessions)",
        ),
        low_20=(
            min(r["low"] for r in rows[-20:]),
            "CNY/share",
            "min(adjusted_low,last_20_sessions)",
        ),
    )
    lengths = dict(
        close=1, ma_20=20, ma_60=60, average_turnover_20=20, turnover_ratio_5_20=20, low_20=20
    )
    metrics = {}
    for k, (v, unit, formula) in rawmetrics.items():
        metric_window = dict(window, start=sessions[-lengths.get(k, 21)])
        if k == "prior_high_20":
            metric_window["end"] = sessions[-2]
        metrics[k] = point(v, unit, formula, metric_window, inputs, reason="zero_turnover")
    benchmark_ma20, benchmark_ma60 = sum(bench[-20:]) / 20, sum(bench[-60:]) / 60
    regime = (
        "UPTREND"
        if bench[-1] > benchmark_ma20 > benchmark_ma60
        else "DOWNTREND"
        if bench[-1] < benchmark_ma20 < benchmark_ma60
        else "RANGE"
    )
    memberships = [
        m
        for m in stock.get("industry_memberships", [])
        if m.get("in_date", "99999999") <= cutoff
        and (not m.get("out_date") or m["out_date"] > cutoff)
    ]
    industries = {(m["l1_code"], m["l1_name"]) for m in memberships}
    if len(industries) != 1:
        raise ValueError("unique current SW2021 level-one industry required")
    industry_code, industry_name = next(iter(industries))
    return dict(
        security_id=security_id,
        name=stock["name"],
        industry_code=industry_code,
        industry_name=industry_name,
        industry_standard="SW2021_L1",
        board=stock.get("market"),
        market_date=cutoff,
        status="COMPUTED",
        metrics=metrics,
        market_regime=regime,
        adjustment="PROVIDER_FACTOR_NORMALIZED_TO_LAST_SESSION",
        financials=financial_features(stock, cutoff),
        chart=[
            dict(date=d, close=str(normalized[d]["close"]), turnover=str(normalized[d]["amount"]))
            for d in sessions[-120:]
            if d in normalized
        ],
        caveats=[
            "复权按供应商因子计算，不代表独立核验的全收益。",
            "成交额强度为日频代理，不是盘中量比或主力资金净流入。",
            "市场状态为均线研究分类，不是收益保证。",
        ],
    )


def compute_features(packet, *, strict=True):
    with localcontext() as context:
        context.prec = 34
        results = []
        for stock in packet["stocks"]:
            try:
                results.append(stock_features(packet, stock))
            except ValueError as error:
                if strict:
                    raise
                results.append(
                    dict(
                        security_id=stock["ts_code"],
                        name=stock["name"],
                        status="BLOCKED_INPUTS",
                        reason=str(error),
                    )
                )
    return dict(feature_version=VERSION, market_date=packet["market_date"], stocks=results)
