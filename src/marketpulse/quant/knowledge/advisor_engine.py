"""Local position decisions over approved research and source-bound method versions."""

import re
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from functools import lru_cache

from marketpulse.telemetry.tracing import stage

from ..decision.next_session.calendar import plain, rule_text
from .advisor_contracts import AdvisorRequest
from .features import number

VERSION = "holding-advisor-local-v1"
REQUIRED_RULES = {"KR-AVOID-RUIN", "KR-CAPACITY-LIQUIDITY", "KR-EXPECTATION-EXIT"}


@lru_cache(maxsize=8)
def sell_rule_text(raw):
    # Cache identical verified bytes, never the source reference or a validation outcome.
    return plain(rule_text(raw))


def sell_rules(report, raw_bytes, security_id):
    exchange = "SSE" if security_id.endswith(".SH") else "SZSE"
    source = next(r for r in report["trading_rules"] if r["exchange"] == exchange)
    text = sell_rule_text(raw_bytes(source["source"]["blob_ref"]))
    lot = re.search(r"(?<!\d\.)3\.3\.8(.{0,180})3\.3\.9", text)
    t1 = re.search(r"3\.1\.4投资者买入的证券(.{0,100})", text)
    if (
        not lot
        or not t1
        or "交收前不得卖出" not in t1[1]
        or any(s not in lot[1] for s in ("100股", "整数倍", "不足100股", "一次性"))
    ):
        raise ValueError("交易所卖出数量与交收条款尚未核对，不能输出卖出股数。")
    star = security_id.startswith("688") and exchange == "SSE"
    locators = [
        dict(start=lot.start(), end=lot.end(), text=lot[0]),
        dict(start=t1.start(), end=t1.end(), text=t1[0]),
    ]
    if star:
        clause = re.search(r"(?<!\.)6\.7(.{0,650})6\.8", text)
        if not clause or any(s not in clause[1] for s in ("200股", "卖出", "一次性")):
            raise ValueError("科创板卖出条款尚未核对。")
        locators.append(dict(start=clause.start(), end=clause.end(), text=clause[0]))
    return dict(
        exchange=exchange,
        source=source["source"],
        locators=locators,
        minimum=200 if star else 100,
        increment=1 if star else 100,
        effective_date=source["effective_date"],
        kind="LIMIT_COMPETITIVE_SELL",
    )


def sell_quantity(required, sellable, rules):
    """Round up a reduction, while never inventing unlocked or fractional shares."""
    if required <= 0 or sellable == 0:
        return 0
    if required >= sellable:
        return sellable  # One complete remaining balance includes an odd-lot remainder.
    step = rules["increment"]
    amount = max(
        rules["minimum"],
        int((number(required) / step).to_integral_value(rounding=ROUND_CEILING)) * step,
    )
    return min(sellable, amount)


def theme(question):
    if re.search(r"为什么|依据|方法|知识|逻辑|MACD|RSI", question, re.I):
        return "BASIS"
    if re.search(r"加仓|补仓|摊薄", question):
        return "ADD"
    if re.search(r"止损|下跌|跌到|跌破|亏损|防守", question):
        return "DEFENSE"
    if re.search(r"到多少|卖|止盈|减仓|落袋", question):
        return "SELL"
    if re.search(r"持有|继续拿|还能拿|怎么办|建议|持仓", question):
        return "HOLD"
    return "GENERAL"


def scenario(request):
    if request.scenario_price is not None:
        return request.scenario_price
    match = re.search(
        r"(?:如果|假如|假设|要是|那么|那|^)[^，。；\n]{0,25}?"
        r"(?:涨到|跌到|到达|到)\s*(\d+(?:\.\d+)?)\s*(?:元|块|呢|[，,？?]|$)",
        request.question,
    )
    if match:
        price = number(match[1])
        if not 0 < price <= 1000000:
            raise ValueError("情景价格必须为正数且不超过一百万元。")
        return price
    return None


def money(value):
    return str(value.quantize(Decimal(".01")))


def allowed_shares(position, price, total_assets, risk):
    cap = int((total_assets * position.single_cap / price).to_integral_value(rounding=ROUND_FLOOR))
    keep = min(position.shares, cap)
    if risk and price > risk:
        budget = total_assets * position.risk_budget
        unit = price - risk + risk * position.other_sell_cost_rate
        # max(minimum_fee, commission) must satisfy the budget in either fee branch.
        risk_shares = max(
            0,
            min(
                int(
                    ((budget - position.minimum_commission) / unit).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                ),
                int(
                    (budget / (unit + risk * position.commission_rate)).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                ),
            ),
        )
        keep = min(keep, risk_shares)
    return keep


def sell_cost(position, price, shares):
    if not shares:
        return Decimal(0)
    gross = price * shares
    return (
        max(position.minimum_commission, gross * position.commission_rate)
        + gross * position.other_sell_cost_rate
    )


@stage("local_account.compute", as_type="span")
def build_answer(request, report, quote, trading, *, pinned_route=None, understanding=None):
    request = AdvisorRequest.model_validate(request)
    with localcontext() as ctx:
        ctx.prec = 34
        return _answer(request, report, quote, trading, pinned_route, understanding)


def _answer(request, report, quote, trading, pinned_route, understanding):
    if len(report["candidates"]) != 1:
        raise ValueError("持仓咨询必须绑定单只股票的已复核研究。")
    c = report["candidates"][0]
    opinion = next(o for o in report["research_opinions"] if o["security_id"] == c["security_id"])
    knowledge = report["knowledge_usage"]
    ids = REQUIRED_RULES | set(opinion["method_rule_ids"])
    route_id = pinned_route or (
        request.route_id if request.route_id != "AUTO" else c["selected_route"]
    )
    route = next(r for r in c["routes"] if r["route_id"] == route_id)
    ids |= (
        {"KR-TREND-MARKET", "KR-TREND-ENTRY-DISCIPLINE"}
        if route_id == "TREND_SWING"
        else {"KR-QUALITY-ECONOMICS", "KR-GROWTH-RETURN"}
    )
    rules = {r["rule_id"]: r for r in knowledge["rules"]}
    if not ids <= rules.keys():
        raise ValueError("退出、风控或原路线的固定知识版本不完整。")
    fragments = {f["rule_id"]: f for f in knowledge["fragments"]}
    if not ids <= fragments.keys():
        raise ValueError("持仓方法原文与规则快照不完整。")
    position = request.position
    price = number(quote["price"])
    close = number(c["metrics"]["close"]["value"])
    value = price * position.shares
    if value > position.total_assets + Decimal(".01"):
        raise ValueError("这只股票的当前市值超过已填总资产，请更新总资产或持股数量。")
    if quote["security_id"] != c["security_id"]:
        raise ValueError("报价股票与研究主体不一致。")
    warnings = []
    quoted_day = datetime.fromisoformat(quote["quoted_at"]).strftime("%Y%m%d")
    # A stale adjustment anchor must never become an executable raw-price stop.
    comparable = quoted_day == report["market_date"] and abs(price - close) <= Decimal(".01")
    if quoted_day > report["market_date"]:
        comparable = abs(number(quote["previous_close"]) - close) <= Decimal(".01")
    if not comparable:
        warnings.append("报价与封存复权参考口径不一致，请更新研究后再使用数值价位条件。")
    if quote["kind"] == "RESEARCH_CLOSE":
        warnings.append("新报价读取失败，本轮使用已封存收盘价；当前盘中点位需要重新读取。")
    if quoted_day > report["market_date"]:
        warnings.append(
            "报价比研究更新；经营、行业与技术判断仍依据所示研究日期，重大变化请更新研究。"
        )
    risk = (
        number(c["metrics"]["low_20"]["value"]).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
        if comparable
        else None
    )
    high = (
        number(c["metrics"]["prior_high_20"]["value"]).quantize(
            Decimal(".01"), rounding=ROUND_CEILING
        )
        if comparable
        else None
    )
    loss_to_risk = max(Decimal(0), price - risk) * position.shares if risk else None
    budget = position.total_assets * position.risk_budget
    keep = allowed_shares(position, price, position.total_assets, risk)
    required = position.shares - keep
    reduction = sell_quantity(required, position.sellable_shares, trading)
    stage = sell_quantity(
        position.shares * position.trim_fraction, position.sellable_shares, trading
    )
    if not route["eligible"]:
        warnings.append(
            "原路线所需输入或口径当前不适用，不能用另一条路线掩盖失效；先复核原持有理由。"
        )
    if risk and price <= risk:
        action, title, now_qty = (
            "EXIT_REVIEW",
            "已触及风险参考，优先复核退出",
            position.sellable_shares,
        )
    elif required:
        action, title, now_qty = "TRIM", "先降低超出预算的持仓，再讨论卖点", reduction
    elif opinion["direction"] == "UNFAVORABLE":
        action, title, now_qty = "DEFEND", "倾向看空，持仓以防守为主", 0
    else:
        action, title, now_qty = "HOLD", "保留持仓，按原路线观察退出条件", 0
    if not comparable:
        title += "；价位参考需更新研究"
    remaining = max(0, required - reduction)
    if action == "EXIT_REVIEW":
        remaining = position.shares - now_qty
    if remaining:
        warnings.append(
            f"受可卖数量限制，仍有约 {remaining} 股对应的超额暴露；不能当作已完成降仓。"
        )
    if position.sellable_shares == 0:
        warnings.append("本次声明可卖数量为零；今日不能按建议执行减仓，待交收后重新核对。")
    subject = theme(request.question)
    if understanding:
        focuses = understanding["tasks"][0]["focuses"]
        subject = next(
            (f for f in focuses if f in {"BASIS", "ADD", "DEFENSE", "SELL", "HOLD"}), "GENERAL"
        )
        if "INVALIDATION" in focuses:
            subject = "INVALIDATION"
    lines = [opinion["summary"]]
    if subject == "ADD":
        lines.append(
            "当前咨询不安排新增买入数量；先核查原持有理由、风险预算和路线入场条件，亏损本身不是补仓依据。"
        )
    if subject == "BASIS":
        lines.extend(fragments[r]["system_rule"] for r in sorted(ids & REQUIRED_RULES))
    if subject == "GENERAL":
        lines.append("本轮结合你已填持仓回答持有、减仓和退出条件；其他股票或其他任务请另开咨询。")
    lines.append(
        f"持仓 {position.shares} 股，可卖 {position.sellable_shares} 股；"
        f"当前市值占总资产 {money(value / position.total_assets * 100)}%。"
    )
    if required:
        lines.append(
            f"按单股上限及价格至风险参考（含卖出费用）的预算，需降低约 {required} 股暴露，"
            f"本轮可卖范围内的数量参考为 {reduction} 股。"
        )
    elif risk:
        lines.append(
            "当前持仓未超过已填单股上限及价格至风险参考的预算；入场条件未满足不会单独触发卖出。"
        )
    if re.search(r"(?:成本|股数|仓位|买了|卖了|加了)[^，。；]{0,12}\d", request.question):
        warnings.append(
            "对话中的数字不会自动改写持仓；如需调整成本或股数，请修改上方资料后再发送。"
        )
    gates = []
    if required:
        gates.append(
            dict(
                kind="EXPOSURE",
                title="当前仓位与预算",
                price=str(price),
                condition="持仓市值超过单股上限，或至风险参考的损失超出本笔预算。",
                shares=reduction,
                rule_ids=["KR-AVOID-RUIN", "KR-CAPACITY-LIQUIDITY"],
            )
        )
    if risk:
        gates.append(
            dict(
                kind="DEFENSE",
                title="下行防守参考",
                price=str(risk),
                condition="到达或跌破此位时复核原路线失效与退出，跳空或跌停时可能无法在此价成交。",
                shares=position.sellable_shares,
                rule_ids=["KR-AVOID-RUIN", "KR-EXPECTATION-EXIT"],
            )
        )
    if high:
        condition = (
            "接近前20日高点时，若出现冲高回落或原有趋势失效，再按已填分批比例考虑减仓；有效突破则不因碰到高点自动卖出。"
            if route_id == "TREND_SWING"
            else "前20日高点仅作价格观察；质量成长按经营兑现、现金回收和预期变化复核退出，"
            "不据此机械止盈。"
        )
        gates.append(
            dict(
                kind="UPSIDE",
                title="上行观察位",
                price=str(high),
                condition=condition,
                shares=stage if route_id == "TREND_SWING" else None,
                rule_ids=["KR-EXPECTATION-EXIT"],
            )
        )
    hypothetical = scenario(request)
    what_if = None
    if hypothetical:
        hypothetical_value = hypothetical * position.shares
        hypothetical_total = position.total_assets + (hypothetical - price) * position.shares
        if hypothetical_total <= 0:
            raise ValueError("情景计算后的总资产必须为正。")
        if risk and hypothetical <= risk:
            decision, quantity = "触及下行防守参考，优先复核退出", position.sellable_shares
        elif (
            allowed := allowed_shares(position, hypothetical, hypothetical_total, risk)
        ) < position.shares:
            quantity = sell_quantity(position.shares - allowed, position.sellable_shares, trading)
            decision = "按情景价格，持仓超过单股或风险预算，先复核减仓"
        elif high and hypothetical >= high and route_id == "TREND_SWING":
            decision, quantity = "到达上行观察位，需同时出现趋势转弱才考虑分批卖出", stage
        else:
            decision, quantity = "该价格本身不触发机械卖出，继续核查原路线与反证", 0
        gross = hypothetical * quantity
        fees = sell_cost(position, hypothetical, quantity)
        what_if = dict(
            price=str(hypothetical),
            conclusion=decision,
            shares=quantity,
            portfolio_weight=str(hypothetical_value / hypothetical_total),
            floating_pnl=money((hypothetical - position.cost_price) * position.shares),
            estimated_net_proceeds=money(gross - fees),
            estimated_sell_cost=money(fees),
            basis="USER_SCENARIO_NOT_FORECAST; other assets unchanged; "
            "current sellable shares unchanged",
        )
    return dict(
        schema_version="holding-advisor-result-v1",
        engine_version=VERSION,
        request=request.model_dump(mode="json"),
        security_id=c["security_id"],
        name=c["name"],
        route_id=route_id,
        route_title=route["title"],
        market_date=report["market_date"],
        quote=quote,
        action=action,
        headline=title,
        suggested_now_shares=now_qty,
        answer=lines,
        theme=subject,
        direction=opinion["direction"],
        price_comparable=comparable,
        opinion=opinion,
        facts=dict(
            price=str(price),
            cost_price=str(position.cost_price),
            market_value=money(value),
            weight=str(value / position.total_assets),
            floating_pnl=money((price - position.cost_price) * position.shares),
            floating_return=str(price / position.cost_price - 1),
            loss_to_risk=money(loss_to_risk) if loss_to_risk is not None else None,
            risk_exit_cost=money(sell_cost(position, risk, position.shares)) if risk else None,
            risk_budget=money(budget),
            reduction_required=required,
            unresolved_reduction=remaining,
        ),
        gates=gates,
        scenario=what_if,
        invalidation=[route["invalidation"], *opinion["invalidation"]],
        knowledge=[
            dict(
                rule_id=r,
                version=rules[r]["version"],
                title=rules[r]["rule"]["title"],
                system_rule=fragments[r]["system_rule"],
                source_title=fragments[r].get("source_title"),
                authors=fragments[r].get("authors", []),
                url=fragments[r].get("url"),
            )
            for r in sorted(ids)
        ],
        trading_rules=trading,
        chart=c["chart"],
        warnings=warnings,
        caveats=[
            "成本用于盈亏试算，不是股价必须回到成本的依据。",
            "价位与数量是条件参考，未提交交易；跳空、跌停、停牌和流动性不足可能导致无法成交。",
            "单股上限、风险预算、分批比例和费用是本地执行偏好与假设，方法及参数的盈利效果尚未验证。",
            "仅核算这只股票；其他持仓的行业集中度与相关性未纳入本轮。",
        ],
        privacy=dict(position_sent_to_model=False, question_sent_to_model=False),
        sources=report["sources"] + ([quote["source"]] if quote.get("source") else []),
    )
