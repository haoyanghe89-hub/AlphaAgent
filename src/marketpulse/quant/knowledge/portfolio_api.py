"""Local account dialogue API; no trading or account details are sent to models."""

import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException, Request, Response

from .planning_api import checked, local_write
from .portfolio_contracts import PortfolioRequest
from .portfolio_service import run
from .portfolio_store import PortfolioStore

router = APIRouter(prefix="/api/quant/portfolio-advisor", tags=["Portfolio advisor"])


def repository(request):
    return PortfolioStore(request.app.state.research_store)


def schedule(request, state):
    active = request.app.state.research_jobs
    if any(not t.done() for t in active.values()):
        raise HTTPException(429, "已有研究执行中，请稍后重试，当前账户输入留在页面。")
    options = {}
    for name, arg in (
        ("investment_research_port", "port"),
        ("investment_research_collector", "collector"),
        ("investment_context_collector", "context_collector"),
    ):
        if getattr(request.app.state, name, None) is not None:
            options[arg] = getattr(request.app.state, name)
    extra = {}
    if getattr(request.app.state, "advisor_quote_collector", None):
        extra["quote_collector"] = request.app.state.advisor_quote_collector
    if state["status"] == "INTERRUPTED":
        state.update(status="RUNNING", error=None)
        repository(request).save(state)
    task = asyncio.create_task(
        run(
            repository(request),
            request.app.state.knowledge_rules,
            state["run_id"],
            threading.Event(),
            request.app.state.settings,
            research_options=options,
            **extra,
        )
    )
    active[state["run_id"]] = task
    task.add_done_callback(lambda _: active.pop(state["run_id"], None))


@router.post("/turns", status_code=202)
async def create(payload: PortfolioRequest, request: Request):
    local_write(request)
    repo = repository(request)
    rid = repo.prefix + payload.request_key.hex
    if repo.exists(rid):
        state = checked(lambda: repo.get(rid))
        if PortfolioRequest.model_validate(state["request"]) != payload:
            raise HTTPException(409, "请求标识已绑定其他账户输入。")
        return state
    if any(not t.done() for t in request.app.state.research_jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后重试。")
    try:
        state, fresh = repo.create(payload.model_dump(mode="json"))
    except KeyError:
        raise HTTPException(404, "未找到上一轮账户咨询。") from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    if fresh:
        schedule(request, state)
    return state


@router.get("/turns/{run_id}")
def get(run_id: str, request: Request, response: Response):
    turns = checked(lambda: repository(request).conversation(run_id))
    state = turns[-1]
    state["conversation"] = [
        dict(
            run_id=t["run_id"],
            issued_at=t.get("issued_at"),
            question=t["request"]["question"],
            data=t.get("data"),
            status=t["status"],
        )
        for t in turns
    ]
    response.headers["Cache-Control"] = "no-store"
    return state


@router.post("/turns/{run_id}/resume", status_code=202)
async def resume(run_id: str, request: Request):
    local_write(request)
    state = checked(lambda: repository(request).get(run_id))
    if state["status"] == "INTERRUPTED":
        schedule(request, state)
    return state


@router.get("/turns/{run_id}/export")
def export(run_id: str, request: Request):
    turns = checked(lambda: repository(request).conversation(run_id))
    return Response(
        json.dumps(
            dict(schema_version="account-dialogue-export-v1", turns=turns),
            ensure_ascii=False,
            indent=2,
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}.json"',
            "Cache-Control": "no-store",
        },
    )
