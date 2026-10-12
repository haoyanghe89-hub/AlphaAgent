"""Explicit historical frozen window presets; never replace a run with latest."""

from sqlalchemy import select

from .contracts import digest
from .storage.models import DatasetSnapshotRow, InstrumentRow


def available_windows(service, instrument_id):
    with service.sessions() as session:
        if session.get(InstrumentRow, instrument_id) is None:
            return []
        payloads = session.scalars(select(DatasetSnapshotRow.frozen_payload)).all()
    relevant = [p for p in payloads if p["result"]["request"]["instruments"] == [instrument_id]]
    groups = {}
    for p in relevant:
        r = p["result"]["request"]
        if r["dataset"] == "daily":
            groups.setdefault((r["start"], r["end"], r["asof"]), []).append(p)
    result = []
    for (start, end, asof), prices in sorted(groups.items()):
        adjustments = [p["result"]["request"]["adjustment"] for p in prices]
        if "raw" not in adjustments or len(set(adjustments)) != len(adjustments):
            continue  # ambiguous provider/revision requires explicit operator pinning
        calendars = [
            p
            for p in relevant
            if p["result"]["request"]["dataset"] == "calendar"
            and p["result"]["request"]["start"] <= start
            and p["result"]["request"]["end"] >= end
            and p["result"]["request"]["asof"] == asof
        ]
        if not calendars:
            continue
        oldest_start = min(p["result"]["request"]["start"] for p in calendars)
        calendars = [p for p in calendars if p["result"]["request"]["start"] == oldest_start]
        if len(calendars) != 1:
            continue
        financials = [
            p
            for p in relevant
            if p["result"]["request"]["normalizer_version"] in {"3-parent-2", "dcf-official-v1"}
            and p["result"]["request"]["asof"] == asof
            and p["result"]["request"]["end"] <= end
            and p["result"]["request"]["dataset"] in {"financial", "valuation"}
        ]
        kinds = [
            (p["result"]["request"]["dataset"], tuple(p["result"]["request"]["fields"]))
            for p in financials
        ]
        if len(set(kinds)) != len(kinds):
            continue
        snapshots = prices + calendars + financials
        result.append(
            dict(
                window_id=digest(sorted(p["snapshot_id"] for p in snapshots)),
                date_start=start,
                date_end=end,
                asof=asof,
                adjustments=adjustments,
                frozen_snapshot_ids=[p["snapshot_id"] for p in snapshots],
                sample_count=next(
                    p["row_count"] for p in prices if p["result"]["request"]["adjustment"] == "raw"
                ),
                note="明确选择历史冻结时点；离线可重算，不作为当前行情。",
            )
        )
    return result[:20]
