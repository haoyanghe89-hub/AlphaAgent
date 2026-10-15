"""Manual next-session experiment endpoints; outcomes never come from clients."""

import asyncio
import re
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from marketpulse.infrastructure.storage.models import BlobError

from ...api import _error
from ...domain import FrozenModel
from ...live.research_api import jobs
from ...live.research_api import store as research_store
from ..store import fingerprint
from . import service
from .contracts import NextSessionRunRequest
from .store import ExperimentStore

router = APIRouter(prefix="/experiments/next-session")


class EmptyRequest(FrozenModel):
    pass


def repository(request):
    if not hasattr(request.state, "next_session_repo"):
        request.state.next_session_repo = ExperimentStore(
            research_store(request), request_cache=True
        )
    return request.state.next_session_repo


def checked(request, rid):
    repo = repository(request)
    if not re.fullmatch(r"RUN-NS-[0-9a-f]{32}", rid) or not repo.exists(rid):
        raise _error(404, "NEXT_SESSION_NOT_FOUND", "预测记录不存在。")
    try:
        return repo.get_run(rid)
    except (ValueError, KeyError, OSError, BlobError) as error:
        raise _error(
            409, "NEXT_SESSION_INTEGRITY_FAILED", "记录或原文完整性检查失败，已隐藏预测。"
        ) from error


def capacity(request):
    if any(not task.done() for task in jobs(request).values()):
        raise _error(429, "NEXT_SESSION_BUSY", "已有研究或核对执行中，请等待完成。")


async def exclusive(request, key, fn, *args):
    capacity(request)
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    active = jobs(request)
    active[key] = task
    try:
        return await task
    finally:
        active.pop(key, None)


@router.get("/status")
async def status(request: Request):
    return await asyncio.to_thread(service.status, repository(request))


@router.post("/enable")
async def enable(payload: EmptyRequest, request: Request):
    try:
        activation = await exclusive(
            request, "DEC-ENABLE", service.enable, repository(request), request.app.state.settings
        )
        return dict(enabled=True, activation=activation["config"])
    except ValueError as error:
        raise _error(409, "NEXT_SESSION_ENABLE_FAILED", str(error)) from error


@router.post("/runs", status_code=202)
async def create(payload: NextSessionRunRequest, request: Request):
    repo = repository(request)
    rid = "RUN-NS-" + payload.request_key.hex
    definition = payload.model_dump(mode="json", exclude_none=True)
    if repo.exists(rid):
        prior = checked(request, rid)
        if prior["request"] != definition:
            raise _error(409, "NEXT_SESSION_KEY_CONFLICT", "请求标识已绑定其他条件。")
        return service.public_run(repo, prior)
    activation = repo.activation(payload.experiment_id)
    if payload.experiment_id != service.template().experiment_id or not activation:
        raise _error(409, "NEXT_SESSION_DISABLED", "请先开启实验，记录实际开启时间与固定样本。")
    config = activation["config"]
    if (
        config["model_version"] != request.app.state.settings.model
        or activation.get("policy_hash") != service.policy_hash()
    ):
        raise _error(409, "NEXT_SESSION_MODEL_CHANGED", "模型已改变，需要新版本实验配置。")
    capacity(request)
    try:
        service.preflight(activation)
    except ValueError as error:
        raise _error(422, "NEXT_SESSION_WAIT_CLOSE", str(error)) from error
    state, fresh = repo.create_run(definition, config, fingerprint())
    if fresh:
        stop = threading.Event()
        if not hasattr(request.app.state, "next_session_stops"):
            request.app.state.next_session_stops = {}
        request.app.state.next_session_stops[rid] = stop
        task = asyncio.create_task(
            service.run(
                ExperimentStore(repo.base),
                rid,
                request.app.state.settings,
                stop,
                getattr(request.app.state, "research_model_port", None),
            )
        )
        active = jobs(request)
        active[rid] = task

        def done(_):
            active.pop(rid, None)
            request.app.state.next_session_stops.pop(rid, None)

        task.add_done_callback(done)
    return service.public_run(repo, state)


@router.get("/runs")
async def history(request: Request):
    return [
        {
            k: r.get(k)
            for k in (
                "run_id",
                "title",
                "status",
                "created_at",
                "base_session",
                "target_session",
                "cohort",
            )
        }
        for r in repository(request).records("RUN-NS-")
    ]


@router.get("/runs/{rid}")
async def get_run(rid: str, request: Request, developer: bool = False):
    state = await asyncio.to_thread(checked, request, rid)
    return await asyncio.to_thread(service.public_run, repository(request), state, developer)


@router.post("/runs/{rid}/cancel")
async def cancel(rid: str, payload: EmptyRequest, request: Request):
    state = checked(request, rid)
    stop = getattr(request.app.state, "next_session_stops", {}).get(rid)
    if state["status"] == "RUNNING" and stop:
        stop.set()
        task = jobs(request).get(rid)
        if task:
            task.cancel()
    return service.public_run(repository(request), state)


@router.post("/runs/{rid}/observe")
async def observe(rid: str, payload: EmptyRequest, request: Request):
    state = checked(request, rid)
    return await exclusive(request, "DEC-OBSERVING", service.observe, repository(request), state)


@router.get("/scoreboard")
async def scoreboard(request: Request):
    try:
        return await asyncio.to_thread(service.scoreboard, repository(request))
    except (ValueError, KeyError, OSError, BlobError) as error:
        raise _error(409, "NEXT_SESSION_INTEGRITY_FAILED", "统计引用完整性检查失败。") from error


@router.get("/runs/{rid}/export")
async def export(rid: str, request: Request):
    repo = repository(request)
    state = await asyncio.to_thread(checked, request, rid)
    value = service.public_run(repo, state, True)
    value["snapshots"] = {
        k: repo.snapshot(ref).model_dump(mode="json") for k, ref in state["snapshots"].items()
    }
    value["outcome_versions"] = {pid: repo.outcome_versions(pid) for pid in state["prediction_ids"]}
    return JSONResponse(
        value,
        headers={
            "Content-Disposition": f'attachment; filename="{rid}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/runs/{rid}/evidence/{pid}/{eid}")
async def evidence(rid: str, pid: str, eid: str, request: Request):
    repo = repository(request)
    state = await asyncio.to_thread(checked, request, rid)
    if pid not in state["prediction_ids"]:
        raise _error(404, "NEXT_SESSION_EVIDENCE_NOT_FOUND", "本次预测没有这条引用。")
    p = repo.prediction(pid)
    snap = repo.snapshot(state["snapshots"][p.security_id])
    for item in snap.evidence.items:
        if item.evidence_id == eid:
            doc = next((d for d in snap.documents if d["raw_ref"] == item.raw_ref), None)
            return dict(
                evidence=item.model_dump(mode="json"),
                document=doc,
                evidence_run_id=snap.evidence_run_id,
            )
    raise _error(404, "NEXT_SESSION_EVIDENCE_NOT_FOUND", "本次预测没有这条引用。")
