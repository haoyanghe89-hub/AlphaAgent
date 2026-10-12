"""Durable bounded market partitions, separate from strict package admission.

The local acquisition runtime has one owner. A server restart never silently
replays network calls: a new explicit request resumes only missing partitions.
"""

import asyncio
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field
from sqlalchemy import select, text

from marketpulse.infrastructure.storage.models import BlobError, BlobRef
from marketpulse.investigation.api import _error
from marketpulse.investigation.domain.enums import AuditActorType
from marketpulse.investigation.persistence.models import AuditEventRow

from ..contracts import canonical, digest
from ..domain import FrozenModel
from .acquisition import MAX_BYTES, MAX_CALLS, MAX_SECONDS
from .history import fetch_query
from .market_plan import GAPS, all_queries, build_plan, coverage, inspect_packet

router = APIRouter()


class MarketIn(FrozenModel):
    request_key: UUID
    batches: int = Field(default=1, ge=1, le=20, strict=True)


def owner(runtime):
    if not hasattr(runtime, "market_owner"):
        runtime.market_owner = uuid4().hex
    return runtime.market_owner


def event(session, parent, key, kind, job, payload):
    session.add(
        AuditEventRow(
            audit_event_id=key,
            investigation_id=parent["investigation_id"],
            run_id=parent["parent_run_id"],
            actor_type=AuditActorType.SYSTEM,
            event_type=kind,
            target_type="MarketAcquisition",
            target_id=job,
            metadata_payload=payload,
            created_at=datetime.now(UTC),
        )
    )


def read_blob(runtime, sha):
    raw = runtime.service.store.blobs.get_bytes(BlobRef(sha))
    value = json.loads(raw)
    if digest(value) != sha:
        raise ValueError("market evidence hash mismatch")
    return value


def frozen_plan(runtime, parent_id, *, create=False):
    from .api import load_check

    parent = load_check(runtime, parent_id)
    with runtime.service.sessions() as session:
        stored = session.get(AuditEventRow, "QM-PLAN-" + parent_id)
        if stored:
            plan = read_blob(runtime, stored.metadata_payload["plan_hash"])
            if plan["parent_hash"] != digest(parent) or plan["parent_run_id"] != parent_id:
                raise ValueError("market parent binding changed")
            return plan
    if not create:
        return None
    if parent["check_status"] != "COMPLETED" or not parent.get("historical_data"):
        raise _error(422, "MARKET_PARENT_REQUIRED", "请先完成历史日历与股票池采集。")
    packets = [read_blob(runtime, s["sha256"]) for s in parent["historical_data"]["sources"]]
    official = [
        r
        for source in parent.get("lifecycle_audit", {}).get("sources", [])
        for r in read_blob(runtime, source["packet_sha256"])["rows"]
    ]
    plan = build_plan(parent, packets, official)
    blob = runtime.service.store.blobs.put_bytes(canonical(plan).encode())
    with runtime.service.sessions.begin() as session:
        if session.bind.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        existing = session.get(AuditEventRow, "QM-PLAN-" + parent_id)
        if not existing:
            event(
                session,
                plan,
                "QM-PLAN-" + parent_id,
                "MARKET_PLAN",
                parent_id,
                dict(plan_hash=blob.ref.sha256),
            )
        elif existing.metadata_payload["plan_hash"] != blob.ref.sha256:
            raise ValueError("market plan creation conflict")
    return plan


def results_for(runtime, plan):
    with runtime.service.sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(
                AuditEventRow.target_type == "MarketAcquisition",
                AuditEventRow.target_id == plan["parent_run_id"],
                AuditEventRow.event_type == "MARKET_SOURCE",
            )
        ).all()
        metas = [e.metadata_payload for e in events]
    results = {}
    plan_hash = digest(plan)
    stocks = {s["code"]: s for s in plan["securities"]}
    for meta in metas:
        packet = read_blob(runtime, meta["packet_hash"])
        query = packet["query"]
        result = inspect_packet(packet, query, plan, stocks[query["code"]])
        if result != meta["result"] or meta["plan_hash"] != plan_hash:
            raise ValueError("market source binding mismatch")
        key = digest(query)
        if key in results:
            raise ValueError("duplicate market partition")
        results[key] = result
    return results


def batches_for(runtime, plan):
    with runtime.service.sessions() as session:
        rows = session.scalars(
            select(AuditEventRow)
            .where(
                AuditEventRow.target_type == "MarketAcquisition",
                AuditEventRow.target_id == plan["parent_run_id"],
                AuditEventRow.event_type == "MARKET_BATCH",
            )
            .order_by(AuditEventRow.created_at.desc())
        ).all()
        return [batch_payload(session, r) for r in rows]


def batch_payload(session, row):
    latest = session.scalars(
        select(AuditEventRow)
        .where(
            AuditEventRow.event_type == "MARKET_PROGRESS",
            AuditEventRow.target_id == row.audit_event_id,
        )
        .order_by(AuditEventRow.created_at.desc(), AuditEventRow.audit_event_id.desc())
        .limit(1)
    ).first()
    return dict((latest or row).metadata_payload)


def state(runtime, parent_id, offset=0, limit=50, *, export=False):
    plan = frozen_plan(runtime, parent_id)
    if not plan:
        return dict(exists=False, parent_run_id=parent_id)
    results = results_for(runtime, plan)
    batches = batches_for(runtime, plan)
    active = runtime.runner.tasks.get("MARKET-" + parent_id)
    latest = batches[0] if batches else None
    status = latest["status"] if latest else "PENDING"
    if status == "RUNNING" and (not active or active.done()) and latest["owner"] != owner(runtime):
        status = "INTERRUPTED"
    cov = coverage(plan, results, offset, len(plan["securities"]) if export else limit)
    payload = dict(
        exists=True,
        schema_version="market-observation-status-v1",
        parent_run_id=parent_id,
        plan_hash=digest(plan),
        start=plan["start"],
        end=plan["end"],
        market=plan["market"],
        acquisition_status=status,
        observation_complete=cov["frozen_count"] == cov["query_count"],
        admission_status="BLOCKED",
        strict_partition_count=0,
        gaps=GAPS,
        coverage=cov,
        budgets=dict(
            calls_per_batch=MAX_CALLS, seconds_per_batch=MAX_SECONDS, bytes_per_batch=MAX_BYTES
        ),
        batches=[{k: v for k, v in b.items() if k != "owner"} for b in batches[:20]],
    )
    if export:
        payload.update(plan=plan, partitions=list(results.values()))
    payload["result_hash"] = digest(payload)
    return payload


def prepare(runtime, parent_id, payload):
    plan = frozen_plan(runtime, parent_id, create=True)
    results = results_for(runtime, plan)
    key = "QM-REQUEST-" + str(payload.request_key)
    fingerprint = digest(dict(plan_hash=digest(plan), batches=payload.batches))
    with runtime.service.sessions.begin() as session:
        if session.bind.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        existing = session.get(AuditEventRow, key)
        if existing:
            if existing.metadata_payload["request_hash"] != fingerprint:
                raise _error(409, "MARKET_RETRY_CONFLICT", "重试标识已绑定另一范围或批次数。")
            return plan, None, existing.metadata_payload["batch_id"]
        # Serialize across every market parent in this single local runtime.
        rows = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "MARKET_BATCH")
        ).all()
        if any(
            b["status"] == "RUNNING" and b["owner"] == owner(runtime)
            for b in (batch_payload(session, r) for r in rows)
        ):
            raise _error(409, "MARKET_ACTIVE", "已有全市场采集运行，请等待或停止。")
        pending = [q for q in all_queries(plan) if digest(q) not in results][
            : MAX_CALLS * payload.batches
        ]
        if not pending:
            raise _error(409, "MARKET_OBSERVED", "公开源计划已采集；严格档案缺项仍须补齐。")
        batch_id = "QM-BATCH-" + uuid4().hex
        event(
            session,
            plan,
            key,
            "MARKET_REQUEST",
            parent_id,
            dict(request_hash=fingerprint, batch_id=batch_id),
        )
        event(
            session,
            plan,
            batch_id,
            "MARKET_BATCH",
            parent_id,
            dict(
                batch_id=batch_id,
                plan_hash=digest(plan),
                owner=owner(runtime),
                status="RUNNING",
                query_hash=digest(pending),
                planned=len(pending),
                calls=0,
                frozen=0,
                failures=[],
                max_batches=payload.batches,
                completed_batches=0,
                started_at=datetime.now(UTC).isoformat(),
                finished_at=None,
            ),
        )
    return plan, pending, batch_id


def update_batch(runtime, batch_id, **fields):
    with runtime.service.sessions.begin() as session:
        row = session.get(AuditEventRow, batch_id)
        # Audit rows are append-only by database trigger. Every progress update
        # is an additional event; old requests and source records never change.
        event(
            session,
            dict(parent_run_id=row.run_id, investigation_id=row.investigation_id),
            "QM-PROGRESS-" + uuid4().hex,
            "MARKET_PROGRESS",
            batch_id,
            {**batch_payload(session, row), **fields},
        )


async def collect(runtime, plan, pending, batch_id):
    calls = frozen = completed = 0
    failures, status = [], "COMPLETED"
    stocks = {s["code"]: s for s in plan["securities"]}
    plan_hash = digest(plan)
    try:
        for offset in range(0, len(pending), MAX_CALLS):
            started, byte_count, consecutive_errors = time.monotonic(), 0, 0
            async with asyncio.timeout(MAX_SECONDS):
                for query in pending[offset : offset + MAX_CALLS]:
                    calls += 1
                    update_batch(runtime, batch_id, calls=calls, current_query=query)
                    try:
                        packet = await fetch_query(
                            query, budget=min(45, MAX_SECONDS - (time.monotonic() - started))
                        )
                        result = inspect_packet(packet, query, plan, stocks[query["code"]])
                        raw = canonical(packet).encode()
                        byte_count += len(raw)
                        if byte_count > MAX_BYTES:
                            raise ValueError("market batch byte budget exhausted")
                        blob = runtime.service.store.blobs.put_bytes(raw)
                        with runtime.service.sessions.begin() as session:
                            event(
                                session,
                                plan,
                                "QM-SOURCE-" + digest([plan["parent_run_id"], query]),
                                "MARKET_SOURCE",
                                plan["parent_run_id"],
                                dict(
                                    packet_hash=blob.ref.sha256,
                                    plan_hash=plan_hash,
                                    result=result,
                                    batch_id=batch_id,
                                ),
                            )
                        frozen += 1
                        consecutive_errors = 0
                        update_batch(runtime, batch_id, frozen=frozen)
                    except (ValueError, OSError, RuntimeError, KeyError) as error:
                        failures.append(dict(query=query, reason=type(error).__name__))
                        consecutive_errors += 1
                        update_batch(runtime, batch_id, failures=failures)
                        if byte_count > MAX_BYTES or consecutive_errors >= 3:
                            raise RuntimeError(
                                "batch halted after budget or repeated source failure"
                            ) from error
            completed += 1
            update_batch(runtime, batch_id, completed_batches=completed)
            if offset + MAX_CALLS < len(pending):
                await asyncio.sleep(1)
        if failures:
            status = "PARTIAL"
    except asyncio.CancelledError:
        status = "CANCELLED"
    except Exception as error:
        status = "FAILED"
        failures.append(dict(reason=type(error).__name__))
    finally:
        update_batch(
            runtime,
            batch_id,
            status=status,
            calls=calls,
            frozen=frozen,
            failures=failures,
            completed_batches=completed,
            finished_at=datetime.now(UTC).isoformat(),
        )


def checked(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except (ValueError, KeyError, TypeError, OSError, BlobError) as error:
        raise _error(
            409, "MARKET_INTEGRITY", "全市场清单或源响应完整性未通过，已停止读取和续采。"
        ) from error


@router.get("/backtests/{run_id}/market")
def market_state(
    run_id: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    from ..api import _runtime

    return checked(state, _runtime(request), run_id, offset, limit)


@router.post("/backtests/{run_id}/market", status_code=202)
async def start_market(run_id: str, payload: MarketIn, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    owner(runtime)
    plan, pending, batch_id = await asyncio.to_thread(checked, prepare, runtime, run_id, payload)
    if pending is not None:
        task_key = "MARKET-" + run_id
        task = asyncio.create_task(collect(runtime, plan, pending, batch_id))
        runtime.runner.tasks[task_key] = task

        def cleanup(done):
            if runtime.runner.tasks.get(task_key) is done:
                runtime.runner.tasks.pop(task_key, None)

        task.add_done_callback(cleanup)
    return dict(parent_run_id=run_id, batch_id=batch_id, plan_hash=digest(plan))


@router.post("/backtests/{run_id}/market/stop")
async def stop_market(run_id: str, request: Request):
    from ..api import _runtime

    runtime = _runtime(request)
    task = runtime.runner.tasks.get("MARKET-" + run_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Cancel can land before collect reaches its try/finally.
            plan = checked(frozen_plan, runtime, run_id)
            latest = batches_for(runtime, plan)[0]
            if latest["status"] == "RUNNING":
                update_batch(
                    runtime,
                    latest["batch_id"],
                    status="CANCELLED",
                    finished_at=datetime.now(UTC).isoformat(),
                )
    return await asyncio.to_thread(checked, state, runtime, run_id)


@router.get("/backtests/{run_id}/market/export")
def export_market(run_id: str, request: Request):
    from ..api import _runtime

    result = checked(state, _runtime(request), run_id, export=True)
    return Response(
        json.dumps(result, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="market-observations.json"'},
    )
