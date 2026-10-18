"""Current daily status and immutable briefing/history reads."""

import asyncio
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request

from ..knowledge.planning_api import checked, local_write
from . import briefing, history

router = APIRouter(prefix="/api/quant/daily", tags=["Daily workspace"])


@router.get("/status")
def status(request: Request):
    runtime = getattr(request.app.state, "daily_runtime", None)
    return runtime.state if runtime else dict(enabled=False, source=dict(status="NOT_CHECKED"))


@router.get("/history")
def records(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=500),
    q: str = Query(default="", max_length=100),
    scenario: str = Query(default="", max_length=40),
):
    try:
        return history.page(
            request.app.state.research_store,
            limit=limit,
            cursor=cursor,
            query=q.strip(),
            scenario=scenario,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def visible(state):
    local = datetime.now(UTC).astimezone(briefing.ZONE)
    if (
        state["request"]["kind"] == "SCHEDULED"
        and state["request"]["trade_date"] == local.strftime("%Y%m%d")
        and local.strftime("%H:%M") < "09:25"
    ):
        return {k: state.get(k) for k in ("run_id", "created_at", "title", "request")} | dict(
            status="PREPARING"
        )
    return {
        k: state.get(k)
        for k in (
            "run_id",
            "created_at",
            "title",
            "status",
            "request",
            "data",
            "error",
            "completed_at",
        )
    }


@router.get("/briefings")
def briefings(request: Request):
    base = request.app.state.research_store
    states = base.list(prefix="RUN-MB-")
    latest = checked(lambda: briefing.get(base, states[0]["run_id"])) if states else None
    return dict(latest=visible(latest) if latest else None)


@router.post("/briefings", status_code=202)
async def current_briefing(request: Request):
    local_write(request)
    active = request.app.state.research_jobs
    if any(not task.done() for task in active.values()):
        raise HTTPException(429, "已有分析或核对执行中，请稍后继续。")
    day = datetime.now(UTC).astimezone(briefing.ZONE).strftime("%Y%m%d")
    state, fresh = briefing.create(request.app.state.research_store, day, "CURRENT_REVIEW")
    if fresh:
        task = asyncio.create_task(
            briefing.run(
                request.app.state.research_store,
                request.app.state.knowledge_rules,
                state["run_id"],
                request.app.state.settings,
            )
        )
        active[state["run_id"]] = task
        task.add_done_callback(lambda _: active.pop(state["run_id"], None))
    return visible(state)


@router.get("/briefings/{rid}")
def get_briefing(rid: str, request: Request):
    if not re.fullmatch(r"RUN-MB-[0-9a-f]{32}", rid):
        raise HTTPException(404, "未找到盘前清单。")
    return visible(checked(lambda: briefing.get(request.app.state.research_store, rid)))


@router.post("/briefings/{rid}/resume", status_code=202)
async def resume_briefing(rid: str, request: Request):
    local_write(request)
    if not re.fullmatch(r"RUN-MB-[0-9a-f]{32}", rid):
        raise HTTPException(404, "未找到盘前清单。")
    base = request.app.state.research_store
    state = checked(lambda: briefing.get(base, rid))
    if state["status"] == "COMPLETED":
        return visible(state)
    active = request.app.state.research_jobs
    if any(not task.done() for task in active.values()):
        raise HTTPException(429, "已有分析或核对执行中，请稍后继续。")
    # Reuse this request's sources, model checkpoints and spent budget exactly.
    state.update(status="RUNNING", error=None)
    base.put(state)
    task = asyncio.create_task(
        briefing.run(base, request.app.state.knowledge_rules, rid, request.app.state.settings)
    )
    active[rid] = task
    task.add_done_callback(lambda _: active.pop(rid, None))
    return visible(state)
