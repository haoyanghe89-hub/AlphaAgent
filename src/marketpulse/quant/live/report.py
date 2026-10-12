"""Shared live report contract with explicit applicability and coverage."""

from decimal import Decimal

from ..domain import DcfAssumptions
from . import calculate as c
from .sources import SYMBOLS, quote_values


def unavailable(reason, status="UNAVAILABLE"):
    return dict(status=status, reason=reason)


def dcf_for_subject(subject, values, reports, asof):
    if subject["company_type"] != "4":
        return unavailable(
            "金融企业不适用合并工业 FCFF；本轮提供财务、估值与风险分析。", "NOT_APPLICABLE"
        )
    try:
        # Identity checks are performed before invoking the existing pure DCF kernel.
        for key in ("cash_em", "income_em", "balance_em", "valuation"):
            c.em_rows(values[key], subject["code"])
        # dcf_input's parsing is shared; its security code is an explicit argument.
        return dict(
            status="AVAILABLE",
            **c.dcf_result(
                c.dcf_input(values, reports, asof, code=subject["code"]), DcfAssumptions()
            ),
        )
    except (ValueError, KeyError, ArithmeticError) as error:
        return unavailable("工业 DCF 所需输入不完整或基期不适用：" + str(error)[:160])


def financial_statements(packet, asof):
    reports = c.statement(packet, asof)
    aliases = {
        "归属于母公司的净利润": "归属于母公司所有者的净利润",
        "归属于母公司股东的权益": "归属于母公司股东权益合计",
    }
    for report in reports.values():
        for source, target in aliases.items():
            if source in report["fields"]:
                if (
                    target in report["fields"]
                    and report["fields"][target] != report["fields"][source]
                ):
                    raise ValueError("财报同义字段数值冲突")
                report["fields"][target] = report["fields"][source]
    return reports


def build(values, end, asof, subject):
    symbol, code = subject["symbol"], subject["code"]
    quotes = quote_values(values["quotes"], (symbol, *SYMBOLS[1:]))
    quote = next(q for q in quotes if q["symbol"] == symbol)
    prices = c.daily(values["prices"], symbol, end)
    benchmark = c.daily(values["benchmark"], "sh000300", end)
    result = dict(
        subject=subject,
        quote=quote,
        indices=[q for q in quotes if q["symbol"] != symbol],
        data_dates=dict(
            price=prices[-1]["date"], financial=None, disclosed=None, margin=None, valuation=None
        ),
        kline=c.kline_chart(prices),
        simulation=c.simulate(prices, benchmark),
        calculation_inputs=dict(prices=prices, benchmark=benchmark),
        metrics=[],
        checks=[],
        risks=[],
        source_errors=values.get("source_errors", {}),
        financial=unavailable("利润表或可得期间不完整"),
        valuation=unavailable("未取得适用历史估值数据"),
        margin=unavailable("未取得融资融券数据；可能不是两融标的"),
        dcf=(
            unavailable("金融企业不适用工业 FCFF", "NOT_APPLICABLE")
            if subject["company_type"] != "4"
            else unavailable("财务输入未通过检查")
        ),
        notes=[
            "财报为本次查询返回版本，历史 PE 不构成原始时点 PIT 证明。",
            "供应商一致、可复算与权威原文核验分别记录；权威数值核验未完成。",
            "行情、日线、两融、财报更新频率不同，以各模块数据日期为准。",
        ],
    )

    def rows(key):
        return c.em_rows(values[key], code)

    def metric(name, value, unit, source):
        result["metrics"].append(dict(name=name, value=c.rounded(value), unit=unit, source=source))

    reports = {}
    try:
        reports = financial_statements(values["lrb"], asof)
        latest = max(reports)
        revenue_key = "营业总收入" if "营业总收入" in reports[latest]["fields"] else "营业收入"
        result["revenue_basis"] = revenue_key
        result["data_dates"].update(financial=latest, disclosed=reports[latest]["published"])
        earnings = c.ttm(reports, latest, "归属于母公司所有者的净利润")
        metric("TTM 归母净利", earnings / Decimal("1e8"), "亿元", "lrb")
        for name, key in [
            ("营收累计同比", revenue_key),
            ("归母净利累计同比", "归属于母公司所有者的净利润"),
        ]:
            try:
                prior = str(int(latest[:4]) - 1) + latest[4:]
                denominator = c.field(reports[prior], key)
                if denominator <= 0:
                    raise ValueError("同比基期非正，增长率不适用")
                metric(name, (c.field(reports[latest], key) / denominator - 1) * 100, "%", "lrb")
            except (KeyError, ValueError, ArithmeticError) as error:
                result["notes"].append(name + "：" + str(error))
        periods = sorted(reports)[-12:]
        series = [[], [], []]
        for p in periods:
            for idx, key in enumerate([revenue_key, "归属于母公司所有者的净利润"]):
                try:
                    series[idx].append(c.rounded(c.quarter(reports, p, key) / Decimal("1e8")))
                except (ValueError, KeyError):
                    series[idx].append(None)
            try:
                base = c.quarter(reports, str(int(p[:4]) - 1) + p[4:], revenue_key)
                series[2].append(
                    c.rounded((c.quarter(reports, p, revenue_key) / base - 1) * 100)
                    if base > 0
                    else None
                )
            except (ValueError, KeyError, ArithmeticError):
                series[2].append(None)
        result["financial"] = dict(
            status="AVAILABLE",
            kind="financial_quarterly",
            title=f"单季{revenue_key} / 归母净利（亿元）与营收同比",
            chart_type="bar",
            x=periods,
            series=[
                dict(name="单季营收(亿)", data=series[0], barMaxWidth=18),
                dict(name="单季净利(亿)", data=series[1], barMaxWidth=18),
                dict(name="营收同比%", data=series[2], symbol="circle"),
            ],
        )
        try:
            balance = financial_statements(values["fzb"], asof)
            b = balance[latest]
            equity = c.field(b, "归属于母公司股东权益合计")
            begin = c.field(balance[str(int(latest[:4]) - 1) + "1231"], "归属于母公司股东权益合计")
            if (begin + equity) / 2 > 0:
                metric(
                    "ROE（累计 / 平均归母权益）",
                    c.field(reports[latest], "归属于母公司所有者的净利润")
                    / ((begin + equity) / 2)
                    * 100,
                    "%",
                    "lrb / fzb",
                )
            goodwill = c.field(b, "商誉")
            if equity > 0:
                metric("商誉 / 归母权益", goodwill / equity * 100, "%", "fzb")
            result["risks"].append(
                dict(
                    title="商誉与减值",
                    text=(
                        f"{latest} 商誉 {goodwill / Decimal('1e8'):.2f} 亿元；"
                        "财报余额不等于未来减值预测。"
                    ),
                )
            )
        except (ValueError, KeyError, ArithmeticError) as error:
            result["risks"].append(
                dict(title="权益与商誉口径", text="资料缺项：" + str(error)[:130])
            )
        result["dcf"] = dcf_for_subject(subject, values, reports, asof)
    except (KeyError, ValueError, ArithmeticError) as error:
        result["financial"] = unavailable("财报输入未通过检查：" + str(error)[:160])

    try:
        valuation = sorted(
            [r for r in rows("valuation") if r["TRADE_DATE"][:10] <= end],
            key=lambda r: r["TRADE_DATE"],
        )
        if not valuation:
            raise ValueError("估值响应无可用日期")
        latest_value = valuation[-1]
        result["data_dates"]["valuation"] = latest_value["TRADE_DATE"][:10]
        own = []
        for row in valuation:
            known = {
                p: r
                for p, r in reports.items()
                if r["published"] < row["TRADE_DATE"][:10].replace("-", "")
            }
            try:
                profit = c.ttm(known, max(known), "归属于母公司所有者的净利润")
                own.append(
                    c.rounded(c.num(row["CLOSE_PRICE"]) * c.num(row["TOTAL_SHARES"]) / profit)
                    if profit > 0
                    else None
                )
            except (KeyError, ValueError, ArithmeticError):
                own.append(None)
        result["valuation"] = dict(
            status="AVAILABLE",
            kind="pe_history",
            title="PE-TTM 历史（自算 / 供应商披露）",
            x=[r["TRADE_DATE"][:10] for r in valuation],
            series=[
                dict(name="自算 PE-TTM", data=own, lineStyle=dict(width=1.2)),
                dict(
                    name="供应商 PE-TTM",
                    data=[r.get("PE_TTM") for r in valuation],
                    lineStyle=dict(width=1.2, type="dashed"),
                ),
            ],
        )
        if latest_value.get("PB_MRQ") is not None:
            metric("供应商 PB", c.num(latest_value["PB_MRQ"]), "倍", "valuation")
        if reports:
            profit = c.ttm(reports, max(reports), "归属于母公司所有者的净利润")
            if profit > 0:
                pe = (
                    c.num(latest_value["CLOSE_PRICE"])
                    * c.num(latest_value["TOTAL_SHARES"])
                    / profit
                )
                metric("自算 PE-TTM", pe, "倍", "lrb / valuation")
                if latest_value.get("PE_TTM") is not None:
                    right = c.num(latest_value["PE_TTM"])
                    result["checks"].append(
                        dict(
                            title="PE-TTM",
                            left=c.rounded(pe),
                            right=c.rounded(right),
                            status="MATCH" if abs(pe - right) <= Decimal(".05") else "CONFLICT",
                            tolerance=".05",
                            note="同日收盘 × 总股数 / 最新版本 TTM；股本/EPS口径可能不同",
                        )
                    )
    except (ValueError, KeyError, ArithmeticError) as error:
        result["valuation"] = unavailable("估值输入未通过检查：" + str(error)[:160])

    if reports:
        try:
            latest = max(reports)
            matches = [
                r
                for r in rows("income_check")
                if r["REPORT_DATE"][:10].replace("-", "") == latest
                and r["NOTICE_DATE"][:10] <= asof
            ]
            if len(matches) != 1:
                raise ValueError("披露业绩核对期间不唯一")
            for name, key, other in [
                ("归母净利", "归属于母公司所有者的净利润", "PARENT_NETPROFIT"),
                ("营收", result["revenue_basis"], "TOTAL_OPERATE_INCOME"),
            ]:
                left, right = c.field(reports[latest], key), c.num(matches[0][other])
                result["checks"].append(
                    dict(
                        title=name,
                        left=c.rounded(left),
                        right=c.rounded(right),
                        status=(
                            "METHOD_DIFFERENCE"
                            if key == "营业收入"
                            else "MATCH"
                            if abs(left - right) <= 1
                            else "CONFLICT"
                        ),
                        tolerance="1",
                        note="新浪与东方财富可能同源；营业总收入/营业收入按报表字段记录",
                    )
                )
        except (ValueError, KeyError, ArithmeticError) as error:
            result["checks"].append(
                dict(
                    title="财报跨源核对",
                    status="UNAVAILABLE",
                    left=None,
                    right=None,
                    note=str(error),
                    tolerance=None,
                )
            )
    try:
        margin = sorted(rows("margin"), key=lambda r: r["DATE"])
        if not margin:
            raise ValueError("响应无两融记录，不能据此认定余额为零")
        result["data_dates"]["margin"] = margin[-1]["DATE"][:10]
        result["margin"] = dict(
            status="AVAILABLE",
            kind="margin",
            title="融资余额与融资净买入（亿元）",
            x=[r["DATE"][:10] for r in margin],
            series=[
                dict(
                    name="融资余额(亿)",
                    data=[c.rounded(c.num(r["RZYE"]) / Decimal("1e8")) for r in margin],
                    lineStyle=dict(width=1.5),
                    areaStyle=dict(opacity=0.08),
                ),
                dict(
                    name="融资净买入(亿)",
                    type="bar",
                    axis=1,
                    barMaxWidth=6,
                    data=[c.rounded(c.num(r["RZJME"]) / Decimal("1e8")) for r in margin],
                ),
            ],
        )
    except (KeyError, ValueError, ArithmeticError) as error:
        result["margin"] = unavailable("两融资料：" + str(error)[:160])
    for key, title, sort in [
        ("pledge", "股权质押", "NOTICE_DATE"),
        ("unlock", "限售股解禁", "FREE_DATE"),
    ]:
        try:
            records = rows(key)
            r = max(records, key=lambda r: r[sort]) if records else None
            text = (
                f"最近收录 {r[sort][:10]}；"
                + str(r.get("UNFREEZE_STATE") or r.get("FREE_SHARES_TYPE") or "")
                if r
                else "接口成功返回零条记录。"
            )
            result["risks"].append(dict(title=title, text=text + "资料观察不证明当前无风险。"))
        except (ValueError, KeyError) as error:
            result["risks"].append(dict(title=title, text="资料未取得：" + str(error)[:120]))
    result["risks"].append(
        dict(
            title="ST / 退市观察",
            text="当前来源名称：" + quote["name"] + "；名称判断不能替代交易所历史风险警示档案。",
        )
    )
    try:
        cash = financial_statements(values["llb"], asof)
        p = max(cash)
        value = c.field(cash[p], "经营活动产生的现金流量净额")
        result["risks"].append(
            dict(
                title="现金流与审计",
                text=f"{p} 经营现金流净额 {value / Decimal('1e8'):.2f} 亿元；审计属性见来源报表。",
            )
        )
    except (KeyError, ValueError) as error:
        result["risks"].append(dict(title="现金流与审计", text="资料未取得：" + str(error)[:120]))
    result["coverage"] = {
        key: result[key].get("status", "AVAILABLE")
        for key in ("financial", "valuation", "margin", "dcf", "kline", "simulation")
    }
    return result
