"""Durable bounded history acquisition, separate from numerical backtesting."""

import asyncio
import copy
import json
import time
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, text

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.investigation.api import _error
from marketpulse.investigation.domain.enums import AuditActorType, RunMode, RunStatus, WorkflowPhase
from marketpulse.investigation.domain.runtime import InvestigationRun, RunBudget
from marketpulse.investigation.persistence.models import (
    AuditEventRow,
    InvestigationRunRow,
    RunBudgetRow,
)

from ..contracts import canonical, digest
from ..data.statement_official import fetch_statement
from .benchmark import fetch_index, inspect_benchmark
from .filings import inspect_filings
from .history import annual_windows, fetch_query, query_segments, summarize
from .inputs import input_queries, inspect_inputs
from .lifecycles import fetch_lifecycles, inspect_lifecycles
from .notices import fetch_notice, inspect_notices, target_for_parent

WORKFLOW = "quant-history-acquisition-v1"
MAX_CALLS = 24
MAX_SECONDS = 180
MAX_BYTES = 16 * 1024 * 1024


def audit(session, run, event_id, event_type, metadata):
    session.add(
        AuditEventRow(
            audit_event_id=event_id,
            investigation_id=run.investigation_id,
            run_id=run.run_id,
            actor_type=AuditActorType.SYSTEM,
            event_type=event_type,
            target_type="Run",
            target_id=run.run_id,
            metadata_payload=metadata,
            created_at=datetime.now(UTC),
        )
    )


def prepare(
    runtime,
    parent_id,
    request_key,
    *,
    input_target=None,
    filing_target=None,
    benchmark_target=None,
    lifecycle_target=None,
    notice_target=None,
):
    from .api import load_check

    parent = load_check(runtime, parent_id)
    if parent["check_status"] in {"RUNNING", "INTERRUPTED"}:
        raise _error(
            409, "HISTORY_PARENT_ACTIVE", "请先结束当前采集；中断后可从原检查报告重新获取。"
        )
    now = datetime.now(UTC)
    fingerprint = digest({"parent": parent_id, "conditions": parent["conditions"]})
    if input_target:
        fingerprint = digest(
            {"parent": parent_id, "conditions": parent["conditions"], "input_target": input_target}
        )
    if filing_target:
        fingerprint = digest(
            {
                "parent": parent_id,
                "conditions": parent["conditions"],
                "filing_target": filing_target,
            }
        )
    if benchmark_target:
        fingerprint = digest(
            {
                "parent": parent_id,
                "conditions": parent["conditions"],
                "benchmark_target": benchmark_target,
            }
        )
    if lifecycle_target:
        fingerprint = digest(
            {
                "parent": parent_id,
                "conditions": parent["conditions"],
                "lifecycle_target": lifecycle_target,
            }
        )
    if notice_target:
        fingerprint = digest(
            {
                "parent": parent_id,
                "conditions": parent["conditions"],
                "notice_target": notice_target,
            }
        )
    with runtime.service.sessions.begin() as session:
        if session.bind.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        existing = session.get(AuditEventRow, "QH-REQ-" + str(request_key))
        if existing:
            if existing.metadata_payload["request_hash"] != fingerprint:
                raise _error(409, "HISTORY_RETRY_CONFLICT", "重试标识已绑定另一份检查报告。")
            return existing.run_id, False
        active = session.scalars(
            select(InvestigationRunRow).where(
                InvestigationRunRow.investigation_id == parent["investigation_id"],
                InvestigationRunRow.workflow_version == WORKFLOW,
                InvestigationRunRow.status == RunStatus.RUNNING,
            )
        ).all()
        if any(
            r.run_id in runtime.runner.tasks and not runtime.runner.tasks[r.run_id].done()
            for r in active
        ):
            raise _error(409, "HISTORY_ACTIVE", "该报告已有历史采集任务，请等待或停止。")
        run_id = "RUN-HIST-" + uuid4().hex[:16]
        run = InvestigationRun(
            run_id=run_id,
            investigation_id=parent["investigation_id"],
            mode=RunMode.LIVE,
            status=RunStatus.RUNNING,
            current_phase=WorkflowPhase.COLLECT,
            checkpoint_version=0,
            state_version=0,
            workflow_version=WORKFLOW,
            origin_run_id=parent_id,
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        runtime.runner.repository.add_in_session(session, run)
        runtime.runner.repository.add_in_session(
            session,
            RunBudget(
                run_id=run_id,
                max_research_rounds=0,
                max_search_calls=0,
                max_fetch_calls=MAX_CALLS,
                max_model_calls=0,
                max_tokens=0,
                max_wall_time_ms=MAX_SECONDS * 1000,
                max_sources=MAX_CALLS,
                updated_at=now,
            ),
        )
        template = copy.deepcopy(parent)
        template.update(
            run_id=run_id,
            checked_at=now.isoformat(),
            check_status="RUNNING",
            parent_run_id=parent_id,
        )
        template["meta"].update(
            task_id=run_id,
            question=f"策略回测近 {parent['conditions']['years']} 年 vs 沪深300 · 历史数据采集",
        )
        if not any(
            (input_target, filing_target, benchmark_target, lifecycle_target, notice_target)
        ):
            template.pop("historical_data", None)
        elif input_target:
            template["meta"]["question"] = (
                f"{input_target['name']} · {input_target['signal_year']} 年信号 · 历史输入核验"
            )
        if not any((filing_target, benchmark_target, lifecycle_target, notice_target)):
            template.pop("input_audit", None)
        elif filing_target:
            template["meta"]["question"] = f"{filing_target['scope']['name']} · 官方财报口径核对"
        if benchmark_target:
            template["meta"]["question"] = "沪深300 · 官方价格及全收益基准核对"
        elif not lifecycle_target and not notice_target:
            template.pop("filing_audit", None)
        if lifecycle_target:
            template["meta"]["question"] = "沪深交易所 · 免费生命周期档案核对"
        elif not notice_target:
            template.pop("benchmark_audit", None)
        if not notice_target:
            template.pop("lifecycle_audit", None)
        template.pop("notice_audit", None)
        if notice_target:
            template["meta"]["question"] = "历史证券改码 · 官方公告取证"
        template.pop("acquisition", None)
        template_blob = runtime.service.store.blobs.put_bytes(canonical(template).encode())
        audit(
            session,
            run,
            "QH-REQ-" + str(request_key),
            "HISTORY_REQUEST",
            {"request_hash": fingerprint},
        )
        audit(
            session,
            run,
            "QPLAN-" + run_id,
            "QUANT_FROZEN_PLAN",
            {
                **parent["conditions"],
                "scenario": "backtest",
                "question": template["meta"]["question"],
            },
        )
        audit(
            session,
            run,
            "QH-BASE-" + run_id,
            "HISTORY_INPUT_BINDING",
            {
                "payload": template,
                "parent_hash": digest(parent),
                "sha256": template_blob.ref.sha256,
                "blob_ref": template_blob.ref.uri,
                "normalizer_version": "history-data-v2",
                **(
                    {
                        "input_target_ref": runtime.service.store.blobs.put_bytes(
                            canonical(input_target).encode()
                        ).ref.uri
                    }
                    if input_target
                    else {}
                ),
                **(
                    {
                        "filing_target_ref": runtime.service.store.blobs.put_bytes(
                            canonical(filing_target).encode()
                        ).ref.uri
                    }
                    if filing_target
                    else {}
                ),
                **(
                    {
                        "benchmark_target_ref": runtime.service.store.blobs.put_bytes(
                            canonical(benchmark_target).encode()
                        ).ref.uri
                    }
                    if benchmark_target
                    else {}
                ),
                **(
                    {
                        "lifecycle_target_ref": runtime.service.store.blobs.put_bytes(
                            canonical(lifecycle_target).encode()
                        ).ref.uri
                    }
                    if lifecycle_target
                    else {}
                ),
                **(
                    {
                        "notice_target_ref": runtime.service.store.blobs.put_bytes(
                            canonical(notice_target).encode()
                        ).ref.uri
                    }
                    if notice_target
                    else {}
                ),
            },
        )
    return run_id, True


def packets_for(session, store, run_id):
    events = session.scalars(
        select(AuditEventRow)
        .where(AuditEventRow.run_id == run_id, AuditEventRow.event_type == "HISTORY_SOURCE")
        .order_by(AuditEventRow.audit_event_id)
    ).all()
    packets = []
    for event in events:
        meta = event.metadata_payload
        ref = BlobRef(meta["sha256"])
        if meta["blob_ref"] != ref.uri:
            raise ValueError("history locator drift")
        store.blobs.verify_hash(ref)
        packet = json.loads(store.blobs.get_bytes(ref))
        if (
            digest(packet) != meta["sha256"]
            or packet["query"] != meta["query"]
            or len(packet["rows"]) != meta["row_count"]
            or packet["retrieved_at"] != meta["retrieved_at"]
            or packet["provider"] != meta["provider"]
        ):
            raise ValueError("history packet drift")
        packets.append(packet)
    return packets


def progress(session, run_id):
    events = session.scalars(
        select(AuditEventRow)
        .where(
            AuditEventRow.run_id == run_id,
            AuditEventRow.event_type.in_(["HISTORY_CALL", "HISTORY_SOURCE"]),
        )
        .order_by(AuditEventRow.created_at)
    ).all()
    calls = [e.metadata_payload["query"] for e in events if e.event_type == "HISTORY_CALL"]
    sources = [e.metadata_payload for e in events if e.event_type == "HISTORY_SOURCE"]
    return dict(
        calls_started=len(calls),
        sources_frozen=len(sources),
        max_calls=MAX_CALLS,
        max_seconds=MAX_SECONDS,
        current_query=calls[-1] if calls else None,
        sources=[{k: v for k, v in s.items() if k != "blob_ref"} for s in sources],
    )


async def acquire(runtime, run_id):
    store = runtime.service.store
    started = time.monotonic()
    packets, failure, outcome = [], None, "COMPLETED"
    with store.sessions() as session:
        base = session.get(AuditEventRow, "QH-BASE-" + run_id).metadata_payload
        template = base["payload"]
        target = (
            json.loads(store.blobs.get_bytes(BlobRef.from_uri(base["input_target_ref"])))
            if base.get("input_target_ref")
            else None
        )
        filing = (
            json.loads(store.blobs.get_bytes(BlobRef.from_uri(base["filing_target_ref"])))
            if base.get("filing_target_ref")
            else None
        )
        benchmark = (
            json.loads(store.blobs.get_bytes(BlobRef.from_uri(base["benchmark_target_ref"])))
            if base.get("benchmark_target_ref")
            else None
        )
        lifecycle = (
            json.loads(store.blobs.get_bytes(BlobRef.from_uri(base["lifecycle_target_ref"])))
            if base.get("lifecycle_target_ref")
            else None
        )
        notice = (
            json.loads(store.blobs.get_bytes(BlobRef.from_uri(base["notice_target_ref"])))
            if base.get("notice_target_ref")
            else None
        )
    start, end = (
        date.fromisoformat(template["meta"]["date_start"]),
        date.fromisoformat(template["meta"]["date_end"]),
    )

    async def fetch(query):
        with store.sessions.begin() as session:
            run = session.get(InvestigationRunRow, run_id)
            budget = session.get(RunBudgetRow, run_id)
            if budget.fetch_calls_used >= MAX_CALLS:
                raise ValueError("history call budget exhausted")
            budget.fetch_calls_used += 1
            ordinal = budget.fetch_calls_used
            audit(session, run, f"QH-CALL-{run_id}-{ordinal:02}", "HISTORY_CALL", {"query": query})
        packet = (
            await fetch_notice(query, store)
            if notice
            else await fetch_lifecycles(query, store)
            if lifecycle
            else await fetch_index(query, store)
            if benchmark
            else await fetch_statement(query["profile_id"], store)
            if filing
            else await fetch_query(
                query,
                budget=min(
                    75 if query["kind"] == "basic" else 45,
                    max(0.1, MAX_SECONDS - (time.monotonic() - started)),
                ),
            )
        )
        # Validate before persisting, not only at report assembly.
        if notice:
            inspect_notices([*packets, packet], notice, store)
        elif lifecycle:
            inspect_lifecycles([*packets, packet], lifecycle, store)
        elif benchmark:
            inspect_benchmark([*packets, packet], benchmark, store)
        elif filing:
            inspect_filings([*packets, packet], filing, store)
        elif target:
            inspect_inputs([*packets, packet], target)
        else:
            summarize([*packets, packet], start, end)
        if (
            sum(len(canonical(p).encode()) + p.get("document_size", 0) for p in [*packets, packet])
            > MAX_BYTES
        ):
            raise ValueError("history aggregate output budget exhausted")
        blob = store.blobs.put_bytes(canonical(packet).encode())
        with store.sessions.begin() as session:
            run = session.get(InvestigationRunRow, run_id)
            budget = session.get(RunBudgetRow, run_id)
            budget.sources_used += 1
            audit(
                session,
                run,
                f"QH-SOURCE-{run_id}-{ordinal:02}",
                "HISTORY_SOURCE",
                dict(
                    query=query,
                    sha256=blob.ref.sha256,
                    blob_ref=blob.ref.uri,
                    row_count=len(packet["rows"]),
                    retrieved_at=packet["retrieved_at"],
                    provider=packet["provider"],
                ),
            )
        packets.append(packet)

    try:
        async with asyncio.timeout(MAX_SECONDS):
            if notice:
                for profile_id in notice["profile_ids"]:
                    await fetch(dict(kind="official_notice", profile_id=profile_id))
            elif lifecycle:
                for query in lifecycle["queries"]:
                    await fetch(query)
            elif benchmark:
                for query in benchmark["queries"]:
                    await fetch(query)
            elif filing:
                for profile_id in filing["profile_ids"]:
                    await fetch(dict(kind="official_filing", profile_id=profile_id))
            elif target:
                for query in input_queries(target):
                    await fetch(query)
            else:
                for first, last in query_segments(start - timedelta(days=31), end):
                    await fetch(dict(kind="calendar", start=str(first), end=str(last)))
                await fetch(dict(kind="basic"))
                sessions = sorted(
                    {
                        r["calendar_date"]
                        for p in packets
                        if p["query"]["kind"] == "calendar"
                        for r in p["rows"]
                        if r["is_trading_day"] == "1"
                    }
                )
                for window in annual_windows(sessions, start, end):
                    await fetch(dict(kind="members", day=window["signal_day"]))
                for first, last in query_segments(start, end):
                    await fetch(dict(kind="benchmark", start=str(first), end=str(last)))
    except asyncio.CancelledError:
        outcome, failure = "CANCELLED", "已停止采集；仅保留停止前真实冻结的输入。"
    except Exception as error:
        outcome, failure = "FAILED", type(error).__name__
    with store.sessions.begin() as session:
        run = session.get(InvestigationRunRow, run_id)
        report = copy.deepcopy(template)
        report.update(check_status=outcome, checked_at=datetime.now(UTC).isoformat())
        report[
            "notice_audit"
            if notice
            else "lifecycle_audit"
            if lifecycle
            else "benchmark_audit"
            if benchmark
            else "filing_audit"
            if filing
            else "input_audit"
            if target
            else "historical_data"
        ] = (
            inspect_notices(packets, notice, store)
            if notice
            else inspect_lifecycles(packets, lifecycle, store)
            if lifecycle
            else inspect_benchmark(packets, benchmark, store)
            if benchmark
            else inspect_filings(packets, filing, store)
            if filing
            else inspect_inputs(packets, target)
            if target
            else summarize(packets, start, end)
        )
        report["acquisition"] = {**progress(session, run_id), "error": failure}
        report["meta"]["universe"] = (
            "已冻结沪深A股历史名单；完整性与财报版本未准入"
            if report["historical_data"]["universes"]
            else "历史股票池尚未准入"
        )
        for check in report["data_check"]["checks"]:
            if filing and check["key"] == "financial_pit" and report["filing_audit"]["documents"]:
                check.update(
                    status="PARTIAL",
                    detail="已定位三期中的官方财报原文并核对口径；最初及修订档案、准确可得时间仍未准入，不能执行选股。",
                )
            if target and check["key"] in {
                "financial_pit",
                "raw_price_coverage",
                "corporate_actions",
                "execution_rules",
            }:
                key = check["key"]
                a = report["input_audit"]
                if (
                    (key == "financial_pit" and a["financials"]["observations"])
                    or (key == "corporate_actions" and a["actions"]["events"])
                    or (
                        key in {"raw_price_coverage", "execution_rules"}
                        and a["prices"]["row_count"]
                    )
                ):
                    check.update(
                        status="PARTIAL",
                        detail=f"新增{target['name']}单股历史核验，详情见下方；并未取得全市场覆盖、严格财报版本或完整可成交证明。",
                    )
            if check["key"] == "historical_universe" and report["historical_data"]["universes"]:
                check.update(
                    status="PARTIAL",
                    detail="已冻结按年度信号日查询的历史名单与生命周期；未知成员、历史版本及独立完整性核对仍未准入。",
                )
            if check["key"] == "benchmark" and report["historical_data"]["benchmark"]["row_count"]:
                check.update(
                    status="PARTIAL",
                    detail="已冻结沪深300价格指数；覆盖检查见下表，尚未与独立权威来源交叉核对，不是全收益指数。",
                )
        report["process"][1].update(
            status="COMPLETED" if outcome == "COMPLETED" else outcome,
            detail=f"实际冻结 {len(packets)} 份历史数据响应；采集状态 {outcome}。",
        )
        if filing:
            report["process"][2].update(
                status="COMPLETED" if outcome == "COMPLETED" else "NOT_EXECUTED",
                detail="已按官方归母累计口径复算研究用TTM及营收同比；不代表信号日可得，不执行选股。"
                if outcome == "COMPLETED"
                else "原文核验未完成，未形成完整财务复算。",
            )
            report["process"][3].update(
                status="BLOCKED",
                detail="逐项核对保留MATCH／口径差异／缺基准；版本和成交准入仍受阻，未升级VERIFIED。",
            )
        if benchmark:
            report["process"][2].update(
                status="COMPLETED" if outcome == "COMPLETED" else "NOT_EXECUTED",
                detail="已核对官方价格基准并冻结独立全收益序列；未执行策略计算。",
            )
            report["process"][3].update(
                status="BLOCKED", detail="仅价格基准逐日核对，其他全市场及历史版本准入仍须完成。"
            )
            for check in report["data_check"]["checks"]:
                if check["key"] == "benchmark":
                    check.update(
                        status="PARTIAL",
                        detail="新增官方价格／全收益基准核对，逐日一致、冲突和缺失详见下表；整体未VERIFIED。",
                    )
        if lifecycle:
            report["process"][2].update(
                status="COMPLETED" if outcome == "COMPLETED" else "NOT_EXECUTED",
                detail="对比官方生命周期回溯观察及父年度名单；差异保留，未执行选股。",
            )
            report["process"][3].update(
                status="BLOCKED", detail="当前下载目录不证明当年完整修订链，整体仍未准入。"
            )
            for check in report["data_check"]["checks"]:
                if check["key"] == "historical_universe":
                    check.update(
                        status="PARTIAL",
                        detail="新增免费交易所生命周期原文及逐年名单差异；尚非完整PIT档案。",
                    )
        if notice:
            report["process"][2].update(
                status="COMPLETED" if outcome == "COMPLETED" else "NOT_EXECUTED",
                detail="定位发行人改码与国证调整公告，核对生效日；保留原始名单差异。",
            )
            report["process"][3].update(
                status="BLOCKED", detail="单项代码延续证据不能替代全市场完整历史档案。"
            )
        blob = store.blobs.put_bytes(canonical(report).encode())
        audit(
            session,
            run,
            "QBT-" + run_id,
            "BACKTEST_INPUT_CHECK",
            {"payload": report, "sha256": blob.ref.sha256, "blob_ref": blob.ref.uri},
        )
        run.status = RunStatus.CANCELLED if outcome == "CANCELLED" else RunStatus.FAILED
        run.current_phase = WorkflowPhase.REPORT
        run.completed_at = run.updated_at = datetime.now(UTC)
        run.state_version += 1
        run.interruption_reason = "历史采集已结束；财报与交易输入尚未准入，不执行回测。"
        session.get(RunBudgetRow, run_id).consumed_wall_time_ms = int(
            (time.monotonic() - started) * 1000
        )


def read_history(runtime, run, *, session=None, _snapshot_cache=None):
    store = runtime.service.store
    with nullcontext(session) if session is not None else store.sessions() as session:
        base = session.get(AuditEventRow, "QH-BASE-" + run.run_id).metadata_payload
        ref = BlobRef(base["sha256"])
        if digest(base["payload"]) != ref.sha256 or base["blob_ref"] != ref.uri:
            raise ValueError("historical plan binding drift")
        store.blobs.verify_hash(ref)
        from .api import load_check

        parent = load_check(
            runtime, run.origin_run_id, session=session, _snapshot_cache=_snapshot_cache
        )
        if digest(parent) != base["parent_hash"]:
            raise ValueError("history parent drift")
        packets = packets_for(session, store, run.run_id)
        if base.get("notice_target_ref"):
            ref = BlobRef.from_uri(base["notice_target_ref"])
            target = json.loads(store.blobs.get_bytes(ref))
            if digest(target) != ref.sha256 or target != target_for_parent(parent):
                raise ValueError("official notice target drift")
            return (
                base["payload"],
                inspect_notices(packets, target, store),
                progress(session, run.run_id),
            )
        if base.get("lifecycle_target_ref"):
            ref = BlobRef.from_uri(base["lifecycle_target_ref"])
            target = json.loads(store.blobs.get_bytes(ref))
            if digest(target) != ref.sha256:
                raise ValueError("official lifecycle target drift")
            return (
                base["payload"],
                inspect_lifecycles(packets, target, store),
                progress(session, run.run_id),
            )
        if base.get("benchmark_target_ref"):
            ref = BlobRef.from_uri(base["benchmark_target_ref"])
            target = json.loads(store.blobs.get_bytes(ref))
            if digest(target) != ref.sha256:
                raise ValueError("official benchmark target drift")
            return (
                base["payload"],
                inspect_benchmark(packets, target, store),
                progress(session, run.run_id),
            )
        if base.get("filing_target_ref"):
            ref = BlobRef.from_uri(base["filing_target_ref"])
            filing = json.loads(store.blobs.get_bytes(ref))
            if digest(filing) != ref.sha256:
                raise ValueError("official filing target drift")
            return (
                base["payload"],
                inspect_filings(packets, filing, store),
                progress(session, run.run_id),
            )
        if base.get("input_target_ref"):
            from ..storage.models import InstrumentRow

            ref = BlobRef.from_uri(base["input_target_ref"])
            target = json.loads(store.blobs.get_bytes(ref))
            if (
                digest(target) != ref.sha256
                or digest(session.get(InstrumentRow, target["instrument_id"]).definition)
                != target["instrument_hash"]
            ):
                raise ValueError("historical input identity drift")
            return base["payload"], inspect_inputs(packets, target), progress(session, run.run_id)
        summary = summarize(
            packets,
            date.fromisoformat(parent["meta"]["date_start"]),
            date.fromisoformat(parent["meta"]["date_end"]),
            version=base.get("normalizer_version", "history-data-v1"),
        )
        return base["payload"], summary, progress(session, run.run_id)


def input_target(runtime, parent_id, instrument_id, signal_year):
    from ..data.instruments import InstrumentMaster
    from ..domain import Instrument
    from ..storage.models import InstrumentRow
    from .api import load_check

    parent = load_check(runtime, parent_id)
    if parent["check_status"] != "COMPLETED" or not parent.get("historical_data"):
        raise _error(422, "INPUT_PARENT_INCOMPLETE", "请先完成历史名单与交易日历采集。")
    if instrument_id not in {i["instrument_id"] for i in parent["data_check"]["instruments"]}:
        raise _error(422, "INPUT_INSTRUMENT_SCOPE", "请选择父报告目录中的已接入证券。")
    windows = [w for w in parent["historical_data"]["annual"] if w["year"] == signal_year]
    if len(windows) != 1:
        raise _error(422, "INPUT_YEAR_SCOPE", "请选择父报告中的年度信号。")
    with runtime.service.sessions() as session:
        definition = session.get(InstrumentRow, instrument_id).definition
    item = Instrument.model_validate(definition)
    master = InstrumentMaster((item,))
    start, end = parent["meta"]["date_start"], parent["meta"]["date_end"]
    alias = master.alias(instrument_id, date.fromisoformat(start))
    if alias != master.alias(instrument_id, date.fromisoformat(end)) or alias.exchange not in {
        "XSHG",
        "XSHE",
    }:
        raise _error(422, "INPUT_ALIAS_SCOPE", "该区间代码生命周期不能唯一对应，已停止取数。")
    sessions, hashes = set(), []
    for source in parent["historical_data"]["sources"]:
        if source["query"]["kind"] != "calendar":
            continue
        packet = json.loads(runtime.service.store.blobs.get_bytes(BlobRef(source["sha256"])))
        if digest(packet) != source["sha256"]:
            raise ValueError("calendar binding mismatch")
        hashes.append(source["sha256"])
        sessions.update(
            r["calendar_date"]
            for r in packet["rows"]
            if r["is_trading_day"] == "1" and start <= r["calendar_date"] <= end
        )
    if not sessions:
        raise _error(422, "INPUT_CALENDAR_SCOPE", "父报告缺少可用的冻结交易日历。")
    return dict(
        instrument_id=instrument_id,
        instrument_hash=digest(definition),
        name=item.name,
        provider_code=("sh." if alias.exchange == "XSHG" else "sz.") + alias.code,
        start=start,
        end=end,
        signal_year=signal_year,
        signal_day=windows[0]["signal_day"],
        execution_day=windows[0]["execution_day"],
        expected_sessions=sorted(sessions),
        calendar_hashes=sorted(hashes),
    )
