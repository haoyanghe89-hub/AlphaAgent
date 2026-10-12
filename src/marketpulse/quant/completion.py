"""Dynamic comparison and admitted strategy delivery over the shared durable store."""

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import Field, model_validator

from marketpulse.infrastructure.storage.models import BlobError

from .api import _error
from .backtest.catalog import PackageCatalog
from .backtest.package import DataPackage, Review
from .backtest.reference import ResultReference
from .comparison_reader import ComparisonMetric
from .comparison_reader import present as comparison_presentation
from .contracts import canonical, digest
from .domain import ExactDecimal, FrozenModel
from .execution.worker import bounded_read
from .live.research_api import jobs, store
from .live.securities import resolve
from .live.service import ResearchWorkflow, process

router = APIRouter(prefix="/api/quant", tags=["quant-completion"])


class CompareIn(FrozenModel):
    request_key: UUID
    entities: tuple[str, str]

    @model_validator(mode="after")
    def distinct(self):
        if any(not v.strip() or len(v) > 80 for v in self.entities):
            raise ValueError("请输入两只证券的名称或代码")
        if self.entities[0].strip() == self.entities[1].strip():
            raise ValueError("请选择两只不同股票")
        return self


class StrategyIn(FrozenModel):
    request_key: UUID
    package_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    pe_max: ExactDecimal | None = Field(default=Decimal("30"), gt=0, le=10000)
    roe_min: ExactDecimal | None = Field(default=Decimal("15"), ge=-100, le=1000)
    rev_yoy_min: ExactDecimal | None = Field(default=None, ge=-100, le=10000)
    cost_pct_per_side: ExactDecimal = Field(default=Decimal(".13"), ge=0, le=1)
    initial_cash: ExactDecimal = Field(default=Decimal("1000000"), ge=1000, le=1000000000)
    risk_free_annual_pct: ExactDecimal = Field(ge=-10, le=20)


def catalog(request):
    custom = getattr(request.app.state, "strategy_catalog", None)
    root = Path(
        os.getenv(
            "QUANT_STRATEGY_PACKAGE_ROOT",
            str(store(request).legacy_root.parent / "strategy-packages"),
        )
    )
    return custom or PackageCatalog(root)


def record(request, run_id):
    if not re.fullmatch(r"RUN-(?:COMP|STRAT)-[0-9a-f]{32}", run_id):
        raise _error(404, "DELIVERY_NOT_FOUND", "记录不存在。")
    if not store(request).exists(run_id):
        raise _error(404, "DELIVERY_NOT_FOUND", "记录不存在。")
    try:
        value = store(request).get(run_id)
        for child in value.get("children", []):
            store(request).get(child)
        return value
    except (ValueError, OSError, KeyError, BlobError) as error:
        raise _error(409, "DELIVERY_INTEGRITY_FAILED", "记录或其来源完整性检查失败。") from error


def register(request, payload, prefix, scenario, title):
    repo = store(request)
    run_id = prefix + payload.request_key.hex
    definition = payload.model_dump(mode="json")
    if repo.exists(run_id):
        previous = record(request, run_id)
        if previous["request"] != definition:
            raise _error(409, "DELIVERY_KEY_CONFLICT", "请求标识已绑定其他条件。")
        return previous, False
    if any(not t.done() for t in jobs(request).values()):
        raise _error(409, "LIVE_BUSY", "已有研究或计算正在执行，请等待完成。")
    state = dict(
        run_id=run_id,
        request=definition,
        status="RUNNING",
        created_at=datetime.now(UTC).isoformat(),
        title=title,
        scenario=scenario,
        process=[],
        calls=[],
        children=[],
    )
    repo.put(state)
    return state, True


def schedule(request, state, operation):
    repo = store(request)

    async def bounded():
        try:
            async with asyncio.timeout(1200 if state["scenario"] == "compare_live" else 150):
                result = await operation(request, state)
            state.update(
                result_ref=repo.blob(result),
                status="COMPLETED",
                execution_status=result.get("execution_status"),
                completed_at=datetime.now(UTC).isoformat(),
                error=None,
            )
        except asyncio.CancelledError:
            state.update(status="INTERRUPTED", error="任务已中断；完成的子研究和来源保留，可继续。")
            raise
        except TimeoutError:
            state.update(status="INTERRUPTED", error="任务达到时间预算；可继续或新建任务。")
        except Exception as error:
            state.update(status="FAILED", error=str(error)[:500])
        finally:
            repo.put(state)

    task = asyncio.create_task(bounded())
    jobs(request)[state["run_id"]] = task
    task.add_done_callback(lambda _: jobs(request).pop(state["run_id"], None))


def compare_data(states, requested_at):
    data = [s["data"] for s in states]
    names = [d["subject"]["name"] for d in data]
    rows = []
    for key, label, metric, unit, date_key in [
        ("growth", "营收累计同比", "营收累计同比", "%", "financial"),
        ("profit", "归母净利累计同比", "归母净利累计同比", "%", "financial"),
        ("roe", "累计 / 平均归母权益 ROE", "ROE（累计 / 平均归母权益）", "%", "financial"),
        ("pe", "自算 PE-TTM", "自算 PE-TTM", "倍", "valuation"),
        ("pb", "供应商 PB", "供应商 PB", "倍", "valuation"),
        ("risk", "商誉 / 归母权益", "商誉 / 归母权益", "%", "financial"),
    ]:
        cells = [next((m for m in d["metrics"] if m["name"] == metric), None) for d in data]
        dates = [d["data_dates"].get(date_key) for d in data]
        same_type = data[0]["subject"]["company_type"] == data[1]["subject"]["company_type"]
        comparable = (
            all(
                c is not None and c.get("value") is not None and c.get("unit") == unit
                for c in cells
            )
            and dates[0] == dates[1]
            and bool(dates[0])
        )
        if key in {"growth", "profit", "roe", "risk"}:
            comparable = (
                comparable
                and same_type
                and data[0].get("revenue_basis") == data[1].get("revenue_basis")
            )
        if key in {"pe", "pb"}:
            comparable = (
                comparable
                and same_type
                and data[0]["subject"]["industry"] == data[1]["subject"]["industry"]
            )
        if key == "pe":
            comparable = comparable and data[0]["data_dates"].get("financial") == data[1][
                "data_dates"
            ].get("financial")
        delta = (
            float(Decimal(str(cells[0]["value"])) - Decimal(str(cells[1]["value"])))
            if comparable
            else None
        )
        rows.append(
            dict(
                key=key,
                label=label,
                cells=cells,
                dates=dates,
                unit=unit,
                difference=delta,
                alignment="ALIGNED"
                if comparable
                else "UNAVAILABLE"
                if any(c is None for c in cells)
                else "METHOD_DIFFERENCE",
                note="差值为左减右；可比不代表已经独立核验，也不代表投资排名。"
                if comparable
                else "期间、行业或字段口径不同／缺项，仅并列展示，不计算差值。",
            )
        )
    price_maps = [{p["date"]: p["close"] for p in d["calculation_inputs"]["prices"]} for d in data]
    days = sorted(set(price_maps[0]) & set(price_maps[1]))
    # Require continuous common trading observations; missing interior dates are explicit.
    union = set(price_maps[0]) | set(price_maps[1])
    interior_missing = [d for d in union if days and days[0] <= d <= days[-1] and d not in days]
    chart = dict(
        kind="comparison_price", title="共同窗口价格走势（前复权，起点=1）", x=[], series=[]
    )
    if len(days) >= 2 and not interior_missing:
        chart.update(
            x=days,
            series=[
                dict(
                    name=names[i],
                    data=[float(Decimal(str(m[d])) / Decimal(str(m[days[0]]))) for d in days],
                )
                for i, m in enumerate(price_maps)
            ],
        )
    return dict(
        schema_version="live-comparison-v1",
        requested_at=requested_at,
        subjects=[
            dict(
                run_id=s["run_id"],
                subject=s["data"]["subject"],
                data_dates=s["data"]["data_dates"],
                acquired_at=s["data"]["acquired_at"],
                claims=s["data"].get("agent_report", {}).get("claims", []),
                evidence=s["data"].get("evidence", []),
                sources=s["data"].get("sources", []),
            )
            for s in states
        ],
        rows=rows,
        chart=chart,
        sources=[],
        child_result_refs=[s["result_ref"] for s in states],
        notes=[
            "同一请求时点分别采集；双方报价并非原子同步快照，各模块日期单独展示。",
            "沿用双方真实五角色研究解释；不按指标个数投票，不形成无依据的综合排名。",
            "前复权价格走势是本次查询版本的价格代理，不冒充全收益或严格历史PIT。",
            "共同窗口内部缺少观察时不插值或补零；请查看双方完整研究。",
        ]
        + (
            [f"共同窗口内部缺少 {len(interior_missing)} 个日期，暂不绘制对齐曲线。"]
            if interior_missing
            else []
        ),
    )


async def run_compare(request, state):
    repo = store(request)
    if not state.get("subjects"):
        subjects, proofs = [], []
        for i, query in enumerate(state["request"]["entities"]):
            raw = {}
            subject, meta = await asyncio.to_thread(
                resolve,
                query.strip(),
                lambda key, value, raw=raw: raw.__setitem__(key, repo.raw(value)),
            )
            subjects.append(subject)
            proofs.extend(dict(s, key=f"{i}-{s['key']}", blob_ref=raw[s["key"]]) for s in meta)
        if subjects[0]["code"] == subjects[1]["code"]:
            raise ValueError("两个名称解析为同一证券，请选择不同股票。")
        state.update(subjects=subjects, identity_sources=proofs)
        repo.put(state)
    now = datetime.fromisoformat(state["created_at"]).astimezone(ZoneInfo("Asia/Shanghai"))
    end = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    for i, subject in enumerate(state["subjects"]):
        child_id = "RUN-LIVE-" + uuid5(NAMESPACE_URL, state["run_id"] + ":" + str(i)).hex
        if not repo.exists(child_id):
            repo.put(
                dict(
                    run_id=child_id,
                    request=dict(
                        question="全面分析" + subject["name"],
                        code=subject["code"],
                        parent_run_id=None,
                    ),
                    status="RUNNING",
                    created_at=state["created_at"],
                    title=subject["name"] + " · 比较子研究",
                    scenario="live",
                    kind="comparison_branch",
                    comparison_run_id=state["run_id"],
                    process=process(),
                    calls=[],
                    raw_refs={},
                    window=dict(
                        start=str(now.date() - timedelta(days=850)),
                        end=str(end),
                        asof=str(now.date()),
                    ),
                )
            )
        if child_id not in state["children"]:
            state["children"].append(child_id)
            repo.put(state)
        child = repo.get(child_id, with_data=False)
        if child["status"] != "COMPLETED":
            await ResearchWorkflow(
                repo,
                request.app.state.settings,
                getattr(request.app.state, "research_model_port", None),
            ).run(child_id)
        child = repo.get(child_id)
        if child["status"] != "COMPLETED":
            raise ValueError(
                subject["name"] + "研究未完成：" + (child.get("error") or child["status"])
            )
    result = compare_data([repo.get(c) for c in state["children"]], state["created_at"])
    result["sources"] = state["identity_sources"]
    return result


async def compute_package(args):
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "marketpulse.quant.backtest.strategy_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            **{
                k: v
                for k, v in os.environ.items()
                if k.upper()
                in {
                    "SYSTEMROOT",
                    "WINDIR",
                    "PATH",
                    "TEMP",
                    "TMP",
                    "PYTHONPATH",
                    "PROCESSOR_ARCHITECTURE",
                    "PROCESSOR_ARCHITEW6432",
                }
            },
            "PYTHONIOENCODING": "utf-8",
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
        },
    )
    try:
        async with asyncio.timeout(120):
            child.stdin.write(canonical(args).encode())
            await child.stdin.drain()
            child.stdin.close()
            output, error = await asyncio.gather(
                bounded_read(child.stdout, 128 * 1024 * 1024),
                bounded_read(child.stderr, 65536),
            )
            await child.wait()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    if child.returncode != 0:
        raise ValueError("隔离计算拒绝执行：" + error.decode("utf-8", errors="replace")[-700:])
    if len(output) > 128 * 1024 * 1024:
        raise ValueError("计算输出超过128MiB预算")
    return json.loads(output)


async def run_strategy(request, state):
    repo, inputs = store(request), state["request"]
    key = inputs["package_id"]
    result = dict(
        schema_version="backtest-strategy-v1",
        sources=[],
        execution_status="BLOCKED",
        metric_status="UNVERIFIED",
        check_status="COMPLETED",
        conditions=inputs,
        meta=dict(question="年度选股策略回测", universe="历史包未接入"),
        admission=dict(
            status="BLOCKED",
            gaps=[dict(domain="package", reason="尚未安装经过独立审核的完整历史数据包")],
        ),
        notes=[
            "历史数据包尚未接入；未生成选股、成交和收益。全市场档案可后续接入，不使用假数据填图。"
        ],
    )
    if key:
        cat = catalog(request)
        package, reviews = await asyncio.to_thread(cat.read, key)
        references = await asyncio.to_thread(cat.references, key)
        identity = dict(
            package=digest(package.model_dump(mode="json")),
            reviews=digest([r.model_dump(mode="json") for r in reviews]),
            references=digest([r.model_dump(mode="json") for r in references]),
        )
        if state.get("input_identity", identity) != identity:
            raise ValueError("已安装输入发生变化；请新建计算版本，不混合恢复。")
        state["input_identity"] = identity
        repo.put(state)
        args = dict(
            root=str(cat.root),
            package_id=key,
            conditions={k: inputs[k] for k in ("pe_max", "roe_min", "rev_yoy_min")},
            initial_cash=inputs["initial_cash"],
            risk_free_annual=str(Decimal(inputs["risk_free_annual_pct"]) / 100),
            cost_pct_per_side=inputs["cost_pct_per_side"],
        )
        result = await compute_package(args)
        if result["admission"]["package_hash"] != identity["package"]:
            raise ValueError("计算期间数据包变化")
        if result["admission"]["review_hash"] != digest(
            sorted([r.model_dump(mode="json") for r in reviews], key=digest)
        ):
            raise ValueError("计算期间审核登记变化")
        result["computation_ref"] = repo.blob(result)
        if result.get("reference_hash", identity["references"]) != identity["references"]:
            raise ValueError("计算期间独立参考变化")
        sources = []
        # Freeze consulted bytes; replay must not depend on a future mutable catalog.
        hashes = {
            e["sha256"] for e in result["admission"]["evidence_files"] if e["status"] == "AVAILABLE"
        }
        hashes.update(p.rows_hash for p in package.daily)
        hashes.update(p.events_hash for p in package.actions)
        hashes.update(r.source_hash for r in references)
        hashes.update(r.review.reference_hash for r in references)
        total = 0
        for sha in sorted(hashes):
            raw = cat.resolve(key, sha)
            if raw is not None:
                total += len(raw)
                if total > 256 * 1024 * 1024:
                    raise ValueError("冻结输入超过256MiB任务预算")
                sources.append(dict(key=sha, sha256=sha, blob_ref=repo.raw(raw)))
        result["sources"] = sources
        result["package_ref"] = repo.blob(package.model_dump(mode="json"))
        result["reviews_ref"] = repo.blob([r.model_dump(mode="json") for r in reviews])
        result["references_ref"] = repo.blob([r.model_dump(mode="json") for r in references])
        result["conditions"] = inputs
    result.update(run_id=state["run_id"], checked_at=datetime.now(UTC).isoformat())
    result.setdefault("meta", {})["task_id"] = state["run_id"]
    return result


@router.get("/strategies/packages")
async def packages(request: Request):
    return await asyncio.to_thread(catalog(request).list)


@router.get("/strategies/schema")
async def package_schema():
    return dict(
        schema_version="strategy-adapter-contract-v1",
        package=DataPackage.model_json_schema(),
        server_owned_reviews=Review.model_json_schema(),
        independent_results=ResultReference.model_json_schema(),
        layout=[
            "<package_id>/package.json",
            "<package_id>/reviews.json",
            "<package_id>/objects/<sha256>",
            "<package_id>/references.json (可选，独立绩效参考；不是同引擎复算)",
        ],
        warning="审核凭证由运营端独立核对后登记，不接受公开API自报MATCH/STRICT；原始来源必须可读取并验哈希。",
    )


@router.get("/research/comparisons/schema")
async def comparison_schema():
    return dict(
        schema_version="comparison-tool-adapter-v1",
        optional_metrics=ComparisonMetric.model_json_schema(),
        slots={"profitability": "TTM_PARENT_ROE", "quality": "YTD_PARENT_CASH_RATIO"},
        note="由确定性数据工具写入子研究comparison_metrics；来源key必须存在于该研究sources，单位、期间、公司类型与定义对齐后才比较。数据未接入时保留缺项，不能用累计ROE替代。",
    )


@router.get("/delivery")
async def delivery(request: Request):
    return dict(
        features=[
            dict(
                key="research",
                title="最新单股研究",
                software="已接通",
                href="#/live/new",
                detail="动态沪深证券身份、五角色模型、确定性财务与DCF、K线、数值依据、条件情景与风险。实际覆盖按证券显示。",
            ),
            dict(
                key="compare",
                title="最新双股比较",
                software="已接通",
                href="#/compare",
                detail="双方新采集、共同请求时点、可比口径检查、对齐价格走势、独立研究与失败恢复。",
            ),
            dict(
                key="versions",
                title="追问、情景、历史与导出",
                software="已接通",
                href="#/library",
                detail="参数新版本、显式父记忆、内容寻址来源及不可改写的完成报告。",
            ),
            dict(
                key="history",
                title="历史输入采集与准入",
                software="已接通",
                href="#/backtest",
                detail="有限批次采集、覆盖账本、历史财报版本、公司行为、成交限制、官方基准及独立来源审核。",
            ),
            dict(
                key="strategy",
                title="完整策略执行链",
                software="已接通，等待合格数据包",
                href="#/strategy",
                detail="年度筛选→原始价格成交→分红与股份结算→三成本独立重算→五项绩效／年度持仓／逐笔账本。未准入不计算收益。",
            ),
        ],
        data_slots=[
            dict(
                key="revisions",
                title="历史财报修订链",
                status="待补齐",
                requires="原始披露和修订版本、准确可得时间、归母口径／股本／权益、原文及独立核对。",
                effect=(
                    "映射 DataPackage.financial_sources / annual.candidates；缺少时阻止历史选股。"
                ),
            ),
            dict(
                key="actions",
                title="完整公司行为档案",
                status="待补齐",
                requires="分红、送转、配股、退市条款与登记／生效／结算日，包含无事件区间的完整性依据。",
                effect=(
                    "映射 DataPackage.actions；现金应收和待上市股份单独记账。"
                    "碎股、特殊合并与税务条款不完整时拒绝计算，扩展条款后接入。"
                ),
            ),
            dict(
                key="execution",
                title="历史股票池与成交限制",
                status="待补齐",
                requires="包含退市的历史成员、有效规则、逐日停牌／方向限制／执行时点证据。",
                effect="映射 securities / annual.members / daily；未知不能视为可成交。",
            ),
            dict(
                key="total_return",
                title="全收益独立核对",
                status="待补齐",
                requires="H00300和策略收益的同口径独立参考、再投资与费用定义、预设容差。",
                effect="价格基准与全收益分别存储；输入准入不把策略指标自动升级VERIFIED。",
            ),
            dict(
                key="business",
                title="业务拆分、同行与行业证据",
                status="预留",
                requires="分部收入利润、同行统一口径、行业需求及有日期的原披露材料。",
                effect="接入研究工具与证据池后才允许归因；现有报告明确保留这些判断缺口。",
            ),
        ],
        packages=await asyncio.to_thread(catalog(request).list),
        reliability=[
            "证券身份由来源确认；模型只解释证据，数值由Python计算，Verifier与输出数值门禁分别工作。",
            "任务UUID幂等、请求冲突拒绝、原文哈希检查、版本不可改写、重启中断与显式恢复。",
            "回测计算使用固定子进程、禁网、内存／时间／输入输出预算，未准入保持BLOCKED。",
            "手算、原披露／独立参考交叉核对、同输入复算分别记录；测试通过不等于真实全市场档案齐全。",
            "不包含实盘下单或无依据的涨跌概率；量化服务目前面向本地单操作者，公网多人部署仍需单独加固。",
        ],
    )


@router.post("/strategies", status_code=202)
async def strategy(payload: StrategyIn, request: Request):
    if payload.package_id and not catalog(request).directory(payload.package_id).is_dir():
        raise _error(422, "PACKAGE_NOT_FOUND", "服务端尚未安装该数据包。")
    state, fresh = register(request, payload, "RUN-STRAT-", "strategy", "年度选股策略 · 完整执行")
    if fresh:
        schedule(request, state, run_strategy)
    return state


@router.post("/research/comparisons", status_code=202)
async def compare(payload: CompareIn, request: Request):
    if not request.app.state.settings.deepseek_api_key and not getattr(
        request.app.state, "research_model_port", None
    ):
        raise _error(503, "MODEL_UNAVAILABLE", "尚未配置模型。")
    state, fresh = register(
        request,
        payload,
        "RUN-COMP-",
        "compare_live",
        " vs ".join(payload.entities) + " · 最新数据比较",
    )
    if fresh:
        schedule(request, state, run_compare)
    return state


@router.get("/deliveries/{run_id}")
async def get_delivery(run_id: str, request: Request):
    return await asyncio.to_thread(delivery_view, request, run_id)


def delivery_view(request, run_id):
    state = record(request, run_id)
    children = [store(request).get(child) for child in state.get("children", [])]
    state["branches"] = [
        {k: c.get(k) for k in ("run_id", "status", "subject", "process", "error")} for c in children
    ]
    if state["scenario"] == "compare_live" and state["status"] == "COMPLETED":
        state["presentation"] = comparison_presentation(children)
    return state


@router.post("/deliveries/{run_id}/resume", status_code=202)
async def resume_delivery(run_id: str, request: Request):
    state = record(request, run_id)
    if state["status"] not in {"FAILED", "INTERRUPTED"}:
        raise _error(409, "RESUME_UNAVAILABLE", "此任务不需要恢复。")
    if any(not t.done() for t in jobs(request).values()):
        raise _error(409, "LIVE_BUSY", "已有任务正在执行。")
    state.update(status="RUNNING", error=None)
    state.pop("data", None)
    store(request).put(state)
    schedule(request, state, run_compare if state["scenario"] == "compare_live" else run_strategy)
    return state


@router.get("/deliveries/{run_id}/export", response_class=PlainTextResponse)
async def export_delivery(run_id: str, request: Request):
    return canonical(await asyncio.to_thread(delivery_view, request, run_id))


@router.post("/deliveries/{run_id}/reproduce")
async def reproduce_delivery(run_id: str, request: Request):
    state = await asyncio.to_thread(record, request, run_id)
    data = state.get("data", {})
    if state["scenario"] != "strategy" or data.get("execution_status") != "COMPLETED":
        raise _error(409, "REPRODUCTION_UNAVAILABLE", "仅已完成计算且输入完整的策略可离线复算。")
    if any(not t.done() for t in jobs(request).values()):
        raise _error(409, "LIVE_BUSY", "已有任务执行中。")
    repo = store(request)
    inputs = state["request"]
    args = dict(
        blob_root=str(repo.blobs._root),
        package_ref=data["package_ref"],
        reviews_ref=data["reviews_ref"],
        references_ref=data["references_ref"],
        conditions={k: inputs[k] for k in ("pe_max", "roe_min", "rev_yoy_min")},
        initial_cash=inputs["initial_cash"],
        risk_free_annual=str(Decimal(inputs["risk_free_annual_pct"]) / 100),
        cost_pct_per_side=inputs["cost_pct_per_side"],
    )
    task = asyncio.create_task(compute_package(args))
    jobs(request)[run_id + "-reproduction"] = task
    try:
        actual = await task
    finally:
        jobs(request).pop(run_id + "-reproduction", None)
    original = repo.value(data["computation_ref"])
    proof = dict(
        status="MATCH" if actual == original else "CONFLICT",
        run_id=run_id,
        expected_output_hash=original.get("output_hash"),
        actual_output_hash=actual.get("output_hash"),
        checked_at=datetime.now(UTC).isoformat(),
        note="冻结原始输入离线重跑；复算一致不替代策略指标的外部独立核验。",
    )
    proof["proof_ref"] = repo.blob(proof)
    return proof
