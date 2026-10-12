"""Additive local-operator API; never exports raw provider datasets."""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request, Response
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import select, update

from marketpulse.investigation.api import (
    InvestigationCreateIn,
    _error,
    _get_run_row,
    _run_out,
    _sessions,
    create_investigation,
)
from marketpulse.investigation.depth import depth_settings
from marketpulse.investigation.domain.enums import RunMode, RunStatus, WorkflowPhase
from marketpulse.investigation.domain.runtime import InvestigationRun, RunBudget
from marketpulse.investigation.persistence.models import (
    AuditEventRow,
    InvestigationRow,
    InvestigationRunRow,
    ReportRow,
)

from .domain import DcfAssumptions, FrozenModel, Instrument
from .storage.models import InstrumentRow

router = APIRouter(prefix="/api/quant", tags=["quant"])
DEPENDENCIES = ("numpy", "pandas", "pyarrow", "duckdb", "akshare", "baostock", "exchange_calendars")


def missing_dependencies():
    return [name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]


class QuantRunIn(FrozenModel):
    instrument: str = Field(min_length=1, max_length=100)
    question: str = Field(default="", max_length=2000)
    date_start: date | None = None
    date_end: date | None = None
    window: Literal["1y", "3y"] = "1y"
    adjustment: Literal["raw", "qfq", "hfq"] = "raw"
    depth: Literal["quick", "standard", "deep"] = "standard"
    asof: AwareDatetime | None = None
    benchmark: str | None = None
    rf: str | Literal["zero"] | None = None
    frozen_snapshot_ids: tuple[str, ...] = Field(default=(), max_length=20)
    dcf: DcfAssumptions = Field(default_factory=DcfAssumptions)

    @model_validator(mode="after")
    def bounds(self):
        if (self.date_start is None) != (self.date_end is None):
            raise ValueError("date_start/date_end must be supplied together")
        if self.date_start and (
            self.date_end < self.date_start or (self.date_end - self.date_start).days > 1096
        ):
            raise ValueError("ordered range <=3 years required")
        return self


class QuantFollowupIn(FrozenModel):
    question: str = Field(min_length=1, max_length=2000)
    request_key: UUID
    dcf: DcfAssumptions | None = None


class QuantComparisonIn(FrozenModel):
    question: str = Field(min_length=1, max_length=2000)
    subjects: tuple[QuantRunIn, QuantRunIn]

    @model_validator(mode="after")
    def uniform(self):
        left, right = self.subjects
        if left.instrument == right.instrument:
            raise ValueError("two distinct instruments required")
        for plan in self.subjects:
            if not plan.frozen_snapshot_ids or not plan.date_start or not plan.asof:
                raise ValueError("comparison requires explicit frozen inputs and window")
            if (
                plan.asof > datetime.now(UTC)
                or plan.date_end >= plan.asof.astimezone(ZoneInfo("Asia/Shanghai")).date()
            ):
                raise ValueError("complete historical sessions required")
            if plan.benchmark or plan.rf:
                raise ValueError("comparison reference inputs unavailable")
        if any(
            getattr(left, field) != getattr(right, field)
            for field in ("date_start", "date_end", "asof", "adjustment", "depth")
        ):
            raise ValueError("comparison window/asof/basis/depth must match")
        return self


def _runtime(request):
    runtime = getattr(request.app.state, "quant_runtime", None)
    if runtime is None:
        raise _error(
            503,
            "QUANT_NOT_CONFIGURED",
            "量化依赖不可用；网页调查仍可使用。请安装 quant extra 并重启。",
        )
    return runtime


def instruments(sessions, q="", limit=20):
    with sessions() as session:
        definitions = session.scalars(
            select(InstrumentRow.definition).order_by(InstrumentRow.instrument_id)
        ).all()
    result = []
    for definition in definitions:
        item = Instrument.model_validate(definition)
        alias = item.aliases[-1]
        if q.casefold() not in (item.name + alias.code + item.instrument_id).casefold():
            continue
        result.append(
            dict(
                instrument_id=item.instrument_id,
                code=alias.code,
                name=item.name,
                market=item.market,
                exchange=alias.exchange,
                board=item.board,
                status="delisted" if item.delisted_at else "listed",
            )
        )
        if len(result) >= limit:
            break
    return result


@router.get("/instruments")
def search_instruments(
    request: Request, q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=100)
):
    _runtime(request)
    return instruments(_sessions(request), q, limit)


@router.post("/runs", status_code=202)
async def start_run(payload: QuantRunIn, request: Request):
    runtime = _runtime(request)
    matches = [
        i
        for i in await asyncio.to_thread(
            instruments, runtime.service.sessions, payload.instrument, 100
        )
        if payload.instrument in (i["instrument_id"], i["code"])
    ]
    if len(matches) != 1:
        raise _error(
            422,
            "QUANT_INSTRUMENT_UNKNOWN",
            "标的未主数据化或代码有歧义，请从证券搜索选择 ins_ ID。",
        )
    asof = payload.asof or datetime.now(UTC)
    if asof > datetime.now(UTC):
        raise _error(422, "QUANT_FUTURE_ASOF", "研究时点不能在未来。")
    cutoff = asof.astimezone(ZoneInfo("Asia/Shanghai")).date()
    end = payload.date_end or cutoff - timedelta(days=1)
    start = payload.date_start or end - timedelta(days=365 if payload.window == "1y" else 1095)
    if end >= cutoff:
        raise _error(
            422,
            "QUANT_INCOMPLETE_SESSION",
            "P0 仅使用已完整收盘的历史交易日；结束日须早于研究时点日期。",
        )
    if payload.benchmark or (payload.rf and payload.rf != "zero"):
        raise _error(
            422,
            "QUANT_REFERENCE_UNAVAILABLE",
            "P0 尚无标准化基准/利率快照契约；不能用代码或文本代替冻结输入。",
        )
    plan = payload.model_copy(
        update={
            "instrument": matches[0]["instrument_id"],
            "asof": asof,
            "date_start": start,
            "date_end": end,
        }
    )
    try:
        if plan.frozen_snapshot_ids:
            await asyncio.to_thread(runtime.validate_pins, plan)
    except (ValueError, KeyError) as error:
        raise _error(
            422, "QUANT_FROZEN_INPUT_INVALID", "冻结输入不匹配、存在歧义或完整性校验失败。"
        ) from error
    archive = await asyncio.to_thread(
        create_investigation,
        InvestigationCreateIn(
            title=f"{matches[0]['name']} · 量化投研",
            event_description=f"冻结区间 {start} 至 {end}",
            investigation_goal="核对收益、风险与同口径估值；不做投资建议或预测。",
            questions=[plan.question.strip() or "冻结数据可确认什么，哪些结论仍未验证？"],
            depth=plan.depth,
        ),
        request,
    )
    run_id = runtime.start(archive.investigation_id, plan)
    return dict(run_id=run_id, investigation_id=archive.investigation_id, status="CREATED")


@router.get("/instruments/{instrument_id}/windows")
def frozen_windows(instrument_id: str, request: Request):
    from .windows import available_windows

    runtime = _runtime(request)
    return available_windows(runtime.service, instrument_id)


@router.post("/comparisons", status_code=202)
async def start_comparison(payload: QuantComparisonIn, request: Request):
    runtime = _runtime(request)
    subjects, branches = [], []
    try:
        for plan in payload.subjects:
            matches = instruments(runtime.service.sessions, plan.instrument, 100)
            matches = [i for i in matches if plan.instrument == i["instrument_id"]]
            if len(matches) != 1:
                raise ValueError("comparison instrument not mastered")
            await asyncio.to_thread(runtime.validate_pins, plan)
            subjects.append(matches[0])
    except (ValueError, KeyError) as error:
        raise _error(
            422,
            "QUANT_COMPARISON_SCOPE_INVALID",
            "两股的冻结范围不匹配、标的未主数据化或输入完整性失败。",
        ) from error
    for plan, subject in zip(payload.subjects, subjects, strict=True):
        archive = await asyncio.to_thread(
            create_investigation,
            InvestigationCreateIn(
                title=subject["name"] + " · 比较研究支路",
                event_description="比较研究的独立冻结支路",
                investigation_goal="独立计算与核验同口径指标。",
                questions=[payload.question],
                depth=plan.depth,
            ),
            request,
        )
        branches.append(
            dict(
                investigation_id=archive.investigation_id,
                plan=plan.model_copy(update={"question": payload.question}).model_dump(mode="json"),
            )
        )
    archive = await asyncio.to_thread(
        create_investigation,
        InvestigationCreateIn(
            title=" vs ".join(s["name"] for s in subjects) + " · 对比分析",
            event_description="同期间、单位与计算口径的双标的比较",
            investigation_goal="并列原单元格与缺项，不做综合评分或投资排名。",
            questions=[payload.question],
            depth=payload.subjects[0].depth,
        ),
        request,
    )
    plan = payload.subjects[0].model_copy(update={"question": payload.question})
    run_id = runtime.start(
        archive.investigation_id, plan, comparison={"branches": branches, "subjects": subjects}
    )
    return dict(run_id=run_id, investigation_id=archive.investigation_id, status="CREATED")


@router.post("/runs/{run_id}/followups", status_code=202)
async def followup(run_id: str, payload: QuantFollowupIn, request: Request):
    from .followups import FollowupError, prepare_followup

    runtime = _runtime(request)
    try:
        context, plan = await asyncio.to_thread(prepare_followup, runtime, run_id, payload)
        if plan is None:
            return context
        return runtime.start(
            context["investigation_id"],
            plan,
            parent_run_id=run_id,
            followup=payload,
            changes=context["changes"],
        )
    except FollowupError as error:
        raise _error(error.status, error.code, str(error)) from error


@router.get("/runs")
def list_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: RunStatus | None = None,
):
    runtime = _runtime(request)
    with _sessions(request)() as session:
        query = select(InvestigationRunRow).where(
            InvestigationRunRow.workflow_version.in_(
                (
                    "quant-v1",
                    "quant-compare-v1",
                    "quant-backtest-readiness-v1",
                    "quant-history-acquisition-v1",
                )
            ),
            ~select(AuditEventRow.audit_event_id)
            .where(
                AuditEventRow.event_type == "QUANT_COMPARISON_BRANCH",
                AuditEventRow.target_id == InvestigationRunRow.run_id,
            )
            .exists(),
        )
        if status:
            query = query.where(InvestigationRunRow.status == status)
        rows = session.scalars(
            query.order_by(InvestigationRunRow.created_at.desc()).offset(offset).limit(limit)
        ).all()
        result = []
        for row in rows:
            plan = session.get(AuditEventRow, "QPLAN-" + row.run_id)
            item = _run_out(row).model_dump(mode="json")
            item["question"] = plan.metadata_payload.get("question", "") if plan else ""
            item["instrument"] = plan.metadata_payload.get("instrument") if plan else None
            item["parent_run_id"] = row.origin_run_id
            item["scenario"] = (
                "backtest"
                if row.workflow_version
                in {"quant-backtest-readiness-v1", "quant-history-acquisition-v1"}
                else "compare"
                if row.workflow_version == "quant-compare-v1"
                else "deep_research"
            )
            if item["scenario"] == "backtest":
                item["execution_status"] = "BLOCKED"
                from .backtest.api import saved_check_status

                item["check_status"] = saved_check_status(runtime, session, row)
            item["branch_investigation_ids"] = (
                [
                    b["investigation_id"]
                    for b in plan.metadata_payload.get("comparison", {}).get("branches", [])
                ]
                if plan
                else []
            )
            report = session.scalar(select(ReportRow).where(ReportRow.run_id == row.run_id))
            item["report_version"] = report.version if report else None
            result.append(item)
        return result


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    runtime = _runtime(request)
    with _sessions(request)() as session:
        row = _get_run_row(session, run_id)
        if row.workflow_version not in {"quant-v1", "quant-compare-v1"}:
            raise _error(404, "RUN_NOT_FOUND", "量化运行不存在。")
        report = session.scalar(
            select(ReportRow)
            .where(ReportRow.run_id == run_id)
            .order_by(ReportRow.created_at.desc())
        )
        result = dict(
            schema_version="quant-v1",
            run=_run_out(row),
            phase=runtime.phase(run_id, row),
            report_id=report.report_id if report else None,
            values=[],
            charts=[],
            gaps=[],
            evidence_count=0,
            citation_count=0,
        )
        plan = session.get(AuditEventRow, "QPLAN-" + run_id)
        result["plan"] = plan.metadata_payload if plan else None
        from .process import process_view

        result["process"] = process_view(session, run_id)
        from .followups import versions

        result["parent_run_id"] = row.origin_run_id
        result["versions"] = versions(session, row.investigation_id)
        subject = (
            instruments(runtime.service.sessions, result["plan"]["instrument"], 1)
            if result["plan"]
            else []
        )
        result["subject"] = subject[0] if subject else None
        result["scenario"] = (
            "compare" if row.workflow_version == "quant-compare-v1" else "deep_research"
        )
        if result["scenario"] == "compare":
            result["subject"] = None
            result["branches"] = [
                dict(child_run_id=e.target_id, instrument=e.metadata_payload["instrument"])
                for e in session.scalars(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.run_id == run_id,
                        AuditEventRow.event_type == "QUANT_COMPARISON_BRANCH",
                    )
                    .order_by(AuditEventRow.created_at)
                )
            ]
    try:
        result.update(runtime.presentation(run_id))
    except (ValueError, KeyError, PermissionError) as error:
        raise _error(
            409, "QUANT_INTEGRITY_FAILED", "计算产物或最新验证链发生漂移，数值已停止展示。"
        ) from error
    return result


@router.get("/overview")
def overview(request: Request):
    """Historical computed cells only; no guessed live prices or new formulas."""
    runtime = _runtime(request)
    stock_list = instruments(runtime.service.sessions, limit=100)
    with runtime.service.sessions() as session:
        plans = list(
            session.scalars(
                select(AuditEventRow)
                .join(InvestigationRunRow, InvestigationRunRow.run_id == AuditEventRow.run_id)
                .where(
                    AuditEventRow.event_type == "QUANT_FROZEN_PLAN",
                    InvestigationRunRow.workflow_version == "quant-v1",
                    InvestigationRunRow.status == RunStatus.COMPLETED,
                )
                .order_by(AuditEventRow.created_at.desc())
            )
        )
    for stock in stock_list:
        stock.update(quote=None, metrics=[], run_id=None, error=None)
        for plan in plans:
            if plan.metadata_payload.get("instrument") != stock["instrument_id"]:
                continue
            try:
                data = runtime.presentation(plan.run_id)
                cells = data.get("values", [])
                if not cells:
                    continue
                prices = [c for c in cells if c["name"] == "chart_price_raw"]
                stock.update(
                    quote=prices[-1] if prices else None,
                    metrics=[c for c in cells if c["name"] in {"pe", "pb", "roe", "return"}],
                    run_id=plan.run_id,
                    asof=data.get("asof"),
                )
            except (ValueError, KeyError, PermissionError):
                # Do not silently fall back to an older quote after integrity failure.
                stock["error"] = "最近成稿的完整性检查失败，已停止展示该标的数值。"
            break
    return dict(stocks=stock_list, mode="historical_frozen", indices=[])


@router.get("/runs/{run_id}/comparison-export")
def export_comparison(run_id: str, request: Request):
    runtime = _runtime(request)
    with runtime.service.sessions() as session:
        row = _get_run_row(session, run_id)
        if row.workflow_version != "quant-compare-v1":
            raise _error(404, "RUN_NOT_FOUND", "比较研究不存在。")
        plan = session.get(AuditEventRow, "QPLAN-" + run_id).metadata_payload
    try:
        data = runtime.presentation(run_id)
    except (ValueError, KeyError, PermissionError) as error:
        raise _error(
            409, "QUANT_INTEGRITY_FAILED", "比较输入或验证链发生漂移，导出已停止。"
        ) from error
    if not data.get("comparison"):
        raise _error(409, "QUANT_COMPARISON_UNFINISHED", "比较尚未成稿。")
    from .comparison import markdown

    return Response(
        markdown(data, plan),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="comparison-report.md"'},
    )


class PublicQuantRuntime:
    def __init__(self, service, runner, journal_root):
        self.service, self.runner, self.journal_root = service, runner, journal_root
        self.phases = {}
        service.public_phase = self.set_phase

    def set_phase(self, run_id, name, phase):
        from .process import enter_stage

        self.phases[run_id] = name
        stage = {
            "取数": "data",
            "确定性计算": "analyst",
            "证据核验": "verifier",
            "成稿": "writer",
        }.get(name)
        if stage:
            enter_stage(self.service.sessions, run_id, stage, name)
        with self.service.sessions.begin() as session:
            row = session.get(InvestigationRunRow, run_id)
            row.current_phase = phase
            row.state_version += 1
            row.updated_at = datetime.now(UTC)

    def phase(self, run_id, row):
        return self.phases.get(run_id) or (
            "成稿"
            if row.status == RunStatus.COMPLETED
            else "证据核验"
            if row.current_phase == WorkflowPhase.VERIFY
            else "确定性计算"
        )

    def validate_pins(self, plan):
        snapshots = tuple(self.service.store.load(s) for s in plan.frozen_snapshot_ids)
        prices = [s for s in snapshots if s.result.request.dataset == "daily"]
        for adjustment in {"raw", plan.adjustment}:
            candidates = [s for s in prices if s.result.request.adjustment == adjustment]
            if len(candidates) != 1:
                raise ValueError("exactly one raw/selected price snapshot required")
            r = candidates[0].result.request
            if (
                r.instruments != (plan.instrument,)
                or r.start != plan.date_start
                or r.end != plan.date_end
                or r.asof != plan.asof
            ):
                raise ValueError("frozen price window/asof mismatch")
        if any(
            s.result.request.instruments != (plan.instrument,) or s.result.request.asof != plan.asof
            for s in snapshots
        ):
            raise ValueError("frozen scope/asof mismatch")
        if len({s.result.request.adjustment for s in prices}) != len(prices):
            raise ValueError("ambiguous exhibit price basis")
        calendars = [s for s in snapshots if s.result.request.dataset == "calendar"]
        if len(calendars) != 1 or not (
            calendars[0].result.request.start <= plan.date_start
            and calendars[0].result.request.end >= plan.date_end
        ):
            raise ValueError("exactly one covering calendar required")
        return tuple(s.snapshot_id for s in snapshots)

    def start(
        self,
        investigation_id,
        plan,
        *,
        parent_run_id=None,
        followup=None,
        changes=None,
        comparison=None,
    ):
        from marketpulse.investigation.domain.enums import AuditActorType
        from marketpulse.investigation.domain.reports import AuditEvent

        now = datetime.now(UTC)
        run_id = "RUN-QUANT-" + uuid.uuid4().hex[:16]
        settings = depth_settings(self.runner.settings, plan.depth)
        with self.service.sessions.begin() as session:
            if parent_run_id:
                from .contracts import digest
                from .followups import FollowupError, existing_request, request_id

                # Lock the shared archive before checking active versions; SQLite also
                # serializes this write. Parent and siblings all share this row.
                session.execute(
                    update(InvestigationRow)
                    .where(InvestigationRow.investigation_id == investigation_id)
                    .values(updated_at=now)
                )
                previous = existing_request(session, investigation_id, followup, parent_run_id)
                if previous:
                    return previous
                active = session.scalar(
                    select(InvestigationRunRow.run_id).where(
                        InvestigationRunRow.investigation_id == investigation_id,
                        InvestigationRunRow.status.not_in(
                            [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED]
                        ),
                    )
                )
                if active:
                    raise FollowupError(
                        409,
                        "QUANT_VERSION_ACTIVE",
                        "该研究已有未结束的版本任务，请完成或停止后再提交。",
                    )
                followup_event = AuditEventRow(
                    audit_event_id=request_id(investigation_id, str(followup.request_key)),
                    investigation_id=investigation_id,
                    run_id=run_id,
                    actor_type=AuditActorType.SYSTEM,
                    event_type="QUANT_FOLLOWUP",
                    target_type="Run",
                    target_id=run_id,
                    metadata_payload={
                        "request_hash": digest(followup.model_dump(mode="json")),
                        "child_run_id": run_id,
                        "parent_run_id": parent_run_id,
                    },
                    created_at=now,
                )
            for entity in (
                InvestigationRun(
                    run_id=run_id,
                    investigation_id=investigation_id,
                    mode=RunMode.LIVE,
                    status=RunStatus.RUNNING,
                    current_phase=WorkflowPhase.COLLECT,
                    checkpoint_version=0,
                    state_version=0,
                    workflow_version="quant-compare-v1" if comparison else "quant-v1",
                    origin_run_id=parent_run_id,
                    created_at=now,
                    started_at=now,
                    updated_at=now,
                ),
                RunBudget(
                    run_id=run_id,
                    max_research_rounds=settings.max_research_rounds,
                    max_search_calls=settings.max_search_queries,
                    max_fetch_calls=settings.max_pages,
                    max_model_calls=0,
                    max_tokens=0,
                    max_wall_time_ms=int(settings.total_timeout_seconds * 1000)
                    * (2 if comparison else 1),
                    max_sources=settings.max_pages,
                    updated_at=now,
                ),
                AuditEvent(
                    audit_event_id="QPLAN-" + run_id,
                    investigation_id=investigation_id,
                    run_id=run_id,
                    actor_type=AuditActorType.SYSTEM,
                    event_type="QUANT_FROZEN_PLAN",
                    target_type="Run",
                    target_id=run_id,
                    metadata={
                        **plan.model_dump(mode="json"),
                        **({"scenario": "compare", "comparison": comparison} if comparison else {}),
                        **(
                            {"parent_run_id": parent_run_id, "changes": changes}
                            if parent_run_id
                            else {}
                        ),
                    },
                    created_at=now,
                ),
            ):
                self.runner.repository.add_in_session(session, entity)
            if parent_run_id:
                session.add(followup_event)
        if comparison:
            from .comparison import execute

            task = execute(self, run_id, investigation_id, plan, comparison)
        else:
            task = self.execute(run_id, investigation_id, plan)
        self.runner.tasks[run_id] = asyncio.create_task(task)
        return (
            dict(run_id=run_id, investigation_id=investigation_id, status="CREATED")
            if parent_run_id
            else run_id
        )

    async def execute(self, run_id, investigation_id, plan):
        from .acquisition import acquire_inputs
        from .domain import MetricSpec
        from .process import enter_stage, record_stage, stop_active

        record_stage(
            self.service.sessions, run_id, "planner", "COMPLETED", "已确认单股、期间与价格口径。"
        )
        self.set_phase(run_id, "取数", WorkflowPhase.COLLECT)
        try:
            timeout = depth_settings(self.runner.settings, plan.depth).total_timeout_seconds
            async with asyncio.timeout(max(1, timeout - 30)):
                ids = (
                    await asyncio.to_thread(self.validate_pins, plan)
                    if plan.frozen_snapshot_ids
                    else await acquire_inputs(self, run_id, plan)
                )
                self.phases[run_id] = "冻结快照"
                for snapshot_id in ids:
                    await asyncio.to_thread(
                        self.service.store.attach, snapshot_id, investigation_id
                    )
                with self.service.sessions.begin() as session:
                    row = session.get(InvestigationRunRow, run_id)
                    row.current_phase = WorkflowPhase.ANALYZE
                    row.state_version += 1
                self.set_phase(run_id, "确定性计算", WorkflowPhase.ANALYZE)
                await self.runner._execute_quant(
                    run_id=run_id,
                    spec=MetricSpec(
                        include_exhibits=True,
                        dcf=plan.dcf,
                        price_adjustment=plan.adjustment,
                        return_basis="price" if plan.adjustment == "raw" else "adjusted_proxy",
                        metrics=(
                            "return",
                            "cagr",
                            "volatility",
                            "drawdown",
                            "pe",
                            "pb",
                            "roe",
                            "trend",
                            "sharpe",
                            "beta",
                        ),
                        explicit_zero_risk_free=plan.rf == "zero",
                    ),
                    inputs=ids,
                    instrument_id=plan.instrument,
                    asof=plan.asof,
                    idempotency_key="public-quant-v1",
                )
        except asyncio.CancelledError:
            stop_active(self.service.sessions, run_id, "任务取消或服务关闭，未完成部分保留缺项。")
            enter_stage(self.service.sessions, run_id, "writer", "生成部分报告")
            await self.runner._finalize_run(
                run_id,
                (
                    "SERVER_SHUTDOWN：服务关闭，保留冻结材料并生成部分报告。"
                    if self.runner._closing
                    else "USER_CANCELLED：已停止取数，保留冻结材料并生成部分报告。"
                ),
            )
        except Exception:
            stop_active(self.service.sessions, run_id, "数据或执行受阻，未完成部分保留缺项。")
            enter_stage(self.service.sessions, run_id, "writer", "生成部分报告")
            await self.runner._finalize_run(
                run_id,
                "QUANT_PARTIAL：数据或时长不足，未完成部分不作为结论；请检查数据源与冻结区间。",
            )
        finally:
            self.phases[run_id] = "成稿"
            # The runner finalizes with a durable report. Do not invent success on failure.
            with self.service.sessions() as session:
                row = session.get(InvestigationRunRow, run_id)
                report = session.scalar(select(ReportRow).where(ReportRow.run_id == run_id))
                complete = row.status == RunStatus.COMPLETED and report is not None
            if complete:
                # If computation stopped internally, its role cannot become complete.
                with self.service.sessions() as session:
                    from .process import process_view

                    active = process_view(session, run_id)
                if any(n["status"] == "RUNNING" and n["stage"] != "writer" for n in active):
                    stop_active(self.service.sessions, run_id, "部分执行结束，详见报告缺项。")
                record_stage(
                    self.service.sessions,
                    run_id,
                    "writer",
                    "COMPLETED",
                    "报告已落盘；未验证指标保留未验证标签。",
                )
            else:
                stop_active(self.service.sessions, run_id, "执行结束，尚无完整落盘报告。")

    def presentation(self, run_id):
        from .charts import presentation

        with self.service.sessions() as session:
            row = session.get(InvestigationRunRow, run_id)
            is_comparison = row and row.workflow_version == "quant-compare-v1"
        if is_comparison:
            from .comparison import presentation as comparison_presentation

            return comparison_presentation(self, run_id)

        return presentation(self.service, run_id)


from .backtest.api import router as backtest_router  # noqa: E402

router.include_router(backtest_router)
