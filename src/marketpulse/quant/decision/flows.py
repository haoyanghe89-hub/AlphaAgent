"""Exact, separate channel calculations. Incomplete windows never become zero."""

from decimal import Decimal, localcontext

from .contracts import FlowInputs, FlowMetric, FlowReport, FlowWindowReport, FlowWindowSpec
from .market import number

METHOD = "separate-flow-windows-2026-10-13-v1"
SERIES_UNITS = {
    "industry_turnover": "CNY",
    "market_turnover": "CNY",
    "vendor_estimate": "CNY",
    "margin_balance": "CNY",
    "margin_buy": "CNY",
    "margin_repay": "CNY",
    "etf_shares": "shares",
    "etf_nav": "CNY/share",
    "etf_split_factor": "ratio",
    "security_turnover": "CNY",
    "security_close": "CNY/share",
    "market_close": "CNY/share",
}
LIMITS = (
    "融资、大单估算和ETF是不同渠道，数值不相加；供应商数据未获得交易所逐值独立核验。",
    "成交额只是活跃度；融资变化不等于全市场净流入，不识别机构身份或动机。",
    "ETF观察样本为半导体主题基金，当前成分和权重未核定，不等于本股现金买入。",
    "价格比较使用未复权收盘价，与沪深三百同周期比较，不是全收益或事件因果贡献。",
    "行业成员、同范围市场分母和历史分类未核定，行业广度与成员集中度不参与判断。",
)


def index(points, unit):
    result = {}
    for p in points:
        if p.session in result or p.unit != unit:
            raise ValueError("duplicate flow session or mismatched unit")
        if p.value is not None:
            number(p.value, positive=unit in {"CNY/share", "ratio"}, nonnegative=unit == "shares")
        result[p.session] = p
    return result


def industry_observation(members, expected_members, market_amount, *, same_scope):
    """Single verified industry/date only; duplicates never inflate a numerator."""
    expected = set(expected_members)
    ids = [m["security_id"] for m in members]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate industry member")
    if not same_scope or not expected or set(ids) != expected or market_amount is None:
        return dict(share=None, breadth=None, concentration=None)
    amounts = [m.get("turnover") for m in members]
    if any(v is None or Decimal(v) < 0 for v in amounts) or Decimal(market_amount) <= 0:
        return dict(share=None, breadth=None, concentration=None)
    amounts = [Decimal(v) for v in amounts]
    total = sum(amounts)
    if total > Decimal(market_amount):
        raise ValueError("industry amount exceeds same-scope market denominator")
    valid = [m for m in members if m.get("reference") is not None and m.get("close") is not None]
    if any(Decimal(m["reference"]) <= 0 or Decimal(m["close"]) <= 0 for m in valid):
        raise ValueError("invalid breadth prices")
    return dict(
        share=total / Decimal(market_amount),
        breadth=Decimal(sum(Decimal(m["close"]) > Decimal(m["reference"]) for m in valid))
        / len(valid)
        if valid
        else None,
        concentration=sum(sorted(amounts, reverse=True)[:3]) / total if total else None,
        eligible=len(valid),
        excluded=len(members) - len(valid),
    )


def distinct_turnover(groups):
    """Overlapping concepts are not separate market turnover."""
    unique = {}
    for group in groups:
        for security, amount in group.items():
            value = Decimal(amount)
            if security in unique and unique[security] != value:
                raise ValueError("conflicting turnover for overlapping member")
            unique[security] = value
    return sum(unique.values(), Decimal(0))


def compute(inputs: FlowInputs, spec: FlowWindowSpec, subject_id: str):
    if inputs.definition_version != METHOD or spec.definition_version != METHOD:
        raise ValueError("unknown flow calculation definition")
    series = {key: getattr(inputs, key) for key in SERIES_UNITS}
    indexed = {key: index(points, SERIES_UNITS[key]) for key, points in series.items()}
    for key, points in indexed.items():
        if key != "vendor_estimate" and SERIES_UNITS[key] == "CNY":
            for point in points.values():
                if point.value is not None:
                    number(point.value, nonnegative=True)

    def available(key, days):
        return [
            indexed[key][d] for d in days if d in indexed[key] and indexed[key][d].value is not None
        ]

    with localcontext() as context:
        context.prec = 28

        def calculate_window(n):
            required = spec.sessions[-n:]
            prior = spec.sessions[-n - 1] if len(spec.sessions) > n else None
            full = required if len(required) == n else ()
            metrics = []

            def metric(key, label, unit, keys, days, count, calculate, formula, note):
                groups = [available(k, days) for k in keys]
                actual = min((len(g) for g in groups), default=0)
                refs = tuple(sorted({p.source_ref for g in groups for p in g}))
                value = calculate() if len(days) == count and actual == count else None
                m = FlowMetric(
                    metric_id=key,
                    label=label,
                    value=value,
                    unit=unit,
                    status="AVAILABLE" if value is not None else "UNAVAILABLE",
                    actual_count=actual,
                    expected_count=count,
                    source_refs=refs,
                    formula=formula,
                    note=note
                    if value is not None
                    else "交易日、字段或核定口径缺失；不补零、不缩短完整窗口。" + note,
                )
                metrics.append(m)
                return m

            def total(key):
                return sum((p.value for p in available(key, full)), Decimal(0))

            baseline_days = (prior, *full) if prior and full else ()
            margin = metric(
                "MARGIN_CHANGE",
                "融资余额变化",
                "CNY",
                ["margin_balance"],
                baseline_days,
                n + 1,
                lambda: (
                    indexed["margin_balance"][full[-1]].value
                    - indexed["margin_balance"][prior].value
                ),
                "末日融资余额－窗口前一交易日融资余额",
                "仅融资渠道；不是全市场净流入。",
            )
            metric(
                "MARGIN_BUY",
                "融资买入合计",
                "CNY",
                ["margin_buy"],
                full,
                n,
                lambda: total("margin_buy"),
                "逐日融资买入额合计",
                "金额为人民币元。",
            )
            metric(
                "MARGIN_REPAY",
                "融资偿还合计",
                "CNY",
                ["margin_repay"],
                full,
                n,
                lambda: total("margin_repay"),
                "逐日融资偿还额合计",
                "偿还包括券商申报口径中的调整。",
            )

            def reconciliation():
                days = list(baseline_days)
                return [
                    indexed["margin_balance"][b].value
                    - indexed["margin_balance"][a].value
                    - indexed["margin_buy"][b].value
                    + indexed["margin_repay"][b].value
                    for a, b in zip(days, days[1:], strict=False)
                ]

            for key, label, calculate, formula in (
                (
                    "MARGIN_RECONCILIATION",
                    "融资未解释勾稽差额",
                    lambda: sum(reconciliation()),
                    "余额差－同期（买入合计－偿还合计）",
                ),
                (
                    "MARGIN_MAX_RESIDUAL",
                    "最大单日勾稽差额绝对值",
                    lambda: max(abs(v) for v in reconciliation()),
                    "逐日余额差与买入减偿还之差的绝对值，取最大",
                ),
            ):
                metric(
                    key,
                    label,
                    "CNY",
                    ["margin_balance", "margin_buy", "margin_repay"],
                    baseline_days,
                    n + 1,
                    calculate,
                    formula,
                    "来源金额可能存在申报调整或不一致；保留差额不强行配平，不把余额差等同买入减偿还。",
                )
            metric(
                "MARGIN_INCREASE_RATIO",
                "融资余额增加日占比",
                "ratio",
                ["margin_balance"],
                baseline_days,
                n + 1,
                lambda: (
                    Decimal(
                        sum(
                            indexed["margin_balance"][b].value > indexed["margin_balance"][a].value
                            for a, b in zip(baseline_days, baseline_days[1:], strict=False)
                        )
                    )
                    / n
                    if margin.status == "AVAILABLE"
                    else None
                ),
                "窗口内余额较前一交易日增加的天数／固定交易日数",
                "不等于资金广度或未来概率。",
            )
            metric(
                "TURNOVER",
                "个股成交额合计",
                "CNY",
                ["security_turnover"],
                full,
                n,
                lambda: total("security_turnover"),
                "逐日成交额合计",
                "成交活跃度，不是净流入。",
            )
            metric(
                "VENDOR_NET",
                "供应商估算大单净额",
                "CNY",
                ["vendor_estimate"],
                full,
                n,
                lambda: total("vendor_estimate"),
                "同源同口径逐日分类净额合计",
                "供应商按订单大小分类估算，不识别机构身份。",
            )
            metric(
                "VENDOR_INTENSITY",
                "供应商估算强度",
                "ratio",
                ["vendor_estimate", "security_turnover"],
                full,
                n,
                lambda: (
                    total("vendor_estimate") / total("security_turnover")
                    if total("security_turnover") > 0
                    else None
                ),
                "供应商分类净额合计／同期本股成交额",
                "不是市场占比或真实主力净流入。",
            )
            price_values = {}
            for key, label in (
                ("security_close", "SECURITY_RETURN"),
                ("market_close", "MARKET_RETURN"),
            ):
                m = metric(
                    label,
                    "个股价格变化" if key == "security_close" else "沪深三百价格变化",
                    "ratio",
                    [key],
                    baseline_days,
                    n + 1,
                    lambda k=key: indexed[k][full[-1]].value / indexed[k][prior].value - 1,
                    "末日未复权收盘价／窗口前一日收盘价－一",
                    "同窗口未复权价格变化，不是全收益。",
                )
                price_values[key] = m.value
            metric(
                "RELATIVE_RETURN",
                "相对沪深三百价格差",
                "ratio",
                ["security_close", "market_close"],
                baseline_days,
                n + 1,
                lambda: price_values["security_close"] - price_values["market_close"],
                "个股同期价格变化－沪深三百同期价格变化",
                "相对表现，不是因果贡献。",
            )

            def etf_delta():
                days = list(baseline_days)
                return [
                    (
                        b,
                        indexed["etf_shares"][b].value
                        - indexed["etf_shares"][a].value * indexed["etf_split_factor"][b].value,
                    )
                    for a, b in zip(days, days[1:], strict=False)
                ]

            def adjusted_change():
                # Express each daily delta in end-date share units after later splits.
                deltas = etf_delta()
                result, factor = Decimal(0), Decimal(1)
                for day, delta in reversed(deltas):
                    result += delta * factor
                    factor *= indexed["etf_split_factor"][day].value
                return result

            etf_keys = ["etf_shares", "etf_split_factor"]
            metric(
                "ETF_CHANGE",
                "ETF调整后份额变化",
                "shares",
                etf_keys,
                baseline_days,
                n + 1,
                adjusted_change,
                "逐日调整后份额差按后续拆并因子换算为末日份额单位，再合计",
                "拆并因子须有逐日确认；未知事件不估算。",
            )
            metric(
                "ETF_ESTIMATE",
                "ETF估算申赎额",
                "CNY",
                [*etf_keys, "etf_nav"],
                baseline_days,
                n + 1,
                lambda: sum(v * indexed["etf_nav"][d].value for d, v in etf_delta()),
                "逐日调整后份额差×同日单位净值，再合计",
                "估算，不等于成分股现金买入，实物申赎与套利可能参与。",
            )

            def industry_share():
                if inputs.membership_ref.startswith("UNAVAILABLE"):
                    return None
                ratios = []
                for d in full:
                    numerator = indexed["industry_turnover"][d].value
                    denominator = indexed["market_turnover"][d].value
                    if denominator <= 0 or numerator > denominator:
                        raise ValueError("invalid same-scope industry denominator")
                    ratios.append(numerator / denominator)
                return sum(ratios) / n

            metric(
                "INDUSTRY_SHARE",
                "行业平均成交占比",
                "ratio",
                ["industry_turnover", "market_turnover"],
                full,
                n,
                industry_share,
                "各日同范围行业成交额／市场成交额，再取平均",
                "需要核定分类、成员与市场分母，不能用个股成交额代替行业。",
            )
            for key, label in (
                ("INDUSTRY_BREADTH", "行业上涨覆盖"),
                ("INDUSTRY_CONCENTRATION", "行业成员成交集中度"),
            ):
                metric(
                    key,
                    label,
                    "ratio",
                    [],
                    (),
                    n,
                    lambda: None,
                    "核定成员范围内逐日计算，缺成员不缩小分母",
                    "当前未核定行业成员与历史成分，保持缺项。",
                )
            price = price_values["security_close"]
            if price is None or margin.value is None:
                divergence = "UNAVAILABLE"
            elif price > 0 and margin.value < 0:
                divergence = "PRICE_UP_MARGIN_DOWN"
            elif price < 0 and margin.value > 0:
                divergence = "PRICE_DOWN_MARGIN_UP"
            elif price > 0 and margin.value > 0:
                divergence = "ALIGNED_UP"
            elif price < 0 and margin.value < 0:
                divergence = "ALIGNED_DOWN"
            else:
                divergence = "MIXED"
            return FlowWindowReport(
                window=n,
                sessions=required,
                baseline_date=prior,
                metrics=metrics,
                divergence=divergence,
            )

        windows = [calculate_window(n) for n in spec.windows]
    return FlowReport(
        method_version=METHOD,
        subject_id=subject_id,
        end_date=spec.end_date,
        status="PARTIAL"
        if any(m.value is not None for w in windows for m in w.metrics)
        else "UNAVAILABLE",
        windows=windows,
        series=series,
        limitations=LIMITS,
    )
