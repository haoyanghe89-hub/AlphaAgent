"""Canonical research endpoints; live aliases use these same handlers and records."""

import asyncio
import copy
import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import Field

from marketpulse.infrastructure.storage.models import BlobError

from ..api import _error
from ..domain import FrozenModel
from .api import ScenarioIn, public
from .calculate import dcf_result, simulate
from .main_flow import MainFlowFeed
from .market_dashboard import MarketDashboard
from .overview import build_overview
from .securities import search
from .service import ResearchWorkflow, process
from .sources import latest_quotes

router = APIRouter(prefix="/api/quant/research", tags=["quant-research"])


class ResearchIn(FrozenModel):
    request_key: UUID
    question: str = Field(default="全面分析美的集团", min_length=1, max_length=2000)
    code: str | None = Field(default=None, pattern=r"^(?:00|30|60|68)\d{4}$")
    parent_run_id: str | None = Field(default=None, pattern=r"^RUN-LIVE-[0-9a-f]{32}$")


def store(request):
    value = getattr(request.app.state, "research_store", None)
    if value is None:
        raise _error(503, "RESEARCH_NOT_READY", "研究服务未就绪。")
    return value


def jobs(request):
    if not hasattr(request.app.state, "research_jobs"):
        request.app.state.research_jobs = {}
    return request.app.state.research_jobs


def checked(request, run_id):
    if not re.fullmatch(r"RUN-LIVE-[0-9a-f]{32}", run_id):
        raise _error(404, "RESEARCH_NOT_FOUND", "研究记录不存在。")
    try:
        return store(request).get(run_id)
    except (OSError, ValueError, KeyError, BlobError) as error:
        raise _error(409, "RESEARCH_INTEGRITY_FAILED", "研究记录或证据完整性检查失败。") from error


def schedule(request, state):
    workflow = ResearchWorkflow(
        store(request),
        request.app.state.settings,
        getattr(request.app.state, "research_model_port", None),
    )

    async def bounded_run():
        try:
            async with asyncio.timeout(600):
                await workflow.run(state["run_id"])
        except TimeoutError:
            record = store(request).get(state["run_id"], with_data=False)
            record.update(status="INTERRUPTED", error="研究达到十分钟时限；成功步骤保留，可继续。")
            store(request).put(record)

    task = asyncio.create_task(bounded_run())
    active = jobs(request)
    active[state["run_id"]] = task
    task.add_done_callback(lambda _: active.pop(state["run_id"], None))


@router.get("/capabilities")
async def capabilities(request: Request):
    settings = request.app.state.settings
    return dict(
        model_configured=bool(settings.deepseek_api_key)
        or bool(getattr(request.app.state, "research_model_port", None)),
        model=settings.model,
        scope="沪深 A 股单股最新研究",
        roles=5,
    )


@router.get("/securities")
async def securities(q: str, request: Request):
    try:
        candidates, _, _ = await asyncio.to_thread(search, q)
        return candidates
    except (ValueError, OSError, RuntimeError) as error:
        raise _error(503, "SECURITY_SEARCH_FAILED", "证券查询失败，请稍后重试。") from error


@router.get("/quotes")
async def quotes(symbol: str | None = None):
    if symbol and (
        len(symbol.split(",")) > 5
        or not all(
            re.fullmatch(r"(?:sz(?:00|30)|sh(?:60|68))\d{4}", item) for item in symbol.split(",")
        )
    ):
        raise _error(422, "QUOTE_SCOPE", "请输入沪深 A 股证券代码。")
    try:
        return await asyncio.to_thread(latest_quotes, symbol)
    except Exception as error:
        raise _error(503, "LIVE_QUOTE_FAILED", "行情刷新失败，未替换为旧数据。") from error


@router.get("/market-dashboard")
async def market_dashboard(request: Request):
    if not hasattr(request.app.state, "market_dashboard"):
        repo = store(request)
        request.app.state.market_dashboard = MarketDashboard(
            repo.legacy_root.parent / "market-live-cache"
        )
    try:
        return await asyncio.to_thread(request.app.state.market_dashboard.snapshot)
    except ValueError as error:
        raise _error(503, "MARKET_FEED_UNAVAILABLE", "市场行情暂未取得，请稍后刷新。") from error


def funding_feed(request):
    if not hasattr(request.app.state, "main_flow"):
        repo = store(request)
        if not hasattr(request.app.state, "market_dashboard"):
            request.app.state.market_dashboard = MarketDashboard(
                repo.legacy_root.parent / "market-live-cache"
            )
        request.app.state.main_flow = MainFlowFeed(repo, request.app.state.market_dashboard)
    return request.app.state.main_flow


@router.get("/main-flow")
async def daily_main_flow(request: Request):
    try:
        return await asyncio.to_thread(funding_feed(request).daily)
    except (ValueError, BlobError) as error:
        raise _error(
            503, "MAIN_FLOW_UNAVAILABLE", "盘后主力资金数据刷新失败，请稍后重试。"
        ) from error


@router.get("/main-flow/{symbol}")
async def stock_main_flow(symbol: str, request: Request):
    if not re.fullmatch(r"(?:sz(?:00|30)|sh(?:60|68))\d{4}", symbol):
        raise _error(422, "MAIN_FLOW_SCOPE", "请输入沪深 A 股证券代码。")
    try:
        return await asyncio.to_thread(funding_feed(request).stock, symbol)
    except (ValueError, BlobError) as error:
        raise _error(
            503, "MAIN_FLOW_UNAVAILABLE", "盘中主力资金源暂时不可用，请稍后重试。"
        ) from error


@router.post("/runs", status_code=202)
async def start(payload: ResearchIn, request: Request):
    repo = store(request)
    run_id = "RUN-LIVE-" + payload.request_key.hex
    definition = payload.model_dump(mode="json")
    if repo.exists(run_id):
        registered = checked(request, run_id)
        if registered["request"] != definition:
            raise _error(409, "LIVE_KEY_CONFLICT", "请求标识已被其他参数使用。")
        return public(checked(request, run_id))
    caps = await capabilities(request)
    if not caps["model_configured"]:
        raise _error(503, "MODEL_NOT_CONFIGURED", "未配置研究模型，无法启动真实 Agent 协作。")
    if any(not task.done() for task in jobs(request).values()):
        raise _error(409, "LIVE_BUSY", "已有研究正在执行，请等待完成。")
    if payload.parent_run_id:
        parent = checked(request, payload.parent_run_id)
        if parent["status"] != "COMPLETED":
            raise _error(409, "PARENT_INCOMPLETE", "请等待父研究完成。")
    state = dict(
        run_id=run_id,
        request=definition,
        status="RUNNING",
        created_at=datetime.now(UTC).isoformat(),
        title=payload.question,
        scenario="live",
        kind="followup" if payload.parent_run_id else "fresh",
        parent_run_id=payload.parent_run_id,
        process=process(),
        calls=[],
        raw_refs={},
    )
    repo.put(state)
    schedule(request, state)
    return public(state)


@router.get("/runs")
async def history(request: Request):
    return [
        {
            k: s.get(k)
            for k in (
                "run_id",
                "status",
                "execution_status",
                "created_at",
                "title",
                "scenario",
                "kind",
                "parent_run_id",
                "subject",
            )
        }
        for s in store(request).list()
    ]


@router.get("/runs/{run_id}")
async def get(run_id: str, request: Request):
    return public(await asyncio.to_thread(checked, request, run_id))


@router.get("/runs/{run_id}/overview")
async def overview(run_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    if state["status"] != "COMPLETED":
        raise _error(409, "LIVE_INCOMPLETE", "请等待研究完成后查看解读。")
    result = await asyncio.to_thread(reader_projection, store(request), state)
    return dict(
        result,
        input_result_ref=state.get("result_ref"),
        input_result_sha256=state.get("result_sha256"),
        interpretation="基于原报告的可复算解读，不改写原报告或原模型结论",
    )


def reader_projection(repo, state):
    profile = None
    meta = next((s for s in state["data"].get("sources", []) if s["key"] == "profile"), None)
    if meta and meta.get("blob_ref"):
        profile = repo.value(meta["blob_ref"])
    return build_overview(state["data"], profile)


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume(run_id: str, request: Request):
    state = checked(request, run_id)
    if state["status"] not in {"FAILED", "INTERRUPTED"} or "calls" not in state:
        raise _error(409, "RESUME_UNAVAILABLE", "该记录不能恢复；请重新发起研究。")
    if any(not t.done() for t in jobs(request).values()):
        raise _error(409, "LIVE_BUSY", "已有研究正在执行。")
    state.pop("data", None)
    state.update(status="RUNNING", error=None)
    store(request).put(state)
    schedule(request, state)
    return public(state)


@router.post("/runs/{run_id}/scenario", status_code=201)
async def scenario(run_id: str, payload: ScenarioIn, request: Request):
    if payload.fast >= payload.slow:
        raise _error(422, "LIVE_INVALID_MA", "快线天数必须小于慢线天数。")
    parent = await asyncio.to_thread(checked, request, run_id)
    if parent["status"] != "COMPLETED":
        raise _error(409, "LIVE_INCOMPLETE", "请等待研究完成。")
    repo, child_id = store(request), "RUN-LIVE-" + payload.request_key.hex
    definition = dict(parent=run_id, **payload.model_dump(mode="json"))
    if repo.exists(child_id):
        existing = checked(request, child_id)
        if existing["request"] != definition:
            raise _error(409, "LIVE_KEY_CONFLICT", "请求标识已被其他参数使用。")
        return public(checked(request, child_id))
    data = copy.deepcopy(parent["data"])
    if "basis" in data["dcf"]:
        data["dcf"] = dcf_result(data["dcf"], payload.dcf)
    inputs = data["calculation_inputs"]
    data["simulation"] = simulate(
        inputs["prices"], inputs["benchmark"], payload.cost_pct / 100, payload.fast, payload.slow
    )
    # Old model interpretations must not survive changed numeric assumptions.
    data.pop("agent_report", None)
    data.pop("evidence", None)
    data["process_note"] = "确定性参数重算；新情景尚未调用模型解读，可在下方追问。"
    # Import old file sources without changing their original bytes or hashes.
    for source in data["sources"]:
        if not source.get("blob_ref"):
            raw = (repo.legacy_root / run_id / (source["key"] + ".raw")).read_bytes()
            source["blob_ref"] = repo.raw(raw)
    state = dict(
        run_id=child_id,
        request=definition,
        status="COMPLETED",
        created_at=datetime.now(UTC).isoformat(),
        title=data["subject"]["name"] + " · 参数情景",
        scenario="live",
        kind="scenario",
        parent_run_id=run_id,
        process=[],
        calls=[],
        result_ref=repo.blob(data),
    )
    repo.put(state)
    return public(dict(state, data=data))


@router.get("/runs/{run_id}/export", response_class=PlainTextResponse)
async def export(run_id: str, request: Request):
    state = await asyncio.to_thread(checked, request, run_id)
    if state["status"] != "COMPLETED":
        raise _error(409, "LIVE_INCOMPLETE", "研究尚未完成。")
    d = state["data"]
    reading = reader_projection(store(request), state)
    price = reading["price"]
    reader_lines = ["## 先读结论", reading["summary"], reading["note"]]
    if price["status"] == "AVAILABLE":
        reader_lines.append(
            f"- {price['start']} 至 {price['end']}：前复权收盘 "
            f"{price['close']:.2f} 元，样本区间 "
            f"{price['low']:.2f}～{price['high']:.2f} 元。"
        )
    reader_lines.extend(
        [
            "- " + reading["growth"]["meaning"],
            "- " + reading["valuation"]["meaning"],
            "",
            "## 后续观察条件",
        ]
    )
    reader_lines.extend(
        f"- {w['horizon']}：{w['condition']}，{w['implication']}" for w in reading["watch"]
    )
    reader_lines.extend(["", "## 判断尚缺的依据", *["- " + g for g in reading["gaps"]], ""])
    lines = [
        "# " + d["subject"]["name"] + "研究报告",
        "",
        f"记录：{run_id}",
        "采集：" + d["acquired_at"],
        "",
        *reader_lines,
        "## 核心指标",
        *[f"- {m['name']}：{m['value']} {m['unit']}" for m in d["metrics"]],
        "",
        "## Agent 解读",
        *[
            c["text"] + " [" + ", ".join(c["evidence_ids"]) + "]"
            for c in d.get("agent_report", {}).get("claims", [])
        ],
        "",
        "## 方法边界",
        d["dcf"].get("note", d["dcf"].get("reason", "")),
        d["simulation"]["note"],
        "",
        "## 来源",
        *[
            f"- [{s['key']}]({s['url']}) · {s['retrieved_at']} · SHA256 {s['sha256']}"
            for s in d["sources"]
        ],
        "",
        "## 说明",
        *d["notes"],
    ]
    return "\n".join(lines)


# Existing bookmarks and clients resolve through the identical shared service.
compat_router = APIRouter(prefix="/api/quant/live", tags=["quant-live-compat"])
for route in router.routes:
    compat_router.add_api_route(
        route.path.removeprefix("/api/quant/research"),
        route.endpoint,
        methods=list(route.methods),
        status_code=route.status_code,
        response_class=route.response_class,
    )
