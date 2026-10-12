"""Evidence-preserving composition of two frozen research branches, no financial math."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from sqlalchemy import select

from marketpulse.investigation.domain.enums import AuditActorType, RunStatus
from marketpulse.investigation.persistence.models import (
    AuditEventRow,
    InvestigationRunRow,
    ReportRow,
)

from .contracts import canonical, digest
from .process import enter_stage, record_stage, stop_active

SCHEMA = "quant-comparison-v1"
DIMENSIONS = (
    ("growth", "成长性", "revenue_growth", "单季营业收入同比"),
    ("profitability", "盈利能力", "roe", "TTM 平均归母权益 ROE"),
    ("quality", "盈利质量", None, "净现比"),
    ("valuation", "估值水位", None, "PE-TTM 历史分位"),
    ("momentum", "一年动量", "return", "一年价格收益 / 复权代理"),
)
LIMITATIONS = (
    "比较层仅并列已有证据，不计算综合评分或领先维度数量，不据此判断哪只股票更好。",
    "净现比与 PE 历史分位缺少同口径输入，单季金额图仍未接入，不以累计金额或最新 PE 替代。",
    "财报为当前接口回溯版本，并非严格时点可得数据；统一研究时点不等于当时已经知道这些数值。",
    "行业、会计政策与资本结构差异未调整；银行与工业企业的盈利能力和估值含义不同。",
    "可比口径检查与权威基准核验分别记录；本步不升级任何原指标验证状态。",
)


def alignment(cells):
    if any(not cell or cell["value"] is None for cell in cells):
        return "UNAVAILABLE", "缺少适用的同口径数值。"
    signatures = [
        (
            c["unit"],
            c["start"],
            c["end"],
            re.sub(r";revision=[^;]+", "", c["definition"]),
        )
        for c in cells
    ]
    if signatures[0] != signatures[1]:
        return "METHOD_DIFFERENCE", "期间、单位或计算定义不一致；仅分列观察，不作数值排名。"
    return "MATCH", "期间、单位与计算定义一致；不代表权威核验通过或经济含义相同。"


def compose(subjects):
    """Consume checked branch presentations. All numerical cells retain original locators."""
    rows = []
    for key, label, metric, caption in DIMENSIONS:
        cells = [
            next((c for c in s["data"]["values"] if c["name"] == metric), None) for s in subjects
        ]
        status, note = alignment(cells)
        if key == "growth" and status != "UNAVAILABLE":
            # Only first-quarter YTD is also a single quarter; no silent YTD substitution.
            if any(c["start"][5:] != "01-01" or c["end"][5:] != "03-31" for c in cells):
                cells, status, note = (
                    [None, None],
                    "UNAVAILABLE",
                    "尚无两股同口径的单季同比；累计同比另列补充观察。",
                )
        if key == "momentum" and status != "UNAVAILABLE":
            from datetime import date

            if any(
                not 364
                <= (date.fromisoformat(c["end"]) - date.fromisoformat(c["start"])).days
                <= 367
                for c in cells
            ):
                cells, status, note = (
                    [None, None],
                    "UNAVAILABLE",
                    "所选区间不足或超过一年；区间价格收益另列，不能冒充一年动量。",
                )
        if metric is None:
            note = (
                "缺少经营现金流与净利润的统一口径。"
                if key == "quality"
                else "缺少同锚点、同历史窗口的 PE 序列。"
            )
        rows.append(
            dict(key=key, label=label, caption=caption, cells=cells, alignment=status, note=note)
        )
    extras = []
    for name, label in (
        ("revenue_growth", "累计营业收入同比"),
        ("net_profit_growth", "累计归母净利同比"),
        ("pe", "PE（归母总额口径）"),
        ("pb", "PB（归母总额口径）"),
        ("return", "区间价格收益"),
        ("volatility", "年化波动率"),
        ("drawdown", "最大回撤"),
    ):
        cells = [
            next((c for c in s["data"]["values"] if c["name"] == name), None) for s in subjects
        ]
        status, note = alignment(cells)
        extras.append(dict(key=name, label=label, cells=cells, alignment=status, note=note))
    return dict(
        schema_version=SCHEMA, dimensions=rows, supplementary=extras, limitations=list(LIMITATIONS)
    )


def freeze(runtime, run_id, payload):
    stored = runtime.service.store.blobs.put_bytes(canonical(payload).encode())
    with runtime.service.sessions.begin() as session:
        row = session.get(InvestigationRunRow, run_id)
        session.add(
            AuditEventRow(
                audit_event_id="QCMP-" + run_id,
                investigation_id=row.investigation_id,
                run_id=run_id,
                actor_type=AuditActorType.SYSTEM,
                event_type="QUANT_COMPARISON_FROZEN",
                target_type="Run",
                target_id=run_id,
                metadata_payload={"payload": payload, "sha256": stored.ref.sha256},
                created_at=datetime.now(UTC),
            )
        )


def load(session, run_id):
    event = session.get(AuditEventRow, "QCMP-" + run_id)
    if not event:
        return None
    payload = event.metadata_payload["payload"]
    if (
        digest(payload) != event.metadata_payload["sha256"]
        or payload.get("schema_version") != SCHEMA
    ):
        raise ValueError("comparison material hash/schema mismatch")
    return payload


def subject_binding(plan, run_id, subject, data):
    return dict(
        run_id=run_id,
        subject=subject,
        plan=plan,
        manifest_hash=data.get("manifest_hash"),
        output_hash=data.get("output_hash"),
        input_snapshot_ids=list(data.get("input_snapshot_ids", [])),
        # Bind only derived displays used here, keeping OHLC and DCF in their own reports.
        data={
            "values": [
                c
                for c in data.get("values", [])
                if (
                    not c["name"].startswith("chart_")
                    or c["name"] in {"chart_revenue_growth", "chart_parent_net_profit_growth"}
                )
                and not c["name"].startswith("dcf_")
            ],
            "charts": [c for c in data.get("charts", []) if c["kind"] == "financial"],
            "gaps": data.get("gaps", []),
        },
    )


def presentation(runtime, run_id):
    from .api import instruments

    with runtime.service.sessions() as session:
        payload = load(session, run_id)
        report = session.scalar(select(ReportRow).where(ReportRow.run_id == run_id))
        if payload and report:
            from marketpulse.investigation.persistence.models import ReportInputSnapshotRow

            snapshot = session.scalar(
                select(ReportInputSnapshotRow).where(
                    ReportInputSnapshotRow.snapshot_hash == report.report_input_snapshot_hash
                )
            )
            if not snapshot or snapshot.semantic_payload.get("comparison_material") != canonical(
                payload
            ):
                raise ValueError("comparison/report snapshot mismatch")
    if payload is None:
        return {}
    from marketpulse.infrastructure.storage.models import BlobError, BlobRef

    try:
        runtime.service.store.blobs.verify_hash(BlobRef(digest(payload)))
    except BlobError as error:
        raise ValueError("comparison frozen blob integrity failed") from error
    checked = []
    for source in payload["subjects"]:
        with runtime.service.sessions() as session:
            run = session.get(InvestigationRunRow, source["run_id"])
            plan = session.get(AuditEventRow, "QPLAN-" + source["run_id"])
            if not run or run.status != RunStatus.COMPLETED or not plan:
                raise ValueError("comparison source unavailable")
        data = runtime.presentation(source["run_id"])
        current = subject_binding(
            plan.metadata_payload,
            source["run_id"],
            instruments(runtime.service.sessions, plan.metadata_payload["instrument"], 1)[0],
            data,
        )
        if digest(current) != digest(source):
            raise ValueError("comparison source drift")
        checked.append(current)
    if digest(compose(checked)) != digest(payload["comparison"]):
        raise ValueError("comparison composition drift")
    return dict(
        scenario="compare",
        comparison=payload["comparison"],
        subjects=checked,
        evidence_count=sum(len(s["data"]["values"]) for s in checked),
        completeness="部分完整",
        manifest_hash=digest(payload),
        input_snapshot_ids=[i for s in checked for i in s["input_snapshot_ids"]],
    )


async def execute(runtime, run_id, investigation_id, plan, comparison):
    """Coordinate two bounded branches. Cancellation never leaves child tasks running."""
    from .api import QuantRunIn, instruments

    children = []
    try:
        record_stage(
            runtime.service.sessions,
            run_id,
            "planner",
            "COMPLETED",
            "两股期间、研究时点与价格口径已统一。",
        )
        enter_stage(runtime.service.sessions, run_id, "data", "两条真实研究支路执行中")
        for entry in comparison["branches"]:
            branch = QuantRunIn.model_validate(entry["plan"])
            child = runtime.start(entry["investigation_id"], branch)
            children.append(child)
            with runtime.service.sessions.begin() as session:
                session.add(
                    AuditEventRow(
                        audit_event_id="QCMP-CHILD-" + child,
                        investigation_id=investigation_id,
                        run_id=run_id,
                        actor_type=AuditActorType.SYSTEM,
                        event_type="QUANT_COMPARISON_BRANCH",
                        target_type="Run",
                        target_id=child,
                        metadata_payload={"child_run_id": child, "instrument": branch.instrument},
                        created_at=datetime.now(UTC),
                    )
                )
            # Sequential work obeys each branch's existing worker and resource budget.
            await asyncio.shield(runtime.runner.tasks[child])
        enter_stage(
            runtime.service.sessions, run_id, "analyst", "并列真实单元格，检查逐项期间、单位与定义"
        )
        subjects = []
        for child in children:
            with runtime.service.sessions() as session:
                child_plan = session.get(AuditEventRow, "QPLAN-" + child).metadata_payload
                report = session.scalar(select(ReportRow).where(ReportRow.run_id == child))
            if report is None:
                raise ValueError("branch report missing")
            data = await asyncio.to_thread(runtime.presentation, child)
            if not data.get("values"):
                raise ValueError("branch numerical evidence missing")
            subject = instruments(runtime.service.sessions, child_plan["instrument"], 1)[0]
            subjects.append(subject_binding(child_plan, child, subject, data))
        enter_stage(
            runtime.service.sessions,
            run_id,
            "verifier",
            "核对子报告证据链与统一口径；不升级验证状态",
        )
        result = dict(schema_version=SCHEMA, subjects=subjects, comparison=compose(subjects))
        await asyncio.to_thread(freeze, runtime, run_id, result)
        await asyncio.to_thread(presentation, runtime, run_id)
        enter_stage(runtime.service.sessions, run_id, "writer", "比较报告成稿")
        await runtime.runner._finalize_run(run_id, None)
    except asyncio.CancelledError:
        stop_active(runtime.service.sessions, run_id, "比较已停止；未完成维度保留缺项。")
        await runtime.runner._finalize_run(
            run_id, "USER_CANCELLED：比较已停止，可回看已经成稿的支路。"
        )
    except Exception:
        stop_active(runtime.service.sessions, run_id, "支路或证据核验受阻；未生成比较数值。")
        await runtime.runner._finalize_run(
            run_id, "QUANT_COMPARISON_PARTIAL：比较受阻，可回看已成稿支路；不使用不完整输入排名。"
        )
    finally:
        for child in children:
            task = runtime.runner.tasks.get(child)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        with runtime.service.sessions() as session:
            row = session.get(InvestigationRunRow, run_id)
            complete = row.status == RunStatus.COMPLETED
        if complete:
            record_stage(
                runtime.service.sessions,
                run_id,
                "writer",
                "COMPLETED",
                "比较报告已落盘；保留原数值验证状态。",
            )


def append_draft(draft, payload):
    """Governance-only narrative: numerical evidence remains in original branch reports."""
    from marketpulse.investigation.reporting.writer import (
        ContentClass,
        DraftSection,
        NarrativeUnit,
        SectionStatus,
    )

    texts = {
        "EXECUTIVE_STATUS": (
            "双标的冻结研究已组合为五维对照。期间、单位和定义逐项核对；"
            "缺项及未验证观察保留，不能据此形成投资排名。"
        ),
        "EVIDENCE_BASE": (
            "比较数值引用两条已归档研究支路的原单元格；原报告、公式、"
            "输入与验证记录可回看，不新增或升级数值声明。"
        ),
        "BLOCKING_GAPS_AND_LIMITATIONS": "\n".join(payload["comparison"]["limitations"]),
        "RESEARCH_APPENDIX": "\n".join(
            "原研究支路：" + s["subject"]["name"] + " / " + s["run_id"] for s in payload["subjects"]
        ),
    }
    return draft.model_copy(
        update={
            "sections": tuple(
                DraftSection(
                    section_key=key,
                    status=SectionStatus.CONTENT,
                    units=(
                        NarrativeUnit(
                            unit_key="comparison-" + key,
                            section_key=key,
                            text=text,
                            content_class=ContentClass.GOVERNANCE_DISCLOSURE,
                        ),
                    ),
                )
                for key, text in texts.items()
            )
        }
    )


def markdown(data, plan):
    lines = [
        "# " + plan["question"],
        "",
        "场景：双标的比较；部分完整，未作投资排名。",
        f"冻结区间：{plan['date_start']} — {plan['date_end']}；"
        f"研究时点：{plan['asof']}；价格口径：{plan['adjustment']}",
        "",
        "## 五维对照与补充观察",
    ]
    for row in data["comparison"]["dimensions"] + data["comparison"]["supplementary"]:
        lines.extend(
            ["", "### " + row["label"], "口径对齐：" + row["alignment"] + "；" + row["note"]]
        )
        for source, cell in zip(data["subjects"], row["cells"], strict=True):
            lines.append(
                "- "
                + source["subject"]["name"]
                + "："
                + (
                    f"{cell['value']} {cell['unit']}；"
                    f"{cell['start']} — {cell['end']}；{cell['status']}"
                    if cell
                    else "数据缺失"
                )
            )
            if cell:
                lines.append("  公式：" + cell["definition"])
                lines.append(
                    f"  证据：{cell['artifact_id']} {cell['metric_path']}；"
                    f"SHA256 {cell['cell_hash']}"
                )
                if cell.get("citation_id"):
                    lines.append("  [原验证记录](/api/citations/" + cell["citation_id"] + ")")
    lines.extend(["", "## 原研究支路"])
    for source in data["subjects"]:
        lines.append(
            f"- [{source['subject']['name']}](/#/report/{source['run_id']})；"
            f"产物 {source['output_hash']}；输入 {', '.join(source['input_snapshot_ids'])}"
        )
    lines.extend(
        [
            "",
            "## 风险与局限",
            *["- " + note for note in data["comparison"]["limitations"]],
            "",
            "本工具为投研参考，不构成投资建议。历史数据不代表未来表现。",
        ]
    )
    return "\n".join(lines)
