"""New candidate source protocol adds independently dated, complete financing windows."""

import copy
import json
from datetime import UTC, datetime

from . import tushare, tushare_candidates
from .candidate_sources import checked
from .contracts import EvidenceItem, Locator
from .market import number
from .tools import SourceClient, SourceUnavailable

VERSION = "candidate-tushare-dated-margin-2026-10-14-v3"


def collect(repo, state, stop, save, step):
    from .candidate_sources import rebuild

    snapshot = tushare_candidates.collect(repo, state, stop, save, step)
    selected = rebuild(repo, snapshot)["selected"]
    client = SourceClient(repo, state, save, stop, max_requests=160)
    days = [d for d in snapshot["calendar"]["sessions"] if d <= snapshot["sessions"][-1]]
    step("financing", "读取入选公司的融资渠道，按实际公布日期核对完整窗口")
    for sid in selected:
        try:
            source, _ = tushare.fetch(
                client,
                "candidate_financing",
                "margin_detail",
                dict(
                    ts_code=sid,
                    start_date=days[-23].replace("-", ""),
                    end_date=days[-1].replace("-", ""),
                ),
            )
            snapshot["entries"].append(dict(role="dated_margin:" + sid, source=source))
        except SourceUnavailable as error:
            snapshot["gaps"].append(dict(code=sid[:6], category="dated_margin", reason=str(error)))
    snapshot["version"] = VERSION
    snapshot["cutoff_at"] = datetime.now(UTC).isoformat()
    snapshot["sources"] = [c["source"] for c in state["calls"] if c.get("source")]
    return snapshot


def window(rows, sid, calendar, price_end):
    values = {}
    for index, row in enumerate(rows):
        day = tushare_candidates.iso(row["trade_date"])
        if row["ts_code"] != sid or day in values or day not in calendar or day > price_end:
            raise ValueError("financing issuer/date conflict")
        values[day] = (index, row)
    if not values or len(values) >= 6000:
        raise ValueError("financing source empty or truncated")
    end = max(values)
    eligible = [d for d in calendar if d <= price_end]
    if end not in eligible[-2:]:
        raise ValueError("financing update is older than the expected previous session")
    days = [d for d in calendar if d <= end][-21:]
    if len(days) != 21 or not set(days) <= values.keys():
        raise ValueError("financing window missing session; no zero fill")
    for d in days:
        for field in ("rzye", "rzmre", "rzche"):
            number(values[d][1][field], nonnegative=True)
    return days, values


def rebuild(repo, snapshot):
    from .candidate_sources import rebuild as base_rebuild

    base = copy.deepcopy(snapshot)
    base["version"] = tushare_candidates.VERSION
    result = base_rebuild(repo, base)
    notes = (
        "融资按其实际最新可得日期形成二十日余额差；与价格窗口分别标日期。"
        "通常次日上午公布，缺日不补零，不推机构或全市场资金。"
    )
    result["limitations"].append(notes)
    for entry in snapshot["entries"]:
        if not entry["role"].startswith("dated_margin:"):
            continue
        sid, source = entry["role"].split(":", 1)[1], entry["source"]
        row = next(r for r in result["members"] if r["security_id"] == sid)
        if (
            sid not in result["selected"]
            or source["source_family"] != "TUSHARE"
            or source.get("api_name") != "margin_detail"
            or source.get("parameters", {}).get("ts_code") != sid
        ):
            raise ValueError("financing source binding conflict")
        packet = checked(repo, source, snapshot["cutoff_at"])
        try:
            days, values = window(
                tushare.records(packet),
                sid,
                snapshot["calendar"]["sessions"],
                snapshot["sessions"][-1],
            )
        except ValueError as error:
            row["gaps"].append(str(error))
            continue
        first, last = values[days[0]], values[days[-1]]
        row["funding_change"] = str(number(last[1]["rzye"]) - number(first[1]["rzye"]))
        row["funding_ref"] = source["blob_ref"]
        row["funding_window"] = dict(
            start=days[0],
            end=days[-1],
            sessions=20,
            source_family="TUSHARE",
            price_end=snapshot["sessions"][-1],
        )
        operating = (
            row["core_complete"]
            and number(row["metrics"]["PARENT_NETPROFIT"]) > 0
            and number(row["metrics"]["NETCASH_OPERATE"]) > 0
        )
        if operating:
            row["group"] = (
                "经营与资金相互支持"
                if number(row["funding_change"]) >= 0
                else "经营有支持但资金分歧"
            )
        for label, (index, value), day in (("START", first, days[0]), ("END", last, days[-1])):
            eid = "EV-FINANCING-" + sid + "-" + label
            item = EvidenceItem(
                evidence_id=eid,
                subject_id=sid,
                kind="flow",
                source_family="TUSHARE",
                original_source_id=source["key"],
                url=source["url"],
                raw_ref=source["blob_ref"],
                content_hash=source["sha256"],
                locator=Locator(
                    kind="json",
                    path=f"/data/items/{index}/{packet['data']['fields'].index('rzye')}",
                ),
                text=day + " 融资余额（元）：" + str(value["rzye"]),
                value=number(value["rzye"], nonnegative=True),
                unit="CNY",
                period_end=day,
                fetched_at=source["fetched_at"],
                available_at=source["fetched_at"],
                time_precision="day",
                method_version=VERSION,
                quality_status="PARTIAL",
                limitations=(notes,),
            )
            result["bundle"]["items"].append(item.model_dump(mode="json"))
            row["evidence_ids"].append(eid)
    result["data_supply"]["financing_note"] = notes
    result["data_supply"]["financing_windows"] = {
        r["security_id"]: r["funding_window"] for r in result["members"] if r.get("funding_window")
    }
    return result


def verified_numeric(repo, item):
    packet = json.loads(repo.raw_bytes(item.raw_ref))
    parts = item.locator.path.split("/")
    if len(parts) != 5 or parts[1:3] != ["data", "items"]:
        raise ValueError("invalid financing locator")
    index, column = int(parts[3]), int(parts[4])
    row = tushare.records(packet)[index]
    if (
        row["ts_code"] != item.subject_id
        or packet["data"]["fields"][column] != "rzye"
        or item.evidence_id
        not in {
            "EV-FINANCING-" + item.subject_id + "-START",
            "EV-FINANCING-" + item.subject_id + "-END",
        }
        or tushare_candidates.iso(row["trade_date"]) != str(item.period_end)
    ):
        raise ValueError("financing evidence identity/field/date conflict")
    return number(row["rzye"], nonnegative=True)
