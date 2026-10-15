"""Explicit second scan protocol: dated SW membership, unchanged two-sector scope.

This is a present-day reconstruction of vendor membership history, not proof
that today's revised classification was available to a historical strategy.
"""

from datetime import datetime
from decimal import Decimal

from ..contracts import digest
from . import tushare, tushare_market
from .contracts import SectorHeatInputs
from .market import number
from .sector_selection import rank_sectors
from .tools import SourceClient

VERSION = "tushare-sw2021-dated-members-2026-10-14-v2"
MEMBERSHIP_PROTOCOL = "SW_DATED_MEMBERS_V2:in_date_and_listing_inclusive,out_date_boundary_blocked"


def collect(repo, state, stop, save, step):
    snapshot = tushare_market.collect(repo, state, stop, save, step)
    entries = tushare_market.tables(repo, snapshot)
    client = SourceClient(repo, state, save, stop, max_requests=100)
    for item in entries["catalog"][1]:
        if item["industry_name"] not in tushare_market.PROFILES:
            continue
        source, _ = tushare.fetch(
            client, "past_members", "index_member_all", dict(l3_code=item["index_code"], is_new="N")
        )
        snapshot["entries"].append(dict(role="past_members:" + item["index_code"], source=source))
    snapshot["version"] = VERSION
    snapshot["sources"] = [c["source"] for c in state["calls"] if c.get("source")]
    from datetime import UTC

    snapshot["cutoff_at"] = datetime.now(UTC).isoformat()
    return snapshot


def dated_members(rows, master, day, first, last):
    """Missing eligibility is an error; never interpret a missing quote as zero."""
    included = []
    for row in rows:
        sid, entered, exited = row["ts_code"], row.get("in_date"), row.get("out_date")
        if sid.endswith(".BJ"):
            continue
        if not entered or len(entered) != 8:
            raise ValueError("missing member effective date")
        datetime.strptime(entered, "%Y%m%d")
        if exited:
            datetime.strptime(exited, "%Y%m%d")
            if exited <= entered:
                raise ValueError("invalid membership interval")
            # The provider does not document intraday removal semantics. Do not
            # guess whether the exit date itself belongs to either industry.
            if first <= exited <= last:
                raise ValueError("membership exit boundary needs original verification")
        if entered > day or exited and exited < day:
            continue
        identity = master.get(sid)
        if not sid.endswith((".SH", ".SZ")) or not identity:
            raise ValueError("dated member missing from security master")
        listed = identity.get("list_date")
        if not listed or len(listed) != 8:
            raise ValueError("missing listing date")
        datetime.strptime(listed, "%Y%m%d")
        if listed > day:
            continue
        if not tushare.same_security_name(identity["name"], row["name"]):
            raise ValueError("dated membership identity conflict")
        if sid in included:
            raise ValueError("overlapping membership intervals")
        included.append(sid)
    return sorted(included)


def rebuild(repo, snapshot):
    # Reuse original calendar, classification, current identity and OHLC checks.
    result = tushare_market.rebuild(repo, snapshot)
    entries = tushare_market.tables(repo, snapshot)
    days = snapshot["sessions"][-20:]
    compact = [d.replace("-", "") for d in days]
    master = {r["ts_code"]: r for r in entries["master"][1]}
    daily = {d: {r["ts_code"]: r for r in entries["daily:" + d][1]} for d in days}
    sectors, memberships, union_by_day = [], [], {d: set() for d in days}
    for current in result["membership"]:
        code, name = current["industry_id"], current["name"]
        source, past = entries["past_members:" + code]
        if len(past) >= 2000 or any(
            r["l3_code"] != code
            or r["l3_name"] != name
            or r["is_new"] != "N"
            or not r.get("out_date")
            for r in past
        ):
            raise ValueError("invalid or truncated past membership")
        present_source, present = entries["members:" + code]
        rows = present + past
        actual_days = []
        for day, stamp in zip(days, compact, strict=True):
            members = dated_members(rows, master, stamp, compact[0], compact[-1])
            if not members or union_by_day[day].intersection(members):
                raise ValueError("empty or overlapping dated industries")
            union_by_day[day].update(members)
            values = []
            for sid in members:
                row = daily[day].get(sid)
                if row is None:
                    continue
                prices = [number(row[f], positive=True) for f in ("open", "close", "high", "low")]
                if not prices[3] <= min(prices[:2]) <= max(prices[:2]) <= prices[2]:
                    raise ValueError("dated daily OHLC conflict")
                values.append(number(row["amount"], nonnegative=True) * 1000)
            actual_days.append(
                dict(
                    session=day,
                    members=members,
                    members_total=len(members),
                    members_available=len(values),
                    turnover=str(sum(values)) if len(values) == len(members) else None,
                )
            )
        membership = dict(
            industry_id=code,
            name=name,
            current_ref=present_source["blob_ref"],
            past_ref=source["blob_ref"],
            protocol=MEMBERSHIP_PROTOCOL,
            days=actual_days,
        )
        memberships.append(membership)
        sectors.append(
            dict(
                industry_id=code,
                membership_ref=digest(membership),
                days=[{k: v for k, v in d.items() if k != "members"} for d in actual_days],
            )
        )
    complete = all(d["turnover"] is not None for s in sectors for d in s["days"])
    heat = SectorHeatInputs.model_validate(
        dict(
            classification_version=result["heat_inputs"]["classification_version"]
            + ":"
            + MEMBERSHIP_PROTOCOL,
            classification_level="INDUSTRY",
            source_family="TUSHARE",
            sessions=days,
            sectors=sectors,
            market_turnover=[
                dict(
                    session=d,
                    value=str(sum(Decimal(s["days"][i]["turnover"]) for s in sectors))
                    if complete
                    else None,
                    unit="CNY",
                    source_ref=digest(memberships),
                    missing_reason=None if complete else "逐日有效成员存在行情缺项，未缩小联合分母",
                )
                for i, d in enumerate(days)
            ],
            market_coverage_complete=complete,
            universe_ref=digest(memberships),
            cutoff_at=snapshot["cutoff_at"],
        )
    )
    ranked = rank_sectors(heat, datetime.fromisoformat(snapshot["cutoff_at"]))
    ranked["selection"] = ranked["selection"].model_dump(mode="json")
    ranking = {r["industry_id"]: r for r in ranked["ranking"]}
    for row in result["metrics"]:
        values = ranking.get(row["industry_id"], {})
        row.update(
            mean_20_share=values.get("mean_20_share"), change_5_share=values.get("change_5_share")
        )
    order = {r["industry_id"]: i for i, r in enumerate(ranked["ranking"])}
    result["metrics"].sort(key=lambda r: (order.get(r["industry_id"], 999), r["industry_id"]))
    result.update(
        heat_inputs=heat.model_dump(mode="json"), selection=ranked, dated_membership=memberships
    )
    result["coverage"].update(
        complete_union=complete,
        membership_protocol=MEMBERSHIP_PROTOCOL,
        catalog_note="Tushare申万2021三级分类，固定设备与制造两个行业的沪深范围；逐日按纳入日期和上市日期计成员，补查已退出成员。新股上市前不属于当天有效成员。当前成员完整价格序列与逐日成交占比覆盖分别显示；不是历史时点可得性证明或正式回测。",
    )
    if ranked["ranking"]:
        result["report"]["summary"] = (
            "在固定设备与制造两个行业范围内，按各日有效成员计算，"
            + result["metrics"][0]["name"]
            + "的二十日平均成交关注占比居前。成交关注不是资金净流入或未来涨幅预测。"
        )
    return result
