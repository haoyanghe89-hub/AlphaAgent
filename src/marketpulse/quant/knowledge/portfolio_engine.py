"""Local account projections: reductions first, conditional additions within every limit."""

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, localcontext

from .advisor_contracts import AdvisorRequest, Position
from .advisor_engine import build_answer, money, sell_quantity
from .features import number
from .playbook import trading_unit
from .portfolio_contracts import PortfolioRequest
from .portfolio_risk import correlations, volatility

VERSION = "portfolio-advisor-local-v1"


def single_report(report, sid):
    c = next(c for c in report["candidates"] if c["security_id"] == sid)
    return dict(
        report,
        candidates=[c],
        research_opinions=[o for o in report["research_opinions"] if o["security_id"] == sid],
    )


def prepare(request, studies, quotes, trading, pinned):
    rows, seen, versions = [], set(), None
    for asset in request.assets:
        matches = [
            (study, c)
            for study in studies
            for c in study["data"]["candidates"]
            if asset.entity.upper() in (c["security_id"], c["security_id"][:6], c["name"].upper())
        ]
        if len(matches) != 1:
            raise ValueError("股票没有唯一的已复核研究：" + asset.entity)
        study, c = matches[0]
        sid = c["security_id"]
        if sid in seen:
            raise ValueError("股票名称和代码指向同一证券，请合并持仓。")
        seen.add(sid)
        if (
            study["data"]["request"]["horizon"] != asset.horizon
            or study["data"]["request"]["profile"] is not None
        ):
            raise ValueError("组合研究周期或隐私边界不一致。")
        kv = {r["rule_id"]: r["version"] for r in study["data"]["knowledge_usage"]["rules"]}
        if versions is not None and kv != versions:
            raise ValueError("分组研究的知识版本不一致，请另开新研究。")
        versions = kv
        quote = quotes[sid]
        price = number(quote["price"])
        if quote["security_id"] != sid or price <= 0:
            raise ValueError("组合报价主体或价格无效。")
        rid = pinned.get(asset.entity) or (
            asset.route_id if asset.route_id != "AUTO" else c["selected_route"]
        )
        route = next(r for r in c["routes"] if r["route_id"] == rid)
        close = number(c["metrics"]["close"]["value"])
        day = datetime.fromisoformat(quote["quoted_at"]).strftime("%Y%m%d")
        comparable = (
            abs(price - close) <= Decimal(".01")
            if day == study["data"]["market_date"]
            else (
                day > study["data"]["market_date"]
                and abs(number(quote["previous_close"]) - close) <= Decimal(".01")
            )
        )
        risk = (
            number(c["metrics"]["low_20"]["value"]).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
            if comparable
            else None
        )
        opinion = next(o for o in study["data"]["research_opinions"] if o["security_id"] == sid)
        row = dict(
            asset=asset,
            security_id=sid,
            candidate=c,
            opinion=opinion,
            report=study["data"],
            study_id=study["run_id"],
            quote=quote,
            price=price,
            route=route,
            risk=risk,
            comparable=comparable,
            sell=0,
            buy=0,
            reasons=[],
            unit=trading_unit(sid),
            trading=trading[sid],
        )
        volume = number(c["metrics"]["average_turnover_20"]["value"]) * request.liquidity_cap
        max_liquid = int((volume / price).to_integral_value(rounding=ROUND_FLOOR))
        unit = row["unit"]
        if max_liquid < asset.sellable_shares:
            max_liquid = max_liquid // unit["increment"] * unit["increment"]
            if max_liquid < unit["minimum"]:
                max_liquid = 0
        row["sell_limit"] = min(asset.sellable_shares, max_liquid, unit["maximum"])
        row["buy_limit"] = min(max_liquid, unit["maximum"])
        rows.append(row)
    return rows


def fees(request, price, quantity, side):
    if not quantity:
        return Decimal(0)
    gross = price * quantity
    other = request.other_sell_cost_rate if side == "sell" else request.other_buy_cost_rate
    return max(request.minimum_commission, gross * request.commission_rate) + gross * other


def projection(request, rows, capital, risk):
    costs = sum(
        fees(request, r["price"], r["sell"], "sell") + fees(request, r["price"], r["buy"], "buy")
        for r in rows
    )
    cash = request.available_cash + sum(
        r["price"] * (r["sell"] - r["buy"])
        - fees(request, r["price"], r["sell"], "sell")
        - fees(request, r["price"], r["buy"], "buy")
        for r in rows
    )
    assets = capital - costs
    values = {
        r["security_id"]: (r["asset"].shares - r["sell"] + r["buy"]) * r["price"] for r in rows
    }
    industries, routes = {}, {}
    losses = {}
    for r in rows:
        sid = r["security_id"]
        value = values[sid]
        qty = r["asset"].shares - r["sell"] + r["buy"]
        ind = r["candidate"]["industry_code"]
        rid = r["route"]["route_id"]
        industries[ind] = industries.get(ind, Decimal(0)) + value
        routes[rid] = routes.get(rid, Decimal(0)) + value
        losses[sid] = (
            (max(Decimal(0), r["price"] - r["risk"]) * qty + fees(request, r["risk"], qty, "sell"))
            if r["risk"]
            else None
        )
    weights = {s: v / assets for s, v in values.items()} if assets > 0 else {}
    return dict(
        assets=assets,
        cash=cash,
        costs=costs,
        values=values,
        invested=sum(values.values()),
        industries=industries,
        routes=routes,
        losses=losses,
        total_risk=sum(v for v in losses.values() if v is not None),
        risk_complete=all(v is not None for v in losses.values()),
        volatility=volatility(risk, weights) if assets > 0 else None,
    )


def constraints(request, rows, projected, risk):
    a = projected["assets"]
    groups = []
    ids = {r["security_id"] for r in rows}

    def add(label, members, actual, limit, unit="VALUE"):
        if actual - limit > Decimal(".01"):
            groups.append(dict(label=label, members=set(members), excess=actual - limit, unit=unit))

    add("账户总仓位", ids, projected["invested"], a * request.equity_cap)
    for r in rows:
        sid = r["security_id"]
        add(
            r["candidate"]["name"] + "单股上限",
            [sid],
            projected["values"][sid],
            a * request.single_cap,
        )
        loss = projected["losses"][sid]
        if loss is not None:
            add(
                r["candidate"]["name"] + "本笔风险预算",
                [sid],
                loss,
                a * request.risk_per_trade,
                "RISK",
            )
    for ind, v in projected["industries"].items():
        members = [r["security_id"] for r in rows if r["candidate"]["industry_code"] == ind]
        name = next(r["candidate"]["industry_name"] for r in rows if r["security_id"] in members)
        add(name + "行业集中度", members, v, a * request.industry_cap)
    for rid, v in projected["routes"].items():
        allocation = (
            request.trend_allocation if rid == "TREND_SWING" else request.quality_allocation
        )
        add(
            ("趋势波段" if rid == "TREND_SWING" else "质量成长") + "路线预算",
            [r["security_id"] for r in rows if r["route"]["route_id"] == rid],
            v,
            a * allocation,
        )
    for i, members in enumerate(risk["groups"]):
        add(
            "相关持仓群" + str(i + 1),
            members,
            sum(projected["values"][s] for s in members),
            a * request.correlated_cap,
        )
    add("账户至风险参考的总预算", ids, projected["total_risk"], a * request.total_risk, "RISK")
    return groups


def weaker(row):
    route = row["route"]
    priority = (
        0
        if row["risk"] and row["price"] <= row["risk"]
        else (
            1
            if row["opinion"]["direction"] == "UNFAVORABLE"
            else 2
            if not route["eligible"]
            else 3
            if route["status"] != "CONDITIONS_MET"
            else 4
        )
    )
    return (
        priority,
        number(row["candidate"]["metrics"]["relative_return_20"]["value"]),
        row["security_id"],
    )


def build(request, studies, quotes, trading, sessions, pinned=None):
    request = PortfolioRequest.model_validate(request)
    with localcontext() as ctx:
        ctx.prec = 34
        return _build(request, studies, quotes, trading, sessions, pinned or {})


def _build(request, studies, quotes, trading, sessions, pinned):
    rows = prepare(request, studies, quotes, trading, pinned)
    dates = {s["data"]["market_date"] for s in studies}
    if len(dates) != 1:
        raise ValueError("分组研究行情日期不一致，不能合并为同窗账户分析。")
    capital = request.available_cash + sum(r["price"] * r["asset"].shares for r in rows)
    if not 0 < capital <= Decimal("100000000000"):
        raise ValueError("账户总资产超出计算边界。")
    risk = correlations(
        {r["security_id"]: r["candidate"]["chart"] for r in rows},
        sessions,
        request.correlation_threshold,
    )
    before = projection(request, rows, capital, risk)
    initial_constraints = constraints(request, rows, before, risk)
    warnings = []
    for r in rows:
        asset = r["asset"]
        if not asset.shares:
            continue
        position = Position(
            input_origin=request.input_origin,
            entity=asset.entity,
            cost_price=asset.cost_price,
            shares=asset.shares,
            sellable_shares=asset.sellable_shares,
            bought_today=asset.bought_today,
            buy_date=asset.buy_date,
            total_assets=capital,
            single_cap=request.single_cap,
            risk_budget=request.risk_per_trade,
            trim_fraction=request.trim_fraction,
            commission_rate=request.commission_rate,
            minimum_commission=request.minimum_commission,
            other_sell_cost_rate=request.other_sell_cost_rate,
        )
        local = build_answer(
            AdvisorRequest(
                request_key=request.request_key,
                position=position,
                horizon=asset.horizon,
                horizon_sessions=20 if asset.horizon == "SWING" else 60,
                route_id=r["route"]["route_id"],
                question=request.question,
            ),
            single_report(r["report"], r["security_id"]),
            r["quote"],
            r["trading"],
        )
        r["holding_advice"] = local
        warnings.extend(r["candidate"]["name"] + "：" + w for w in local["warnings"])
        if local["action"] == "EXIT_REVIEW":
            required = asset.shares
            why = "触及原路线风险参考，优先复核退出"
        elif r["opinion"]["direction"] == "UNFAVORABLE":
            required = asset.shares * request.trim_fraction
            why = "已复核看空，按已填分批比例防守"
        else:
            required = local["facts"]["reduction_required"]
            why = "原持仓超出单股或本笔风险预算"
        r["sell"] = sell_quantity(required, r["sell_limit"], r["trading"])
        if r["sell"]:
            r["reasons"].append(why)
    ordered = sorted(rows, key=weaker)
    # Accumulate one sell quantity per stock; never sum independent sell suggestions.
    for _ in range(64):
        projected = projection(request, rows, capital, risk)
        breaches = constraints(request, rows, projected, risk)
        progress = False
        for breach in breaches:
            for r in ordered:
                if r["security_id"] not in breach["members"] or r["sell"] >= r["sell_limit"]:
                    continue
                unit = (
                    r["price"]
                    if breach["unit"] == "VALUE"
                    else (
                        max(Decimal(0), r["price"] - r["risk"])
                        + r["risk"] * (request.commission_rate + request.other_sell_cost_rate)
                        if r["risk"]
                        else Decimal(0)
                    )
                )
                if unit <= 0:
                    continue
                amount = sell_quantity(
                    breach["excess"] / unit, r["sell_limit"] - r["sell"], r["trading"]
                )
                if amount:
                    r["sell"] += amount
                    if breach["label"] not in r["reasons"]:
                        r["reasons"].append(breach["label"])
                    progress = True
                    break
            if progress:
                break  # Recalculate fees, denominator and every constraint after this reduction.
        if not progress:
            break
    after_sales = projection(request, rows, capital, risk)
    unresolved = constraints(request, rows, after_sales, risk)
    stamps = [datetime.fromisoformat(r["quote"]["quoted_at"]) for r in rows]
    consistent_quotes = (
        len({s.date() for s in stamps}) == 1 and (max(stamps) - min(stamps)).total_seconds() <= 1800
    )
    can_add = (
        not unresolved
        and risk["status"] == "COMPUTED"
        and not risk["reason"]
        and all(r["comparable"] and r["quote"]["kind"] == "OBSERVED_QUOTE" for r in rows)
        and consistent_quotes
        and after_sales["cash"] >= request.reserve_cash
    )
    if not can_add:
        warnings.append("集中度未解除、相关性缺项、报价不一致或现金不足时，本轮不安排新增买入。")
    if risk["reason"]:
        warnings.append(risk["reason"])
    if not consistent_quotes:
        warnings.append("报价日期或时间间隔不一致，账户仅按各自标注时间展示防守试算。")
    for r in sorted(rows, key=weaker, reverse=True):
        eligible = (
            can_add
            and r["sell"] == 0
            and r["opinion"]["direction"] == "FAVORABLE"
            and r["route"]["eligible"]
            and r["route"]["status"] == "CONDITIONS_MET"
            and r["risk"] is not None
            and r["price"] > r["risk"]
            and r["quote"]["trading_status"] == "TRADED"
        )
        if not eligible:
            r["add_reason"] = "账户或原路线条件未满足，保留现金，等待确认"
            continue
        unit = r["unit"]
        lower = 0
        upper = r["buy_limit"] // unit["increment"]
        while lower < upper:
            mid = (lower + upper + 1) // 2
            r["buy"] = mid * unit["increment"]
            projected = projection(request, rows, capital, risk)
            if (
                projected["assets"] > 0
                and projected["cash"] >= request.reserve_cash
                and not constraints(request, rows, projected, risk)
            ):
                lower = mid
            else:
                upper = mid - 1
        r["buy"] = lower * unit["increment"] if lower * unit["increment"] >= unit["minimum"] else 0
        r["add_reason"] = (
            "原路线和账户约束同时满足，先确认减仓成交与可用现金后再按最新报价复核"
            if r["buy"]
            else "剩余额度不足最小买入单位，保留现金"
        )
    after = projection(request, rows, capital, risk)
    if (
        after["assets"] <= 0
        or after["cash"] < 0
        or sum(after["values"].values()) + after["cash"] != after["assets"]
    ):
        raise ValueError("账户现金或投影守恒检查失败。")
    changes = []
    for r in rows:
        sid = r["security_id"]
        asset = r["asset"]
        action = (
            "REDUCE" if r["sell"] else "ADD" if r["buy"] else "HOLD" if asset.shares else "WATCH"
        )
        changes.append(
            dict(
                security_id=sid,
                entity=asset.entity,
                name=r["candidate"]["name"],
                industry_code=r["candidate"]["industry_code"],
                industry_name=r["candidate"]["industry_name"],
                route_id=r["route"]["route_id"],
                route_title=r["route"]["title"],
                direction=r["opinion"]["direction"],
                opinion=r["opinion"],
                quote=r["quote"],
                action=action,
                sell_shares=r["sell"],
                buy_shares=r["buy"],
                current_shares=asset.shares,
                target_shares=asset.shares - r["sell"] + r["buy"],
                sellable_shares=asset.sellable_shares,
                before_value=money(before["values"][sid]),
                after_value=money(after["values"][sid]),
                before_weight=str(before["values"][sid] / before["assets"]),
                after_weight=str(after["values"][sid] / after["assets"]),
                floating_pnl=money((r["price"] - asset.cost_price) * asset.shares)
                if asset.cost_price
                else None,
                sell_cost=money(fees(request, r["price"], r["sell"], "sell")),
                buy_cost=money(fees(request, r["price"], r["buy"], "buy")),
                reduction_reasons=r["reasons"],
                add_reason=r["add_reason"],
                sell_limit=r["sell_limit"],
                liquidity_participation=str(request.liquidity_cap),
                risk_reference=str(r["risk"]) if r["risk"] else None,
                invalidation=[r["route"]["invalidation"], *r["opinion"]["invalidation"]],
                research_run_id=r["study_id"],
                trading_rules=r["trading"],
                unit=r["unit"],
            )
        )
    reduced = any(c["sell_shares"] for c in changes)
    added = any(c["buy_shares"] for c in changes)
    headline = (
        "先解除无法完成的风险约束，暂停加仓"
        if unresolved
        else "先减集中风险，再按条件调入"
        if reduced and added
        else "先减仓降低组合风险，余款保留现金"
        if reduced
        else "组合约束内存在条件加仓额度"
        if added
        else "维持持仓与现金，等待符合原路线的机会"
    )

    def exposed(p):
        return dict(
            total_assets=money(p["assets"]),
            cash=money(p["cash"]),
            invested=money(p["invested"]),
            equity_weight=str(p["invested"] / p["assets"]),
            costs=money(p["costs"]),
            risk_to_reference=money(p["total_risk"]) if p["risk_complete"] else None,
            annualized_sample_volatility=p["volatility"],
            industries=[
                dict(
                    code=i,
                    name=next(
                        r["candidate"]["industry_name"]
                        for r in rows
                        if r["candidate"]["industry_code"] == i
                    ),
                    value=money(v),
                    weight=str(v / p["assets"]),
                )
                for i, v in sorted(p["industries"].items())
            ],
            routes=[
                dict(route_id=k, value=money(v), weight=str(v / p["assets"]))
                for k, v in sorted(p["routes"].items())
            ],
        )

    def describe(items):
        return [
            dict(
                label=b["label"],
                members=sorted(b["members"]),
                excess=money(b["excess"]),
                unit=b["unit"],
            )
            for b in items
        ]

    reference = studies[0]["data"]["knowledge_usage"]
    return dict(
        schema_version="portfolio-advisor-result-v1",
        engine_version=VERSION,
        request=request.model_dump(mode="json"),
        headline=headline,
        market_date=next(iter(dates)),
        before=exposed(before),
        after_sales=exposed(after_sales),
        after=exposed(after),
        changes=changes,
        initial_breaches=describe(initial_constraints),
        unresolved=describe(unresolved),
        correlation=risk,
        correlated_groups=[
            dict(
                members=g,
                before_weight=str(sum(before["values"][s] for s in g) / before["assets"]),
                after_weight=str(sum(after["values"][s] for s in g) / after["assets"]),
            )
            for g in risk["groups"]
        ],
        pinned_routes={r["asset"].entity: r["route"]["route_id"] for r in rows},
        research_bindings=[dict(run_id=s["run_id"], sha256=s["result_sha256"]) for s in studies],
        knowledge=[
            dict(
                rule_id=r["rule_id"],
                version=r["version"],
                title=r["rule"]["title"],
                system_rule=next(
                    f["system_rule"] for f in reference["fragments"] if f["rule_id"] == r["rule_id"]
                ),
            )
            for r in reference["rules"]
        ],
        warnings=list(dict.fromkeys(warnings)),
        execution=[
            "先逐项确认建议减仓；数量按本轮原持仓累计，只允许卖出已声明可卖部分。",
            "只有减仓实际成交、资金可用且条件仍成立，才复核新增买入；未成交卖款不是当前现金。",
            "任何成交后更新全部股数与现金再咨询，不把不同轮次或独立条件的数量相加。",
        ],
        caveats=[
            "现金与全部已声明持仓市值构成普通现金账户；未纳入融资、ETF或其他账户。",
            "相关性为六十个共同交易日日收益样本，不是价格水平相关，也不能保证未来分散效果。",
            "相关群按阈值边的连通关系形成，群内不是每对股票都高相关；负相关不保证危机对冲。",
            "当前权重套用历史协方差的波动试算不是账户历史收益，不含交易择时回测。",
            "仓位、相关性阈值、分批、费用和额度均为可改研究假设，未证明盈利；风险参考不保证退出成交。",
        ],
        privacy=dict(
            account_sent_to_model=False, holdings_sent_to_model=False, question_sent_to_model=False
        ),
        sources=list(
            {s["blob_ref"]: s for study in studies for s in study["data"]["sources"]}.values()
        )
        + list(
            {
                quotes[c["security_id"]]["source"]["blob_ref"]: quotes[c["security_id"]]["source"]
                for study in studies
                for c in study["data"]["candidates"]
                if quotes[c["security_id"]].get("source")
            }.values()
        ),
    )
