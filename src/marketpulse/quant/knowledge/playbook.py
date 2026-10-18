"""Conservative conditional sizes; Decimal tools, never model-written numbers."""

from decimal import ROUND_FLOOR, Decimal

from .planning_contracts import Profile


def trading_unit(security_id):
    # Bound to the captured 2026 exchange rules by planning_service before use.
    if security_id.startswith("688") and security_id.endswith(".SH"):
        return dict(minimum=200, increment=1, maximum=100000, clause="SSE 6.7")
    return dict(
        minimum=100,
        increment=100,
        maximum=300000 if security_id.startswith(("300", "301")) else 1000000,
        clause="SZSE 3.3.8/3.3.9" if security_id.endswith(".SZ") else "SSE 3.3.8/3.3.9",
    )


def allocate_plans(candidates, profile):
    if profile is None:
        return dict(
            status="NO_PROFILE", plans=[], message="填写资金和持仓后才试算个性化金额与股数。"
        )
    profile = Profile.model_validate(profile)
    cash = profile.available_cash - profile.reserve_cash
    single, industry, routes = {}, {}, {}
    for h in profile.holdings:
        single[h.security_id] = single.get(h.security_id, Decimal(0)) + h.market_value
        industry[h.industry_code] = industry.get(h.industry_code, Decimal(0)) + h.market_value
        routes[h.route_id] = routes.get(h.route_id, Decimal(0)) + h.market_value
    initial_cash, plans = cash, []
    remaining_risk = profile.capital * profile.total_new_risk
    for candidate in candidates:
        if candidate["status"] != "COMPUTED":
            continue
        sid, ind = candidate["security_id"], candidate["industry_code"]
        route = next(r for r in candidate["routes"] if r["route_id"] == candidate["selected_route"])
        rid = route["route_id"]
        unit = trading_unit(sid)
        bearish = candidate.get("research_direction") == "UNFAVORABLE"
        if bearish or not route["budget_eligible"]:
            plans.append(
                dict(
                    security_id=sid,
                    route_id=rid,
                    shares=0,
                    reserved_cash="0",
                    reason="研究方向看空，暂不预留新增买入额度"
                    if bearish
                    else "路线不适用或风险距离无效",
                )
            )
            continue
        price, risk = Decimal(route["entry_reference"]), Decimal(route["risk_reference"])
        allocation = (
            profile.trend_allocation if rid == "TREND_SWING" else profile.quality_allocation
        )
        limits = dict(
            cash=cash,
            single=profile.capital * profile.single_cap - single.get(sid, 0),
            industry=profile.capital * profile.industry_cap - industry.get(ind, 0),
            route=profile.capital * allocation - routes.get(rid, 0),
            liquidity=Decimal(candidate["metrics"]["average_turnover_20"]["value"])
            * profile.liquidity_cap,
        )
        # Include two minimum commissions and round-trip proportional cost estimates.
        risk_cash = max(
            Decimal(0),
            min(profile.capital * profile.risk_per_trade, remaining_risk)
            - 2 * profile.minimum_commission,
        )
        per_share_risk = (
            price - risk + 2 * price * (profile.commission_rate + profile.other_cost_rate)
        )
        caps = {
            k: max(
                Decimal(0),
                (v - profile.minimum_commission)
                / (price * (1 + profile.commission_rate + profile.other_cost_rate)),
            )
            for k, v in limits.items()
        }
        caps["risk"] = risk_cash / per_share_risk
        cap = min(*caps.values(), Decimal(unit["maximum"]))
        shares = int(cap.to_integral_value(rounding=ROUND_FLOOR))
        shares = shares // unit["increment"] * unit["increment"]
        if shares < unit["minimum"]:
            shares = 0
        value = price * shares
        fee = (
            max(profile.minimum_commission, value * profile.commission_rate)
            + value * profile.other_cost_rate
            if shares
            else Decimal(0)
        )
        reserved = value + fee
        cash -= reserved
        estimated_risk = (
            shares * per_share_risk + 2 * profile.minimum_commission if shares else Decimal(0)
        )
        remaining_risk -= estimated_risk
        for mapping, key in ((single, sid), (industry, ind), (routes, rid)):
            mapping[key] = mapping.get(key, Decimal(0)) + value
        plans.append(
            dict(
                security_id=sid,
                route_id=rid,
                shares=shares,
                estimated_value=str(value),
                reserved_cash=str(reserved),
                estimated_cost=str(fee),
                risk_budget=str(profile.capital * profile.risk_per_trade),
                estimated_risk=str(estimated_risk),
                stress_scenarios=[
                    dict(
                        kind="ASSUMED_GAP_AND_UNABLE_TO_EXIT",
                        assumed_price_drop=str(drop),
                        estimated_loss=str(value * drop + 2 * fee),
                        note="假设跳空且不能退出的压力情景，不是预测、概率或涨跌停价格。",
                    )
                    for drop in (Decimal("0.1"), Decimal("0.2"))
                ],
                risk_reference=str(risk),
                entry_reference=str(price),
                unit=unit,
                binding_limit=min(caps, key=caps.get),
                limits={k: str(v) for k, v in limits.items()},
                conditional=route["status"] != "CONDITIONS_MET",
                reason="满足条件后仍需按当时价格重新试算"
                if shares
                else "当前约束下不足最小买入单位",
            )
        )
    return dict(
        status="CONDITIONAL_ESTIMATE",
        plans=plans,
        available_for_plans=str(initial_cash),
        reserved_cash=str(initial_cash - cash),
        remaining_cash=str(cash),
        remaining_new_risk_budget=str(remaining_risk),
        caveats=[
            "仅按已声明现金与持仓试算，按输入顺序预留条件额度；不是立即下单指令。",
            "费用是可修改的研究假设；风险参考不保证成交或最大损失，停牌、跳空、跌停和T+1仍有风险。",
            "两条路线是替代方案，每只股票只给当前周期所选路线预留额度。",
        ],
    )
