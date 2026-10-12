"""Deterministic full-universe observation plan; never an admission certificate."""

from collections import Counter
from datetime import date, timedelta

from ..contracts import digest
from .history import is_a_share, query_segments
from .inputs import inspect_inputs

VERSION = "market-observation-plan-v1"
DOMAINS = {
    "stock_daily": "原始行情与停牌观察",
    "dividends": "分红送转观察",
    "profit": "年度信号财报观察",
}
GAPS = [
    "历史名单及改码延续：目录并集只用于避免漏采，仍需完整历史身份与独立核对。",
    "财报：公开季报接口不提供完整初始披露/修订链、准确可得时间及同口径独立指标核对。",
    "公司行为：分红接口不能证明配股、合并、退市安排及无事件区间的完整性。",
    "成交限制：日线的停牌/ST观察不能替代历史规则、涨跌停与开盘可成交档案。",
    "全收益：官方H00300已有观察仍需覆盖整个冻结区间的独立交叉核对。",
]


def build_plan(parent, packets, official=()):
    start, end = parent["meta"]["date_start"], parent["meta"]["date_end"]
    notice = parent.get("notice_audit")
    aliases, boundaries = {}, {}
    if notice and notice["complete"]:
        for event in notice["events"]:
            if event["status"] != "MATCH":
                continue
            old, new = event["old_code"], event["new_code"]
            day = date.fromisoformat(event["effective_day"])
            if (
                old == new
                or not is_a_share(old)
                or not is_a_share(new)
                or old in boundaries
                or new in boundaries
                or not start <= str(day) <= end
                or len(set(event["source_hashes"])) < 2
            ):
                raise ValueError("ambiguous symbol continuity evidence")
            aliases[new] = old
            boundaries[old] = dict(end=str(day - timedelta(days=1)))
            boundaries[new] = dict(start=str(day))
    basics, observed, sessions = {}, {}, set()
    for packet in packets:
        kind = packet["query"]["kind"]
        for row in packet["rows"]:
            if kind == "basic":
                if row["code"] in basics:
                    raise ValueError("ambiguous basic identity")
                basics[row["code"]] = row
            elif kind == "members" and is_a_share(row["code"]):
                observed.setdefault(row["code"], []).append(packet["query"]["day"])
            elif kind == "calendar" and row["is_trading_day"] == "1":
                if start <= row["calendar_date"] <= end:
                    sessions.add(row["calendar_date"])
    if not sessions or not basics or not observed:
        raise ValueError("frozen historical universe and calendar required")
    official_by_code = {}
    for row in official:
        official_by_code.setdefault(row["code"], []).append(row)
    securities = []
    for code in sorted(set(basics) | set(observed) | set(official_by_code)):
        if not is_a_share(code):
            continue
        native = basics.get(code, {})
        if native.get("type") in {"2", "3", "4", "5"} and code not in official_by_code:
            continue
        intervals = [(r["listed_at"], r.get("delisted_at")) for r in official_by_code.get(code, [])]
        if native.get("ipoDate"):
            intervals.append((native["ipoDate"], native.get("outDate")))
        for listed, retired in intervals:
            date.fromisoformat(listed)
            if retired and date.fromisoformat(retired) < date.fromisoformat(listed):
                raise ValueError("invalid observed lifecycle")
        intersects = [(a, b) for a, b in intervals if a <= end and (not b or b >= start)]
        if not intersects and intervals and code not in observed:
            continue
        # Keep unknown identities and either side of conflicting intervals. No
        # source gets silently preferred; these bounds only schedule observations.
        first = max(start, min([a for a, _ in intersects] + observed.get(code, []) or [start]))
        last = min(end, max([b or end for _, b in intersects] + observed.get(code, []) or [end]))
        if not intersects:
            # A single annual observation is not a one-day lifecycle.
            first, last = start, end
        if code in boundaries:
            first = max(first, boundaries[code].get("start", start))
            last = min(last, boundaries[code].get("end", end))
            if first > last:
                continue
        securities.append(
            dict(
                code=code,
                name=native.get("code_name")
                or next((r["name"] for r in official_by_code.get(code, [])), code),
                start=first,
                end=last,
                identity_status="PARTIAL" if intervals else "UNAVAILABLE",
                delisted_observed=any(b and start <= b <= end for _, b in intervals),
                intervals=[
                    dict(listed_at=a, delisted_at=b or None)
                    for a, b in sorted(set(intervals), key=str)
                ],
                **(
                    dict(
                        observed_identity_id="OBS-"
                        + digest(dict(market="XSHG_XSHE_A", anchor=aliases.get(code, code)))[:24],
                        continuity_status="CONFIRMED_EVENT" if code in boundaries else "UNREVIEWED",
                    )
                    if notice
                    else {}
                ),
            )
        )
    if not securities or len(securities) > 20000:
        raise ValueError("invalid market observation scope")
    years = [a["year"] for a in parent["historical_data"]["annual"]]
    if not years:
        raise ValueError("annual signals required")
    return dict(
        schema_version="market-observation-plan-v2" if notice else VERSION,
        parent_run_id=parent["run_id"],
        parent_hash=digest(parent),
        investigation_id=parent["investigation_id"],
        start=start,
        end=end,
        sessions=sorted(sessions),
        years=years,
        securities=securities,
        source_hashes=sorted(digest(p) for p in packets),
        official_rows_hash=digest(list(official)),
        market="XSHG_XSHE_A",
        **(dict(notice_audit_hash=digest(notice)) if notice else {}),
    )


def queries_for(plan, stock):
    code = stock["code"]
    queries = [
        dict(kind="stock_daily", code=code, start=str(a), end=str(b))
        for a, b in query_segments(
            date.fromisoformat(stock["start"]), date.fromisoformat(stock["end"])
        )
    ]
    queries += [
        dict(kind="dividends", code=code, year=y)
        for y in range(
            date.fromisoformat(stock["start"]).year, date.fromisoformat(stock["end"]).year + 1
        )
    ]
    periods = sorted({p for y in plan["years"] for p in [(y - 1, 1), (y - 1, 4), (y, 1)]})
    queries += [dict(kind="profit", code=code, year=y, quarter=q) for y, q in periods]
    return queries


def all_queries(plan):
    return [q for stock in plan["securities"] for q in queries_for(plan, stock)]


def inspect_packet(packet, query, plan, stock):
    if packet["query"] != query or packet["provider"] != "baostock":
        raise ValueError("market source/query mismatch")
    if query not in queries_for(plan, stock):
        raise ValueError("query outside frozen market plan")
    sessions = [s for s in plan["sessions"] if stock["start"] <= s <= stock["end"]]
    year = (
        query["year"] + (query.get("quarter") == 4)
        if query["kind"] == "profit"
        else plan["years"][0]
    )
    target = dict(
        instrument_id=stock["code"],
        name=stock["name"],
        provider_code=stock["code"],
        start=stock["start"],
        end=stock["end"],
        signal_year=year,
        signal_day=f"{year}-04-30",
        execution_day=f"{year}-05-01",
        expected_sessions=sessions,
    )
    result = inspect_inputs([packet], target)
    expected = [
        s
        for s in sessions
        if query.get("start", stock["start"]) <= s <= query.get("end", stock["end"])
    ]
    return dict(
        query=query,
        packet_hash=digest(packet),
        rows=len(packet["rows"]),
        empty=not packet["rows"],
        missing_sessions=len(expected) - result["prices"]["row_count"]
        if query["kind"] == "stock_daily"
        else None,
        status="PARTIAL" if packet["rows"] else "UNAVAILABLE",
    )


def coverage(plan, results, offset=0, limit=50):
    # A provider's outDate can describe retirement of a code, rather than the
    # issuer. Only a confirmed shared identity with matching transition bounds
    # permits this classification; old v1 plans retain their original observation.
    identity_groups = {}
    for stock in plan["securities"]:
        if stock.get("continuity_status") == "CONFIRMED_EVENT":
            identity_groups.setdefault(stock["observed_identity_id"], []).append(stock)
    symbol_exits = {
        old["code"]
        for group in identity_groups.values()
        for old in group
        for new in group
        if old["end"] < new["start"]
        and any(i["delisted_at"] == new["start"] for i in old["intervals"])
    }
    domains = {
        k: dict(kind=k, label=v, planned=0, frozen=0, empty=0, rows=0) for k, v in DOMAINS.items()
    }
    stocks, totals = [], Counter()
    for stock in plan["securities"]:
        queries = queries_for(plan, stock)
        done = [results[digest(q)] for q in queries if digest(q) in results]
        for q in queries:
            domain = domains[q["kind"]]
            domain["planned"] += 1
            if item := results.get(digest(q)):
                domain["frozen"] += 1
                domain["empty"] += item["empty"]
                domain["rows"] += item["rows"]
        stocks.append(
            {
                **stock,
                "symbol_change_exit": stock["code"] in symbol_exits,
                "delisted_observed": stock["delisted_observed"]
                and stock["code"] not in symbol_exits,
                "planned": len(queries),
                "frozen": len(done),
                "empty": sum(r["empty"] for r in done),
                "missing_sessions": sum(r["missing_sessions"] or 0 for r in done)
                if any(r["query"]["kind"] == "stock_daily" for r in done)
                else None,
                "status": "OBSERVED"
                if len(done) == len(queries)
                else "PARTIAL"
                if done
                else "PENDING",
            }
        )
        totals[stocks[-1]["status"]] += 1
    return dict(
        stock_count=len(stocks),
        observed_identity_count=len({s.get("observed_identity_id", s["code"]) for s in stocks}),
        delisted_count=sum(s["delisted_observed"] for s in stocks),
        symbol_change_exit_count=len(symbol_exits),
        unknown_identity_count=sum(s["identity_status"] == "UNAVAILABLE" for s in stocks),
        observed_stock_count=totals["OBSERVED"],
        partial_stock_count=totals["PARTIAL"],
        query_count=sum(d["planned"] for d in domains.values()),
        frozen_count=sum(d["frozen"] for d in domains.values()),
        domains=list(domains.values()),
        stocks=stocks[offset : offset + limit],
        offset=offset,
        limit=limit,
    )
