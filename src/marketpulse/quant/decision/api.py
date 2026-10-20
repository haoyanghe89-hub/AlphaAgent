"""Versioned decision evidence tasks and capability inspection."""

import asyncio
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from marketpulse.infrastructure.storage.models import BlobError

from ..api import _error
from ..live.research_api import jobs
from ..live.research_api import store as research_store
from . import contracts
from .capabilities import capabilities
from .methods import load_cases
from .next_session import contracts as daily_contracts
from .next_session.api import router as next_session_router
from .service import run
from .store import DecisionStore, fingerprint

router = APIRouter(prefix="/api/quant/decision", tags=["quant-decision"])

router.include_router(next_session_router)


def repository(request):
    if not hasattr(request.app.state, "decision_store"):
        request.app.state.decision_store = DecisionStore(research_store(request))
    return request.app.state.decision_store


def checked(request, run_id, *, with_data=True):
    if not re.fullmatch(r"RUN-DEC-[0-9a-f]{32}", run_id) or not repository(request).exists(run_id):
        raise _error(404, "DECISION_NOT_FOUND", "取证记录不存在。")
    try:
        return repository(request).get(run_id, with_data=with_data)
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(
            409, "DECISION_INTEGRITY_FAILED", "记录或引用完整性检查失败，已隐藏报告。"
        ) from error


def capacity(request):
    if any(not task.done() for task in jobs(request).values()):
        raise _error(429, "DECISION_BUSY", "已有研究或计算执行中，请等待完成。")


def schedule(request, state):
    stop = threading.Event()
    if not hasattr(request.app.state, "decision_stops"):
        request.app.state.decision_stops = {}
    request.app.state.decision_stops[state["run_id"]] = stop
    options = {}
    if getattr(request.app.state, "decision_collector", None):
        options["collector"] = request.app.state.decision_collector
    if state["request"].get("research_mode") == "market_scan":
        from .market_scan import run as market_run

        task = asyncio.create_task(
            market_run(
                repository(request),
                state["run_id"],
                stop,
                request.app.state.settings,
                port=getattr(request.app.state, "decision_model_port", None),
                rules=getattr(request.app.state, "knowledge_rules", None),
                collector=getattr(request.app.state, "decision_market_collector", None),
            )
        )
    elif state["request"].get("research_mode") == "candidates":
        from .screening import run as candidate_run

        task = asyncio.create_task(
            candidate_run(
                repository(request),
                state["run_id"],
                stop,
                request.app.state.settings,
                port=getattr(request.app.state, "decision_model_port", None),
                rules=getattr(request.app.state, "knowledge_rules", None),
                collector=getattr(request.app.state, "decision_candidate_collector", None),
            )
        )
    elif state["request"].get("research_mode") == "medium_term":
        from .medium import run as medium_run

        task = asyncio.create_task(
            medium_run(
                repository(request),
                state["run_id"],
                stop,
                request.app.state.settings,
                port=getattr(request.app.state, "decision_model_port", None),
                **options,
            )
        )
    else:
        task = asyncio.create_task(run(repository(request), state["run_id"], stop, **options))
    active = jobs(request)
    active[state["run_id"]] = task

    def done(_):
        active.pop(state["run_id"], None)
        request.app.state.decision_stops.pop(state["run_id"], None)

    task.add_done_callback(done)


@router.post("/runs", status_code=202)
async def create_run(payload: contracts.ResearchRequest, request: Request):
    repo = repository(request)
    definition = payload.model_dump(mode="json")
    run_id = "RUN-DEC-" + payload.request_key.hex
    if repo.exists(run_id):
        previous = checked(request, run_id)
        prior_request = dict(previous["request"])
        prior_request.setdefault("research_mode", "evidence")
        prior_request.setdefault("include_flows", False)
        prior_request.setdefault("include_industry", False)
        prior_request.setdefault("include_methods", False)
        if prior_request != definition:
            raise _error(409, "DECISION_KEY_CONFLICT", "请求标识已绑定其他条件。")
        return previous
    if payload.scope != "single" and payload.research_mode not in {"candidates", "market_scan"}:
        raise _error(422, "DECISION_SCOPE_UNAVAILABLE", "当前阶段只接通单股取证。")
    if payload.cutoff_at and payload.cutoff_at > datetime.now(UTC):
        raise _error(422, "DECISION_FUTURE_CUTOFF", "截止时点不能在未来。")
    capacity(request)
    if payload.research_mode in {"medium_term", "candidates"} or payload.enhanced_research:
        settings = getattr(request.app.state, "settings", None)
        if not getattr(settings, "deepseek_api_key", None) and not getattr(
            request.app.state, "decision_model_port", None
        ):
            raise _error(503, "DECISION_MODEL_UNAVAILABLE", "研究模型尚未配置，无法生成判断。")
    if payload.parent_run_id:
        parent = checked(request, payload.parent_run_id)
        if parent["status"] != "COMPLETED":
            raise _error(409, "DECISION_PARENT_INCOMPLETE", "父记录尚未完成。")
        if any(
            parent["request"].get(k, "evidence" if k == "research_mode" else None)
            != definition.get(k)
            for k in ("scope", "research_mode")
        ) or (payload.scope != "single" and parent["request"]["entity"] != payload.entity):
            raise _error(409, "DECISION_PARENT_SCOPE", "更新须保持原研究范围与模式。")
    try:
        state, fresh = repo.create(definition, fingerprint())
    except ValueError as error:
        raise _error(409, "DECISION_KEY_CONFLICT", "请求标识已绑定其他条件。") from error
    if fresh:
        schedule(request, state)
    return state


@router.get("/runs")
async def list_runs(request: Request):
    return [
        {
            "page": "market"
            if s["request"].get("research_mode") == "market_scan"
            else "candidates"
            if s["request"].get("research_mode") == "candidates"
            else "decision",
            **{
                k: s.get(k)
                for k in (
                    "run_id",
                    "status",
                    "created_at",
                    "title",
                    "subject",
                    "parent_run_id",
                    "forecast_id",
                    "scenario",
                )
            },
        }
        for s in repository(request).list()
    ]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    enhanced_research: bool | None = None


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: str, payload: RefreshRequest, request: Request):
    from .recovery import prepare_retry

    repo = repository(request)
    previous = await asyncio.to_thread(checked, request, run_id, with_data=False)
    if previous["status"] not in {"FAILED", "INTERRUPTED"}:
        raise _error(409, "DECISION_RETRY_UNAVAILABLE", "仅未完成的研究可以复用资料重试。")
    if previous["request"].get("enhanced_research"):
        return await resume_run(run_id, request)
    target = "RUN-DEC-" + payload.request_key.hex
    if repo.exists(target):
        prior = checked(request, target)
        if prior.get("recovery_of") != run_id:
            raise _error(409, "DECISION_KEY_CONFLICT", "重试标识已绑定其他任务。")
        return prior
    capacity(request)
    try:
        state, fresh = await asyncio.to_thread(
            prepare_retry, repo, previous, payload.request_key, fingerprint()
        )
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(
            409, "DECISION_RETRY_INTEGRITY", "资料完整性检查未通过，请重新取数研究。"
        ) from error
    if fresh:
        schedule(request, state)
    return state


@router.post("/runs/{run_id}/refresh", status_code=202)
async def refresh_run(run_id: str, payload: RefreshRequest, request: Request):
    parent = await asyncio.to_thread(checked, request, run_id)
    if parent["status"] != "COMPLETED" or parent["request"]["mode"] != "latest":
        raise _error(
            409, "DECISION_REFRESH_UNAVAILABLE", "仅已完成的最新研究可以更新；历史时点不回填。"
        )
    definition = dict(parent["request"], request_key=str(payload.request_key), parent_run_id=run_id)
    if payload.enhanced_research is not None:
        definition["enhanced_research"] = payload.enhanced_research
    if "subject" in parent:
        definition["entity"] = parent["subject"]["security_id"]
    return await create_run(contracts.ResearchRequest.model_validate(definition), request)


@router.get("/runs/{run_id}/comparison")
async def compare_run(run_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    if state["status"] != "COMPLETED":
        raise _error(409, "DECISION_INCOMPLETE", "更新尚未完成。")
    parent_id = state.get("parent_run_id")
    if not parent_id:
        return dict(parent=None, current=run_id, changes=[])
    parent = await asyncio.to_thread(checked, request, parent_id)
    from .versions import compare

    return compare(parent, state)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    from .recovery import present

    state = await asyncio.to_thread(checked, request, run_id)
    try:
        return await asyncio.to_thread(present, repository(request), state)
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(
            409, "DECISION_INTEGRITY_FAILED", "已保存资料完整性检查失败，已隐藏内容。"
        ) from error


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    if state["status"] != "COMPLETED":
        raise _error(409, "DECISION_INCOMPLETE", "报告尚未完成，过程可在任务页面查看。")
    enhancement = state.get("data", {}).get("enhanced_research")
    if enhancement:
        repo = repository(request)
        state = dict(
            state,
            enhancement_audit=dict(
                knowledge=repo.value(enhancement["knowledge_ref"]),
                manifest=repo.value(enhancement["manifest_ref"]),
                model_calls=[
                    dict(
                        record=call,
                        request=repo.value(call["request_ref"]),
                        response=repo.value(call["response_ref"])
                        if call.get("response_ref")
                        else None,
                    )
                    for call in state["calls"]
                    if call.get("kind") == "model"
                ],
            ),
        )
        if enhancement.get("focus_ref"):
            state["enhancement_audit"]["focus"] = repo.value(enhancement["focus_ref"])
    return JSONResponse(
        state,
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/runs/{run_id}/evidence/{evidence_id}")
async def get_evidence(run_id: str, evidence_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    for item in state.get("data", {}).get("enhanced_research", {}).get("evidence", []):
        if item["evidence_id"] == evidence_id:
            return dict(evidence=item, document=None)
    data = state.get("data", {})
    items = data.get("bundle", {}).get("items", [])
    if state["request"].get("enhanced_research") and data.get("report", {}).get("stage") == "P5":
        items = data["evidence"]
    for item in items:
        if item["evidence_id"] == evidence_id:
            doc = next(
                (
                    d
                    for d in state["data"]["documents"]
                    + state["data"].get("industry_documents", [])
                    if d["raw_ref"] == item["raw_ref"]
                ),
                None,
            )
            return dict(evidence=item, document=doc)
    raise _error(404, "DECISION_EVIDENCE_NOT_FOUND", "本任务没有这条引用。")


@router.get("/runs/{run_id}/documents/{document_id}/raw")
async def get_document(run_id: str, document_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    for doc in state.get("data", {}).get("documents", []) + state.get("data", {}).get(
        "industry_documents", []
    ):
        if doc["document_id"] == document_id:
            return Response(
                repository(request).raw_bytes(doc["raw_ref"]),
                media_type="application/pdf",
                headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
            )
    raise _error(404, "DECISION_DOCUMENT_NOT_FOUND", "本任务没有这份归档原文。")


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    checked(request, run_id, with_data=False)
    try:
        state = repository(request).cancel(run_id)
    except ValueError as error:
        raise _error(409, "DECISION_CANCEL_UNAVAILABLE", "该任务当前不能停止。") from error
    stop = getattr(request.app.state, "decision_stops", {}).get(run_id)
    if stop:
        stop.set()
    return state


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, request: Request):
    state = checked(request, run_id, with_data=False)
    if state["status"] not in {"FAILED", "INTERRUPTED"}:
        raise _error(409, "DECISION_RESUME_UNAVAILABLE", "该任务不能恢复；需要新资料时请新建任务。")
    if state["implementation"] != fingerprint():
        raise _error(
            409, "DECISION_ENVIRONMENT_CHANGED", "实现或依赖版本改变，请新建任务，避免混合旧步骤。"
        )
    if state.get("elapsed_seconds", 0) >= state["request"]["budget_profile"]["max_seconds"]:
        raise _error(409, "DECISION_BUDGET_EXHAUSTED", "累计时间预算已用尽，请新建任务。")
    capacity(request)
    state.update(status="RUNNING", error=None)
    repository(request).save(state)
    schedule(request, state)
    return state


@router.get("/runs/{run_id}/forecast")
async def get_forecast(run_id: str, request: Request):
    checked(request, run_id)
    from .forecasts import forecast_chain

    try:
        return await asyncio.to_thread(forecast_chain, repository(request), run_id)
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(
            409, "DECISION_FORECAST_INTEGRITY", "首次观点或版本链完整性检查失败。"
        ) from error


@router.get("/runs/{run_id}/outcomes")
async def get_outcomes(run_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    if not state.get("forecast_id"):
        raise _error(409, "DECISION_FORECAST_UNAVAILABLE", "该记录尚无中期判断。")
    from .forecasts import observations
    from .forecasts import schedule as observation_schedule

    try:
        return await asyncio.to_thread(
            lambda: dict(
                schedule=observation_schedule(repository(request), state["data"]),
                observations=observations(repository(request), run_id),
            )
        )
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(409, "DECISION_OUTCOME_INTEGRITY", "观察记录或日历核对失败。") from error


@router.post("/runs/{run_id}/observe")
async def observe_run(run_id: str, request: Request):
    if await request.body():
        raise _error(
            422, "DECISION_ACTUAL_FORBIDDEN", "观察数据由服务端取得，不能上传收益或实际结果。"
        )
    state = await asyncio.to_thread(checked, request, run_id)
    if not state.get("forecast_id"):
        raise _error(409, "DECISION_FORECAST_UNAVAILABLE", "该记录尚无中期判断。")
    capacity(request)
    from .forecasts import observe

    try:
        task = asyncio.create_task(asyncio.to_thread(observe, repository(request), state))
        key = "OBSERVE-" + run_id
        jobs(request)[key] = task
        try:
            return await task
        finally:
            jobs(request).pop(key, None)
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(409, "DECISION_OBSERVE_FAILED", "本次观察无法核对，原观点保留。") from error


class WithdrawalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str


@router.post("/runs/{run_id}/withdraw")
async def withdraw_run(run_id: str, payload: WithdrawalRequest, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    if not state.get("forecast_id") or not 1 <= len(payload.reason.strip()) <= 500:
        raise _error(422, "DECISION_WITHDRAWAL_INVALID", "请提供撤回原因，原预测不会删除。")
    from .forecasts import withdraw

    return await asyncio.to_thread(withdraw, repository(request), run_id, payload.reason.strip())


@router.get("/capabilities")
def get_capabilities(request: Request):
    settings = getattr(request.app.state, "settings", None)
    try:
        result = capabilities(
            model_configured=bool(settings and settings.deepseek_api_key),
            model=settings.model if settings else None,
            root=Path(__file__).resolve().parents[4],
        )
        from .release import latest

        result["release"] = (
            latest(repository(request))
            if getattr(request.app.state, "research_store", None)
            else dict(status="NOT_RUN", executed=0)
        )
        if result["release"].get("executed"):
            result["evaluation"].update(
                executed=result["release"]["executed"], status=result["release"]["status"]
            )
        return result
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(409, "DECISION_AUDIT_INTEGRITY", "能力/来源台账完整性检查失败。") from error


@router.get("/methods")
def get_methods():
    from .methods import load_methods

    return [m.model_dump(mode="json") for m in load_methods()]


@router.get("/industry-cache")
async def industry_cache(request: Request):
    from .industry_cache import index

    try:
        return await asyncio.to_thread(index, repository(request))
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(409, "DECISION_CACHE_INTEGRITY", "行业归档材料核查失败，未复用。") from error


@router.get("/evaluation-cases")
def get_cases():
    return [c.model_dump(mode="json") for c in load_cases()]


@router.get("/acceptance")
def get_acceptance(request: Request):
    from .release import latest

    try:
        return latest(repository(request))
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(409, "DECISION_ACCEPTANCE_INTEGRITY", "验收记录完整性检查失败。") from error


@router.get("/schema")
def get_schema():
    types = {}
    for module in (contracts, daily_contracts):
        for name, value in vars(module).items():
            if (
                isinstance(value, type)
                and value.__module__ == module.__name__
                and hasattr(value, "model_json_schema")
            ):
                types[name] = value.model_json_schema()
    return dict(schema_version="decision-v1", contracts=types)
