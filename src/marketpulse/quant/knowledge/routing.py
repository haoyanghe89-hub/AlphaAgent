"""Versioned executable adaptations, distinct from source-author claims."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .features import number

VERSION = "alpha-routes-v1"
CATALOG = [
    dict(
        route_id="TREND_SWING",
        title="趋势波段",
        status="RESEARCH_EXECUTABLE",
        required_inputs=["复权日线", "沪深300", "交易日历", "成交额", "行业归属"],
    ),
    dict(
        route_id="QUALITY_GROWTH",
        title="质量成长",
        status="RESEARCH_EXECUTABLE",
        required_inputs=["同口径非金融合并财报", "公告日期", "复权日线", "沪深300"],
    ),
    dict(
        route_id="VALUE_EXPECTATIONS",
        title="价值与预期差",
        status="REFERENCE_ONLY",
        required_inputs=["经营驱动与估值情景", "市场隐含预期", "资本成本与终值边界", "风险与反证"],
    ),
    dict(
        route_id="POLICY_EVENT",
        title="政策与事件",
        status="REFERENCE_ONLY",
        required_inputs=["政策原文与发布时间", "生效时间", "公司业务关联", "兑现路径与反证"],
    ),
]
RULE_IDS = (
    "KR-TREND-MARKET",
    "KR-TREND-ENTRY-DISCIPLINE",
    "KR-CAPACITY-LIQUIDITY",
    "KR-AVOID-RUIN",
    "KR-QUALITY-ECONOMICS",
    "KR-GROWTH-RETURN",
    "KR-EXPECTATION-EXIT",
    "KR-DCF-DISTRESS",
)
PARAMETERS = dict(
    origin="SYSTEM_RESEARCH_ASSUMPTION",
    validation="UNVALIDATED",
    ma_sessions=[20, 60],
    breakout_sessions=20,
    cash_conversion_min="1",
    turnover_ratio_min="1",
    relative_return_min="0",
)


def evaluate_routes(feature, knowledge_versions, horizon="SWING"):
    if feature["status"] != "COMPUTED":
        return dict(**feature, routes=[], action="补齐行情后再试算")
    metrics = feature["metrics"]

    def v(key):
        raw = metrics.get(key, {}).get("value")
        return number(raw) if raw is not None else None

    def condition(label, passed, observed):
        return dict(label=label, passed=bool(passed), observed=observed)

    close, high, low = v("close"), v("prior_high_20"), v("low_20")
    common = [
        condition(
            "市场未处于均线下行状态",
            feature["market_regime"] != "DOWNTREND",
            feature["market_regime"],
        ),
        condition("股价站上20日和60日均线", close > v("ma_20") > v("ma_60"), str(close)),
    ]
    trend = common + [
        condition("突破前20个交易日最高价", close > high, str(high)),
        condition("20日表现强于沪深300", v("relative_return_20") > 0, str(v("relative_return_20"))),
        condition(
            "近5日成交额强度不低于20日均值",
            v("turnover_ratio_5_20") is not None and v("turnover_ratio_5_20") >= 1,
            str(v("turnover_ratio_5_20")),
        ),
    ]
    financials = feature["financials"]
    fm = financials["metrics"]

    def positive(key):
        raw = fm.get(key, {}).get("value")
        return raw is not None and number(raw) > 0

    cash = fm.get("cash_conversion", {}).get("value")
    quality = common + [
        condition(
            "收入与净利润TTM同比均为正增长",
            positive("revenue_growth_ttm") and positive("profit_growth_ttm"),
            financials["period"],
        ),
        condition(
            "经营现金流为正且覆盖合并净利润",
            positive("operating_cashflow_ttm") and cash is not None and number(cash) >= 1,
            cash,
        ),
    ]
    result = []
    for route_id, title, conditions in (
        ("TREND_SWING", "趋势波段", trend),
        ("QUALITY_GROWTH", "质量成长", quality),
    ):
        eligible = (
            route_id != "QUALITY_GROWTH"
            or financials["scope"] == "NON_FINANCIAL_CONSOLIDATED"
            and all(
                fm.get(k, {}).get("value") is not None
                for k in ("revenue_growth_ttm", "profit_growth_ttm", "cash_conversion")
            )
        )
        entry = max(close, high) if route_id == "TREND_SWING" else max(close, v("ma_60"))
        entry = entry.quantize(Decimal(".01"), rounding=ROUND_CEILING)
        risk = low.quantize(Decimal(".01"), rounding=ROUND_FLOOR)
        valid_risk = 0 < risk < entry
        ready = eligible and valid_risk and all(c["passed"] for c in conditions)
        result.append(
            dict(
                route_id=route_id,
                title=title,
                eligible=eligible,
                status="CONDITIONS_MET"
                if ready
                else "WAIT_TRIGGER"
                if eligible
                else "NOT_APPLICABLE",
                action="条件已满足，先核对风险预算"
                if ready
                else "等待条件，不追涨"
                if eligible
                else "该路线输入或口径不适用",
                conditions=conditions,
                entry_reference=str(entry),
                risk_reference=str(risk) if valid_risk else None,
                invalidation="跌破20日低点时重新评估；趋势失效不能改成长线补仓理由。"
                if route_id == "TREND_SWING"
                else "增长或现金回收恶化时复核；价格跌破风险参考也需重新评估。",
                budget_eligible=eligible and valid_risk,
                knowledge_versions=knowledge_versions,
            )
        )
    preferred = "TREND_SWING" if horizon == "SWING" else "QUALITY_GROWTH"
    primary = next((r for r in result if r["route_id"] == preferred and r["eligible"]), result[0])
    return dict(
        **feature,
        routes=result,
        selected_route=primary["route_id"],
        action=primary["action"],
        route_version=VERSION,
        parameters=PARAMETERS,
    )
