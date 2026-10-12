"""Pure calculations over a fresh, recorded packet; no prototype calculations."""

import math
import statistics
from datetime import date, timedelta
from decimal import Decimal

from ..compute.dcf import fcff_basis, scenario
from ..domain import DcfAssumptions
from .sources import quote_values


def num(value):
    if value is None or str(value).strip() in {"", "--", "nan"}:
        raise ValueError("required numeric input missing")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite input")
    return result


def rounded(value, digits=6):
    return round(float(value), digits)


def statement(packet, today):
    result = {}
    for period, report in packet["result"]["data"]["report_list"].items():
        published = report.get("publish_date")
        if not published or published > today.replace("-", "") or period > today.replace("-", ""):
            continue
        if report.get("rCurrency") != "CNY" or report.get("rType") != "合并期末":
            raise ValueError("consolidated CNY statements required")
        # Lists retain duplicate names (e.g. financing vs operating interest income).
        fields = {}
        for item in report["data"]:
            fields.setdefault(item["item_title"], []).append(item.get("item_value"))
        result[period] = dict(fields=fields, published=published, meta=report)
    if not result:
        raise ValueError("no disclosed statements")
    return result


def field(report, key):
    values = [num(v) for v in report["fields"].get(key, []) if v not in (None, "", "--")]
    if not values or len(set(values)) != 1:
        raise ValueError("missing/ambiguous statement field: " + key)
    return values[0]


def ttm(reports, period, key):
    current = field(reports[period], key)
    if period.endswith("1231"):
        return current
    year = int(period[:4]) - 1
    return current + field(reports[f"{year}1231"], key) - field(reports[f"{year}{period[4:]}"], key)


def quarter(reports, period, key):
    current = field(reports[period], key)
    month = int(period[4:6])
    if month == 3:
        return current
    previous = {6: "0331", 9: "0630", 12: "0930"}[month]
    return current - field(reports[period[:4] + previous], key)


def financial_chart(reports):
    periods = sorted(reports)[-12:]
    revenue, profit, yoy = [], [], []
    for p in periods:
        rev = quarter(reports, p, "营业总收入")
        prior = quarter(reports, str(int(p[:4]) - 1) + p[4:], "营业总收入")
        revenue.append(rounded(rev / Decimal("1e8")))
        profit.append(rounded(quarter(reports, p, "归属于母公司所有者的净利润") / Decimal("1e8")))
        yoy.append(rounded((rev / prior - 1) * 100) if prior > 0 else None)
    return dict(
        kind="financial_quarterly",
        title="单季营收 / 归母净利（亿元）与营收同比",
        chart_type="bar",
        x=periods,
        series=[
            dict(name="单季营收(亿)", data=revenue, barMaxWidth=18),
            dict(name="单季净利(亿)", data=profit, barMaxWidth=18),
            dict(name="营收同比%", data=yoy, symbol="circle"),
        ],
    )


def daily(packet, symbol, end):
    item = packet["data"][symbol]
    rows = item["qfqday"] if "qfqday" in item else item["day"]
    result = []
    for row in rows:
        if row[0] > end:
            continue
        o, c, h, low, vol = map(float, row[1:6])
        if not 0 < low <= min(o, c) <= max(o, c) <= h or vol < 0:
            raise ValueError("invalid daily OHLC")
        result.append(dict(date=row[0], open=o, close=c, high=h, low=low, volume=vol))
    if len(result) < 100 or len({r["date"] for r in result}) != len(result):
        raise ValueError("insufficient/duplicate daily rows")
    return sorted(result, key=lambda r: r["date"])


def kline_chart(rows):
    closes = [r["close"] for r in rows]

    def ma(w):
        return [
            rounded(sum(closes[i - w + 1 : i + 1]) / w) if i >= w - 1 else None
            for i in range(len(rows))
        ]

    return dict(
        kind="kline",
        title="日 K 线（前复权）+ MA20 / MA60",
        x=[r["date"] for r in rows],
        ohlc=[[r[k] for k in ("open", "close", "low", "high")] for r in rows],
        # Fixed Tencent sz000333 endpoint returns lots; no SDK unit guessing.
        volume=[r["volume"] for r in rows],
        ma20=ma(20),
        ma60=ma(60),
    )


def em_rows(packet, code="000333"):
    if packet.get("success") is False and packet.get("code") != 9201:
        raise ValueError("provider rejected query")
    data = packet.get("result", packet)
    if not data:
        raise ValueError("provider returned no dataset")
    rows = data.get("data", [])
    for r in rows:
        if r.get("SECURITY_CODE", r.get("SCODE")) != code:
            raise ValueError("provider returned wrong security")
    return rows


def dcf_input(values, statements, asof, code="000333"):
    annual = max(p for p in statements if p.endswith("1231"))
    period = f"{annual[:4]}-{annual[4:6]}-{annual[6:]}"

    def pick(key):
        rows = em_rows(values[key], code)
        found = [
            r
            for r in rows
            if r["REPORT_DATE"][:10] == period
            and r["NOTICE_DATE"][:10] <= asof
            and r.get("CURRENCY") == "CNY"
        ]
        if len(found) != 1:
            raise ValueError("latest annual DCF statement missing/ambiguous")
        return found[0]

    inc, cash, balance = pick("income_em"), pick("cash_em"), pick("balance_em")
    mappings = {
        "profit_before_tax": (inc, "TOTAL_PROFIT"),
        "income_tax": (inc, "INCOME_TAX"),
        "interest_expense": (inc, "FE_INTEREST_EXPENSE"),
        "interest_income": (inc, "FE_INTEREST_INCOME"),
        "investment_income": (inc, "INVEST_INCOME"),
        "fair_value_gain": (inc, "FAIRVALUE_CHANGE_INCOME"),
        "nonoperating_income": (inc, "NONBUSINESS_INCOME"),
        "nonoperating_expense": (inc, "NONBUSINESS_EXPENSE"),
        "depreciation": (cash, "FA_IR_DEPR"),
        "capex": (cash, "CONSTRUCT_LONG_ASSET"),
        "inventory_decrease": (cash, "INVENTORY_REDUCE"),
        "receivables_increase": (cash, "OPERATE_RECE_REDUCE"),
        "payables_increase": (cash, "OPERATE_PAYABLE_ADD"),
        "cash": (cash, "END_CCE"),
        "short_debt": (balance, "SHORT_LOAN"),
        "long_debt": (balance, "LONG_LOAN"),
        "bonds": (balance, "BOND_PAYABLE"),
        "leases": (balance, "LEASE_LIAB"),
        "current_debt": (balance, "NONCURRENT_LIAB_1YEAR"),
        "minority_equity": (balance, "MINORITY_EQUITY"),
    }
    missing = [key for _, (row, key) in mappings.items() if row.get(key) is None]
    if missing:
        raise ValueError("缺少明确披露值：" + ", ".join(missing))
    inputs = {k: num(row[key]) for k, (row, key) in mappings.items()}
    inputs["receivables_increase"] *= -1
    value_rows = sorted(em_rows(values["valuation"], code), key=lambda r: r["TRADE_DATE"])
    inputs["issued_shares"] = num(value_rows[-1]["TOTAL_SHARES"])
    # A stated denominator assumption, NOT a claim that treasury shares are zero.
    # Do not mistake the CNY TREASURY_SHARES balance for a count of shares.
    inputs["treasury_shares"] = Decimal(0)
    basis = fcff_basis(inputs)
    return dict(
        period=period,
        basis={k: str(v) for k, v in basis.items()},
        inputs={k: str(v) for k, v in inputs.items()},
        input_fields={k: key for k, (_, key) in mappings.items()},
        note="合并工业 FCFF 代理：金融子公司未拆分；折旧摊销采用供应商现金流补充项；"
        "最新年报现金流作为标准化基期，预测轴为从估算时点起的模型年度；"
        "现金、债务和少数股东权益为年末账面数。分母采用最新全部发行股数（含 A/H），"
        "不扣库存股数量；库存股账面金额不当作股数。参数属于情景假设，不是目标价。",
        shares_date=value_rows[-1]["TRADE_DATE"][:10],
    )


def dcf_result(data, assumptions):
    basis = {k: num(v) for k, v in data["basis"].items()}
    result = scenario(basis, assumptions)
    sensitivity = []
    for delta in (Decimal("-0.02"), Decimal("-0.01"), Decimal(0), Decimal("0.01"), Decimal("0.02")):
        discount = assumptions.discount + delta
        if discount <= assumptions.g_term:
            continue
        alt = assumptions.model_copy(update={"discount": discount})
        sensitivity.append(
            dict(
                discount=rounded(discount * 100),
                per_share=rounded(scenario(basis, alt)["per_share"]),
            )
        )
    result_data = dict(data)
    result_data.update(
        assumptions=assumptions.model_dump(mode="json"),
        per_share=rounded(result["per_share"]),
        terminal_weight=rounded(result["terminal_pv"] / result["enterprise"] * 100),
        sensitivity=sensitivity,
        chart=dict(
            kind="dcf_forecast",
            chart_type="bar",
            title="DCF 未来十年 FCFF 情景（亿元）",
            x=[f"第{i}年" for i in range(1, 11)],
            series=[
                dict(
                    name="预测 FCFF(亿)",
                    data=[rounded(v / Decimal("1e8")) for v in result["forecast"]],
                )
            ],
        ),
    )
    return result_data


def performance(nav, dates):
    returns = [b / a - 1 for a, b in zip(nav[:-1], nav[1:], strict=True)]
    vol = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0
    high, worst = nav[0], 0
    for x in nav:
        high = max(high, x)
        worst = min(worst, x / high - 1)
    years = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    if years <= 0:
        raise ValueError("ordered performance dates required")
    return dict(
        total_ret=(nav[-1] / nav[0] - 1) * 100,
        cagr=((nav[-1] / nav[0]) ** (1 / years) - 1) * 100,
        vol=vol * 100,
        sharpe=statistics.mean(returns) * 252 / vol if vol else None,
        max_dd=worst * 100,
    )


def simulate(prices, benchmark, cost=0.0013, fast=20, slow=60):
    if not 2 <= fast < slow <= 120 or not 0 <= cost <= 0.02:
        raise ValueError("invalid simulation assumptions")
    bmap = {r["date"]: r["close"] for r in benchmark}
    start = (date.fromisoformat(prices[-1]["date"]) - timedelta(days=365)).isoformat()
    first = next(i for i, r in enumerate(prices) if r["date"] >= start and i >= slow)
    sample = prices[first - 1 :]
    if any(r["date"] not in bmap for r in sample):
        raise ValueError("benchmark does not cover every simulation day")
    dates = [r["date"] for r in sample]

    def run(rate):
        nav, position, trades = [1.0], 0, []
        for i in range(first, len(prices)):
            r, prev = prices[i], prices[i - 1]
            short = sum(p["close"] for p in prices[i - fast : i]) / fast
            long = sum(p["close"] for p in prices[i - slow : i]) / slow
            target = int(short > long)
            value = nav[-1] * (1 + position * (r["open"] / prev["close"] - 1))
            if target != position:
                charge = value * rate
                value -= charge
                trades.append(
                    dict(
                        date=r["date"],
                        signal_date=prev["date"],
                        side="买入" if target else "卖出",
                        price=r["open"],
                        cost_nav=charge,
                        ma_fast=short,
                        ma_slow=long,
                    )
                )
            value *= 1 + target * (r["close"] / r["open"] - 1)
            nav.append(value)
            position = target
        return nav, trades

    nav, trades = run(cost)
    low, _ = run(0)
    high, _ = run(0.003)
    base = bmap[dates[0]]
    index = [bmap[d] / base for d in dates]
    annual = []
    for year in sorted({d[:4] for d in dates[1:]}):
        ix = [i for i, d in enumerate(dates) if i > 0 and d[:4] == year]
        a, b = ix[0] - 1, ix[-1]
        ret = (nav[b] / nav[a] - 1) * 100
        ref = (index[b] / index[a] - 1) * 100
        annual.append(
            dict(
                year=year,
                strategy=ret,
                benchmark=ref,
                excess=ret - ref,
                trade_count=sum(t["date"].startswith(year) for t in trades),
            )
        )
    metrics, benchmark_metrics = performance(nav, dates), performance(index, dates)
    return dict(
        cost_pct=cost * 100,
        fast=fast,
        slow=slow,
        metrics=metrics,
        benchmark_metrics=benchmark_metrics,
        annual=annual,
        trades=trades,
        chart=dict(
            kind="nav",
            title="单股均线模拟 vs 沪深300（含成本敏感性）",
            x=dates,
            series=[dict(data=v) for v in (nav, index, low, high)],
        ),
        note="美的集团单股 MA 快线高于慢线时持有，否则持币；前一交易日信号、下一交易日开盘调仓。"
        "三种单侧成本分别重算，期末按市值计价，未强制平仓；Sharpe 无风险利率显式设为 0。"
        "使用本次取得的前复权价格和连续仓位，不含整手、停牌/涨跌停成交约束、分红再投资及税费分项。"
        "这是研究模拟，不能作为全市场 PIT 回测或实盘可实现收益证明。",
    )


def build(values, end, asof):
    reports = statement(values["lrb"], asof)
    balance = statement(values["fzb"], asof)
    cash = statement(values["llb"], asof)
    latest = max(reports)
    quotes = quote_values(values["quotes"])
    quote = next(q for q in quotes if q["symbol"] == "sz000333")
    prices = daily(values["prices"], "sz000333", end)
    benchmark = daily(values["benchmark"], "sh000300", end)
    valuation = sorted(em_rows(values["valuation"]), key=lambda r: r["TRADE_DATE"])
    valuation = [r for r in valuation if r["TRADE_DATE"][:10] <= end]
    margin = sorted(em_rows(values["margin"]), key=lambda r: r["DATE"])
    if not valuation or not margin:
        raise ValueError("valuation/margin dataset empty")
    own_pe = []
    for r in valuation:
        day = r["TRADE_DATE"][:10].replace("-", "")
        known = {p: v for p, v in reports.items() if v["published"] < day}
        try:
            earnings = ttm(known, max(known), "归属于母公司所有者的净利润")
            own_pe.append(
                rounded(num(r["CLOSE_PRICE"]) * num(r["TOTAL_SHARES"]) / earnings)
                if earnings > 0
                else None
            )
        except (KeyError, ValueError):
            own_pe.append(None)
    earnings = ttm(reports, latest, "归属于母公司所有者的净利润")
    latest_value = valuation[-1]
    pe = num(latest_value["CLOSE_PRICE"]) * num(latest_value["TOTAL_SHARES"]) / earnings
    prior = str(int(latest[:4]) - 1) + latest[4:]
    growth = (field(reports[latest], "营业总收入") / field(reports[prior], "营业总收入") - 1) * 100
    profit_growth = (
        field(reports[latest], "归属于母公司所有者的净利润")
        / field(reports[prior], "归属于母公司所有者的净利润")
        - 1
    ) * 100
    b = balance[max(balance)]
    equity = field(b, "归属于母公司股东权益合计")
    previous_equity = field(balance[str(int(latest[:4]) - 1) + "1231"], "归属于母公司股东权益合计")
    roe = (
        field(reports[latest], "归属于母公司所有者的净利润")
        / ((equity + previous_equity) / 2)
        * 100
    )
    # Feed crosschecks do not assert independent issuer-document verification.
    other = next(
        r
        for r in em_rows(values["income_check"])
        if r["REPORT_DATE"][:10].replace("-", "") == latest
    )
    checks = []
    for title, left, right, tolerance, note in (
        (
            "归母净利润",
            field(reports[latest], "归属于母公司所有者的净利润"),
            num(other["PARENT_NETPROFIT"]),
            Decimal(1),
            "新浪三表与东方财富披露汇总；可能同源",
        ),
        (
            "营业总收入",
            field(reports[latest], "营业总收入"),
            num(other["TOTAL_OPERATE_INCOME"]),
            Decimal(1),
            "使用营业总收入；不与仅营业收入混比",
        ),
        (
            "PE-TTM",
            pe,
            num(latest_value["PE_TTM"]),
            Decimal("0.05"),
            "自算：同日收盘×总发行股数 / TTM 归母净利；供应商 EPS/股本口径可能不同",
        ),
    ):
        checks.append(
            dict(
                title=title,
                left=rounded(left),
                right=rounded(right),
                status="MATCH" if abs(left - right) <= tolerance else "CONFLICT",
                note=note,
                tolerance=str(tolerance),
            )
        )
    pledge, unlock = em_rows(values["pledge"]), em_rows(values["unlock"])
    latest_pledge = max(pledge, key=lambda r: r["NOTICE_DATE"]) if pledge else None
    latest_unlock = max(unlock, key=lambda r: r["FREE_DATE"]) if unlock else None
    risks = [
        dict(
            title="股权质押",
            text=(
                f"最新收录公告 {latest_pledge['NOTICE_DATE'][:10]}："
                f"{latest_pledge['HOLDER_NAME']}，{latest_pledge['UNFREEZE_STATE']}。"
                "记录日期较早，不据此断言当前无质押。"
                if latest_pledge
                else "接口成功返回零条记录；不等同于无风险。"
            ),
        ),
        dict(
            title="商誉与减值",
            text=f"{max(balance)} 商誉 {field(b, '商誉') / Decimal('1e8'):.2f} 亿元，"
            f"占归母权益 {field(b, '商誉') / equity * 100:.2f}%。财报余额不等同未来减值预测。",
        ),
        dict(
            title="限售股解禁",
            text=(
                f"最近收录日期 {latest_unlock['FREE_DATE'][:10]}，"
                f"类型：{latest_unlock['FREE_SHARES_TYPE']}。本次共取回 {len(unlock)} 个批次。"
                if latest_unlock
                else "接口成功返回零条记录；不能认定未来无解禁。"
            ),
        ),
        dict(
            title="ST / 退市观察",
            text=f"当前来源名称为“{quote['name']}”，"
            "名称带 ST 的观察："
            + ("是。" if "ST" in quote["name"].upper() else "否。")
            + "这是名称规则观察，不能代替交易所风险警示档案。",
        ),
        dict(
            title="现金流与审计",
            text=f"{max(cash)} 经营现金流净额 "
            f"{field(cash[max(cash)], '经营活动产生的现金流量净额') / Decimal('1e8'):.2f} 亿元；"
            "报告标注："
            + str(
                cash[max(cash)]["meta"].get("audit_opinion")
                or cash[max(cash)]["meta"].get("is_audit")
            )
            + "。",
        ),
    ]
    dcf = dcf_input(values, reports, asof)
    return dict(
        subject=dict(code="000333", name="美的集团", exchange="SZSE"),
        quote=quote,
        indices=[q for q in quotes if q["symbol"] != "sz000333"],
        data_dates=dict(
            price=prices[-1]["date"],
            financial=latest,
            disclosed=reports[latest]["published"],
            margin=margin[-1]["DATE"][:10],
            valuation=latest_value["TRADE_DATE"][:10],
        ),
        financial=financial_chart(reports),
        kline=kline_chart(prices),
        valuation=dict(
            kind="pe_history",
            title="PE-TTM 历史（自算 / 供应商披露）",
            x=[r["TRADE_DATE"][:10] for r in valuation],
            series=[
                dict(name="自算 PE-TTM", data=own_pe, lineStyle=dict(width=1.2)),
                dict(
                    name="供应商 PE-TTM",
                    data=[r["PE_TTM"] for r in valuation],
                    lineStyle=dict(width=1.2, type="dashed"),
                ),
            ],
        ),
        margin=dict(
            kind="margin",
            title="融资余额与融资净买入（亿元）",
            x=[r["DATE"][:10] for r in margin],
            series=[
                dict(
                    name="融资余额(亿)",
                    data=[rounded(num(r["RZYE"]) / Decimal("1e8")) for r in margin],
                    lineStyle=dict(width=1.5),
                    areaStyle=dict(opacity=0.08),
                ),
                dict(
                    name="融资净买入(亿)",
                    type="bar",
                    axis=1,
                    barMaxWidth=6,
                    data=[rounded(num(r["RZJME"]) / Decimal("1e8")) for r in margin],
                ),
            ],
        ),
        metrics=[
            dict(
                name="TTM 归母净利",
                value=rounded(earnings / Decimal("1e8")),
                unit="亿元",
                source="lrb",
            ),
            dict(name="营收累计同比", value=rounded(growth), unit="%", source="lrb"),
            dict(name="归母净利累计同比", value=rounded(profit_growth), unit="%", source="lrb"),
            dict(name="自算 PE-TTM", value=rounded(pe), unit="倍", source="lrb / valuation"),
            dict(name="供应商 PB", value=latest_value["PB_MRQ"], unit="倍", source="valuation"),
            dict(
                name="商誉 / 归母权益",
                value=rounded(field(b, "商誉") / equity * 100),
                unit="%",
                source="fzb",
            ),
            dict(
                name="ROE（累计 / 平均归母权益）", value=rounded(roe), unit="%", source="lrb / fzb"
            ),
        ],
        dcf=dcf_result(dcf, DcfAssumptions()),
        simulation=simulate(prices, benchmark),
        checks=checks,
        risks=risks,
        calculation_inputs=dict(prices=prices, benchmark=benchmark),
        notes=[
            "财报为本次查询返回的最新披露/修订版本，PE 历史重建不构成原始时点 PIT 证明。",
            "供应商交叉核对与原始公告独立核验分开：本次原始公告核验为 UNAVAILABLE。",
            "行情、日线、两融、财报更新频率不同，以各模块数据日期为准。",
        ],
    )
