"""Fresh research jobs, immutable recorded inputs and replayable scenario versions."""

import asyncio
import hashlib
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import Field

from ..api import _error, _runtime
from ..contracts import canonical
from ..domain import DcfAssumptions, FrozenModel
from .calculate import build, dcf_result, simulate
from .sources import collect, latest_quotes

router = APIRouter(prefix="/api/quant/live", tags=["quant-live"])
# Windows denies replacement while another thread holds the destination open.
# The local server uses one process; serialize short file reads and atomic writes.
IO_LOCK = threading.RLock()
ROLES = ("Planner", "Data", "Analyst", "Verifier", "Writer")
LABELS = (
    "确定美的集团最新数据研究范围",
    "现场获取行情、财报、估值与风险资料",
    "计算单季财务、TTM、DCF 与单股模拟",
    "核对字段、时点与供应商差异",
    "整理可追溯报告",
)


class LiveIn(FrozenModel):
    request_key: UUID
    code: Literal["000333"] = "000333"


class ScenarioIn(FrozenModel):
    request_key: UUID
    dcf: DcfAssumptions = Field(default_factory=DcfAssumptions)
    cost_pct: float = Field(default=0.13, ge=0, le=2, allow_inf_nan=False)
    fast: int = Field(default=20, ge=2, le=119)
    slow: int = Field(default=60, ge=3, le=120)


def root(request):
    path = Path(_runtime(request).journal_root).parent / "live-research"
    path.mkdir(exist_ok=True)
    return path


def directory(base, run_id):
    if not re.fullmatch(r"RUN-LIVE-[0-9a-f]{32}", run_id):
        raise _error(404, "LIVE_NOT_FOUND", "实时研究记录不存在。")
    return base / run_id


def write(path, value):
    with IO_LOCK:
        temp = path.with_suffix("." + uuid4().hex + ".tmp")
        temp.write_text(canonical(value), encoding="utf-8")
        temp.replace(path)


def read(path):
    with IO_LOCK:
        return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def jobs(request):
    if not hasattr(request.app.state, "live_jobs"):
        request.app.state.live_jobs = {}
    return request.app.state.live_jobs


def load(base, run_id):
    path = directory(base, run_id)
    if not (path / "state.json").is_file():
        raise _error(404, "LIVE_NOT_FOUND", "实时研究记录不存在。")
    state = read(path / "state.json")
    if state["status"] == "COMPLETED":
        data = read(path / "result.json")
        if digest(data) != state["result_sha256"]:
            raise ValueError("live result hash mismatch")
        for source in data["sources"]:
            raw = (path / (source["key"] + ".raw")).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError("recorded source hash mismatch")
        state["data"] = data
    return state


def public(state):
    state = dict(state)
    if state.get("data"):
        state["data"] = {k: v for k, v in state["data"].items() if k != "calculation_inputs"}
    return state


async def execute(base, run_id):
    path = directory(base, run_id)
    state = read(path / "state.json")

    def stage(index):
        state["process"] = [
            dict(
                role=role,
                detail=LABELS[i],
                status="COMPLETED" if i < index else "RUNNING" if i == index else "PENDING",
            )
            for i, role in enumerate(ROLES)
        ]
        write(path / "state.json", state)

    def work():
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        end = (now.date() - timedelta(days=1)).isoformat()
        start = (now.date() - timedelta(days=850)).isoformat()
        stage(1)

        def progress(count):
            state["sources_received"] = count
            write(path / "state.json", state)

        values, sources = collect(
            start, end, lambda k, raw: (path / (k + ".raw")).write_bytes(raw), progress
        )
        stage(2)
        result = build(values, end, now.date().isoformat())
        stage(3)
        result.update(
            sources=sources,
            acquired_at=datetime.now(UTC).isoformat(),
            requested_at=state["created_at"],
            model_version="live-research-v1",
            process_note="复用多智能体职责划分；此单股链路由确定性工具编排执行，无虚构模型调用。",
        )
        stage(4)
        write(path / "result.json", result)
        state.update(status="COMPLETED", result_sha256=digest(result))
        stage(5)

    try:
        await asyncio.to_thread(work)
    except Exception as error:
        state.update(
            status="FAILED",
            error="实时研究未完成：" + type(error).__name__ + " · " + str(error)[:240],
        )
        for item in state.get("process", []):
            if item["status"] == "RUNNING":
                item["status"] = "FAILED"
        write(path / "state.json", state)


@router.get("/quotes")
async def quotes():
    try:
        return await asyncio.to_thread(latest_quotes)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        raise _error(503, "LIVE_QUOTE_FAILED", "行情刷新失败，未替换为旧数据。") from error


@router.post("/runs", status_code=202)
async def start(payload: LiveIn, request: Request):
    base = root(request)
    run_id = "RUN-LIVE-" + payload.request_key.hex
    path = directory(base, run_id)
    if path.exists():
        state = read(path / "state.json")
        if state["request"] != payload.model_dump(mode="json"):
            raise _error(409, "LIVE_KEY_CONFLICT", "请求标识已被其他参数使用。")
        return public(state)
    active = jobs(request)
    if any(not task.done() for task in active.values()):
        raise _error(409, "LIVE_BUSY", "已有实时研究正在采集，请等待当前任务完成。")
    path.mkdir()
    state = dict(
        run_id=run_id,
        status="RUNNING",
        created_at=datetime.now(UTC).isoformat(),
        title="美的集团 · 实时完整研究",
        scenario="live",
        kind="fresh",
        request=payload.model_dump(mode="json"),
        sources_received=0,
        process=[
            dict(role=r, detail=LABELS[i], status="COMPLETED" if i == 0 else "PENDING")
            for i, r in enumerate(ROLES)
        ],
    )
    write(path / "state.json", state)
    active[run_id] = asyncio.create_task(execute(base, run_id))
    active[run_id].add_done_callback(lambda _: active.pop(run_id, None))
    return state


@router.get("/runs")
async def history(request: Request):
    entries = []
    for path in root(request).glob("RUN-LIVE-*/state.json"):
        value = read(path)
        entries.append(
            {k: value[k] for k in ("run_id", "status", "created_at", "title", "scenario", "kind")}
        )
    return sorted(entries, key=lambda x: x["created_at"], reverse=True)[:100]


@router.get("/runs/{run_id}")
async def get(run_id: str, request: Request):
    base = root(request)
    try:
        state = await asyncio.to_thread(load, base, run_id)
    except (OSError, ValueError, KeyError) as error:
        raise _error(409, "LIVE_INTEGRITY_ERROR", "研究记录完整性检查失败。请重新采集。") from error
    if state["status"] == "RUNNING" and run_id not in jobs(request):
        # Completion can occur between the threaded read and task-registry check.
        # Reconcile the persisted terminal state before diagnosing a restart.
        state = await asyncio.to_thread(load, base, run_id)
        if state["status"] == "RUNNING":
            state.update(status="INTERRUPTED", error="服务曾重启；请重新发起采集。")
    return public(state)


@router.post("/runs/{run_id}/scenario", status_code=201)
async def recalculate(run_id: str, payload: ScenarioIn, request: Request):
    if payload.fast >= payload.slow:
        raise _error(422, "LIVE_INVALID_MA", "快线天数必须小于慢线天数。")
    base = root(request)
    parent = await asyncio.to_thread(load, base, run_id)
    if parent["status"] != "COMPLETED":
        raise _error(409, "LIVE_INCOMPLETE", "请等待原始研究完成。")
    child_id = "RUN-LIVE-" + payload.request_key.hex
    path = directory(base, child_id)
    definition = dict(parent=run_id, **payload.model_dump(mode="json"))
    if path.exists():
        existing = load(base, child_id)
        if existing["request"] != definition:
            raise _error(409, "LIVE_KEY_CONFLICT", "请求标识已被其他参数使用。")
        return public(existing)
    result = parent["data"]
    result["dcf"] = dcf_result(result["dcf"], payload.dcf)
    inputs = result["calculation_inputs"]
    result["simulation"] = simulate(
        inputs["prices"], inputs["benchmark"], payload.cost_pct / 100, payload.fast, payload.slow
    )
    path.mkdir()
    for source in result["sources"]:
        (path / (source["key"] + ".raw")).write_bytes(
            (directory(base, run_id) / (source["key"] + ".raw")).read_bytes()
        )
    write(path / "result.json", result)
    state = dict(
        run_id=child_id,
        status="COMPLETED",
        created_at=datetime.now(UTC).isoformat(),
        title="美的集团 · DCF / 策略情景",
        scenario="live",
        kind="scenario",
        parent_run_id=run_id,
        request=definition,
        result_sha256=digest(result),
        process=parent["process"],
    )
    write(path / "state.json", state)
    return public(dict(**state, data=result))


@router.get("/runs/{run_id}/export", response_class=PlainTextResponse)
async def export(run_id: str, request: Request):
    state = await asyncio.to_thread(load, root(request), run_id)
    if state["status"] != "COMPLETED":
        raise _error(409, "LIVE_INCOMPLETE", "研究尚未完成。")
    d = state["data"]
    lines = [
        "# 美的集团实时研究",
        "",
        f"记录：{run_id}",
        f"采集时间：{d['acquired_at']}",
        "",
        "## 核心指标",
        *[f"- {m['name']}：{m['value']} {m['unit']}" for m in d["metrics"]],
        "",
        "## DCF 情景",
        f"每股：{d['dcf']['per_share']} 元",
        d["dcf"]["note"],
        "",
        "## 单股研究模拟",
        d["simulation"]["note"],
        canonical(d["simulation"]["metrics"]),
        "",
        "## 风险资料",
        *[f"- {r['title']}：{r['text']}" for r in d["risks"]],
        "",
        "## 来源",
        *[
            f"- [{s['key']}]({s['url']}) · {s['retrieved_at']} · SHA256 {s['sha256']}"
            for s in d["sources"]
        ],
        "",
        *d["notes"],
    ]
    return "\n".join(lines)
