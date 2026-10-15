"""Time-ordered observations, never a decomposition into causal contributions."""

from datetime import date, datetime, time
from decimal import Decimal, localcontext

from .next_session.labels import SHANGHAI

METHOD = "event-window-observation-2026-10-13-v1"
LIMITS = (
    "同窗涨跌、相对差和公告先后只是观察，不能识别事件因果贡献。",
    "价格为未复权收盘，未核定除权息与全收益；下跌不能直接解释为经营恶化。",
    "半导体主题ETF含不同产业环节，成分权重未核定，只作价格代理，不是设备行业指数。",
    "公告目录最多读取前两页；标题不代表正文结论，遗漏事件、宏观与估值变化仍是替代解释。",
)


def effective_session(published, precision, sessions):
    """Day-only records begin NEXT session even if API supplies a midnight timestamp."""
    if not published or precision == "unknown":
        return None
    stamp = datetime.fromisoformat(published).astimezone(SHANGHAI)
    days = sorted(date.fromisoformat(str(d)) for d in sessions)
    eligible = [
        d
        for d in days
        if d > stamp.date()
        or precision in {"second", "minute"}
        and d == stamp.date()
        and stamp.time() < time(15)
    ]
    return str(eligible[0]) if eligible else None


def observed_return(prices, baseline, sessions):
    required = [baseline, *sessions]
    if not sessions or not baseline or any(prices.get(d) is None for d in required):
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        values = [Decimal(str(prices[d])) for d in required]
        if any(not v.is_finite() or v <= 0 for v in values):
            raise ValueError("invalid observation price")
        return str(values[-1] / values[0] - 1)


def build(flow_report, calendar, events, proxy):
    series = flow_report["series"]
    security = {p["session"]: p["value"] for p in series.get("security_close", [])}
    market = {p["session"]: p["value"] for p in series.get("market_close", [])}
    sessions = calendar["calendar"]["sessions"] if calendar else []
    timeline = []
    for event in events:
        effective = effective_session(event.get("published_at"), "day", sessions)
        timeline.append(
            dict(
                event,
                effective_session=effective,
                precision="day",
                temporal_note="仅公告日期；从下一交易日开始观察",
            )
        )
    windows = []
    for w in flow_report["windows"]:
        days, baseline = w["sessions"], w["baseline_date"]
        sr, mr, pr = (
            observed_return(p, baseline, days) if len(days) == w["window"] else None
            for p in (security, market, proxy)
        )
        overlapping = [
            e
            for e in timeline
            if e["effective_session"] and days and days[0] <= e["effective_session"] <= days[-1]
        ]
        windows.append(
            dict(
                window=w["window"],
                sessions=days,
                baseline_date=baseline,
                security_return=sr,
                market_return=mr,
                proxy_return=pr,
                industry_return=None,
                relative_market=str(Decimal(sr) - Decimal(mr))
                if sr is not None and mr is not None
                else None,
                relative_proxy=str(Decimal(sr) - Decimal(pr))
                if sr is not None and pr is not None
                else None,
                events=overlapping,
                identification="NOT_IDENTIFIED",
                alternative_explanations=[
                    "市场与同主题共同变化",
                    "同窗其他公司披露",
                    "除权息、估值及未覆盖消息",
                ],
                limitations=list(LIMITS),
            )
        )
    return dict(
        method_version=METHOD,
        windows=windows,
        timeline=timeline,
        adjustment="raw",
        return_basis="PRICE",
        causal_identification="NOT_IDENTIFIED",
        proxy_label="半导体主题ETF价格代理（512480）",
        industry_benchmark_status="UNAVAILABLE",
        limitations=list(LIMITS),
    )
