"""A declared two-industry scan, with one fixed union denominator and no model per stock."""

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal

from ..contracts import digest
from ..live.sources import KLINE
from .candidate_sources import LIST, checked, rows
from .contracts import SectorHeatInputs, SectorSelectionRecord
from .market import number
from .next_session.calendar import load_calendar, parse_annual
from .sector_profiles import PROFILES, profile
from .sector_selection import rank_sectors
from .service import SHANGHAI
from .tools import SourceClient, SourceUnavailable, Stopped

VERSION = "covered-market-2026-10-14-v1"
PARTIAL_VERSION = "covered-market-2026-10-14-partial-v2"
NAMES = tuple(PROFILES)
SCOPE_NOTE = (
    "固定范围为半导体设备与集成电路制造的当次完整成分联合池；"
    "占比是范围内成交占比，不是全市场成交占比。"
)


def collect(repo, state, stop, save, step):
    from . import market_history, tushare

    if tushare.load_token():
        return market_history.collect(repo, state, stop, save, step)
    client = SourceClient(repo, state, save, stop, max_requests=100)
    snapshot = dict(version=VERSION, entries=[], gaps=[])

    def fetch(role, key, url, **kwargs):
        source, value = client.fetch(key, url, **kwargs)
        snapshot["entries"].append(dict(role=role, source=source))
        return value

    step("classification", "读取当前行业分类，固定设备与制造两类观察范围")
    catalog, total = [], None
    for page in range(1, 7):
        packet = fetch(
            "catalog:" + str(page),
            "market_catalog_" + str(page),
            LIST,
            family="EASTMONEY_CLASSIFICATION",
            params=dict(
                pn=page, pz=100, fs="m:90+s:8+f:!50", fid="f12", po=1, fields="f12,f14", np=1
            ),
        )
        total = int(packet["data"]["total"])
        catalog.extend(rows(packet))
        if len(catalog) >= total:
            break
    if len(catalog) != total or len({r["f12"] for r in catalog}) != total:
        raise SourceUnavailable("行业目录分页不完整，未改用固定板块代码")
    snapshot["boards"] = []
    all_members = {}
    for name in NAMES:
        matches = [r for r in catalog if r["f14"] == name]
        if len(matches) != 1:
            raise SourceUnavailable(name + "分类缺失或冲突，未缩小观察范围")
        board = matches[0]
        packet = fetch(
            "members:" + board["f12"],
            "market_members_" + board["f12"],
            LIST,
            family="EASTMONEY_CLASSIFICATION",
            params=dict(
                pn=1, pz=100, fs="b:" + board["f12"], fid="f12", po=1, fields="f12,f13,f14", fltt=2
            ),
        )
        members = rows(packet)
        if not members or len(members) != int(packet["data"]["total"]) or len(members) > 30:
            raise SourceUnavailable(name + "成分未完整取得或超出有界预算，未截断")
        snapshot["boards"].append(board)
        for member in members:
            code = member["f12"]
            if code in all_members:
                raise ValueError(
                    "declared separate industries overlap; classification needs review"
                )
            all_members[code] = member
    snapshot["calendar"] = load_calendar(client, "SSE").model_dump(mode="json")
    now = datetime.now(UTC).astimezone(SHANGHAI)
    if (
        not snapshot["calendar"]["coverage_start"]
        <= str(now.date())
        <= snapshot["calendar"]["coverage_end"]
    ):
        raise SourceUnavailable("当前年度日历尚未核定")
    snapshot["market_at"] = now.isoformat()
    snapshot["sessions"] = [
        d
        for d in snapshot["calendar"]["sessions"]
        if datetime.fromisoformat(d + "T15:00:00+08:00") <= now
    ][-21:]
    step("prices", f"批量核对{len(all_members)}只成分的共同交易窗口，不逐股调用模型")
    for code, member in all_members.items():
        if member["f13"] not in (0, 1) or len(code) != 6 or not code.isdigit():
            raise ValueError("invalid market member identity")
        symbol = ("sh" if member["f13"] == 1 else "sz") + code
        try:
            fetch(
                "daily:" + code,
                "market_daily_" + code,
                KLINE,
                family="TENCENT",
                params=dict(
                    param=f"{symbol},day,{snapshot['sessions'][0]},{snapshot['sessions'][-1]},640,"
                ),
            )
        except SourceUnavailable as error:
            snapshot["gaps"].append(dict(code=code, reason=str(error)))
    snapshot["cutoff_at"] = datetime.now(UTC).isoformat()
    snapshot["sources"] = [c["source"] for c in state["calls"] if c.get("source")]
    return snapshot


def rebuild(repo, snapshot):
    from . import market_history, tushare_market

    if snapshot["version"] == market_history.VERSION:
        return market_history.rebuild(repo, snapshot)
    if snapshot["version"] == tushare_market.VERSION:
        return tushare_market.rebuild(repo, snapshot)
    if snapshot["version"] == PARTIAL_VERSION:
        return rebuild_partial(repo, snapshot)
    if snapshot["version"] != VERSION:
        raise ValueError("unknown market protocol")
    entries = {
        e["role"]: (e["source"], checked(repo, e["source"], snapshot["cutoff_at"]))
        for e in snapshot["entries"]
    }
    catalogs = [r for key, (_, p) in entries.items() if key.startswith("catalog:") for r in rows(p)]
    total = int(entries["catalog:1"][1]["data"]["total"])
    if len(catalogs) != total or len({r["f12"] for r in catalogs}) != total:
        raise ValueError("market catalog coverage changed")
    if [r["f14"] for r in snapshot["boards"]] != list(NAMES):
        raise ValueError("market scope must keep both industries")
    calendar = snapshot["calendar"]
    official = [
        str(d)
        for d in parse_annual(repo.raw_bytes(calendar["source_refs"][0]).decode("utf8"), 2026)
    ]
    repo.raw_bytes(calendar["source_refs"][1])
    anchor = datetime.fromisoformat(snapshot["market_at"])
    days = snapshot["sessions"]
    if (
        anchor > datetime.fromisoformat(snapshot["cutoff_at"])
        or official != calendar["sessions"]
        or days
        != [d for d in official if datetime.fromisoformat(d + "T15:00:00+08:00") <= anchor][-21:]
        or len(days) != 21
    ):
        raise ValueError("market sessions/calendar differ from sealed originals")
    sectors, union, metrics, membership = [], set(), [], []
    amounts = {d: Decimal(0) for d in days[-20:]}
    complete_union = True
    for board in snapshot["boards"]:
        if [r for r in catalogs if r["f12"] == board["f12"]] != [board]:
            raise ValueError("market classification identity changed")
        src, packet = entries["members:" + board["f12"]]
        members = rows(packet)
        codes = [m["f12"] for m in members]
        if (
            len(codes) != len(set(codes))
            or not codes
            or len(codes) != int(packet["data"]["total"])
            or set(codes) & union
        ):
            raise ValueError("market membership duplicate/missing/overlapping")
        union.update(codes)
        membership.append(
            dict(
                industry_id=board["f12"],
                name=board["f14"],
                members=members,
                raw_ref=src["blob_ref"],
            )
        )
        available, missing, closes, member_amounts = [], [], [], []
        for m in members:
            code = m["f12"]
            try:
                symbol = ("sh" if m["f13"] == 1 else "sz") + code
                original = entries["daily:" + code][1]["data"][symbol]["day"]
                selected = [r for r in original if r[0] in days]
                if len(selected) != 21 or {r[0] for r in selected} != set(days):
                    raise ValueError("incomplete common daily window")
                selected.sort(key=lambda r: r[0])
                values = [number(r[8], nonnegative=True) * 10000 for r in selected[-20:]]
                prices = [number(r[2], positive=True) for r in selected]
                for r in selected:
                    opening, closing, high, low = [
                        number(r[i], positive=True) for i in (1, 2, 3, 4)
                    ]
                    if not low <= min(opening, closing) <= max(opening, closing) <= high:
                        raise ValueError("market OHLC mismatch")
                available.append(code)
                closes.append(prices)
                member_amounts.append(values)
            except (KeyError, ValueError, TypeError, IndexError):
                missing.append(code)
        complete = not missing
        complete_union &= complete
        totals = [sum(v[i] for v in member_amounts) for i in range(20)] if complete else None
        if totals:
            for d, v in zip(days[-20:], totals, strict=True):
                amounts[d] += v
        sectors.append(
            dict(
                industry_id=board["f12"],
                membership_ref=src["blob_ref"],
                days=[
                    dict(
                        session=d,
                        turnover=str(totals[i]) if totals else None,
                        members_total=len(members),
                        members_available=len(available),
                    )
                    for i, d in enumerate(days[-20:])
                ],
            )
        )
        metrics.append(
            dict(
                industry_id=board["f12"],
                name=board["f14"],
                members_total=len(members),
                available=len(available),
                missing=missing,
                last_turnover=str(totals[-1]) if totals else None,
                median_member_return_20=None,
                breadth=None,
                top3_turnover_share=None,
                business_profile=profile(board["f14"]),
            )
        )
        if complete:
            from statistics import median

            returns = [p[-1] / p[0] - 1 for p in closes]
            traded = [i for i, values in enumerate(member_amounts) if values[-1] > 0]
            metrics[-1].update(
                median_member_return_20=str(median(returns)),
                breadth=str(
                    Decimal(sum(closes[i][-1] > closes[i][-2] for i in traded)) / len(traded)
                )
                if traded
                else None,
                breadth_eligible_count=len(traded),
                not_traded_count=len(closes) - len(traded),
                top3_turnover_share=str(
                    sum(sorted([v[-1] for v in member_amounts], reverse=True)[:3]) / totals[-1]
                )
                if totals[-1]
                else None,
            )
    heat = SectorHeatInputs.model_validate(
        dict(
            classification_version="EM_DC_LEVEL3_CURRENT:" + digest(snapshot["boards"]),
            classification_level="INDUSTRY",
            source_family="TENCENT",
            sessions=days[-20:],
            sectors=sectors,
            market_turnover=[
                dict(
                    session=d,
                    value=str(amounts[d]) if complete_union else None,
                    unit="CNY",
                    source_ref=digest(membership),
                    missing_reason=None if complete_union else "固定联合池成员序列缺失，未缩小分母",
                )
                for d in days[-20:]
            ],
            market_coverage_complete=complete_union,
            universe_ref=digest(membership),
            cutoff_at=snapshot["cutoff_at"],
        )
    )
    ranked = rank_sectors(heat, datetime.fromisoformat(snapshot["cutoff_at"]))
    ranked["selection"] = ranked["selection"].model_dump(mode="json")
    for metric in metrics:
        rank = next((r for r in ranked["ranking"] if r["industry_id"] == metric["industry_id"]), {})
        metric.update(
            mean_20_share=rank.get("mean_20_share"), change_5_share=rank.get("change_5_share")
        )
    order = {r["industry_id"]: i for i, r in enumerate(ranked["ranking"])}
    metrics.sort(key=lambda r: (order.get(r["industry_id"], 999), r["industry_id"]))
    return dict(
        report=dict(
            stage="P6_MARKET",
            title="市场观察 · 设备与制造",
            cutoff_at=snapshot["cutoff_at"],
            summary=(
                "在本次两行业范围内，"
                + metrics[0]["name"]
                + "的二十日平均成交关注占比居前；先看行业依据，再决定研究哪些公司。"
            )
            if ranked["ranking"]
            else "共同窗口或固定分母不完整，本次保留覆盖与单项观察，未形成成交关注排序。",
        ),
        metrics=metrics,
        selection=ranked,
        heat_inputs=heat.model_dump(mode="json"),
        membership=membership,
        coverage=dict(
            scope_note=SCOPE_NOTE,
            full_market_share_status="UNAVAILABLE",
            industries_covered=2,
            members_total=len(union),
            members_available=sum(r["available"] for r in metrics),
            complete_union=complete_union,
            unconnected=[r for r in catalogs if r["f14"] not in NAMES],
            catalog_note="东方财富三级行业目录；其余行业未接入本次扫描。以本次成分回看二十日，不是当时成分的历史回放。",
        ),
        sources=snapshot["sources"],
        documents=[],
        evidence=[],
        gaps=snapshot["gaps"],
    )


def verify(repo, data):
    snapshot = repo.value(data["market_snapshot_ref"])
    if {
        k: v for k, v in data.items() if k not in {"market_snapshot_ref", "enhanced_research"}
    } != rebuild(repo, snapshot):
        raise ValueError("market source/calculation/report changed")
    if "enhanced_research" in data:
        from .market_enhanced import verify_result

        verify_result(repo, data)


def collect_partial(repo, state, stop, save, step, reason):
    """An unavailable industry source cannot suppress independently available market prices."""
    client = SourceClient(repo, state, save, stop, max_requests=100)
    step("market_prices", "行业目录不可用，独立核对沪深三百行情；不计算行业排序")
    calendar = load_calendar(client, "SSE").model_dump(mode="json")
    anchor = datetime.now(UTC).astimezone(SHANGHAI)
    if not calendar["coverage_start"] <= str(anchor.date()) <= calendar["coverage_end"]:
        raise SourceUnavailable("当前年度日历未核定")
    days = [
        d for d in calendar["sessions"] if datetime.fromisoformat(d + "T15:00:00+08:00") <= anchor
    ][-21:]
    source, _ = client.fetch(
        "market_csi300",
        KLINE,
        family="TENCENT",
        params=dict(param=f"sh000300,day,{days[0]},{days[-1]},640,"),
    )
    return dict(
        version=PARTIAL_VERSION,
        calendar=calendar,
        market_at=anchor.isoformat(),
        sessions=days,
        cutoff_at=datetime.now(UTC).isoformat(),
        index_source=source,
        reason=reason,
        sources=[c["source"] for c in state["calls"] if c.get("source")],
    )


def rebuild_partial(repo, snapshot):
    calendar = snapshot["calendar"]
    official = [
        str(d)
        for d in parse_annual(repo.raw_bytes(calendar["source_refs"][0]).decode("utf8"), 2026)
    ]
    repo.raw_bytes(calendar["source_refs"][1])
    anchor = datetime.fromisoformat(snapshot["market_at"])
    days = snapshot["sessions"]
    if (
        official != calendar["sessions"]
        or len(days) != 21
        or days
        != [d for d in official if datetime.fromisoformat(d + "T15:00:00+08:00") <= anchor][-21:]
        or anchor > datetime.fromisoformat(snapshot["cutoff_at"])
    ):
        raise ValueError("partial market calendar/window changed")
    packet = checked(repo, snapshot["index_source"], snapshot["cutoff_at"])
    selected = [r for r in packet["data"]["sh000300"]["day"] if r[0] in days]
    if len(selected) != 21 or {r[0] for r in selected} != set(days):
        raise ValueError("market index window incomplete; cannot publish market observation")
    selected.sort(key=lambda r: r[0])
    prices = []
    for row in selected:
        opening, closing, high, low = [number(row[i], positive=True) for i in (1, 2, 3, 4)]
        if not low <= min(opening, closing) <= max(opening, closing) <= high:
            raise ValueError("market index OHLC mismatch")
        prices.append(closing)
    change = prices[-1] / prices[0] - 1
    selection = SectorSelectionRecord(
        selected_at=snapshot["cutoff_at"],
        data_cutoff=snapshot["cutoff_at"],
        classification_version="EM_DC_LEVEL3_UNAVAILABLE",
        coverage_ref=digest(dict(names=NAMES, reason=snapshot["reason"])),
        ranking_method="HEAT_V1:mean20_share_desc,mean5_minus_prior5_desc,industry_id_asc",
        metric_refs=(),
        chosen_scope="NONE",
        selection_reason="行业目录未取得，不计算分母与行业成交关注排序。",
        provisional=False,
    ).model_dump(mode="json")
    direction = "上涨" if change > 0 else "下跌" if change < 0 else "持平"
    return dict(
        report=dict(
            stage="P6_MARKET",
            title="市场观察 · 行业资料待补",
            cutoff_at=snapshot["cutoff_at"],
            research_status="PARTIAL",
            summary="沪深三百近二十个交易日"
            + direction
            + "；行业目录本次不可用，尚不能比较设备与制造的成交关注。",
        ),
        market_index=dict(
            name="沪深三百",
            symbol="sh000300",
            first_session=days[0],
            last_session=days[-1],
            close=str(prices[-1]),
            base_close=str(prices[0]),
            return_20=str(change),
            basis="RAW_PRICE",
            source_ref=snapshot["index_source"]["blob_ref"],
        ),
        metrics=[],
        heat_inputs=None,
        membership=[],
        selection=dict(
            ranking=[],
            excluded=[],
            selection=selection,
            reason=selection["selection_reason"],
            coverage_label="行业覆盖尚未取得",
        ),
        coverage=dict(
            scope_note=SCOPE_NOTE,
            full_market_share_status="UNAVAILABLE",
            industries_covered=0,
            declared_industries=list(NAMES),
            members_total=None,
            members_available=None,
            complete_union=False,
            unconnected=[],
            catalog_note="本次未取得行业目录及成分，覆盖数量未知，不用零代替缺失。",
        ),
        sources=snapshot["sources"],
        documents=[],
        evidence=[],
        gaps=[dict(channel="CLASSIFICATION", reason=snapshot["reason"])],
    )


async def run(repo, run_id, stop, settings=None, *, collector=None, port=None, rules=None):
    state = repo.get(run_id, with_data=False)
    prior, started = state.get("elapsed_seconds", 0), time.monotonic()

    def save():
        if stop.is_set():
            raise Stopped("市场扫描已停止")
        state["elapsed_seconds"] = prior + time.monotonic() - started
        if state["elapsed_seconds"] >= state["request"]["budget_profile"]["max_seconds"]:
            raise Stopped("市场扫描达到时间上限")
        repo.save(state)

    def step(key, detail):
        state["process"].append(dict(key=key, detail=detail, status="RUNNING"))
        save()

    try:
        if not state.get("market_snapshot_ref"):
            try:
                snapshot = await asyncio.to_thread(
                    collector or collect, repo, state, stop, save, step
                )
            except SourceUnavailable as error:
                snapshot = await asyncio.to_thread(
                    collect_partial, repo, state, stop, save, step, str(error)
                )
            state["market_snapshot_ref"] = repo.blob(snapshot)
            save()
        data = await asyncio.to_thread(rebuild, repo, repo.value(state["market_snapshot_ref"]))
        data["market_snapshot_ref"] = state["market_snapshot_ref"]
        if state["request"].get("enhanced_research"):
            from .market_enhanced import analyze

            data["enhanced_research"] = await analyze(
                repo, state, data, settings, save, stop, port=port, rules=rules
            )
        await asyncio.to_thread(verify, repo, data)
        state.update(
            status="COMPLETED",
            title=data["report"]["title"],
            result_ref=repo.blob(data),
            result_sha256=digest(data),
            completed_at=datetime.now(UTC).isoformat(),
        )
        step("done", "本次扫描已封存；行业深研由用户选择，更新另存版本")
    except asyncio.CancelledError:
        if not stop.is_set():
            state.update(status="INTERRUPTED", error="扫描中断，已完成请求保留")
            repo.save(state)
        raise
    except Exception as error:
        if not stop.is_set():
            state.update(
                status="FAILED",
                error=str(error),
                elapsed_seconds=prior + time.monotonic() - started,
            )
            repo.save(state)
