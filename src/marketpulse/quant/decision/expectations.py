"""Bounded inverse perpetuity, with explicit units/assumptions; never a target price."""

from decimal import Decimal

from .contracts import ExpectationsReport


def unavailable(reason):
    return ExpectationsReport(
        status="UNAVAILABLE",
        comparison=reason,
        implied_value=None,
        sensitivity=(),
        assumptions=(),
        limitations=(reason,),
    )


def evaluate_expectations(data):
    if data.benchmark_kind != "NONE" and not data.benchmark_refs:
        return unavailable("缺少可核对的预期基准，不能判断超预期。")
    values, fixed = dict(data.valuation_inputs), dict(data.fixed_assumptions)
    comparison = None
    if data.benchmark_kind != "NONE" and data.benchmark_refs and data.input_evidence_ids:
        if {"observed_value", "benchmark_value"} <= values.keys():
            difference = values["observed_value"] - values["benchmark_value"]
            label = {
                "GUIDANCE": "此前公司指引",
                "HISTORICAL_TREND": "同口径历史基准",
                "CONSENSUS": "提供的一致预期基准",
            }[data.benchmark_kind]
            relation = "高于" if difference > 0 else "低于" if difference < 0 else "等于"
            comparison = f"观察值{relation}{label}；差值={difference}（输入相同单位）。"
    if comparison and (
        data.solve_variable is None or not {"discount_rate", "growth"} <= fixed.keys()
    ):
        return ExpectationsReport(
            status="AVAILABLE",
            comparison=comparison,
            implied_value=None,
            sensitivity=(),
            assumptions=("基准与观察值必须同主体、同期间、同单位。",),
            limitations=(
                "历史基准不能冒充市场一致预期；比较不等于股价将上涨。",
                "未提供完整估值输入，逆向估值未启用。",
            ),
        )
    if not data.input_evidence_ids or not {"discount_rate", "growth"} <= fixed.keys():
        return unavailable("估值输入或固定假设依据不足，逆向估值未启用。")
    r, g = fixed["discount_rate"], fixed["growth"]
    if not Decimal("0") < r <= Decimal("1") or not Decimal("-0.5") <= g < r:
        return unavailable("折现率与永续增长边界不满足，拒绝求解。")
    if data.solve_variable != "cash_flow" or values.get("price", 0) <= 0:
        return unavailable("未接通该求解变量或缺少正的价值输入，不生成目标价。")
    # P=CF/(r-g); CF is next-period unlevered CF in the SAME units as P.
    price = values["price"]
    implied = price * (r - g)
    sensitivity = tuple(
        (f"discount_rate={rr}", price * (rr - g))
        for rr in (r - Decimal("0.01"), r + Decimal("0.01"))
        if rr > g and rr > 0
    )
    return ExpectationsReport(
        status="AVAILABLE",
        comparison=comparison or "给定永续模型和固定假设下，价值要求的下一期现金流。",
        implied_value=implied,
        sensitivity=sensitivity,
        assumptions=(f"折现率={r}", f"永续增长={g}", "价值与现金流使用相同单位；无期限目标价。"),
        limitations=(
            "这是条件求解，不能唯一识别市场真实预期；不是三个月目标价。",
            "未证明经营可达到该要求；实际研究必须另行核验输入引用和币种。",
        ),
    )
