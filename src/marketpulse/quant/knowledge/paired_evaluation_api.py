"""Loopback-only, recoverable paired evaluation using the application's shared job slot."""

import asyncio
import copy
import json
import threading

from fastapi import APIRouter, HTTPException, Request, Response

from .paired_evaluation import EvaluationRequest, EvaluationStore, execute, seal
from .planning_api import checked, local_write

router = APIRouter(prefix="/api/quant/research-evaluation", tags=["Research evaluation"])


def read_view(base):
    class VerifiedBlobs:
        def __init__(self):
            self.values = {}

        def get_bytes(self, ref):
            if ref.uri not in self.values:
                self.values[ref.uri] = base.blobs.get_bytes(ref)
            return self.values[ref.uri]

        def __getattr__(self, name):
            return getattr(base.blobs, name)

    view = copy.copy(base)
    view.blobs = VerifiedBlobs()
    return view


def repository(request):
    base = request.app.state.research_store
    return EvaluationStore(read_view(base) if request.method == "GET" else base)


def schedule(request, state):
    jobs = request.app.state.research_jobs
    if any(not t.done() for t in jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后继续评测。")
    repo = repository(request)
    options = {}
    if getattr(request.app.state, "investment_research_port", None):
        options["port"] = request.app.state.investment_research_port
    task = asyncio.create_task(
        execute(
            repo,
            request.app.state.knowledge_rules,
            state["run_id"],
            threading.Event(),
            request.app.state.settings,
            **options,
        )
    )
    jobs[state["run_id"]] = task
    task.add_done_callback(lambda _: jobs.pop(state["run_id"], None))


@router.get("/runs")
def list_runs(request: Request):
    return repository(request).list()


@router.post("/runs", status_code=202)
async def create(payload: EvaluationRequest, request: Request):
    local_write(request)
    repo = repository(request)
    rid = repo.prefix + payload.request_key.hex
    if repo.exists(rid):
        return checked(lambda: repo.get(rid))
    if any(not t.done() for t in request.app.state.research_jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后开始评测。")
    state, fresh = repo.create(payload.model_dump(mode="json"), request.app.state.settings.model)
    if fresh:
        schedule(request, state)
    return state


@router.get("/runs/{rid}")
def get_run(rid: str, request: Request):
    return checked(lambda: repository(request).get(rid))


@router.post("/runs/{rid}/resume", status_code=202)
async def resume(rid: str, request: Request):
    local_write(request)
    repo = repository(request)
    state = checked(lambda: repo.get(rid, with_data=False))
    if state["status"] != "COMPLETED":
        schedule(request, state)
    return state


@router.get("/runs/{rid}/export")
def export(rid: str, request: Request):
    repo = repository(request)
    state = checked(lambda: repo.get(rid))
    # Both the sealed engineering result and latest append-only outcomes are included.
    if state.get("report_ref"):
        state["sealed_report"] = checked(lambda: repo.value(state["report_ref"]))
    return Response(
        json.dumps(state, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{rid}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/runs/{rid}/seal")
def seal_run(rid: str, request: Request):
    local_write(request)
    if any(not t.done() for t in request.app.state.research_jobs.values()):
        raise HTTPException(429, "已有研究执行中，请待本轮结束再封存。")
    return checked(lambda: seal(repository(request), rid))


@router.post("/runs/{rid}/quality-review", status_code=202)
async def quality_review(rid: str, request: Request):
    local_write(request)
    repo = repository(request)
    state = checked(lambda: repo.get(rid, with_data=False))
    if state.get("version") != "research-effectiveness-v1" or state["status"] != "COMPLETED":
        raise HTTPException(409, "需要已封存的效果对照")
    jobs = request.app.state.research_jobs
    if any(not t.done() for t in jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后补充复评。")
    from .effectiveness import recheck_quality

    task = asyncio.create_task(recheck_quality(repo, rid, request.app.state.settings))
    key = "quality:" + rid
    jobs[key] = task
    task.add_done_callback(lambda _: jobs.pop(key, None))
    return dict(run_id=rid, status="RUNNING", protocol="blind-answer-quality-v2")
