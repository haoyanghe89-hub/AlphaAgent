"""Loopback-only plan writes; knowledge supplements remain local-tool writes."""

import asyncio
import threading
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from marketpulse.infrastructure.storage.models import BlobError

from .planning_contracts import PlanRequest
from .planning_service import run
from .planning_store import PlanningStore
from .supplements import SupplementStore

router = APIRouter(prefix="/api/quant", tags=["Investment plans"])


def repo(request):
    return PlanningStore(request.app.state.research_store)


def checked(action):
    try:
        return action()
    except KeyError:
        raise HTTPException(404, "未找到投资计划。") from None
    except (ValueError, BlobError, OSError):
        raise HTTPException(409, "资料或版本校验失败，已隐藏结果。") from None


def local_write(request):
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "仅允许本机创建投资计划。")
    origin = request.headers.get("origin")
    host = urlsplit("http://" + request.headers.get("host", "")).hostname
    if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
        raise HTTPException(403, "仅接受本机服务地址。")
    if origin and (
        urlsplit(origin).netloc != request.headers.get("host")
        or urlsplit(origin).scheme not in ("http", "https")
    ):
        raise HTTPException(403, "不接受跨站写入。")


def schedule(request, state):
    jobs = request.app.state.research_jobs
    if any(not t.done() for t in jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后继续。")
    if state["status"] == "INTERRUPTED":
        state.update(status="RUNNING", error=None)
        repo(request).save(state)
    options = {}
    if getattr(request.app.state, "investment_collector", None):
        options["collector"] = request.app.state.investment_collector
    task = asyncio.create_task(
        run(
            repo(request),
            request.app.state.knowledge_rules,
            state["run_id"],
            threading.Event(),
            **options,
        )
    )
    jobs[state["run_id"]] = task
    task.add_done_callback(lambda _: jobs.pop(state["run_id"], None))


@router.get("/investment-plans")
def plans(request: Request):
    return repo(request).list()


@router.post("/investment-plans", status_code=202)
async def create(payload: PlanRequest, request: Request):
    local_write(request)
    repository = repo(request)
    run_id = "RUN-IP-" + payload.request_key.hex
    if repository.exists(run_id):
        previous = checked(lambda: repository.get(run_id))
        if PlanRequest.model_validate(previous["request"]) != payload:
            raise HTTPException(409, "请求标识已绑定其他输入。")
        return previous
    if any(not t.done() for t in request.app.state.research_jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后重试。")
    state, fresh = repository.create(payload.model_dump(mode="json"))
    if fresh:
        schedule(request, state)
    return state


@router.get("/investment-plans/{run_id}")
def get_plan(run_id: str, request: Request):
    return checked(lambda: repo(request).get(run_id))


@router.post("/investment-plans/{run_id}/resume", status_code=202)
async def resume(run_id: str, request: Request):
    local_write(request)
    state = checked(lambda: repo(request).get(run_id))
    if state["status"] == "INTERRUPTED":
        schedule(request, state)
    return state


@router.get("/knowledge/supplements")
def supplements(request: Request):
    return checked(lambda: SupplementStore(request.app.state.knowledge_rules).list())


@router.get("/knowledge/supplements/{supplement_id}")
def supplement(supplement_id: str, request: Request):
    return checked(lambda: SupplementStore(request.app.state.knowledge_rules).get(supplement_id))
