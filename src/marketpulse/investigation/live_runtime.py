"""Owned background LIVE runs using the same persisted agent workflow as replay."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from marketpulse.adapters.investigation_search import PublicSearchPortAdapter
from marketpulse.adapters.search import PublicSearchClient
from marketpulse.budget import RunBudget as SearchBudget
from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.adapters.fetch import HttpxFetchAdapter
from marketpulse.investigation.adapters.model import OpenAICompatibleModelAdapter
from marketpulse.investigation.depth import (
    FINALIZE_RESERVE_SECONDS,
    PRESETS,
    SOURCE_LIMITS,
    Depth,
    depth_settings,
    selected_depth,
)
from marketpulse.investigation.domain.enums import (
    ExecutionStepStatus,
    ReportType,
    RunMode,
    RunStatus,
    ValidationStatus,
    WorkflowPhase,
)
from marketpulse.investigation.domain.runtime import Investigation, InvestigationRun, RunBudget
from marketpulse.investigation.feedback.guards import ProposalGuardError
from marketpulse.investigation.feedback.orchestrator import AgentFeedbackOrchestrator
from marketpulse.investigation.feedback.store import FeedbackStore
from marketpulse.investigation.harness.calls import BoundExternalCalls
from marketpulse.investigation.harness.model_retry import ModelRetryExhaustedError
from marketpulse.investigation.harness.model_routing import ModelRoutingPolicy
from marketpulse.investigation.harness.persistence import HarnessStore
from marketpulse.investigation.harness.runtime import InvestigationHarness
from marketpulse.investigation.harness.stage_budget import StageBudgetPolicy
from marketpulse.investigation.ingestion.registry import DocumentParserRegistry
from marketpulse.investigation.persistence.models import (
    AuditEventRow,
    ExecutionStepRow,
    InvestigationRunRow,
    ReportRow,
)
from marketpulse.investigation.persistence.repositories import InvestigationRepository
from marketpulse.investigation.ports.external import FetchPort, ModelPort, SearchPort
from marketpulse.investigation.recording.diagnostics import provider_diagnostics
from marketpulse.investigation.recording.errors import InvalidProviderResponseError
from marketpulse.investigation.recording.store import RepositoryRecordedCallStore
from marketpulse.investigation.recovery import (
    WORKFLOW_VERSION,
    RecoveryConflict,
    ResumeRequest,
    RunRecovery,
    live_feedback_config,
)
from marketpulse.investigation.reporting.pipeline import ReportPipeline
from marketpulse.investigation.reporting.writer import DeterministicWriter
from marketpulse.investigation.validation.integrity import EvidenceIntegrityValidator
from marketpulse.investigation.validation.models import RecognizedArtifactVersions
from marketpulse.investigation.validation.policy import ValidationPolicy

LOGGER = logging.getLogger(__name__)
_ACTIVE = {
    RunStatus.CREATED,
    RunStatus.PENDING,
    RunStatus.WAITING_FOR_EXECUTION,
    RunStatus.RUNNING,
    RunStatus.VERIFYING,
}


class LiveNotConfiguredError(RuntimeError):
    pass


@dataclass(frozen=True)
class LivePorts:
    """Injection boundary for offline integration tests and alternative providers."""

    search: SearchPort
    fetch: FetchPort
    model: ModelPort


class LiveInvestigationService:
    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        repository: InvestigationRepository,
        settings: Settings,
        blob_root: Path,
        ports: LivePorts | None = None,
        quant_service=None,
    ) -> None:
        self.sessions = sessions
        self.repository = repository
        self.settings = settings
        self.blobs = LocalContentAddressedBlobStorage(blob_root)
        self.ports = ports
        self.quant_service = quant_service
        self.recovery = RunRecovery(
            sessions,
            repository,
            self.blobs,
            settings,
            restore_quarantine=(blob_root / ".restore-quarantine.json").exists(),
        )
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False
        self._stalled: set[str] = set()
        self.worker_activity: dict[str, dict[str, str]] = {}

    def recover_interrupted(self) -> None:
        """Single-worker API: abandoned jobs must not look like running work forever."""
        recovered = []
        with self.sessions.begin() as session:
            rows = session.scalars(
                select(InvestigationRunRow).where(
                    InvestigationRunRow.mode == RunMode.LIVE,
                    or_(
                        InvestigationRunRow.status.in_(_ACTIVE),
                        and_(
                            InvestigationRunRow.status.in_(
                                (RunStatus.READY_FOR_REPORT, RunStatus.BLOCKED)
                            ),
                            InvestigationRunRow.current_phase == WorkflowPhase.REPORT,
                            InvestigationRunRow.completed_at.is_(None),
                        ),
                    ),
                )
            ).all()
            for row in rows:
                recovered.append(row.run_id)
                row.status = RunStatus.INTERRUPTED
                row.state_version += 1
                row.interruption_reason = "SERVER_RESTARTED: inspect recovery options"
                row.updated_at = datetime.now(UTC)
                row.completed_at = row.updated_at
                row.owner_instance_id = None
                row.owner_heartbeat_at = None
                steps = session.scalars(
                    select(ExecutionStepRow).where(
                        ExecutionStepRow.run_id == row.run_id,
                        ExecutionStepRow.status == ExecutionStepStatus.RUNNING,
                    )
                ).all()
                for step in steps:
                    step.status = ExecutionStepStatus.INTERRUPTED
                    step.error_code = "SERVER_RESTARTED"
                    step.completed_at = row.updated_at
        for run_id in recovered:
            self._schedule(run_id, finalize_only=True)

    def _ensure_available(self) -> None:
        if self._closing:
            raise LiveNotConfiguredError("服务正在关闭，请稍后重试。")
        key = self.settings.deepseek_api_key
        if self.ports is None and (key is None or not key.get_secret_value().strip()):
            raise LiveNotConfiguredError(
                "实时调查需要模型凭据：请配置 DEEPSEEK_API_KEY 后重启服务；"
                "DEEPSEEK_BASE_URL 和 MARKETPULSE_MODEL 可用于兼容的模型服务。"
            )

    def start(self, investigation_id: str, depth: Depth | None = None) -> str:
        self._ensure_available()
        self.repository.get(Investigation, investigation_id)
        with self.sessions() as session:
            depth = depth or selected_depth(session, investigation_id)
        settings = depth_settings(self.settings, depth)
        run_id = f"RUN-LIVE-{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC)
        run = InvestigationRun(
            run_id=run_id,
            investigation_id=investigation_id,
            mode=RunMode.LIVE,
            status=RunStatus.CREATED,
            current_phase=WorkflowPhase.CREATED,
            checkpoint_version=0,
            state_version=0,
            workflow_version=WORKFLOW_VERSION,
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        budget = RunBudget(
            run_id=run_id,
            max_research_rounds=settings.max_research_rounds,
            max_search_calls=settings.max_search_queries,
            max_fetch_calls=settings.max_pages,
            max_model_calls=settings.max_model_calls,
            max_tokens=settings.max_tokens,
            max_wall_time_ms=int(settings.total_timeout_seconds * 1000),
            max_sources=min(settings.max_pages, SOURCE_LIMITS[depth]),
            updated_at=now,
        )
        with self.sessions.begin() as session:
            config = self.recovery.config_event(run_id, investigation_id)
            config = config.model_copy(
                update={
                    "metadata": {
                        **config.metadata,
                        "depth": depth,
                        "depth_settings": {
                            name: getattr(settings, name) for name in PRESETS[depth]
                        },
                    }
                }
            )
            for entity in (run, budget, config):
                self.repository.add_in_session(session, entity)
        self._schedule(run_id)
        return run_id

    def resume(self, run_id: str, request: ResumeRequest, *, automatic: bool = False) -> None:
        run = self.repository.get(InvestigationRun, run_id)
        if run.workflow_version in {"quant-backtest-readiness-v1", "quant-history-acquisition-v1"}:
            raise RecoveryConflict(
                "BACKTEST_INPUTS_UNAVAILABLE",
                "该任务已保存数据检查结果；补齐历史输入后请创建新检查，不能恢复为网页调查。",
            )
        self._ensure_available()
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            raise RecoveryConflict("RUN_STILL_ACTIVE", "任务仍在执行，请等待停止后恢复。")
        authorized = self.recovery.prepare(
            run_id,
            request,
            automatic=automatic,
            max_auto_attempts=self.settings.auto_resume_max_attempts,
            backoff_seconds=self.settings.auto_resume_backoff_seconds,
        )
        self._schedule(run_id, authorized)

    def _schedule(
        self, run_id: str, authorized: frozenset[str] = frozenset(), *, finalize_only: bool = False
    ) -> None:
        self.worker_activity[run_id] = {}
        task = asyncio.create_task(
            self._finalize_run(run_id, "SERVER_RESTARTED：服务重启，已用归档材料完成收尾。")
            if finalize_only
            else self._execute(run_id, authorized),
            name=run_id,
        )
        self.tasks[run_id] = task

        def cleanup(completed: asyncio.Task[None]) -> None:
            if self.tasks.get(run_id) is completed:
                self.tasks.pop(run_id, None)
                self.worker_activity.pop(run_id, None)
                self._stalled.discard(run_id)

        task.add_done_callback(cleanup)

    async def cancel(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        if task is None or task.done():
            return False
        # Persist the user's stop intent before waiting, including a process-kill window.
        with self.sessions.begin() as session:
            row = session.get(InvestigationRunRow, run_id)
            if row is not None:
                row.status = RunStatus.CANCELLED
                row.interruption_reason = "USER_CANCELLED"
                row.state_version += 1
                row.updated_at = datetime.now(UTC)
                row.completed_at = row.updated_at
                # Keep ownership until the cancelled step records its final checkpoint.
        task.cancel()
        run = self.repository.get(InvestigationRun, run_id)
        if run.workflow_version == "quant-history-acquisition-v1":
            await asyncio.gather(task, return_exceptions=True)
            return True
        if self.quant_service is not None:
            await self.quant_service.cancel_run(run_id)
        await asyncio.gather(task, return_exceptions=True)
        await self._finalize_run(run_id, "USER_CANCELLED：用户停止调查，已用现有材料完成收尾。")
        return True

    async def interrupt_stalled(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        self._stalled.add(run_id)
        if task is not None and not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=5)
            if not done:
                return False
        current = self.repository.get(InvestigationRun, run_id)
        if (
            current.status is RunStatus.CANCELLED
            and current.interruption_reason == "USER_CANCELLED"
        ):
            # Cancellation initiated by the watchdog uses a distinct reason below.
            return True
        await self._finalize_run(run_id, "WATCHDOG_STALLED：执行停滞，已用现有材料完成收尾。")
        return True

    async def shutdown(self) -> None:
        self._closing = True
        pending = tuple((run_id, task) for run_id, task in self.tasks.items() if not task.done())
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(task for _, task in pending), return_exceptions=True)
        for run_id, _ in pending:
            if (
                self.repository.get(InvestigationRun, run_id).workflow_version
                == "quant-history-acquisition-v1"
            ):
                continue
            await self._finalize_run(run_id, "SERVER_SHUTDOWN：服务关闭，已用现有材料完成收尾。")

    async def _execute(self, run_id: str, authorized: frozenset[str] = frozenset()) -> None:
        try:
            run = self.repository.get(InvestigationRun, run_id)
            if run.current_phase is WorkflowPhase.REPORT:
                # Report regeneration is local and does not need external-call budget.
                async with asyncio.timeout(30):
                    await self._report(
                        run_id,
                        ReportType.FULL_INVESTIGATION
                        if run.status is RunStatus.READY_FOR_REPORT
                        else ReportType.INVESTIGATION_STATUS,
                    )
                self._finish(run_id, RunStatus.COMPLETED, run.interruption_reason)
                return
            if run.workflow_version == "quant-compare-v1":
                # Restart is bounded and local; never relaunch the comparison as web research.
                await self._finalize_run(
                    run_id,
                    "QUANT_COMPARISON_INTERRUPTED：比较研究中断，保留已归档支路与冻结材料；请重新发起比较。",
                )
                return
            if run.workflow_version == "quant-v1":
                if self.quant_service is None:
                    await self._finalize_run(
                        run_id, "QUANT_PARTIAL：量化服务未配置，已保留冻结材料。"
                    )
                    return
                from marketpulse.quant.storage.models import ComputeJobRow

                with self.sessions() as session:
                    jobs = list(
                        session.scalars(select(ComputeJobRow).where(ComputeJobRow.run_id == run_id))
                    )
                    if len(jobs) != 1:
                        raise ValueError("quant recovery requires one pinned computation")
                    job_id, key = jobs[0].job_id, jobs[0].idempotency_key
                pinned = await asyncio.to_thread(self.quant_service.input_for, job_id)
                await self._execute_quant(
                    run_id=run_id,
                    spec=pinned.spec,
                    inputs=tuple(s.snapshot_id for s in pinned.snapshots),
                    instrument_id=pinned.instrument_id,
                    asof=pinned.asof,
                    idempotency_key=key,
                )
                return
            budget = HarnessStore(self.sessions, self.repository).read_budget(run_id)
            remaining_seconds = max(
                0.001,
                (budget.max_wall_time_ms - budget.consumed_wall_time_ms) / 1000
                - FINALIZE_RESERVE_SECONDS,
            )
            search_settings = self.settings.model_copy(
                update={
                    "total_timeout_seconds": remaining_seconds,
                    "max_search_queries": max(
                        0, budget.max_search_calls - budget.search_calls_used
                    ),
                    "max_pages": max(0, budget.max_fetch_calls - budget.fetch_calls_used),
                }
            )
            async with AsyncExitStack() as stack:
                ports = self.ports
                if ports is None:
                    http = await stack.enter_async_context(
                        httpx.AsyncClient(follow_redirects=False)
                    )
                    key = self.settings.deepseek_api_key
                    assert key is not None
                    model = await stack.enter_async_context(
                        AsyncOpenAI(
                            api_key=key.get_secret_value(),
                            base_url=self.settings.deepseek_base_url,
                            timeout=httpx.Timeout(
                                self.settings.model_read_timeout_seconds,
                                connect=self.settings.model_connect_timeout_seconds,
                                write=30,
                                pool=15,
                            ),
                            # Avoid hidden SDK retries after ambiguous provider outcomes.
                            max_retries=0,
                        )
                    )
                    ports = LivePorts(
                        search=PublicSearchPortAdapter(
                            PublicSearchClient(
                                http,
                                self.settings,
                                SearchBudget.start(search_settings),
                            )
                        ),
                        fetch=HttpxFetchAdapter(
                            http,
                            allow_proxy_dns=self.settings.allow_proxy_dns,
                            user_agent=self.settings.user_agent,
                            max_retries=self.settings.max_retries,
                            accept_language=(
                                "zh-CN,zh;q=0.9,en;q=0.7"
                                if any(
                                    "\u4e00" <= ch <= "\u9fff"
                                    for ch in self.repository.get(
                                        Investigation, run.investigation_id
                                    ).title
                                )
                                else "en-US,en;q=0.8"
                            ),
                        ),
                        model=OpenAICompatibleModelAdapter(
                            model,
                            provider="openai-compatible",
                            default_model=self.settings.model,
                            thinking_enabled=self.settings.model_thinking_enabled,
                        ),
                    )
                async with asyncio.timeout(remaining_seconds):
                    outcome = await self._orchestrator(run_id, ports, authorized).run(run_id)
                await self._finalize_run(
                    run_id,
                    None
                    if outcome.termination == "READY_FOR_REPORT"
                    else outcome.summary.termination_reason,
                )
        except asyncio.CancelledError:
            await self._finalize_run(
                run_id,
                "SERVER_SHUTDOWN：服务关闭，已用现有材料完成报告收尾。"
                if self._closing
                else "WATCHDOG_STALLED：执行停滞，已用现有材料完成报告收尾。"
                if run_id in self._stalled
                else "USER_CANCELLED：用户停止调查，已用现有材料完成报告收尾。",
            )
            raise
        except Exception as error:
            # Provider exceptions can embed credentials or response bodies. Persist only type/code.
            reason = "RUN_TIMEOUT" if isinstance(error, TimeoutError) else type(error).__name__
            detail = (
                AgentFeedbackOrchestrator._proposal_stop_detail("planning or verification")
                if isinstance(error, (ProposalGuardError, InvalidProviderResponseError))
                else f"{reason}: inspect configuration and retry"
            )
            status = provider_diagnostics(error).get("http_status")
            if isinstance(error, ModelRetryExhaustedError):
                detail = (
                    "MODEL_RETRY_EXHAUSTED：网络不稳定，无法稳定连接模型服务，"
                    "请检查网络/代理后重试。"
                )
            elif status == 402:
                detail = "MODEL_INSUFFICIENT_BALANCE：模型服务余额不足，请充值后重试。"
            elif status == 404:
                detail = "MODEL_ID_INVALID：配置的模型 ID 无效，请核对官方模型清单。"
            elif isinstance(error, TimeoutError):
                detail = (
                    "RUN_TIMEOUT：已到本档研究时间上限，停止扩展采集；"
                    "现有材料已用于报告收尾，可选更长档位继续核查。"
                )
            LOGGER.warning("Live run %s entering report finalization (%s)", run_id, reason)
            await self._finalize_run(run_id, detail)

    async def _finalize_run(self, run_id: str, reason: str | None) -> None:
        """Local, bounded, paid-call-free finalization. Persist report before COMPLETED."""
        with self.sessions.begin() as session:
            row = session.get(InvestigationRunRow, run_id)
            if row is None:
                return
            if row.status is RunStatus.COMPLETED and session.scalar(
                select(ReportRow.report_id).where(ReportRow.run_id == run_id).limit(1)
            ):
                return
            row.status = RunStatus.BLOCKED if reason else RunStatus.READY_FOR_REPORT
            row.current_phase = WorkflowPhase.REPORT
            row.interruption_reason = reason
            row.completed_at = None
            row.updated_at = datetime.now(UTC)
        for attempt in range(3):
            try:
                await self._report(run_id, ReportType.INVESTIGATION_STATUS)
                break
            except Exception as error:
                LOGGER.warning(
                    "Report finalization %s attempt %s (%s)",
                    run_id,
                    attempt + 1,
                    type(error).__name__,
                )
                if attempt < 2:
                    await asyncio.sleep(0.1 * 2**attempt)
        else:
            for attempt in range(3):
                try:
                    await ReportPipeline(
                        self.sessions, self.repository, DeterministicWriter()
                    ).minimal(
                        run_id=run_id,
                        now=datetime.now(UTC),
                        reason=reason or "报告完整性校验未通过",
                    )
                    break
                except Exception as error:
                    LOGGER.error(
                        "Minimal finalization %s attempt %s (%s)",
                        run_id,
                        attempt + 1,
                        type(error).__name__,
                    )
                    if attempt < 2:
                        await asyncio.sleep(0.1 * 2**attempt)
            else:
                # Never claim COMPLETED without a durable report. Leave a
                # REPORT checkpoint for paid-call-free recovery on restart.
                with self.sessions.begin() as session:
                    row = session.get(InvestigationRunRow, run_id)
                    row.status = RunStatus.BLOCKED
                    row.interruption_reason = (
                        "REPORT_FINALIZATION_PENDING：报告收尾暂未成功；"
                        "请检查服务日志与存储，修复后重启服务将自动重试。"
                    )
                    row.owner_instance_id = None
                    row.owner_heartbeat_at = None
                return
        self._finish(run_id, RunStatus.COMPLETED, reason)

    def _run_settings(self, run_id: str) -> Settings:
        with self.sessions() as session:
            event = session.get(AuditEventRow, f"RUN-CONFIG-{run_id}")
            updates = event.metadata_payload.get("depth_settings", {}) if event else {}
            depth = event.metadata_payload.get("depth", "standard") if event else "standard"
        return self.settings.model_copy(update={**updates, "investigation_depth": depth})

    def _finish(self, run_id: str, status: RunStatus, reason: str | None) -> None:
        with self.sessions.begin() as session:
            row = session.get(InvestigationRunRow, run_id)
            if row is not None:
                if row.status is RunStatus.COMPLETED and status is not RunStatus.COMPLETED:
                    return
                if (
                    row.status is RunStatus.CANCELLED
                    and row.interruption_reason == "USER_CANCELLED"
                    and status is not RunStatus.CANCELLED
                ):
                    return
                row.status = status
                row.state_version += 1
                row.interruption_reason = reason
                row.updated_at = datetime.now(UTC)
                row.completed_at = row.updated_at
                row.owner_instance_id = None
                row.owner_heartbeat_at = None

    async def _report(self, run_id: str, report_type: ReportType) -> None:
        state = FeedbackStore(self.sessions, self.repository).state(run_id)
        claims = state.claims
        if (
            report_type is ReportType.INVESTIGATION_STATUS
            and all(c.latest_validation_id is not None for c in claims)
            and any(
                c.validation_status in (ValidationStatus.VERIFIED, ValidationStatus.PROBABLE)
                for c in claims
            )
        ):
            # Preserve supported findings on a bounded stop. The unchanged release
            # gate still evaluates citation integrity, gaps and critical coverage.
            report_type = ReportType.FULL_INVESTIGATION
        await ReportPipeline(
            self.sessions, self.repository, DeterministicWriter(), quant_service=self.quant_service
        ).generate(
            run_id=run_id,
            report_type=report_type,
            now=datetime.now(UTC),
            finalization_reason=state.run.interruption_reason,
            completion_disclosure=True,
        )

    def start_quant(self, *, run_id, spec, inputs, instrument_id, asof, idempotency_key):
        """Internal opt-in only; no public route, search, model or paid report call."""
        if self.quant_service is None or self._closing:
            raise ValueError("quant service not configured")
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise ValueError("run already active")
        with self.sessions.begin() as session:
            row = session.get(InvestigationRunRow, run_id)
            if row is None or row.status is RunStatus.COMPLETED:
                raise ValueError("quant run must be an existing unfinished run")
            row.workflow_version = "quant-v1"
            row.status = RunStatus.RUNNING
            row.current_phase = WorkflowPhase.ANALYZE
            row.updated_at = datetime.now(UTC)
        self.tasks[run_id] = asyncio.create_task(
            self._execute_quant(
                run_id=run_id,
                spec=spec,
                inputs=inputs,
                instrument_id=instrument_id,
                asof=asof,
                idempotency_key=idempotency_key,
            )
        )

    async def _execute_quant(self, **request):
        run_id = request["run_id"]
        reason = None
        try:
            await AgentFeedbackOrchestrator.run_quant(self.quant_service, **request)
        except asyncio.CancelledError:
            reason = (
                "SERVER_SHUTDOWN：服务关闭，已保留完成的冻结材料。"
                if self._closing
                else "WATCHDOG_STALLED：执行停滞，已保留完成的冻结材料。"
                if run_id in self._stalled
                else "USER_CANCELLED：用户停止量化计算，已保留完成的冻结材料。"
            )
        except Exception as error:
            LOGGER.warning("Quant branch stopped (%s)", type(error).__name__)
            reason = "QUANT_PARTIAL：部分计算未完成，不能据此确认完整投研结论。"
        # Same durable-report-first finalization as web tasks, without a new model call.
        await self._finalize_run(run_id, reason)

    def _orchestrator(
        self,
        run_id: str,
        ports: LivePorts,
        authorized: frozenset[str] = frozenset(),
    ) -> AgentFeedbackOrchestrator:
        settings = self._run_settings(run_id)
        integrity = EvidenceIntegrityValidator(
            blobs=self.blobs,
            recognized_versions=RecognizedArtifactVersions(
                snapshot_parsers=frozenset(
                    {
                        ("html", "1", "text-normalizer-v1"),
                        ("html", "2", "text-normalizer-v1"),
                        ("html", "3", "text-normalizer-v1"),
                        ("plain-text", "1", "text-normalizer-v1"),
                        ("pypdf-text-layer", "1", "text-normalizer-v1"),
                        ("pypdf-text-layer", "2", "text-normalizer-v1"),
                    }
                ),
                artifact_processors=frozenset(
                    {
                        ("html", "1"),
                        ("html", "2"),
                        ("html", "3"),
                        ("plain-text", "1"),
                        ("pypdf-text-layer", "1"),
                        ("pypdf-text-layer", "2"),
                    }
                ),
            ),
        )
        return AgentFeedbackOrchestrator(
            harness=InvestigationHarness(HarnessStore(self.sessions, self.repository), self.blobs),
            calls=BoundExternalCalls(
                sessions=self.sessions,
                repository=self.repository,
                recordings=RepositoryRecordedCallStore(self.repository, self.blobs),
                live_search=ports.search,
                live_fetch=ports.fetch,
                live_model=ports.model,
                model_routing=ModelRoutingPolicy(self.settings),
                model_retry_attempts=self.settings.model_retry_attempts
                if self.settings.model_auto_retry
                else 0,
                model_retry_backoff_seconds=self.settings.model_retry_backoff_seconds,
                authorized_unknown_intent_ids=authorized,
                pause_on_unknown_outcome=True,
                model_budget_policy=StageBudgetPolicy(
                    verify_tokens=self.settings.verify_token_reserve_fraction,
                    report_tokens=self.settings.report_token_reserve_fraction,
                    verify_calls=self.settings.verify_call_reserve_fraction,
                    report_calls=self.settings.report_call_reserve_fraction,
                ),
            ),
            store=FeedbackStore(self.sessions, self.repository),
            repository=self.repository,
            blobs=self.blobs,
            parsers=DocumentParserRegistry.default(),
            validation_policy=ValidationPolicy(integrity=integrity),
            integrity=integrity,
            owner_instance_id=f"live-{run_id}",
            progress=lambda name, status: self.worker_activity.setdefault(run_id, {}).__setitem__(
                name, status
            ),
            config=live_feedback_config(settings),
        )
