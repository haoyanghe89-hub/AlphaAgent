"""SW2021 market observations rebuilt from sealed Tushare originals.

The declared Shanghai/Shenzhen scope excludes Beijing explicitly. Current
membership is not used to claim a historical constituent backtest.
"""

from datetime import UTC, datetime
from decimal import Decimal
from statistics import median

from ..contracts import digest
from . import tushare
from .candidate_sources import checked
from .contracts import SectorHeatInputs
from .market import number
from .next_session.calendar import load_calendar, parse_annual
from .sector_profiles import PROFILES, profile
from .sector_selection import rank_sectors
from .service import SHANGHAI
from .tools import SourceClient, SourceUnavailable

VERSION = "tushare-sw2021-market-2026-10-14-v1"
SCOPE = (
    "申万2021三级分类中，半导体设备与集成电路制造的当次沪深A股完整成分联合池；不是全市场成交占比。"
)


def ready_sessions(calendar, anchor):
    # Daily ingestion is documented as after close. Avoid the ingestion window.
    return [
        d for d in calendar["sessions"] if datetime.fromisoformat(d + "T17:30:00+08:00") <= anchor
    ][-21:]


def collect(repo, state, stop, save, step):
    client = SourceClient(repo, state, save, stop, max_requests=100)
    snapshot = dict(version=VERSION, entries=[], gaps=[])

    def fetch(role, api, params):
        source, values = tushare.fetch(client, "ts_market_" + role, api, params)
        snapshot["entries"].append(dict(role=role, source=source))
        return values

    step("classification", "读取申万2021三级行业与完整成员；核对沪深范围")
    fetch("catalog", "index_classify", dict(level="L3", src="SW2021"))
    fetch("master", "stock_basic", dict(exchange="", list_status="L"))
    catalog = tushare.records(
        checked(repo, snapshot["entries"][0]["source"], datetime.now(UTC).isoformat())
    )
    for name in PROFILES:
        matches = [r for r in catalog if r["industry_name"] == name]
        if len(matches) != 1:
            raise SourceUnavailable(name + "申万分类缺失或冲突")
        code = matches[0]["index_code"]
        fetch("members:" + code, "index_member_all", dict(l3_code=code, is_new="Y"))
    snapshot["calendar"] = load_calendar(client, "SSE").model_dump(mode="json")
    anchor = datetime.now(UTC).astimezone(SHANGHAI)
    snapshot["market_at"] = anchor.isoformat()
    snapshot["sessions"] = ready_sessions(snapshot["calendar"], anchor)
    if len(snapshot["sessions"]) != 21 or not (
        snapshot["calendar"]["coverage_start"]
        <= str(anchor.date())
        <= snapshot["calendar"]["coverage_end"]
    ):
        raise SourceUnavailable("当年核定日历与共同窗口不足")
    fetch(
        "calendar",
        "trade_cal",
        dict(
            exchange="SSE",
            start_date=snapshot["sessions"][0].replace("-", ""),
            end_date=snapshot["sessions"][-1].replace("-", ""),
        ),
    )
    step("prices", "按交易日批量读取行情并封存；复用六小时内已核验原文")
    for day in snapshot["sessions"]:
        fetch("daily:" + day, "daily", dict(trade_date=day.replace("-", "")))
    snapshot["cutoff_at"] = datetime.now(UTC).isoformat()
    snapshot["sources"] = [c["source"] for c in state["calls"] if c.get("source")]
    return snapshot


def tables(repo, snapshot):
    entries = {}
    for item in snapshot["entries"]:
        role, source = item["role"], item["source"]
        if (
            role in entries
            or source["source_family"] != "TUSHARE"
            or source["url"].rstrip("/") != tushare.URL
        ):
            raise ValueError("invalid Tushare source binding")
        entries[role] = (source, tushare.records(checked(repo, source, snapshot["cutoff_at"])))
    return entries


def rebuild(repo, snapshot):
    entries = tables(repo, snapshot)
    anchor = datetime.fromisoformat(snapshot["market_at"])
    calendar, days = snapshot["calendar"], snapshot["sessions"]
    official = [
        str(d)
        for d in parse_annual(repo.raw_bytes(calendar["source_refs"][0]).decode("utf8"), 2026)
    ]
    repo.raw_bytes(calendar["source_refs"][1])
    if (
        official != calendar["sessions"]
        or ready_sessions(calendar, anchor) != days
        or len(days) != 21
        or anchor > datetime.fromisoformat(snapshot["cutoff_at"])
    ):
        raise ValueError("Tushare scan calendar/window mismatch")
    trading = entries["calendar"][1]
    if len({r["cal_date"] for r in trading}) != len(trading) or sorted(
        r["cal_date"] for r in trading if int(r["is_open"]) == 1
    ) != [d.replace("-", "") for d in days]:
        raise ValueError("Tushare calendar differs from exchange originals")
    catalog = entries["catalog"][1]
    if (
        not catalog
        or len(catalog) >= 2000
        or len({r["index_code"] for r in catalog}) != len(catalog)
        or any(r["level"] != "L3" or r["src"] != "SW2021" for r in catalog)
    ):
        raise ValueError("incomplete or mixed industry classification")
    master_rows = entries["master"][1]
    master = {r["ts_code"]: r for r in master_rows}
    if len(master) != len(master_rows) or not master or len(master) >= 6000:
        raise ValueError("incomplete security master")
    daily = {}
    for day in days:
        values = entries["daily:" + day][1]
        by_id = {r["ts_code"]: r for r in values}
        if (
            not values
            or len(values) >= 6000
            or len(by_id) != len(values)
            or any(r["trade_date"] != day.replace("-", "") for r in values)
        ):
            raise ValueError("daily batch empty, truncated, duplicated or cross-date")
        daily[day] = by_id
    memberships, metrics, sectors, excluded, union = [], [], [], [], set()
    totals_union = [Decimal(0) for _ in range(20)]
    complete_union = True
    boards = []
    for name in PROFILES:
        matches = [r for r in catalog if r["industry_name"] == name]
        if len(matches) != 1:
            raise ValueError("declared industry classification missing")
        board = matches[0]
        boards.append(board)
        code = board["index_code"]
        source, members = entries["members:" + code]
        if (
            not members
            or len(members) >= 2000
            or len({r["ts_code"] for r in members}) != len(members)
        ):
            raise ValueError("membership empty, truncated or duplicated")
        if any(
            r["l3_code"] != code or r["l3_name"] != name or r["is_new"] != "Y" or r.get("out_date")
            for r in members
        ):
            raise ValueError("membership classification or validity conflict")
        included = []
        for member in members:
            sid = member["ts_code"]
            if sid.endswith(".BJ"):
                excluded.append(
                    dict(
                        security_id=sid,
                        name=member["name"],
                        industry=name,
                        reason="本期明确限定沪深A股，北交所未接入",
                    )
                )
                continue
            identity = master.get(sid)
            if (
                not sid.endswith((".SH", ".SZ"))
                or not identity
                or not tushare.same_security_name(identity["name"], member["name"])
                or identity["list_status"] != "L"
            ):
                raise ValueError("constituent identity differs from master")
            if sid in union:
                raise ValueError("separate industries overlap")
            union.add(sid)
            included.append(member)
        if not included:
            raise ValueError("declared industry has no eligible market members")
        memberships.append(
            dict(industry_id=code, name=name, members=included, raw_ref=source["blob_ref"])
        )
        available, missing, prices, amounts, missing_reasons = [], [], [], [], []
        for member in included:
            sid = member["ts_code"]
            try:
                values = [daily[d][sid] for d in days]
                closes = [number(r["close"], positive=True) for r in values]
                for r in values:
                    o, c, h, low = [
                        number(r[f], positive=True) for f in ("open", "close", "high", "low")
                    ]
                    if not low <= min(o, c) <= max(o, c) <= h:
                        raise ValueError("OHLC conflict")
                turnover = [number(r["amount"], nonnegative=True) * 1000 for r in values[-20:]]
                available.append(sid)
                prices.append(closes)
                amounts.append(turnover)
            except (KeyError, ValueError, TypeError):
                missing.append(sid)
                listed = master[sid]["list_date"]
                missing_reasons.append(
                    dict(
                        security_id=sid,
                        reason="上市日期为" + listed + "，尚未形成共同二十一收盘点窗口"
                        if listed > days[0].replace("-", "")
                        else "共同窗口存在缺项或价格冲突，未补零；须进一步核对状态",
                    )
                )
        complete = not missing
        complete_union &= complete
        totals = [sum(v[i] for v in amounts) for i in range(20)] if complete else None
        if totals:
            totals_union = [a + b for a, b in zip(totals_union, totals, strict=True)]
        sectors.append(
            dict(
                industry_id=code,
                membership_ref=source["blob_ref"],
                days=[
                    dict(
                        session=d,
                        turnover=str(totals[i]) if totals else None,
                        members_total=len(included),
                        members_available=len(available),
                    )
                    for i, d in enumerate(days[-20:])
                ],
            )
        )
        latest = [daily[days[-1]].get(r["ts_code"]) for r in included]
        latest_complete = all(r is not None for r in latest)
        latest_amounts = (
            [number(r["amount"], nonnegative=True) * 1000 for r in latest]
            if latest_complete
            else []
        )
        latest_total = sum(latest_amounts) if latest_complete else None
        traded = [
            i
            for i, a in enumerate(latest_amounts)
            if a > 0 and number(latest[i]["pre_close"], nonnegative=True) > 0
        ]
        metrics.append(
            dict(
                industry_id=code,
                name=name,
                members_total=len(included),
                available=len(available),
                missing=missing,
                missing_reasons=missing_reasons,
                latest_complete=latest_complete,
                last_turnover=str(latest_total) if latest_complete else None,
                median_member_return_20=str(median(p[-1] / p[0] - 1 for p in prices))
                if complete
                else None,
                breadth=str(
                    Decimal(
                        sum(
                            number(latest[i]["close"], positive=True)
                            > number(latest[i]["pre_close"], positive=True)
                            for i in traded
                        )
                    )
                    / len(traded)
                )
                if latest_complete and traded
                else None,
                breadth_eligible_count=len(traded),
                not_traded_count=len(included) - len(traded),
                top3_turnover_share=str(sum(sorted(latest_amounts)[-3:]) / latest_total)
                if latest_total
                else None,
                business_profile=profile(name),
            )
        )
    heat = SectorHeatInputs.model_validate(
        dict(
            classification_version="SW2021:L3:" + digest(boards),
            classification_level="INDUSTRY",
            source_family="TUSHARE",
            sessions=days[-20:],
            sectors=sectors,
            market_turnover=[
                dict(
                    session=d,
                    value=str(totals_union[i]) if complete_union else None,
                    unit="CNY",
                    source_ref=digest(memberships),
                    missing_reason=None
                    if complete_union
                    else "固定沪深联合池成员序列缺失，未缩小分母",
                )
                for i, d in enumerate(days[-20:])
            ],
            market_coverage_complete=complete_union,
            universe_ref=digest(memberships),
            cutoff_at=snapshot["cutoff_at"],
        )
    )
    ranked = rank_sectors(heat, datetime.fromisoformat(snapshot["cutoff_at"]))
    ranked["selection"] = ranked["selection"].model_dump(mode="json")
    order = {r["industry_id"]: i for i, r in enumerate(ranked["ranking"])}
    for metric in metrics:
        rank = next((r for r in ranked["ranking"] if r["industry_id"] == metric["industry_id"]), {})
        metric.update(
            mean_20_share=rank.get("mean_20_share"), change_5_share=rank.get("change_5_share")
        )
    metrics.sort(key=lambda r: (order.get(r["industry_id"], 999), r["industry_id"]))
    return dict(
        report=dict(
            stage="P6_MARKET",
            title="市场观察 · 设备与制造",
            cutoff_at=snapshot["cutoff_at"],
            summary=(
                "在本次沪深两行业范围内，"
                + metrics[0]["name"]
                + "的二十日平均成交关注占比居前；先看业务依据，再决定研究哪些公司。"
            )
            if ranked["ranking"]
            else (
                "最新完整交易日，"
                + max(metrics, key=lambda r: Decimal(r["last_turnover"]))["name"]
                + "的成交额在这两个行业中较高。部分成员尚无完整二十日窗口，未发布持续成交关注排名。"
            )
            if all(r["latest_complete"] for r in metrics)
            else "当前成分的共同窗口不完整，保留已取得观察，未缩小分母生成排名。",
        ),
        metrics=metrics,
        selection=ranked,
        heat_inputs=heat.model_dump(mode="json"),
        membership=memberships,
        coverage=dict(
            scope_note=SCOPE,
            full_market_share_status="UNAVAILABLE",
            industries_covered=2,
            members_total=len(union),
            members_available=sum(r["available"] for r in metrics),
            complete_union=complete_union,
            excluded_market_members=excluded,
            unconnected=[r for r in catalog if r not in boards],
            catalog_note="Tushare申万2021三级分类；明确排除北交所。以当次成分回看二十日，不是历史成分回放；价格变化为未复权观察。",
            data_source="Tushare",
            classification="SW2021",
            data_date=days[-1],
            cache_policy="六小时内复用已封存原文，保留实际采集时间；晚于17:30才纳入当日收盘",
        ),
        sources=snapshot["sources"],
        documents=[],
        evidence=[],
        gaps=snapshot["gaps"],
    )
