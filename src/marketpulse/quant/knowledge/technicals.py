"""Fixed auxiliary technicals from sealed, adjusted OHLC; no model arithmetic."""

import copy
from decimal import Decimal, localcontext

from ..compute.indicators import _ema
from .features import number, point, series

VERSION = "investment-technicals-v1"
GUIDES = {
    "MACD": "https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd",
    "RSI": "https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI",
    "ATR": "https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr",
}
GUIDANCE = (
    "MACD和均线属于同源价格证据，不能作为互相独立的确认票；交叉仅是当期状态，不保证趋势。",
    "MACD在震荡中可能频繁反复；RSI高低不是立即反转或买卖指令，强趋势可持续极端。",
    "ATR只衡量波动、不判断方向；仅辅助理解风险距离，不替代原路线退出纪律，不承诺止损成交。",
)


def wilder(values, window=14):
    out, current, seed = [], None, []
    for value in values:
        if value is None:
            current, seed = None, []
        elif current is None:
            seed.append(value)
            if len(seed) == window:
                current = sum(seed) / window
        else:
            current = (current * (window - 1) + value) / window
        out.append(current)
    return out


def compute(packet):
    with localcontext() as context:
        context.prec = 40
        stocks = []
        for stock in packet["stocks"]:
            sid, cutoff = stock["ts_code"], packet["market_date"]
            exchange = "SSE" if sid.endswith(".SH") else "SZSE"
            sessions = sorted(
                {
                    r["cal_date"]
                    for r in packet["calendars"][exchange]
                    if str(r["is_open"]) == "1" and r["cal_date"] <= cutoff
                }
            )[-120:]
            rows, factors = (
                series(stock["daily"], sid, cutoff),
                series(stock["adjustments"], sid, cutoff),
            )
            if (
                len(sessions) < 61
                or cutoff not in factors
                or number(factors[cutoff]["adj_factor"]) <= 0
            ):
                raise ValueError("指标历史或锚点复权因子不足")
            anchor = number(factors[cutoff]["adj_factor"])
            prices = []
            for day in sessions:
                if day not in rows or day not in factors:
                    prices.append(None)
                    continue
                factor = number(factors[day]["adj_factor"])
                close, high, low = (number(rows[day][k]) for k in ("close", "high", "low"))
                if factor <= 0 or not 0 < low <= close <= high:
                    raise ValueError("技术指标价格或因子无效")
                prices.append(
                    dict(
                        close=close * factor / anchor,
                        high=high * factor / anchor,
                        low=low * factor / anchor,
                    )
                )
            closes = [p["close"] if p else None for p in prices]
            dif = [
                a - b if a is not None and b is not None else None
                for a, b in zip(_ema(closes, 12), _ema(closes, 26), strict=True)
            ]
            dea = _ema(dif, 9)
            hist = [
                a - b if a is not None and b is not None else None
                for a, b in zip(dif, dea, strict=True)
            ]
            changes = [None] + [
                b - a if a is not None and b is not None else None
                for a, b in zip(closes[:-1], closes[1:], strict=True)
            ]
            gain = wilder([max(v, Decimal(0)) if v is not None else None for v in changes])
            loss = wilder([max(-v, Decimal(0)) if v is not None else None for v in changes])
            rsi = [
                100 * g / (g + loss_value)
                if g is not None and loss_value is not None and g + loss_value > 0
                else None
                for g, loss_value in zip(gain, loss, strict=True)
            ]
            tr = [None] + [
                max(
                    p["high"] - p["low"],
                    abs(p["high"] - prev["close"]),
                    abs(p["low"] - prev["close"]),
                )
                if p and prev
                else None
                for prev, p in zip(prices[:-1], prices[1:], strict=True)
            ]
            atr = wilder(tr)
            values = dict(
                macd_dif=dif,
                macd_dea=dea,
                macd_histogram=hist,
                rsi_14=rsi,
                atr_14=atr,
                atr_ratio=[
                    v / p if v is not None and p is not None else None
                    for v, p in zip(atr, closes, strict=True)
                ],
            )
            definitions = dict(
                macd_dif="EMA12-EMA26;SMA_seed",
                macd_dea="EMA9(DIF);SMA_seed",
                macd_histogram="DIF-DEA;no_x2",
                rsi_14="Wilder_RSI14;14_changes_seed;flat=null",
                atr_14="Wilder_ATR14;TR=max(H-L,abs(H-prevC),abs(L-prevC));14_changes_seed",
                atr_ratio="ATR14/adjusted_close",
            )
            window = dict(
                start=sessions[0],
                end=cutoff,
                warmup="reset_on_missing;fixed_120_session_max",
                adjustment="provider_factor_normalized_to_cutoff",
            )
            metrics = {
                k: point(
                    v[-1],
                    "ratio"
                    if k == "atr_ratio"
                    else "index_0_100"
                    if k == "rsi_14"
                    else "CNY/share",
                    definitions[k],
                    window,
                    ["daily", "adj_factor", "trade_cal"],
                    reason="warmup_missing_or_flat",
                )
                for k, v in values.items()
            }
            cross = "INSUFFICIENT"
            if hist[-1] is not None and hist[-2] is not None:
                cross = (
                    "CROSS_UP"
                    if hist[-2] <= 0 < hist[-1]
                    else "CROSS_DOWN"
                    if hist[-2] >= 0 > hist[-1]
                    else "ABOVE_SIGNAL"
                    if hist[-1] > 0
                    else "BELOW_SIGNAL"
                    if hist[-1] < 0
                    else "ON_SIGNAL"
                )
            stocks.append(
                dict(
                    security_id=sid,
                    metrics=metrics,
                    macd_state=cross,
                    points=[
                        dict(
                            date=d,
                            **{
                                k: str(v[i]) if v[i] is not None else None
                                for k, v in values.items()
                            },
                        )
                        for i, d in enumerate(sessions)
                    ],
                    guidance=list(GUIDANCE),
                )
            )
        return dict(
            version=VERSION,
            stocks=stocks,
            definitions=definitions,
            parameters=dict(macd=[12, 26, 9], rsi=14, atr=14, histogram_multiplier=1),
            guides=GUIDES,
            validation="AUXILIARY_UNVALIDATED",
        )


def enrich(context, evidence, technicals, packet):
    result, facts = copy.deepcopy(context), copy.deepcopy(evidence)
    result["protocol"] = "public-investment-context-v3"
    result["technical_guidance"] = list(GUIDANCE)
    for view in technicals["stocks"]:
        sid = view["security_id"]
        refs = [
            s["source"]["blob_ref"]
            for s in packet["sources"]
            if sid in s["role"] and s["role"].startswith(("plan_daily_", "plan_adjustment_"))
        ]
        for key in ("macd_histogram", "rsi_14", "atr_ratio"):
            metric = view["metrics"][key]
            if metric["value"] is None:
                continue
            fact = dict(
                evidence_id="T-" + sid + "-" + key,
                security_id=sid,
                kind="TECHNICAL",
                label={
                    "macd_histogram": "MACD柱(DIF-DEA，不乘二)",
                    "rsi_14": "Wilder RSI14",
                    "atr_ratio": "Wilder ATR14占收盘价",
                }[key],
                value=metric["value"],
                unit=metric["unit"],
                window=metric["window"],
                formula=metric["formula"],
                source_refs=refs,
            )
            facts.append(fact)
            result["evidence"].append(
                {k: v for k, v in fact.items() if k not in {"formula", "source_refs"}}
            )
    facts.sort(key=lambda f: f["evidence_id"])
    result["evidence"].sort(key=lambda f: f["evidence_id"])
    return result, facts
