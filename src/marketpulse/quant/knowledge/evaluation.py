"""Forward-only price proxies and condition observations, never trade performance."""

import copy
from datetime import datetime
from decimal import localcontext

from ..contracts import digest
from .features import compute_features, number, series
from .inputs import SHANGHAI
from .routing import evaluate_routes

VERSION = "investment-forward-v1"


def protocol(state):
    request = state["request"]
    profile = request.get("profile")
    capital = number(profile["capital"]) if profile else None
    return dict(
        version=VERSION,
        run_id=state["run_id"],
        registered_at=state["created_at"],
        request_digest=digest(request),
        entities=request["entities"],
        horizon=request["horizon"],
        horizon_sessions=request["horizon_sessions"],
        start_after=datetime.fromisoformat(state["created_at"])
        .astimezone(SHANGHAI)
        .strftime("%Y%m%d"),
        profile_group="NO_PROFILE"
        if not profile
        else "SYNTHETIC"
        if profile.get("input_origin") == "SYNTHETIC_ACCEPTANCE"
        else "SMALL"
        if capital < 100000
        else "MEDIUM"
        if capital < 1000000
        else "LARGE",
        costs={
            k: profile[k] if profile else v
            for k, v in dict(
                commission_rate="0.0003", minimum_commission="5", other_cost_rate="0.001"
            ).items()
        },
        direction_rule="sign(target_close*target_factor/anchor_factor/anchor_close-1);flat_is_separate",
        target_rule="Nth_exchange_open_strictly_after_registration_local_date;no_shortening",
        baselines=["ALWAYS_UP", "ALWAYS_DOWN", "PAST_20_SESSION_SIGN"],
        condition_arms=["NO_METHOD_PRICE_ABOVE_MA20", "FIXED_TREND_SWING", "FROZEN_SELECTED_ROUTE"],
        comparison_scope="DETERMINISTIC_CONDITIONS_ONLY;NOT_MATCHED_LLM_KNOWLEDGE_ABLATION",
        missing_rule="missing_not_zero;no_forward_fill;retain_unscorable_and_failures",
        suspension_rule="full_day_S_without_timing_is_suspended;no_fictitious_trade;horizon_not_extended",
        simulation="NOT_SIMULATED;original_5.2_admission_and_independent_total_return_required",
        knowledge_updates="candidate_only;no_auto_tuning_or_publication",
    )


def open_days(rows, exchange, after, through=None):
    seen, opened = set(), []
    for row in rows:
        day = row["cal_date"]
        datetime.strptime(day, "%Y%m%d")
        if (
            day in seen
            or row.get("exchange", exchange) != exchange
            or str(row["is_open"]) not in {"0", "1"}
        ):
            raise ValueError("交易日历身份、日期或开闭市记录无效")
        seen.add(day)
        if str(row["is_open"]) == "1" and day > after and (through is None or day <= through):
            opened.append(day)
    return sorted(opened)


def evaluate(report, initial, snapshot):
    with localcontext() as ctx:
        ctx.prec = 34
        return _evaluate(report, initial, snapshot)


def _evaluate(report, initial, snapshot):
    spec = report["forward_protocol"]
    if spec["version"] != VERSION:
        raise ValueError("未知前瞻协议")
    issued = datetime.fromisoformat(report["issued_at"])
    asof = snapshot["through"]
    opinions = {o["security_id"]: o for o in report["research_opinions"]}
    stocks = {s["ts_code"]: s for s in initial["stocks"]}
    future = {s["security_id"]: s for s in snapshot["stocks"]}
    result = []
    for candidate in report["candidates"]:
        sid = candidate["security_id"]
        exchange = "SSE" if sid.endswith(".SH") else "SZSE"
        declared = open_days(spec["calendars"].get(exchange, []), exchange, spec["start_after"])[
            : spec["horizon_sessions"]
        ]
        actual = open_days(
            snapshot["calendars"].get(exchange, []), exchange, spec["start_after"], asof
        )[: spec["horizon_sessions"]]
        target = (
            declared[-1]
            if len(declared) == spec["horizon_sessions"]
            else actual[-1]
            if len(actual) == spec["horizon_sessions"]
            else None
        )
        days = [d for d in actual if target is None or d <= target]
        opinion = opinions[sid]
        view = dict(
            security_id=sid,
            name=candidate["name"],
            route_id=candidate["selected_route"],
            profile_group=spec["profile_group"],
            horizon_sessions=spec["horizon_sessions"],
            target_date=target,
            observed_sessions=len(days),
            status="PENDING",
            actual_direction=None,
            correct=None,
            price_return=None,
            baseline_correct=None,
            points=[],
            signals=[],
            first_trigger={},
            counterarguments=[
                dict(claim_id=c["claim_id"], text=c["text"], status="EVIDENCE_REVIEW_REQUIRED")
                for c in opinion["opposing"]
            ],
            invalidation=[
                dict(text=text, status="EVIDENCE_REVIEW_REQUIRED")
                for text in opinion["invalidation"]
            ],
            simulation=dict(status="NOT_SIMULATED", trades=0, return_after_cost=None),
        )
        first = declared[0] if declared else actual[0] if actual else None
        if first and issued >= datetime.strptime(first + "0930", "%Y%m%d%H%M").replace(
            tzinfo=SHANGHAI
        ):
            view["status"] = "LATE_ISSUE_UNSCORABLE"
            result.append(view)
            continue
        if declared and [d for d in declared if d <= asof] != actual:
            view["status"] = "CALENDAR_CHANGED_UNSCORABLE"
            result.append(view)
            continue
        original = stocks[sid]
        update = future.get(sid, dict(daily=[], adjustments=[], suspensions=[]))
        rows = series(update["daily"], sid, asof)
        factors = series(update["adjustments"], sid, asof)
        anchor = initial["market_date"]
        suspensions = set()
        for row in update["suspensions"]:
            if row.get("ts_code") != sid or row["trade_date"] > asof:
                raise ValueError("停牌记录身份或日期不一致")
            if row.get("suspend_type") == "S" and not row.get("suspend_timing"):
                suspensions.add(row["trade_date"])
        anchor_factor = factors.get(anchor, {}).get("adj_factor")
        old_factor = series(original["adjustments"], sid, anchor)[anchor]["adj_factor"]
        anchor_price = number(candidate["metrics"]["close"]["value"])
        valid_anchor = anchor_factor is not None and number(anchor_factor) > 0
        for day in days:
            suspended = day in suspensions
            close = factor = None
            if day in rows and day in factors and not suspended and valid_anchor:
                row = rows[day]
                close, factor = (number(row["close"]), number(factors[day]["adj_factor"]))
                if not 0 < number(row["low"]) <= close <= number(row["high"]) or factor <= 0:
                    raise ValueError("观察行情价格或复权因子无效")
            price = close * factor / number(anchor_factor) if close is not None else None
            view["points"].append(
                dict(
                    date=day,
                    price=str(price) if price is not None else None,
                    status="SUSPENDED"
                    if suspended
                    else "OBSERVED"
                    if price is not None
                    else "MISSING",
                )
            )
            signal = dict(
                date=day,
                status="SUSPENDED" if suspended else "MISSING",
                arms=None,
                risk_reference_breached=None,
            )
            if price is not None:
                rolling = copy.deepcopy(initial)
                rolling["market_date"] = day
                rolling["calendars"] = snapshot["calendars"]
                rolling["benchmark"] = [
                    r for r in initial["benchmark"] if r["trade_date"] <= anchor
                ] + [r for r in snapshot["benchmark"] if anchor < r["trade_date"] <= day]
                stock = copy.deepcopy(original)
                stock["daily"] = [r for r in original["daily"] if r["trade_date"] <= anchor] + [
                    r for r in update["daily"] if anchor < r["trade_date"] <= day
                ]
                stock["adjustments"] = [
                    dict(
                        r,
                        adj_factor=str(
                            number(r["adj_factor"]) / number(old_factor) * number(anchor_factor)
                        ),
                    )
                    for r in original["adjustments"]
                    if r["trade_date"] <= anchor
                ] + [r for r in update["adjustments"] if anchor < r["trade_date"] <= day]
                rolling["stocks"] = [stock]
                feature = compute_features(rolling, strict=False)["stocks"][0]
                if feature["status"] == "COMPUTED":
                    # Only original financial vintage and industry membership can be used.
                    feature["financials"] = candidate["financials"]
                    routes = evaluate_routes(feature, report["knowledge"], spec["horizon"])[
                        "routes"
                    ]
                    ready = {r["route_id"]: r["status"] == "CONDITIONS_MET" for r in routes}
                    arms = dict(
                        NO_METHOD_PRICE_ABOVE_MA20=number(feature["metrics"]["close"]["value"])
                        > number(feature["metrics"]["ma_20"]["value"]),
                        FIXED_TREND_SWING=ready["TREND_SWING"],
                        FROZEN_SELECTED_ROUTE=ready[candidate["selected_route"]],
                    )
                    selected = next(
                        r
                        for r in candidate["routes"]
                        if r["route_id"] == candidate["selected_route"]
                    )
                    risk = selected["risk_reference"]
                    signal.update(
                        status="OBSERVED",
                        arms=arms,
                        risk_reference_breached=price <= number(risk) if risk is not None else None,
                    )
                    for arm, triggered in arms.items():
                        if triggered:
                            view["first_trigger"].setdefault(arm, day)
                else:
                    signal["status"] = "WINDOW_INCOMPLETE"
            view["signals"].append(signal)
        matured = target is not None and asof >= target
        if matured:
            gaps = [p for p in view["points"] if p["status"] != "OBSERVED"]
            if gaps or not view["points"]:
                view["status"] = (
                    "SUSPENDED_UNSCORABLE"
                    if gaps and all(p["status"] == "SUSPENDED" for p in gaps)
                    else "DATA_MISSING_UNSCORABLE"
                )
            else:
                value = number(view["points"][-1]["price"]) / anchor_price - 1
                direction = "UP" if value > 0 else "DOWN" if value < 0 else "FLAT"
                predicted = "UP" if opinion["direction"] == "FAVORABLE" else "DOWN"
                past = number(candidate["metrics"]["return_20"]["value"])
                view.update(
                    status="MATURED",
                    actual_direction=direction,
                    correct=direction == predicted,
                    price_return=str(value),
                    baseline_correct=dict(
                        ALWAYS_UP=direction == "UP",
                        ALWAYS_DOWN=direction == "DOWN",
                        PAST_20_SESSION_SIGN=direction
                        == ("UP" if past > 0 else "DOWN" if past < 0 else "FLAT"),
                    ),
                )
        elif not target:
            view["status"] = "PENDING_CALENDAR"
        result.append(view)
    return dict(
        version=VERSION,
        through=asof,
        stocks=result,
        limitations=[
            "方向按供应商复权价格代理核对，平盘单列；缺失或停牌保留且不计为正确。",
            "条件仅按收盘日频观察。质量财报及行业使用原版本，未证明后续财报仍有效；政策、资金和文字反证须另核新证据。",
            "固定路线、适配路线与无方法价格条件仅作功能对比，不是同输入有无知识模型实验，不能归因知识增益。",
            "没有模拟成交或收益；费用已冻结，旧5.2正式回测门禁不变。",
        ],
    )


def summarize(items):
    groups = {}
    for item in items:
        key = (item.get("route_id", "UNASSIGNED"), item["profile_group"], item["horizon_sessions"])
        group = groups.setdefault(
            key,
            dict(
                route_id=key[0],
                profile_group=key[1],
                horizon_sessions=key[2],
                registered=0,
                matured=0,
                correct=0,
                status_counts={},
                baseline_correct={
                    k: 0 for k in ("ALWAYS_UP", "ALWAYS_DOWN", "PAST_20_SESSION_SIGN")
                },
                condition_triggers={
                    k: 0
                    for k in (
                        "NO_METHOD_PRICE_ABOVE_MA20",
                        "FIXED_TREND_SWING",
                        "FROZEN_SELECTED_ROUTE",
                    )
                },
            ),
        )
        group["registered"] += 1
        status = item["status"]
        group["status_counts"][status] = group["status_counts"].get(status, 0) + 1
        if status == "MATURED":
            group["matured"] += 1
            group["correct"] += int(item["correct"])
            for k, v in item["baseline_correct"].items():
                group["baseline_correct"][k] += int(v)
        for k in item.get("first_trigger", {}):
            group["condition_triggers"][k] += 1
    for group in groups.values():
        n = group["matured"]
        group["accuracy"] = str(number(group["correct"]) / n) if n else None
        group["baseline_accuracy"] = {
            k: str(number(v) / n) if n else None for k, v in group["baseline_correct"].items()
        }
    return dict(
        groups=list(groups.values()),
        registered_samples=len(items),
        effect="UNVALIDATED",
        matched_knowledge_ablation="NOT_RUN",
        simulation_trades=0,
    )
