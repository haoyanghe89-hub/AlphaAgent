"""Persistent backtest input checks. Numerical backtests remain gated on data."""

import asyncio
import calendar
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from pydantic import Field
from sqlalchemy import text

from marketpulse.infrastructure.storage.models import BlobError, BlobRef
from marketpulse.investigation.api import _error
from marketpulse.investigation.domain.enums import AuditActorType, RunMode, RunStatus, WorkflowPhase
from marketpulse.investigation.domain.runtime import (
    Investigation,
    InvestigationQuestion,
    InvestigationRun,
    InvestigationScope,
)
from marketpulse.investigation.persistence.models import AuditEventRow, InvestigationRunRow

from ..contracts import canonical, digest
from ..domain import ExactDecimal, FrozenModel
from ..storage.models import SnapshotOwnershipRow
from .acquisition import WORKFLOW as HISTORY_WORKFLOW
from .acquisition import acquire, input_target, prepare, read_history
from .benchmark import benchmark_target
from .filings import filing_target
from .lifecycles import lifecycle_target
from .market import router as market_router
from .notices import notice_target
from .readiness import inspect

router = APIRouter()
router.include_router(market_router)
WORKFLOW = "quant-backtest-readiness-v1"
SCHEMA = "backtest-input-check-v1"


def summary_field(summary):
    return {
        "backtest-input-audit-v1": "input_audit",
        "backtest-filing-audit-v1": "filing_audit",
        "backtest-benchmark-audit-v1": "benchmark_audit",
        "backtest-lifecycle-audit-v1": "lifecycle_audit",
        "backtest-notice-audit-v1": "notice_audit",
    }.get(summary.get("schema_version"), "historical_data")


class BacktestIn(FrozenModel):
    request_key: UUID
    pe_max: ExactDecimal | None = Field(default=Decimal("30"), gt=0, le=10000)
    roe_min: ExactDecimal | None = Field(default=Decimal("15"), ge=-100, le=1000)
    rev_yoy_min: ExactDecimal | None = Field(default=None, ge=-100, le=10000)
    years: int = Field(default=5, ge=3, le=8)
    cost_pct_per_side: ExactDecimal = Field(default=Decimal("0.13"), ge=0, le=1)


class HistoryIn(FrozenModel):
    request_key: UUID


class InputsIn(HistoryIn):
    instrument_id: str = Field(pattern=r"^ins_[a-zA-Z0-9_-]{8,}$")
    signal_year: int = Field(ge=1990, le=2100)


def saved_check_status(runtime, session, run):
    """List the saved check outcome; full source-chain validation remains on read."""
    event = session.get(AuditEventRow, "QBT-" + run.run_id)
    if not event:
        return run.status.value
    try:
        metadata = event.metadata_payload
        payload = metadata["payload"]
        ref = BlobRef(digest(payload))
        if (
            payload["schema_version"] != SCHEMA
            or payload["run_id"] != run.run_id
            or metadata["sha256"] != ref.sha256
            or metadata.get("blob_ref", ref.uri) != ref.uri
        ):
            raise ValueError("saved check drift")
        runtime.service.store.blobs.verify_hash(ref)
        return payload["check_status"]
    except (ValueError, KeyError, OSError, TypeError, BlobError):
        return "INTEGRITY_FAILED"


def create_check(runtime, payload):
    conditions = payload.model_dump(mode="json", exclude={"request_key"})
    request_hash = digest(conditions)
    request_id = "QBT-REQ-" + str(payload.request_key)
    now = datetime.now(UTC)
    end = now.astimezone(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    start = end.replace(
        year=end.year - payload.years,
        day=min(end.day, calendar.monthrange(end.year - payload.years, end.month)[1]),
    )
    store = runtime.service.store
    with store.sessions.begin() as session:
        if session.bind.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        previous = session.get(AuditEventRow, request_id)
        if previous:
            if previous.metadata_payload["request_hash"] != request_hash:
                raise _error(409, "BACKTEST_RETRY_CONFLICT", "重试标识已绑定其他条件，请重新提交。")
            # Retry is safe even after an application restart; original integrity
            # is checked by the read path and never silently regenerated.
            return dict(
                run_id=previous.run_id,
                investigation_id=previous.investigation_id,
                status="FAILED",
                execution_status="BLOCKED",
            )
        data_check = inspect(session, store, start, end)
        run_id, archive_id = "RUN-BT-" + uuid4().hex[:16], "INV-" + uuid4().hex
        question = f"策略回测近 {payload.years} 年 vs 沪深300 · 回测数据检查"
        investigation = Investigation(
            investigation_id=archive_id,
            title=question,
            event_description="策略条件与历史输入检查",
            investigation_goal="输入准入通过后才能执行回测；不生成缺乏依据的收益。",
            scope=InvestigationScope(summary="历史股票池、财报可得版本与交易数据检查"),
            questions=(InvestigationQuestion(question_id="Q-" + uuid4().hex, text=question),),
            created_at=now,
            updated_at=now,
        )
        runtime.runner.repository.add_in_session(session, investigation)
        runtime.runner.repository.add_in_session(
            session,
            InvestigationRun(
                run_id=run_id,
                investigation_id=archive_id,
                mode=RunMode.LIVE,
                status=RunStatus.FAILED,
                current_phase=WorkflowPhase.REPORT,
                checkpoint_version=0,
                state_version=1,
                workflow_version=WORKFLOW,
                created_at=now,
                started_at=now,
                updated_at=now,
                completed_at=now,
                interruption_reason="回测输入尚未准入；数据检查报告已保存。",
            ),
        )
        report = dict(
            schema_version=SCHEMA,
            run_id=run_id,
            investigation_id=archive_id,
            check_status="COMPLETED",
            execution_status="BLOCKED",
            metric_status="UNVERIFIED",
            checked_at=now.isoformat(),
            conditions=conditions,
            meta=dict(
                question=question,
                period=f"{start} ~ {end}",
                date_start=start.isoformat(),
                date_end=end.isoformat(),
                task_id=run_id,
                universe="历史股票池尚未准入",
            ),
            data_check=data_check,
            process=[
                dict(role="Planner", status="COMPLETED", detail="已冻结用户条件、期间与成本假设。"),
                dict(
                    role="Data", status="COMPLETED", detail="已检查现有真实快照，记录所有准入缺项。"
                ),
                dict(
                    role="Analyst",
                    status="NOT_EXECUTED",
                    detail="数据未通过准入，未执行选股、成交或绩效计算。",
                ),
                dict(role="Verifier", status="BLOCKED", detail="回测指标和独立基准核对尚未执行。"),
                dict(
                    role="Writer",
                    status="COMPLETED",
                    detail="数据检查报告已持久化；不是成功回测报告。",
                ),
            ],
            notes=[
                "历史回测存在样本选择与过拟合风险；当前没有生成任何收益结果。",
                "每年5月首个交易日为拟议调仓日，选股只可使用执行前已知的历史版本。",
                "成本为用户指定的单侧综合情景假设，不代表历史实际税费与成交滑点。",
                "3–8年数据须按现有三年单次请求边界分段冻结并检查完整性。",
                "价格收益与全收益基准须明确区分；无风险利率缺失时不生成 Sharpe。",
            ],
            disclaimer="本工具为投研参考，不构成投资建议。历史回测不代表未来收益。",
        )
        stored = store.blobs.put_bytes(canonical(report).encode())
        for event_id, event_type, metadata in (
            (request_id, "BACKTEST_REQUEST", {"request_hash": request_hash}),
            (
                "QPLAN-" + run_id,
                "QUANT_FROZEN_PLAN",
                {**conditions, "question": question, "scenario": "backtest"},
            ),
            (
                "QBT-" + run_id,
                "BACKTEST_INPUT_CHECK",
                {"payload": report, "sha256": stored.ref.sha256, "blob_ref": stored.ref.uri},
            ),
        ):
            session.add(
                AuditEventRow(
                    audit_event_id=event_id,
                    investigation_id=archive_id,
                    run_id=run_id,
                    actor_type=AuditActorType.SYSTEM,
                    event_type=event_type,
                    target_type="Run",
                    target_id=run_id,
                    metadata_payload=metadata,
                    created_at=now,
                )
            )
        for snapshot in data_check["snapshots"]:
            session.add(
                SnapshotOwnershipRow(
                    investigation_id=archive_id,
                    snapshot_id=snapshot["snapshot_id"],
                    rights_scope=store.load(snapshot["snapshot_id"]).result.request.scope.value,
                )
            )
    return dict(
        run_id=run_id, investigation_id=archive_id, status="FAILED", execution_status="BLOCKED"
    )


def load_check(runtime, run_id, *, session=None, _snapshot_cache=None):
    store = runtime.service.store
    # Reuse source validation only inside this one parent-chain read. A later
    # HTTP request starts afresh, so file tampering can never hide in a cache.
    if _snapshot_cache is None:
        _snapshot_cache = {}
    # A parent chain is one read transaction. Nested checkouts can deadlock
    # the pool when users open a child while the parent request is finishing.
    with nullcontext(session) if session is not None else store.sessions() as session:
        run = session.get(InvestigationRunRow, run_id)
        event = session.get(AuditEventRow, "QBT-" + run_id)
        plan = session.get(AuditEventRow, "QPLAN-" + run_id)
        if not run or run.workflow_version not in {WORKFLOW, HISTORY_WORKFLOW}:
            raise _error(404, "BACKTEST_NOT_FOUND", "回测检查任务不存在。")
        if run.workflow_version == HISTORY_WORKFLOW:
            try:
                template, history, acquisition = read_history(
                    runtime, run, session=session, _snapshot_cache=_snapshot_cache
                )
            except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
                raise _error(
                    409, "BACKTEST_CHECK_INTEGRITY", "历史源或父报告完整性失败，已停止展示。"
                ) from error
            if not event:
                import copy

                payload = copy.deepcopy(template)
                task = runtime.runner.tasks.get(run_id)
                payload["check_status"] = "RUNNING" if task and not task.done() else "INTERRUPTED"
                if payload["check_status"] == "INTERRUPTED" and run.status == RunStatus.RUNNING:
                    run.status = RunStatus.INTERRUPTED
                    run.updated_at = datetime.now(UTC)
                    run.state_version += 1
                    run.interruption_reason = (
                        "历史采集执行器已丢失；保留冻结输入，请从父报告重新获取。"
                    )
                    session.commit()
                payload[summary_field(history)] = history
                payload["acquisition"] = acquisition
                payload["process"][1].update(
                    status=payload["check_status"],
                    detail=f"已冻结 {acquisition['sources_frozen']} 份真实响应。",
                )
                return payload
        elif not event:
            raise _error(409, "BACKTEST_CHECK_INTEGRITY", "检查报告缺失。")
        payload = event.metadata_payload["payload"]
        try:
            payload_hash = digest(payload)
            payload_ref = BlobRef(payload_hash)
            if (
                payload.get("schema_version") != SCHEMA
                or payload_hash != event.metadata_payload["sha256"]
            ):
                raise ValueError("check payload drift")
            store.blobs.verify_hash(payload_ref)
            if event.metadata_payload.get("blob_ref", payload_ref.uri) != payload_ref.uri:
                raise ValueError("blob locator mismatch")
            frozen_conditions = {k: plan.metadata_payload[k] for k in payload["conditions"]}
            if frozen_conditions != payload["conditions"] or payload["run_id"] != run_id:
                raise ValueError("plan/result mismatch")
            if (
                run.workflow_version == HISTORY_WORKFLOW
                and payload[summary_field(history)] != history
            ):
                raise ValueError("history summary cannot reproduce from frozen packets")
            for s in payload["data_check"]["snapshots"]:
                snapshot_id = s["snapshot_id"]
                original = _snapshot_cache.get(snapshot_id)
                if original is None:
                    original = store.load(snapshot_id, session=session)
                    _snapshot_cache[snapshot_id] = original
                if original.semantic_hash != s["semantic_hash"] or not session.get(
                    SnapshotOwnershipRow, (run.investigation_id, s["snapshot_id"])
                ):
                    raise ValueError("source integrity/ownership drift")
        except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
            raise _error(
                409, "BACKTEST_CHECK_INTEGRITY", "检查记录或源快照完整性失败，已停止展示。"
            ) from error
        return payload


async def start_acquisition(
    runtime,
    run_id,
    request_key,
    target=None,
    filing=None,
    benchmark=None,
    lifecycle=None,
    notice=None,
):
    child_id, created = await asyncio.to_thread(
        prepare,
        runtime,
        run_id,
        request_key,
        input_target=target,
        filing_target=filing,
        benchmark_target=benchmark,
        lifecycle_target=lifecycle,
        notice_target=notice,
    )
    if created:
        task = asyncio.create_task(acquire(runtime, child_id))
        runtime.runner.tasks[child_id] = task

        def cleanup(done):
            if runtime.runner.tasks.get(child_id) is done:
                runtime.runner.tasks.pop(child_id, None)

        task.add_done_callback(cleanup)
        # prepare already validated and froze the parent. Re-reading the whole
        # child chain here repeats that expensive work before returning 202.
        # A retry still validates the existing child's inputs, including raw PDFs.
        with runtime.service.sessions() as session:
            investigation_id = session.get(InvestigationRunRow, child_id).investigation_id
    else:
        existing = await asyncio.to_thread(load_check, runtime, child_id)
        investigation_id = existing["investigation_id"]
    return dict(
        run_id=child_id,
        investigation_id=investigation_id,
        parent_run_id=run_id,
    )


@router.post("/backtests/{run_id}/history", status_code=202)
async def submit_history(run_id: str, payload: HistoryIn, request: Request):
    from ..api import _runtime

    return await start_acquisition(_runtime(request), run_id, payload.request_key)


@router.post("/backtests/{run_id}/inputs", status_code=202)
async def submit_inputs(run_id: str, payload: InputsIn, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    try:
        target = await asyncio.to_thread(
            input_target, runtime, run_id, payload.instrument_id, payload.signal_year
        )
        return await start_acquisition(runtime, run_id, payload.request_key, target)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
        raise _error(
            409, "INPUT_SOURCE_INTEGRITY", "证券生命周期或父来源完整性未通过，已停止取数。"
        ) from error


@router.post("/backtests/{run_id}/filings", status_code=202)
async def submit_filings(run_id: str, payload: HistoryIn, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    try:
        target = await asyncio.to_thread(filing_target, runtime, run_id)
        return await start_acquisition(runtime, run_id, payload.request_key, filing=target)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
        raise _error(
            409, "FILING_SOURCE_INTEGRITY", "父版本或官方原文配置完整性失败，已停止取证。"
        ) from error


@router.post("/backtests/{run_id}/benchmark", status_code=202)
async def submit_benchmark(run_id: str, payload: HistoryIn, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    try:
        target = await asyncio.to_thread(benchmark_target, runtime, run_id)
        return await start_acquisition(runtime, run_id, payload.request_key, benchmark=target)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
        raise _error(
            409, "BENCHMARK_SOURCE_INTEGRITY", "父版本或官方基准来源完整性失败，已停止取证。"
        ) from error


@router.post("/backtests/{run_id}/lifecycles", status_code=202)
async def submit_lifecycles(run_id: str, payload: HistoryIn, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    try:
        target = await asyncio.to_thread(lifecycle_target, runtime, run_id)
        return await start_acquisition(runtime, run_id, payload.request_key, lifecycle=target)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
        raise _error(
            409, "LIFECYCLE_SOURCE_INTEGRITY", "父股票池或官方目录完整性失败，已停止核对。"
        ) from error


@router.post("/backtests/{run_id}/stop")
async def stop_history(run_id: str, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    with runtime.service.sessions() as session:
        run = session.get(InvestigationRunRow, run_id)
        if not run or run.workflow_version != HISTORY_WORKFLOW:
            raise _error(404, "HISTORY_NOT_FOUND", "历史采集任务不存在。")
    task = runtime.runner.tasks.get(run_id)
    if task and not task.done():
        task.cancel()
        await task
    return load_check(runtime, run_id)


@router.post("/backtests/{run_id}/notices", status_code=202)
async def submit_notices(run_id: str, payload: HistoryIn, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    try:
        target = await asyncio.to_thread(notice_target, runtime, run_id)
        return await start_acquisition(runtime, run_id, payload.request_key, notice=target)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, BlobError) as error:
        raise _error(
            409, "NOTICE_SOURCE_INTEGRITY", "父档案或官方公告配置完整性失败，已停止取证。"
        ) from error


@router.post("/backtests", status_code=201)
async def submit_check(payload: BacktestIn, request: Request):
    from ..api import _runtime

    try:
        return await asyncio.to_thread(create_check, _runtime(request), payload)
    except (ValueError, KeyError, OSError, BlobError) as error:
        raise _error(
            409, "BACKTEST_SOURCE_INTEGRITY", "源快照完整性检查失败，未创建回测结果。"
        ) from error


@router.get("/backtests/{run_id}")
def read_check(run_id: str, request: Request):
    from ..api import _runtime

    return load_check(_runtime(request), run_id)


@router.get("/backtests/{run_id}/export")
def export_check(run_id: str, request: Request):
    from ..api import _runtime

    payload = load_check(_runtime(request), run_id)
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="backtest-input-check.json"'},
    )
