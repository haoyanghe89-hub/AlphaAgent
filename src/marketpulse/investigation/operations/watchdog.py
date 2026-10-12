"""Bounded automatic recovery and durable, deduplicated operational alerts."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marketpulse.investigation.domain.enums import AuditActorType, RunMode, RunStatus
from marketpulse.investigation.domain.reports import AuditEvent
from marketpulse.investigation.live_runtime import LiveInvestigationService, LiveNotConfiguredError
from marketpulse.investigation.operations.ownership import ServerOwnership
from marketpulse.investigation.persistence.models import (
    AuditEventRow,
    ExecutionStepRow,
    InvestigationRunRow,
)
from marketpulse.investigation.recovery import WORKFLOW_VERSION, RecoveryConflict, ResumeRequest

LOGGER = logging.getLogger(__name__)
_ACTIVE = {
    RunStatus.CREATED,
    RunStatus.PENDING,
    RunStatus.WAITING_FOR_EXECUTION,
    RunStatus.RUNNING,
    RunStatus.VERIFYING,
}
_STOPPED = {RunStatus.INTERRUPTED, RunStatus.FAILED, RunStatus.BLOCKED}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RecoveryWatchdog:
    def __init__(self, runner: LiveInvestigationService, ownership: ServerOwnership) -> None:
        self.runner = runner
        self.settings = runner.settings
        self.ownership = ownership
        self.task: asyncio.Task[None] | None = None
        self.stop = asyncio.Event()
        self.last_tick = time.monotonic()
        self.error: str | None = None
        self._decisions: dict[str, int] = {}

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop(), name="recovery-watchdog")

    async def close(self) -> None:
        self.stop.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def healthy(self) -> bool:
        return (
            self.error is None
            and self.task is not None
            and not self.task.done()
            and time.monotonic() - self.last_tick < max(60, self.settings.recovery_scan_seconds * 3)
        )

    async def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), self.settings.recovery_scan_seconds)
                return
            except TimeoutError:
                pass
            try:
                if not self.ownership.healthy():
                    self.error = "OWNERSHIP_LOST"
                    LOGGER.error("operations ownership lost; stopping owned runs")
                    await self.runner.shutdown()
                    return
                self.error = None
                await self.tick()
                self.last_tick = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = type(error).__name__
                LOGGER.error("operations scan failed (%s)", self.error)

    def _stalled(self, run: InvestigationRunRow, now: datetime) -> bool:
        if run.status not in _ACTIVE:
            return False
        heartbeat = run.owner_heartbeat_at or run.updated_at
        if (now - utc(heartbeat)).total_seconds() > self.settings.stall_heartbeat_seconds:
            return True
        if run.current_step_key:
            with self.runner.sessions() as session:
                step = session.scalar(
                    select(ExecutionStepRow)
                    .where(
                        ExecutionStepRow.run_id == run.run_id,
                        ExecutionStepRow.logical_step_key == run.current_step_key,
                    )
                    .order_by(ExecutionStepRow.attempt.desc())
                )
                if step is not None and step.started_at is not None:
                    return (
                        now - utc(step.started_at)
                    ).total_seconds() > self.settings.stall_step_seconds
        return False

    def _alert(self, run: InvestigationRunRow, code: str, message: str) -> None:
        identity = hashlib.sha256(f"{run.run_id}:{run.state_version}:{code}".encode()).hexdigest()
        event_id = "OPS-ALERT-" + identity
        with self.runner.sessions() as session:
            if session.get(AuditEventRow, event_id) is not None:
                return
        try:
            self.runner.repository.add(
                AuditEvent(
                    audit_event_id=event_id,
                    investigation_id=run.investigation_id,
                    run_id=run.run_id,
                    actor_type=AuditActorType.SYSTEM,
                    event_type="OPS_ALERT",
                    target_type="InvestigationRun",
                    target_id=run.run_id,
                    metadata={"state_version": run.state_version, "code": code, "message": message},
                    created_at=datetime.now(UTC),
                )
            )
        except IntegrityError:
            return
        LOGGER.warning(
            "operations_alert run_id=%s code=%s state_version=%s",
            run.run_id,
            code,
            run.state_version,
        )

    async def tick(self) -> None:
        now = datetime.now(UTC)
        with self.runner.sessions() as session:
            runs = session.scalars(
                select(InvestigationRunRow).where(
                    InvestigationRunRow.mode == RunMode.LIVE,
                    InvestigationRunRow.status.in_(_ACTIVE | _STOPPED),
                )
            ).all()
        for run in runs:
            if run.workflow_version in {
                "quant-backtest-readiness-v1",
                "quant-history-acquisition-v1",
            }:
                # Finished input admission is not a failed model execution.
                # It requires new historical data, not an automatic web retry.
                continue
            if run.status in _ACTIVE:
                # Offline recording uses LIVE mode with a historical synthetic clock.
                # It has a separate executor and must never be stopped by this service.
                if run.workflow_version != WORKFLOW_VERSION:
                    continue
                if self._stalled(run, now):
                    self._alert(run, "RUN_STALLED", "执行心跳或步骤进度超时，正在尝试安全停止。")
                    if not await self.runner.interrupt_stalled(run.run_id):
                        self._alert(
                            run,
                            "TASK_NOT_STOPPED",
                            "执行器未确认停止，已禁止并发接管；需要进程守护重启。",
                        )
                        self.error = "TASK_NOT_STOPPED"
                continue
            local_task = self.runner.tasks.get(run.run_id)
            if local_task is not None and not local_task.done():
                continue
            if self._decisions.get(run.run_id) == run.state_version:
                continue
            if run.status is RunStatus.BLOCKED:
                self._alert(
                    run, "RUN_BLOCKED", "调查已停止，请检查预算或证据缺口；不会自动追加费用额度。"
                )
                self._decisions[run.run_id] = run.state_version
                continue
            if not self.settings.auto_resume_enabled:
                self._alert(run, "AUTO_RESUME_DISABLED", "自动恢复已关闭，请手动检查并继续调查。")
                self._decisions[run.run_id] = run.state_version
                continue
            try:
                self.runner.resume(
                    run.run_id,
                    ResumeRequest(expected_state_version=run.state_version),
                    automatic=True,
                )
                LOGGER.info("automatic_resume run_id=%s", run.run_id)
            except RecoveryConflict as error:
                if error.code in {"STALE_RUN_STATE", "RUN_STILL_ACTIVE", "AUTO_RESUME_BACKOFF"}:
                    continue
                self._alert(run, error.code, str(error))
                self._decisions[run.run_id] = run.state_version
            except LiveNotConfiguredError:
                self._alert(
                    run, "LIVE_NOT_CONFIGURED", "自动恢复需要可用的模型配置，请检查服务配置。"
                )
                self._decisions[run.run_id] = run.state_version

    def status(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        alerts: list[dict[str, Any]] = []
        with self.runner.sessions() as session:
            events = session.scalars(
                select(AuditEventRow)
                .join(InvestigationRunRow, InvestigationRunRow.run_id == AuditEventRow.run_id)
                .where(
                    AuditEventRow.event_type == "OPS_ALERT",
                    InvestigationRunRow.status.in_(_ACTIVE | _STOPPED),
                    AuditEventRow.metadata_payload["state_version"].as_integer()
                    == InvestigationRunRow.state_version,
                )
                .order_by(AuditEventRow.created_at.desc())
                .limit(50)
            ).all()
            for event in events:
                run = session.get(InvestigationRunRow, event.run_id)
                if run is None or run.state_version != event.metadata_payload.get("state_version"):
                    continue
                if run.status not in _STOPPED and not self._stalled(run, now):
                    continue
                alerts.append(
                    {
                        "alert_id": event.audit_event_id,
                        "run_id": event.run_id,
                        "investigation_id": event.investigation_id,
                        **event.metadata_payload,
                        "created_at": event.created_at.isoformat(),
                    }
                )
                if len(alerts) >= 50:
                    break
        return {
            "healthy": self.healthy(),
            "auto_resume_enabled": self.settings.auto_resume_enabled,
            "max_auto_attempts": self.settings.auto_resume_max_attempts,
            "watchdog_error": self.error,
            "alerts": alerts,
        }
