"""Explain recorded research inputs, with reproducible judgments and visible gaps.

This read model never changes the saved report, uses no refreshed quote, and
does not turn historical position or conditional DCF into a return forecast.
"""

from datetime import date, timedelta
from decimal import Decimal

from ..compute.dcf import scenario
from ..domain import DcfAssumptions
from .calculate import num, rounded

VERSION = "reader-overview-v1"


def missing(reason, **extra):
    return dict(status="UNAVAILABLE", reason=reason, **extra)


def proof(title, text, source, locator):
    return dict(title=title, text=text, source=source, locator=locator)


def metric(data, name):
    matches = [m for m in data.get("metrics", []) if m["name"] == name]
    if len(matches) != 1:
        return None
    try:
        return num(matches[0]["value"])
    except (ValueError, ArithmeticError):
        return None


def price_context(data):
    k = data.get("kline", {})
    unknown = missing(
        "日线未通过日期、数值或窗口检查", position="一年位置待判断", trend="近期走势待判断"
    )
    try:
        dates, bars = k["x"], k["ohlc"]
        if not dates or len(dates) != len(bars) or dates != sorted(set(dates)):
            return unknown
        asof = data["data_dates"]["price"]
        end = date.fromisoformat(asof)
        start = end - timedelta(days=364)
        eligible = [
            (i, dt, row)
            for i, (dt, row) in enumerate(zip(dates, bars, strict=True))
            if date.fromisoformat(dt) <= end
        ]
        if not eligible or eligible[-1][1] != asof:
            return unknown
        sample = [
            (i, dt, num(row[1])) for i, dt, row in eligible if date.fromisoformat(dt) >= start
        ]
        if not sample or any(v <= 0 for _, _, v in sample):
            return unknown
        index, last_date, close = sample[-1]
        _, low_date, low = min(sample, key=lambda r: r[2])
        _, high_date, high = max(sample, key=lambda r: r[2])
        position = (close - low) / (high - low) * 100 if high > low else None
        sufficient = (
            date.fromisoformat(sample[0][1]) <= start + timedelta(days=14) and len(sample) >= 120
        )
        label = (
            "样本收盘价相同"
            if position is None
            else "区间偏下"
            if position < 33
            else "区间偏上"
            if position > 67
            else "区间中部"
        )
        if not sufficient:
            label = "一年位置待判断"
        ma20 = ma60 = None
        trend = "近期走势待判断"
        try:
            ma20, ma60 = num(k["ma20"][index]), num(k["ma60"][index])
            if min(ma20, ma60) <= 0:
                raise ValueError("non-positive MA")
            trend = (
                "近期偏弱"
                if close < ma20 < ma60
                else "近期偏强"
                if close > ma20 > ma60
                else "均线信号不一致"
            )
        except (ValueError, KeyError, IndexError, ArithmeticError):
            pass
        details = (
            f"{sample[0][1]} 至 {last_date}，{len(sample)} 个前复权收盘样本；"
            f"最低 {low} 元（{low_date}），最高 {high} 元（{high_date}），"
            f"末日收盘 {close} 元。窗口为截止日向前 364 个自然日（含首尾共 52 周）。"
            "区间位置=(末日收盘−最低收盘)/(最高收盘−最低收盘)×100；"
            "低于33%为偏下，高于67%为偏上，其余为中部。这不是价格历史分位或估值判断。"
            "窗口首个样本距起点超过14天或样本少于120个时不评一年位置。"
            "近期偏弱要求收盘<MA20<MA60，偏强要求收盘>MA20>MA60；其余不一致。"
            f"计算得到区间位置 {rounded(position) if position is not None else '未定义'}%，"
            f"位置判定 {label}；均线判定 {trend}。"
            f"本期 MA20={ma20}、MA60={ma60}。日线盘中高低价未用于此区间。"
        )
        return dict(
            status="AVAILABLE" if sufficient else "PARTIAL",
            start=sample[0][1],
            end=last_date,
            count=len(sample),
            low=rounded(low),
            high=rounded(high),
            close=rounded(close),
            range_position=rounded(position) if position is not None else None,
            from_high=rounded((close / high - 1) * 100),
            position=label,
            trend=trend,
            trend_meaning=(
                "本期收盘低于20日、60日均价，近期走势偏弱。"
                if trend == "近期偏弱"
                else "本期收盘高于20日、60日均价，近期走势偏强。"
                if trend == "近期偏强"
                else "现有均线关系不足以确认明确方向。"
            ),
            ma20=rounded(ma20) if ma20 else None,
            ma60=rounded(ma60) if ma60 else None,
            proof=proof("价格位置与近期走势的依据", details, ["prices"], "reader/price"),
        )
    except (ValueError, KeyError, IndexError, TypeError, ArithmeticError):
        return unknown


def growth_context(data):
    revenue, profit = metric(data, "营收累计同比"), metric(data, "归母净利累计同比")
    result = dict(
        status="PARTIAL",
        judgment="经营增长待判断",
        meaning="收入或利润同比尚缺，不能判断整体增长。",
        quarter_comparison=None,
    )
    if revenue is not None and profit is not None:
        label = (
            "收入、利润均增长"
            if revenue > 0 and profit > 0
            else "收入增长、利润下降"
            if revenue > 0 and profit < 0
            else "收入下降、利润增长"
            if revenue < 0 and profit > 0
            else "收入、利润均下降"
            if revenue < 0 and profit < 0
            else "收入或利润同比持平"
        )
        relationship = "低于" if profit < revenue else "高于" if profit > revenue else "持平于"
        result.update(
            status="AVAILABLE",
            judgment=label,
            meaning=f"利润增速{relationship}收入增速；仅凭增速不能直接判断原因，需结合毛利率与费用。",
        )
    financial = data.get("financial", {})
    try:
        periods = financial["x"]
        series = next(s["data"] for s in financial["series"] if s["name"] == "营收同比%")
        period = data["data_dates"]["financial"]
        prior = str(int(period[:4]) - 1) + period[4:]
        current, previous = num(series[periods.index(period)]), num(series[periods.index(prior)])
        result["quarter_comparison"] = dict(
            period=period,
            prior=prior,
            current=rounded(current),
            previous=rounded(previous),
            direction="低于" if current < previous else "高于" if current > previous else "持平于",
        )
    except (ValueError, KeyError, IndexError, StopIteration, ArithmeticError):
        pass
    result["proof"] = proof(
        "经营判断的依据",
        f"报告期 {data.get('data_dates', {}).get('financial')}；累计收入同比 {revenue}%，"
        f"累计归母净利同比 {profit}%。季度对照 {result['quarter_comparison']}；"
        "累计同比与单季同比单独比较，均不等于未来增长预测。",
        ["lrb"],
        "reader/growth",
    )
    return result


def valuation_context(data):
    unknown = missing(
        "同口径历史估值样本不足或末值与当前指标不一致",
        judgment="贵便宜待判断",
        meaning="单个市盈率不能说明便宜或昂贵，还需历史与同行比较。",
    )
    try:
        pe = metric(data, "自算 PE-TTM")
        v = data["valuation"]
        dates = v["x"]
        values = next(s["data"] for s in v["series"] if s["name"] == "自算 PE-TTM")
        asof = data["data_dates"]["valuation"]
        if dates != sorted(set(dates)) or len(dates) != len(values) or pe is None or pe <= 0:
            return unknown
        sample = []
        for dt, value in zip(dates, values, strict=True):
            date.fromisoformat(dt)
            if dt > asof or value is None:
                continue
            n = num(value)
            if n > 0:
                sample.append((dt, n))
        if (
            len(sample) < 120
            or sample[-1][0] != asof
            or (date.fromisoformat(asof) - date.fromisoformat(sample[0][0])).days < 365
            or abs(sample[-1][1] - pe) > Decimal(".00001")
        ):
            return unknown
        pct = Decimal(sum(v <= pe for _, v in sample)) / len(sample) * 100
        label = "偏低" if pct < 33 else "偏高" if pct > 67 else "居中"
        details = (
            f"{sample[0][0]} 至 {asof}，{len(sample)} 个正值自算 PE-TTM 样本；末值 {pe} 倍。"
            f"样本分位={sum(v <= pe for _, v in sample)}/{len(sample)}×100={rounded(pct)}%。"
            "分位统计小于等于当前PE的样本比例；低于33%为偏低，高于67%为偏高。"
            "财报为最新查询版本，不是严格 PIT，不称为上市以来分位；"
            "不能由样本偏低推导低估，同行、增长与风险比较仍缺。"
        )
        return dict(
            status="AVAILABLE",
            start=sample[0][0],
            end=asof,
            count=len(sample),
            percentile=rounded(pct),
            judgment=f"历史样本{label}，低估待确认"
            if label == "偏低"
            else f"历史样本{label}，贵便宜待确认",
            meaning=f"这个倍数在本次自身历史样本中{label}，但还缺同行、增长与风险比较，不能据此确认便宜。",
            proof=proof("历史估值样本的依据", details, ["lrb", "valuation"], "reader/valuation"),
        )
    except (KeyError, ValueError, IndexError, TypeError, StopIteration, ArithmeticError):
        return unknown


def dcf_context(data):
    d = data.get("dcf", {})
    if d.get("status") == "NOT_APPLICABLE":
        return dict(status="NOT_APPLICABLE", note="金融企业不适用工业 DCF。")
    try:
        assumptions = DcfAssumptions.model_validate(d["assumptions"])
        basis = {k: num(v) for k, v in d["basis"].items()}
        current = scenario(basis, assumptions)["per_share"]
        if abs(current - num(d["per_share"])) > Decimal(".00001"):
            raise ValueError("saved DCF differs from kernel")
        rows = []
        for delta in (Decimal("-.04"), Decimal(0), Decimal(".04")):
            candidate = assumptions.model_dump()
            candidate["g1"] = assumptions.g1 + delta
            try:
                alt = DcfAssumptions.model_validate(candidate)
            except ValueError:
                continue
            rows.append(
                dict(g1=rounded(alt.g1 * 100), per_share=rounded(scenario(basis, alt)["per_share"]))
            )
        a = {k: rounded(num(v) * 100) for k, v in assumptions.model_dump().items()}
        note = "增长率仅为压力测试，其他参数固定；不是预测概率、合理价区间或收益承诺。"
        parameters = (
            f"前5年现金流年增长率 {a['g1']:g}%，后5年 {a['g2']:g}%，"
            f"永续增长率 {a['g_term']:g}%，折现率 {a['discount']:g}%。"
        )
        stress = "；".join(
            f"前5年增长率 {r['g1']:g}% 对应每股估算 {r['per_share']} 元" for r in rows
        )
        return dict(
            status="AVAILABLE",
            per_share=rounded(current),
            assumptions=a,
            growth_sensitivity=rows,
            note=note,
            proof=proof(
                "DCF 情景与增长压力测试",
                parameters + stress + "。"
                "复用原报告 FCFF、股数、现金、债务与少数股东权益，调用同一折现计算内核；" + note,
                ["cash_em", "income_em", "balance_em", "valuation"],
                "reader/dcf",
            ),
        )
    except (KeyError, ValueError, ArithmeticError):
        return missing("DCF 输入不齐或复算未通过", note="不能由现有资料推导目标价。")


def build_overview(data, profile=None):
    price, growth, valuation, dcf = (
        price_context(data),
        growth_context(data),
        valuation_context(data),
        dcf_context(data),
    )
    risk = dict(
        judgment="风险资料待补证",
        meaning="已收录风险记录，不能据此认定当前无风险。"
        if data.get("risks")
        else "风险资料不足，不能据此认定当前无风险。",
    )
    summary = (
        f"{growth['judgment']}；价格{price['position']}，{price['trend']}；"
        f"{valuation['judgment']}。"
    )
    description = data.get("subject", {}).get("description", "")
    if profile:
        rows = profile.get("jbzl") or []
        if len(rows) == 1 and rows[0].get("SECURITY_CODE") == data.get("subject", {}).get("code"):
            description = rows[0].get("ORG_PROFILE") or ""
    return dict(
        version=VERSION,
        summary=summary,
        data_dates=data.get("data_dates", {}),
        company=dict(
            industry=data.get("subject", {}).get("industry"),
            description=str(description).strip()[:1500],
            note="业务收入与利润拆分尚缺，暂不能解释各业务对增长的贡献。",
        ),
        price=price,
        growth=growth,
        valuation=valuation,
        dcf=dcf,
        risk=risk,
        watch=[
            dict(
                horizon="下一期财报",
                condition="若利润增速改善，且现金流同步支持",
                implication="经营改善的依据增加；若收入增长而利润持续承压，则需下调增长判断。",
            ),
            dict(
                horizon="后续交易日",
                condition="若价格与均线关系持续改善",
                implication="重新评估近期走势；单次突破或单日融资流入不足以确认趋势反转。",
            ),
            dict(
                horizon="估值复核",
                condition="若增长、现金流假设及同行估值获得更多证据支持",
                implication="再评估估值吸引力；现金流或增长假设下调时，应重算情景。",
            ),
        ],
        gaps=[
            "分业务与地区的收入、利润贡献",
            "可比公司同日期、同口径估值与增长",
            "行业需求、订单与竞争变化",
            "权威原文数值独立核验与当前风险状态",
        ],
        note="本页判断基于报告保存的输入；顶部报价刷新不改变研究结论。价格位置不等于估值贵便宜。",
    )
