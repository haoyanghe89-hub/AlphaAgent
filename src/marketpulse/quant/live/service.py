"""Supervisor for one shared, versioned, real-model quantitative workflow."""

import asyncio
import hashlib
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..domain import DcfAssumptions
from . import sources
from .agents import (
    Analysis,
    DataPlan,
    Plan,
    RoleRunner,
    Verification,
    Writing,
    numeric_bindings,
    validate_analysis,
)
from .calculate import dcf_result, simulate
from .evidence import announcements, facts, retrieve
from .report import build
from .securities import resolve

ROLES = ("Planner", "Data", "Analyst", "Verifier", "Writer")
LABELS = (
    "解析问题与研究范围",
    "选择并调用数据、计算及检索工具",
    "基于证据分析",
    "独立复核推断与引用",
    "组织通过复核的报告",
)


def implementation_hash():
    root = Path(__file__).resolve().parent
    files = sorted(root.glob("*.py")) + sorted((root.parent / "compute").glob("*.py"))
    lock = Path(__file__).resolve().parents[4] / "uv.lock"
    if lock.is_file():
        files.append(lock)
    return hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in files)).hexdigest()


def process():
    return [dict(role=role, detail=LABELS[i], status="PENDING") for i, role in enumerate(ROLES)]


class ResearchWorkflow:
    def __init__(self, store, settings, model_port=None):
        self.store, self.settings, self.model_port = store, settings, model_port

    async def run(self, run_id):
        state = self.store.get(run_id, with_data=False)
        cancelled = threading.Event()

        def save():
            if cancelled.is_set() and state["status"] == "RUNNING":
                raise InterruptedError("研究已中断")
            self.store.put(state)

        def stage(index):
            for i, node in enumerate(state["process"]):
                node["status"] = (
                    "COMPLETED" if i < index else "RUNNING" if i == index else "PENDING"
                )
            state["updated_at"] = datetime.now(UTC).isoformat()
            save()

        async def tool(name, arguments, operation, *, optional=False):
            request_ref = self.store.blob(dict(name=name, arguments=arguments))
            for call in reversed(state.get("calls", [])):
                if (
                    call["kind"] == "tool"
                    and call["request_ref"] == request_ref
                    and call["status"] == "COMPLETED"
                ):
                    return self.store.value(call["response_ref"])
            call = dict(
                kind="tool",
                role="Data",
                name=name,
                request_ref=request_ref,
                status="RUNNING",
                started_at=datetime.now(UTC).isoformat(),
            )
            state.setdefault("calls", []).append(call)
            save()
            try:
                result = await asyncio.to_thread(operation)
                call.update(
                    status="COMPLETED",
                    response_ref=self.store.blob(result),
                    completed_at=datetime.now(UTC).isoformat(),
                )
                save()
                return result
            except Exception as error:
                call.update(
                    status="FAILED",
                    error=type(error).__name__,
                    completed_at=datetime.now(UTC).isoformat(),
                )
                save()
                if optional:
                    return dict(documents=[], sources=[], error=type(error).__name__)
                raise

        raw_refs = state.setdefault("raw_refs", {})

        def raw_save(key, raw):
            if cancelled.is_set():
                raise InterruptedError("研究已中断")
            if key not in raw_refs:
                total = state.get("raw_bytes", 0) + len(raw)
                if len(raw_refs) >= 24 or total > 16 * 1024 * 1024:
                    raise ValueError("本次研究达到24份响应/16MiB采集预算")
                state["raw_bytes"] = total
            raw_refs[key] = self.store.raw(raw)

        def bind(source_list):
            return [dict(s, blob_ref=raw_refs[s["key"]]) for s in source_list]

        try:
            fingerprint = implementation_hash()
            if state.get("implementation_hash", fingerprint) != fingerprint:
                raise ValueError("执行环境已变化；请新建研究，不能混合旧步骤继续执行。")
            state["implementation_hash"] = fingerprint
            state.update(status="RUNNING", error=None)
            stage(0)
            parent = self.store.get(state["parent_run_id"]) if state.get("parent_run_id") else None
            if parent and parent["status"] != "COMPLETED":
                raise ValueError("父研究尚未完成，不能建立追问上下文")
            parent_data = (parent or {}).get("data")
            memory = (
                dict(
                    subject=parent_data["subject"],
                    previous_question=parent["request"].get("question"),
                    prior_focus=parent_data.get("agent_report", {}).get("focus"),
                    parameters=dict(
                        dcf=parent_data.get("dcf", {}).get("assumptions"),
                        fast=parent_data.get("simulation", {}).get("fast", 20),
                        slow=parent_data.get("simulation", {}).get("slow", 60),
                        cost_pct=parent_data.get("simulation", {}).get("cost_pct", 0.13),
                    ),
                    boundary="父任务只提供研究上下文；本轮重新取数，不沿用历史数值",
                )
                if parent_data
                else None
            )
            async with RoleRunner(
                self.settings, self.store, state, save, self.model_port
            ) as runner:
                plan = await runner.run(
                    "Planner",
                    Plan,
                    dict(
                        question=state["request"]["question"],
                        explicit_code=state["request"].get("code"),
                        memory=memory,
                    ),
                )
                state["plan"] = plan.model_dump()
                if plan.scope != "single":
                    state.update(
                        status="NEEDS_INPUT" if plan.scope == "clarify" else "UNSUPPORTED",
                        error=plan.reason,
                    )
                    state["process"][0]["status"] = state["status"]
                    save()
                    return
                query = state["request"].get("code") or plan.entity
                if not query and parent_data:
                    query = parent_data["subject"]["code"]
                resolved = await tool(
                    "resolve_security", dict(query=query), lambda: resolve(query, raw_save)
                )
                subject, identity_sources = resolved
                if state["request"].get("code") and subject["code"] != state["request"]["code"]:
                    raise ValueError("显式股票代码与解析结果不一致")
                state.update(
                    subject=subject,
                    title=subject["name"] + " · " + state["request"]["question"][:60],
                )
                stage(1)

                def guard_data(output):
                    if set(output.tools) != {
                        "collect_market_data",
                        "calculate_metrics",
                        "retrieve_disclosures",
                    }:
                        raise ValueError("缺少必要工具或重复工具")

                await runner.run(
                    "Data", DataPlan, dict(plan=state["plan"], subject=subject), guard_data
                )
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                window = state.setdefault(
                    "window",
                    dict(
                        start=(now.date() - timedelta(days=850)).isoformat(),
                        end=(
                            now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
                        ).isoformat(),
                        asof=now.date().isoformat(),
                    ),
                )

                def collect():
                    def progress(count):
                        state["sources_received"] = count
                        save()

                    values, meta = sources.collect(
                        window["start"], window["end"], raw_save, progress, subject
                    )
                    return dict(values=values, sources=bind(meta))

                packet = await tool("collect_market_data", dict(subject=subject, **window), collect)

                def calculate():
                    result = build(packet["values"], window["end"], window["asof"], subject)
                    if memory and subject["code"] == memory["subject"]["code"]:
                        parameters = memory["parameters"]
                        if parameters["dcf"] and "basis" in result["dcf"]:
                            result["dcf"] = dcf_result(
                                result["dcf"], DcfAssumptions.model_validate(parameters["dcf"])
                            )
                        inputs = result["calculation_inputs"]
                        result["simulation"] = simulate(
                            inputs["prices"],
                            inputs["benchmark"],
                            parameters["cost_pct"] / 100,
                            parameters["fast"],
                            parameters["slow"],
                        )
                    return result

                data = await tool(
                    "calculate_metrics",
                    dict(
                        packet_ref=self.store.blob(packet),
                        asof=window["asof"],
                        subject=subject,
                        kernel=fingerprint,
                        parameters=memory.get("parameters") if memory else None,
                    ),
                    calculate,
                )

                def docs():
                    documents, meta = announcements(subject, raw_save)
                    return dict(documents=documents, sources=bind(meta))

                disclosures = await tool(
                    "retrieve_disclosures", dict(subject=subject), docs, optional=True
                )
                documents = disclosures["documents"]
                # Financial facts are retrieved from a frozen, normalized text document.
                financial_raw = packet["values"].get("lrb", {})
                if financial_raw:
                    from ..contracts import canonical

                    report_list = (
                        financial_raw.get("result", {}).get("data", {}).get("report_list", {})
                    )
                    selected_reports = {p: report_list[p] for p in sorted(report_list)[-4:]}
                    body = canonical(selected_reports)
                    documents.append(
                        dict(
                            source="lrb",
                            title="本次披露利润表：最新查询版本",
                            text=body[:32000],
                            url=next(s["url"] for s in packet["sources"] if s["key"] == "lrb"),
                            sha256=next(
                                s["sha256"] for s in packet["sources"] if s["key"] == "lrb"
                            ),
                        )
                    )
                for document in documents:
                    document["text_ref"] = self.store.blob({"text": document["text"]})
                retrieved = retrieve(documents, state["request"]["question"])
                evidence = facts(data) + retrieved
                data.update(
                    sources=bind(identity_sources) + packet["sources"] + disclosures["sources"],
                    evidence=evidence,
                    acquired_at=datetime.now(UTC).isoformat(),
                    requested_at=state["created_at"],
                    model_version="unified-research-v2",
                    process_note="五角色实际模型协作；所有数值由确定性工具产生。",
                    retrieval=dict(
                        method="bounded lexical",
                        documents=len(documents),
                        passages=len(retrieved),
                        error=disclosures.get("error"),
                    ),
                    memory=memory,
                )
                stage(2)
                context = dict(
                    question=state["request"]["question"],
                    plan=state["plan"],
                    subject=subject,
                    bounded_evidence=evidence,
                    memory=memory,
                    checks=data["checks"],
                    coverage=data["coverage"],
                    asof=window["asof"],
                    data_dates=data.get("data_dates", {}),
                    method_notes=data.get("notes", []),
                )
                analysis = await runner.run(
                    "Analyst", Analysis, context, lambda output: validate_analysis(output, evidence)
                )
                stage(3)

                def guard_verification(output):
                    indices = [d.claim_index for d in output.decisions]
                    if sorted(indices) != list(range(len(analysis.claims))):
                        raise ValueError("复核结论必须覆盖全部分析且不能重复")

                verified = await runner.run(
                    "Verifier",
                    Verification,
                    dict(**context, analysis=analysis.model_dump()),
                    guard_verification,
                )
                approved = [d.claim_index for d in verified.decisions if d.approved]
                if not approved:
                    raise ValueError("没有结论通过独立复核；数据已保存，可重试或查看执行记录")
                stage(4)

                def guard_writing(output):
                    if not set(output.claim_order) <= set(approved) or len(
                        set(output.claim_order)
                    ) != len(output.claim_order):
                        raise ValueError("Writer 引用了未通过复核或重复的结论")

                writing = await runner.run(
                    "Writer",
                    Writing,
                    dict(
                        question=state["request"]["question"],
                        approved_indices=approved,
                        analysis=analysis.model_dump(),
                    ),
                    guard_writing,
                )
                evidence_map = {e["id"]: e for e in evidence}
                claims = []
                for idx in writing.claim_order:
                    claim = analysis.claims[idx]
                    text = re.sub(
                        r"\{\{([FJ]\d+)\}\}", lambda m: evidence_map[m[1]]["text"], claim.text
                    )
                    claims.append(
                        dict(
                            text=text,
                            evidence_ids=claim.evidence_ids,
                            numeric_bindings=numeric_bindings(claim, evidence),
                            verifier_reason=next(
                                d.reason for d in verified.decisions if d.claim_index == idx
                            ),
                        )
                    )
                data["agent_report"] = dict(
                    heading=writing.heading,
                    focus=plan.focus,
                    claims=claims,
                    verification=verified.model_dump(),
                    rejected=len(analysis.claims) - len(approved),
                    note="解释经过角色复核；指标验证等级仍以独立数值核对为准。",
                )
                state["result_ref"] = self.store.blob(data)
                state["result_sha256"] = state["result_ref"].rsplit("/", 1)[-1]
                stage(5)
                state.update(status="COMPLETED", completed_at=datetime.now(UTC).isoformat())
                save()
        except asyncio.CancelledError:
            cancelled.set()
            state.update(status="INTERRUPTED", error="任务被中断，成功步骤与证据已保存。")
            for item in state.get("calls", []) + state["process"]:
                if item["status"] == "RUNNING":
                    item["status"] = "INTERRUPTED"
            save()
            raise
        except Exception as error:
            ambiguous = isinstance(error, ValueError) and any(
                text in str(error) for text in ("匹配多只", "未找到沪深")
            )
            state.update(
                status="NEEDS_INPUT" if ambiguous else "FAILED",
                error=type(error).__name__ + " · " + str(error)[:260],
            )
            for node in state["process"]:
                if node["status"] == "RUNNING":
                    node["status"] = "FAILED"
            save()
