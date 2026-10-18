"""Local investment research API, fixed source reads and private offline export."""

import asyncio
import io
import json
import re
import threading
import zipfile

from fastapi import APIRouter, HTTPException, Request, Response

from .observations import ObservationStore
from .planning_api import checked, local_write
from .research_contracts import InvestmentRequest
from .research_service import run_with_recovery
from .research_store import InvestmentStore

router = APIRouter(prefix="/api/quant/investment-research", tags=["Investment research"])


def repository(request):
    return InvestmentStore(request.app.state.research_store)


def schedule(request, state):
    active = request.app.state.research_jobs
    if any(not task.done() for task in active.values()):
        raise HTTPException(429, "已有研究执行中，请稍后重试。")
    if state["status"] == "INTERRUPTED":
        state.update(status="RUNNING", error=None)
        repository(request).save(state)
    options = {}
    for name, argument in (
        ("investment_research_port", "port"),
        ("investment_research_collector", "collector"),
        ("investment_context_collector", "context_collector"),
    ):
        if getattr(request.app.state, name, None) is not None:
            options[argument] = getattr(request.app.state, name)
    task = asyncio.create_task(
        run_with_recovery(
            repository(request),
            request.app.state.knowledge_rules,
            state["run_id"],
            threading.Event(),
            request.app.state.settings,
            **options,
        )
    )
    active[state["run_id"]] = task
    task.add_done_callback(lambda _: active.pop(state["run_id"], None))


@router.get("/runs")
def list_runs(request: Request):
    return repository(request).list()


@router.post("/runs", status_code=202)
async def create(payload: InvestmentRequest, request: Request):
    local_write(request)
    repo = repository(request)
    run_id = repo.prefix + payload.request_key.hex
    if repo.exists(run_id):
        state = checked(lambda: repo.get(run_id))
        if InvestmentRequest.model_validate(state["request"]) != payload:
            raise HTTPException(409, "请求标识已经绑定其他研究输入。")
        return state
    if any(not t.done() for t in request.app.state.research_jobs.values()):
        raise HTTPException(429, "已有研究执行中，请稍后重试。")
    state, fresh = repo.create(payload.model_dump(mode="json"))
    if fresh:
        schedule(request, state)
    return state


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return checked(lambda: repository(request).get(run_id))


@router.get("/evaluation")
def evaluation_summary(request: Request):
    return checked(lambda: ObservationStore(repository(request)).summary())


@router.get("/runs/{run_id}/observations")
def observations(run_id: str, request: Request):
    repo = repository(request)
    return checked(lambda: ObservationStore(repo).view(repo.get(run_id)))


@router.post("/runs/{run_id}/observe")
async def observe(run_id: str, request: Request):
    local_write(request)
    repo = repository(request)
    state = checked(lambda: repo.get(run_id))
    if (
        state["request"]["schema_version"] != "investment-research-v4"
        or state["status"] != "COMPLETED"
    ):
        raise HTTPException(409, "此研究尚不能前瞻核对；仅已事先登记并完成的新研究可以核对。")
    active = request.app.state.research_jobs
    if any(not task.done() for task in active.values()):
        raise HTTPException(429, "已有研究或观察取数执行中，请稍后再试。")
    options = {}
    if getattr(request.app.state, "investment_observation_collector", None):
        options["collector"] = request.app.state.investment_observation_collector
    task = asyncio.create_task(
        asyncio.to_thread(checked, lambda: ObservationStore(repo).observe(state, **options))
    )
    active["observe:" + run_id] = task
    try:
        result = await asyncio.shield(task)
        return result
    finally:
        task.add_done_callback(lambda _: active.pop("observe:" + run_id, None))


@router.post("/runs/{run_id}/review-candidate")
def review_candidate(run_id: str, request: Request):
    local_write(request)
    repo = repository(request)
    return checked(
        lambda: ObservationStore(repo).suggest(repo.get(run_id), request.app.state.knowledge_rules)
    )


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume(run_id: str, request: Request):
    local_write(request)
    state = checked(lambda: repository(request).get(run_id))
    if state["status"] == "INTERRUPTED":
        schedule(request, state)
    return state


@router.get("/runs/{run_id}/sources/{sha256}")
def source(run_id: str, sha256: str, request: Request):
    repo = repository(request)
    state = checked(lambda: repo.get(run_id))
    data = state.get("data", {})
    allowed = {s["blob_ref"] for s in data.get("sources", [])}
    allowed.update(d["raw_ref"] for d in data.get("knowledge_usage", {}).get("documents", []))
    ref = "blob://sha256/" + sha256
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or ref not in allowed:
        raise HTTPException(404, "该来源不在此报告中。")
    return Response(
        checked(lambda: repo.raw_bytes(ref)),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="source-{sha256[:12]}.bin"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@router.get("/runs/{run_id}/audit")
def audit(run_id: str, request: Request):
    repo = repository(request)
    state = checked(lambda: repo.get(run_id))
    calls = []
    for call in state["calls"]:
        if call.get("kind") != "model":
            continue
        calls.append(
            dict(
                call,
                request=repo.value(call["request_ref"]),
                output=repo.value(call["response_ref"]) if call.get("response_ref") else None,
            )
        )
    return dict(
        public_context=repo.value(state["public_context_ref"])
        if state.get("public_context_ref")
        else None,
        model_calls=calls,
        profile_sent_to_model=False,
        holdings_sent_to_model=False,
    )


@router.get("/runs/{run_id}/export")
def export(run_id: str, request: Request):
    repo = repository(request)
    state = checked(lambda: repo.get(run_id))
    if state["status"] != "COMPLETED":
        raise HTTPException(409, "研究完成后才能导出核验包。")
    if state["request"]["schema_version"] == "investment-research-v4":
        state["observations"] = ObservationStore(repo).view(state)["history"]
        for observation in state["observations"]:
            observation["record_ref"] = "blob://sha256/" + observation["observation_id"]
    # Only refs in this verified record/snapshots are eligible; never accept arbitrary paths.
    refs = set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(state)))
    for key in (
        "inputs_ref",
        "public_context_ref",
        "core_ref",
        "knowledge_ref",
        "evidence_ref",
        "context_snapshot_ref",
        "context_report_ref",
        "forward_protocol_ref",
    ):
        if key not in state:
            continue
        refs.update(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(repo.value(state[key]))))
    for observation in state.get("observations", []):
        if observation.get("snapshot_ref"):
            refs.update(
                re.findall(
                    r"blob://sha256/[0-9a-f]{64}",
                    json.dumps(repo.value(observation["snapshot_ref"])),
                )
            )
    if len(refs) > 200:
        raise HTTPException(409, "核验包超出当前文件数上限。")
    stream = io.BytesIO()
    total = 0
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("record.json", json.dumps(state, ensure_ascii=False, indent=2))
        for ref in sorted(refs):
            raw = checked(lambda ref=ref: repo.raw_bytes(ref))
            total += len(raw)
            if total > 60_000_000:
                raise HTTPException(409, "核验包超出当前大小上限。")
            archive.writestr("blobs/" + ref.rsplit("/", 1)[1] + ".bin", raw)
        archive.writestr(
            "README.txt",
            "本机私有核验包，可能包含已声明资金与持仓。record.json保存报告与调用，blobs文件名为原始SHA256；按inputs_ref、core_ref、knowledge_ref和public_context_ref读取固定输入。公开模型上下文不含资金、持仓或数量。研究效果未验证，非交易指令。",
        )
    return Response(
        stream.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}.zip"',
            "Cache-Control": "no-store",
        },
    )
