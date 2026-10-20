"""Keyset history across product features, with no stage-specific 100-row ceiling."""

import base64
import json

from sqlalchemy import and_, func, or_, select

from ..live.store import ResearchRunRow


def page(base, *, limit=50, cursor=None, query="", scenario=""):
    statement = select(ResearchRunRow).where(
        ResearchRunRow.run_id.like("RUN-%"), ~ResearchRunRow.run_id.like("RUN-QU-%")
    )
    if query:
        # Search the declared title/identity, never private profiles or model traces.
        title = ResearchRunRow.payload["title"].as_string()
        statement = statement.where(
            or_(
                title.contains(query, autoescape=True),
                ResearchRunRow.run_id.contains(query, autoescape=True),
            )
        )
    if scenario:
        statement = statement.where(ResearchRunRow.payload["scenario"].as_string() == scenario)
    with base.sessions() as session:
        total = session.scalar(select(func.count()).select_from(statement.subquery()))
        if cursor:
            try:
                stamp, identity = json.loads(base64.urlsafe_b64decode(cursor))
                if not isinstance(stamp, str) or not isinstance(identity, str):
                    raise ValueError()
            except Exception:
                raise ValueError("历史分页标识无效") from None
            statement = statement.where(
                or_(
                    ResearchRunRow.created_at < stamp,
                    and_(ResearchRunRow.created_at == stamp, ResearchRunRow.run_id < identity),
                )
            )
        rows = session.scalars(
            statement.order_by(
                ResearchRunRow.created_at.desc(), ResearchRunRow.run_id.desc()
            ).limit(limit + 1)
        ).all()
        items = []
        for row in rows[:limit]:
            payload = row.payload
            item = {
                key: payload.get(key)
                for key in (
                    "run_id",
                    "scenario",
                    "title",
                    "status",
                    "created_at",
                    "report_version",
                    "page",
                    "investigation_id",
                    "branch_investigation_ids",
                )
            }
            item["title"] = item["title"] or payload.get("question") or row.run_id
            if (
                item["scenario"] == "next_session"
                and item["status"] == "COMPLETED"
                and payload.get("attempt_ids")
                and not payload.get("prediction_ids")
            ):
                # Older batches used COMPLETED for finished processing even if nothing was issued.
                item["status"] = "FAILED"
            if item["scenario"] == "decision":
                mode = payload.get("request", {}).get("research_mode")
                item["page"] = (
                    "market"
                    if mode == "market_scan"
                    else "candidates"
                    if mode == "candidates"
                    else "decision"
                )
            items.append(item)
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([last.created_at, last.run_id]).encode()
            ).decode()
    return dict(items=items, total=total, next_cursor=next_cursor)
