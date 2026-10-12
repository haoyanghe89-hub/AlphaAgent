"""Persist actual quant execution boundaries; completion is not financial verification."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from marketpulse.investigation.domain.enums import AuditActorType
from marketpulse.investigation.persistence.models import AuditEventRow, InvestigationRunRow

STAGES = (
    ("planner", "Supervisor / Planner", "确认标的与数据范围"),
    ("data", "Data", "取数与冻结快照"),
    ("analyst", "Analyst", "确定性工具计算"),
    ("verifier", "Verifier", "证据完整性与复算核验"),
    ("writer", "Writer", "报告成稿"),
)


def process_events(session, run_id):
    return list(
        session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.run_id == run_id, AuditEventRow.event_type == "QUANT_STAGE")
            .order_by(AuditEventRow.created_at, AuditEventRow.audit_event_id)
        )
    )


def process_view(session, run_id):
    latest = {event.metadata_payload["stage"]: event for event in process_events(session, run_id)}
    return [
        dict(
            stage=key,
            role=role,
            title=title,
            status=latest[key].new_state if key in latest else "NOT_RECORDED",
            detail=latest[key].reason if key in latest else "尚无执行记录",
            updated_at=latest[key].created_at.isoformat() if key in latest else None,
        )
        for key, role, title in STAGES
    ]


def record_stage(sessions, run_id, stage, status, detail):
    with sessions.begin() as session:
        row = session.get(InvestigationRunRow, run_id)
        session.add(
            AuditEventRow(
                audit_event_id="QSTAGE-" + uuid4().hex,
                investigation_id=row.investigation_id,
                run_id=run_id,
                actor_type=AuditActorType.SYSTEM,
                event_type="QUANT_STAGE",
                target_type="Run",
                target_id=run_id,
                new_state=status,
                reason=detail,
                metadata_payload={"stage": stage},
                created_at=datetime.now(UTC),
            )
        )


def enter_stage(sessions, run_id, stage, detail):
    with sessions() as session:
        events = process_events(session, run_id)
    active = next((e for e in reversed(events) if e.new_state == "RUNNING"), None)
    if active:
        key = active.metadata_payload["stage"]
        last = next(e for e in reversed(events) if e.metadata_payload["stage"] == key)
        if last.new_state == "RUNNING":
            if key == stage:
                return
            record_stage(sessions, run_id, key, "COMPLETED", "该职责执行完成；不代表指标已证实。")
    record_stage(sessions, run_id, stage, "RUNNING", detail)


def stop_active(sessions, run_id, detail):
    with sessions() as session:
        nodes = process_view(session, run_id)
    for node in nodes:
        if node["status"] == "RUNNING":
            record_stage(sessions, run_id, node["stage"], "DEGRADED", detail)
