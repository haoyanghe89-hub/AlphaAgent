"""Reuse saved research as dated material, with explicit freshness and original references."""

from datetime import UTC, datetime

from .sector_profiles import PROFILES


def index(repo, now=None):
    now = now or datetime.now(UTC)
    records, found = [], set()
    for row in sorted(repo.list(), key=lambda r: r["created_at"], reverse=True):
        request = row["request"]
        name = request["entity"]
        if (
            row["status"] != "COMPLETED"
            or request.get("research_mode") != "candidates"
            or name not in PROFILES
            or name in found
        ):
            continue
        state = repo.get(row["run_id"])
        data = state["data"]
        cutoff = datetime.fromisoformat(data["report"]["cutoff_at"])
        age = (now - cutoff).total_seconds()
        records.append(
            dict(
                entity=name,
                run_id=row["run_id"],
                cutoff_at=cutoff.isoformat(),
                freshness="RECENT_SAVED_NOT_REVALIDATED" if 0 <= age <= 86400 else "STALE",
                originals=[
                    dict(document_id=d["document_id"], raw_ref=d["raw_ref"], title=d["title"])
                    for d in data["documents"]
                ],
                note="复用归档原文与已有研究入口；未重新核对当前行业成分及市场数据，更新仍需新取数。",
            )
        )
        found.add(name)
    return records
