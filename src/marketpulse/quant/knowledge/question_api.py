"""Local-only question intake; provider sees an allowlisted semantic projection."""

import asyncio
import threading

from fastapi import APIRouter, HTTPException, Request

from .planning_api import checked, local_write
from .question_understanding import QuestionRequest, QuestionStore, run

router = APIRouter(prefix="/api/quant/questions", tags=["Question understanding"])


def schedule(request, state):
    jobs = request.app.state.research_jobs
    if any(not t.done() for t in jobs.values()):
        raise HTTPException(429, "已有研究执行中，问题已留在页面，请稍后发送。")
    if state["status"] == "INTERRUPTED":
        state.update(status="RUNNING", error=None)
        QuestionStore(request.app.state.research_store).save(state)
    options = {}
    if getattr(request.app.state, "question_understanding_port", None):
        options["port"] = request.app.state.question_understanding_port
    task = asyncio.create_task(
        run(
            QuestionStore(request.app.state.research_store),
            state["run_id"],
            threading.Event(),
            request.app.state.settings,
            **options,
        )
    )
    jobs[state["run_id"]] = task
    task.add_done_callback(lambda _: jobs.pop(state["run_id"], None))


@router.post("/understand", status_code=202)
async def create(payload: QuestionRequest, request: Request):
    local_write(request)
    if payload.channel != "workbench":
        raise HTTPException(422, "持仓与账户问题须使用已核对的咨询入口。")
    repo = QuestionStore(request.app.state.research_store)
    rid = repo.prefix + payload.request_key.hex
    jobs = request.app.state.research_jobs
    if rid not in jobs and any(not t.done() for t in jobs.values()):
        raise HTTPException(429, "已有研究执行中，问题保留，请稍后重试。")
    state, fresh = checked(lambda: repo.create(payload.model_dump(mode="json")))
    if fresh or (state["status"] in {"RUNNING", "INTERRUPTED"} and rid not in jobs):
        schedule(request, state)
    return state


@router.get("/{run_id}")
def get(run_id: str, request: Request):
    return checked(lambda: QuestionStore(request.app.state.research_store).get(run_id))
