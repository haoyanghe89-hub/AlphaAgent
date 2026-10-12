"""Persisted v2 sidecar and read-only presentation, no model numerical output."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from marketpulse.investigation.domain.enums import ClaimImportance, ClaimType, ValidationStatus
from marketpulse.investigation.reporting.models import SnapshotClaim
from marketpulse.investigation.validation.profiles import QuantitativeProfile

from .contracts import digest
from .domain import FrozenModel
from .evidence import ComputationEvidence, computation_evidence_cells
from .storage.models import QuantReportMaterialRow
from .validation import ComputationValidation, verify_computation_claims


class QuantClaim(FrozenModel):
    claim_id: str
    statement: str
    evidence: ComputationEvidence
    validation: ComputationValidation


class QuantReportMaterial(FrozenModel):
    schema_version: str = "quant-report-v2"
    job_id: str = ""
    artifact_id: str
    manifest_hash: str
    output_hash: str
    claims: tuple[QuantClaim, ...]
    limitations: tuple[tuple[str, str], ...]
    source_count: int
    family_count: int
    completeness: str
    diagnostics: tuple[str, ...]
    instrument_name: str
    asof: str
    return_basis: str
    annual_sessions: int
    sample_count: int

    @property
    def semantic_hash(self):
        return digest(self.model_dump(mode="json", exclude={"job_id"}))


LABELS = {
    "dcf_per_share": "DCF条件情景每股估算（非目标价，基于年报期末输入）",
    "sharpe": "夏普比率",
    "beta": "Beta",
    "correlation": "相关系数",
    "return": "区间价格收益",
    "cagr": "年化价格收益",
    "volatility": "年化波动率",
    "drawdown": "最大回撤",
    "pe": "TTM市盈率",
    "pb": "市净率",
    "roe": "平均权益ROE",
    "disclosed_roe": "供应商披露ROE",
    "disclosed_net_profit": "供应商披露净利润",
    "disclosed_pe_ttm": "供应商披露TTM市盈率",
    "disclosed_pb_mrq": "供应商披露市净率",
    "net_profit_growth": "净利润同比",
    "revenue_growth": "收入同比",
    "pe_disclosed_relative_difference": "独立PE相对供应商披露值的差异",
    "pb_disclosed_relative_difference": "独立PB相对供应商披露值的差异",
}
REASONS = {
    "missing_valuation_inputs": (
        "valuation_inputs",
        "估值所需的当时股数、TTM归母净利或归母权益口径不齐；应补充有发布时点与版本的一手财报及股数记录。",
    ),
    "missing_average_parent_equity": (
        "valuation_inputs",
        "估值所需的当时股数、TTM归母净利或归母权益口径不齐；应补充有发布时点与版本的一手财报及股数记录。",
    ),
    "missing_publication_provenance": (
        "publication",
        "部分披露数据缺少可用的发布时点，不能倒推历史可知性；应补充原始公告与修订档案。",
    ),
    "insufficient_yoy_history": (
        "financial_history",
        "可比财报期间不足，暂不能形成财务增长结论；应补充同口径历史报告。",
    ),
    "incomparable_periods": (
        "financial_history",
        "可比财报期间不足，暂不能形成财务增长结论；应补充同口径历史报告。",
    ),
    "missing_risk_free": (
        "rf",
        "未取得冻结无风险利率，不计算夏普比率；应补充利率快照或由用户明确选择零利率假设。",
    ),
    "missing_benchmark": (
        "benchmark",
        "未取得可对齐的冻结基准，不计算相关性或Beta；应补充同频基准。",
    ),
    "nonpositive_earnings": (
        "loss",
        "盈利非正时市盈率不适用，也不参与便宜程度排名；应结合盈利持续性分析。",
    ),
}


async def build_material(service, *, job_id) -> QuantReportMaterial:
    bundle = await asyncio.to_thread(service.bundle_for, job_id)
    manifest = json.loads(bundle.manifest_json)
    instrument_name = manifest["instrument"]["name"]
    claims, limits, diagnostics = [], {}, []
    scalar_names = {
        "dcf_per_share",
        "sharpe",
        "beta",
        "correlation",
        "return",
        "cagr",
        "volatility",
        "drawdown",
        "pe",
        "pb",
        "roe",
        "net_profit_growth",
        "revenue_growth",
        "disclosed_roe",
        "disclosed_net_profit",
        "disclosed_pe_ttm",
        "disclosed_pb_mrq",
        "pe_disclosed_relative_difference",
        "pb_disclosed_relative_difference",
    }
    evidence_cells = computation_evidence_cells(bundle)
    claimed = tuple(
        evidence
        for cell, evidence in zip(bundle.values, evidence_cells, strict=True)
        if cell.name in scalar_names and cell.value is not None
    )
    checked = await verify_computation_claims(service, job_id=job_id, evidence=claimed)
    validations = {e.metric_path: v for e, v in zip(claimed, checked, strict=True)}
    for index, cell in enumerate(bundle.values):
        if cell.name not in scalar_names:
            continue
        if cell.value is None:
            key, reason = REASONS.get(
                cell.missing_reason,
                ("other_input", "部分计算输入或口径未满足要求；应补齐原始材料后重新核对。"),
            )
            limits[key] = reason
            diagnostics.append(f"{cell.name}: {cell.missing_reason}")
            continue
        evidence = evidence_cells[index]
        validation = validations[evidence.metric_path]
        profile = QuantitativeProfile.evaluate_computation(validation)
        if profile.recommended_status != validation.status:
            raise ValueError("quantitative profile disagrees with deterministic gate")
        label = LABELS.get(cell.name, cell.name)
        parent_aggregate = any(
            f.get("share_basis") == "total_issued_shares_parent_aggregate_not_ordinary_eps"
            for f in json.loads(bundle.manifest_json).get("financial_inputs", [])
        )
        if parent_aggregate:
            label = {
                "pe": "独立计算PE（归母总额口径）",
                "pb": "独立计算PB（归母总额口径）",
                "roe": "独立计算TTM ROE（期初期末平均归母总权益）",
                "net_profit_growth": "归母净利润同比",
                "revenue_growth": "合并营业收入同比",
            }.get(cell.name, label)
            limits["parent_aggregate_basis"] = (
                "独立估值采用raw价格、同日总发行股数和归母总额，"
                "TTM为上年全年加本期累计减上年同期；ROE用该TTM期间期初/期末归母总权益。"
                "归母权益含其他权益工具、利润未扣优先股或永续分配，不能视为普通股EPS口径，"
                "亦不能将供应商差异平均或据此排名；应补齐分配口径与原始公告后再核对。"
            )
            limits["historical_revision"] = (
                "财报由当前接口回溯取得，部分修订晚于研究时点；股本仅有历史交易日标签，"
                "无原始公告或当时首次入库记录。公告日期和下一交易日可用标签不构成STRICT PIT，"
                "这些结果仅为冻结的回溯观察；应补齐当时版本档案后再作历史可知性判断。"
            )
        if cell.row_keys and (
            cell.name.startswith("disclosed_") or "disclosed_relative_difference" in cell.name
        ):
            providers = {
                s["snapshot_id"]: s["provider"] for s in json.loads(bundle.manifest_json)["inputs"]
            }
            provider = providers.get(cell.row_keys[-1])
            if provider:
                label += (
                    "（"
                    + {"baostock": "BaoStock", "akshare_eastmoney": "AkShare/东方财富"}.get(
                        provider, provider
                    )
                    + "）"
                )
        if cell.unit.startswith("fraction"):
            number = format(cell.value * 100, ".4f") + "%"
        elif cell.unit == "CNY":
            number = format(cell.value / 100000000, ".4f") + "亿元"
        else:
            number = format(cell.value, ".4f") + ("倍" if cell.unit == "ratio" else " " + cell.unit)
        period = (
            f"报告期 {cell.end}"
            if cell.name in {"disclosed_net_profit", "disclosed_roe"}
            else f"{cell.end}时点"
            if cell.start == cell.end
            else f"{cell.start} 至 {cell.end}"
        )
        statement = f"{instrument_name} 在{period}的{label}为 {number}。"
        claims.append(
            QuantClaim(
                claim_id="qclaim_" + evidence.cell_hash,
                statement=statement,
                evidence=evidence,
                validation=validation,
            )
        )
        if validation.missing:
            limits["validation"] = (
                "当前计算虽可复现，但授权、历史可知性、独立来源或验证条件尚未全部满足，相关数值观察尚未证实；应补齐这些条件后重新验证，不能据此推导投资建议。"
            )
            diagnostics.append(f"{cell.name} gate: {','.join(validation.missing)}")
    manifest = json.loads(bundle.manifest_json)
    sources = {(s["provider"], s["upstream"]) for s in manifest["inputs"]}
    families = {s["family"] for s in manifest["inputs"] if s["lineage"] == "verified"}
    return QuantReportMaterial(
        job_id=job_id,
        artifact_id=bundle.artifact_id,
        manifest_hash=bundle.manifest_hash,
        output_hash=bundle.output_hash,
        claims=tuple(claims),
        limitations=tuple(sorted(limits.items())),
        source_count=len(sources),
        family_count=len(families),
        completeness="部分完整" if limits else "完整",
        diagnostics=tuple(sorted(set(diagnostics))),
        instrument_name=instrument_name,
        asof=manifest["asof"],
        return_basis=manifest["spec"]["return_basis"],
        annual_sessions=manifest["spec"]["annual_sessions"],
        sample_count=manifest["sampling"]["count"],
    )


def persist_material(sessions, *, run_id, material: QuantReportMaterial):
    identity = "qmat_" + digest({"run": run_id, "semantic": material.semantic_hash})
    with sessions.begin() as session:
        from marketpulse.investigation.domain.claims import Claim, ValidationResult
        from marketpulse.investigation.domain.enums import EntailmentStatus
        from marketpulse.investigation.persistence.models import ClaimRow, InvestigationRunRow
        from marketpulse.investigation.persistence.repositories import InvestigationRepository

        repository = InvestigationRepository(sessions)
        run = session.get(InvestigationRunRow, run_id)
        if run is None:
            raise ValueError("quant report run missing")
        now = datetime.now(UTC)
        for claim in material.claims:
            row_id = claim.claim_id + "-" + digest(run_id)[:8]
            validation_id = "qval_" + claim.validation.validation_hash + "-" + digest(run_id)[:8]
            if session.get(ClaimRow, row_id):
                continue
            repository.add_in_session(
                session,
                Claim(
                    claim_id=row_id,
                    investigation_id=run.investigation_id,
                    run_id=run_id,
                    statement=claim.statement,
                    claim_type=ClaimType.QUANTITATIVE,
                    importance=ClaimImportance.HIGH,
                    is_critical=True,
                    validation_status=ValidationStatus(claim.validation.status),
                    confidence=0,
                    qualifiers={
                        "evidence_kind": "COMPUTATION",
                        "value": claim.evidence.model_dump(mode="json")["value"],
                        "unit": claim.evidence.unit,
                        "time": [claim.evidence.start, claim.evidence.end],
                        "scope": claim.evidence.scope,
                        "definition": claim.evidence.definition,
                        "provenance": claim.evidence.artifact_id,
                    },
                    created_at=now,
                    updated_at=now,
                ),
            )
            repository.add_in_session(
                session,
                ValidationResult(
                    validation_id=validation_id,
                    claim_id=row_id,
                    run_id=run_id,
                    claim_type=ClaimType.QUANTITATIVE,
                    profile_version="quantitative-computation-v1",
                    policy_version="quant-validation-v1",
                    citation_valid=True,
                    entailment_result=EntailmentStatus.ENTAILED,
                    independent_source_count=claim.validation.independent_families,
                    strong_contradiction=False,
                    sufficiency_result="sufficient"
                    if claim.validation.status == "VERIFIED"
                    else "insufficient",
                    status=ValidationStatus(claim.validation.status),
                    confidence=0,
                    validation_basis=(
                        "Deterministic frozen computation; "
                        "reproducibility is not source independence"
                    ),
                    validation_basis_payload={
                        "computation": claim.validation.model_dump(mode="json"),
                        "cell_hash": claim.evidence.cell_hash,
                    },
                    created_at=now,
                ),
            )
            session.get(ClaimRow, row_id).latest_validation_id = validation_id
        if not session.get(QuantReportMaterialRow, identity):
            session.add(
                QuantReportMaterialRow(
                    material_id=identity,
                    run_id=run_id,
                    semantic_hash=material.semantic_hash,
                    payload=material.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
            )


def load_material(session, run_id) -> QuantReportMaterial | None:
    row = session.scalar(
        select(QuantReportMaterialRow)
        .where(QuantReportMaterialRow.run_id == run_id)
        .order_by(QuantReportMaterialRow.created_at.desc(), QuantReportMaterialRow.material_id)
        .limit(1)
    )
    if not row:
        return None
    material = QuantReportMaterial.model_validate(row.payload)
    if material.semantic_hash != row.semantic_hash:
        raise ValueError("quant report material integrity mismatch")
    return material


def snapshot_claims(material):
    return tuple(
        SnapshotClaim(
            stable_key=c.claim_id,
            semantic_hash=digest({"statement": c.statement, "evidence": c.evidence.cell_hash}),
            statement=c.statement,
            claim_type=ClaimType.QUANTITATIVE,
            validation_status=ValidationStatus(c.validation.status),
            confidence=0,
            validation_semantic_hash=c.validation.validation_hash,
            importance=ClaimImportance.HIGH,
            is_critical=True,
            report_section="QUANTITATIVE_FINDINGS",
        )
        for c in material.claims
    )


def append_quant_draft(draft, material):
    from marketpulse.investigation.reporting.writer import (
        ContentClass,
        DraftSection,
        NarrativeUnit,
        SectionStatus,
    )

    summary = (
        "EXECUTIVE_STATUS"
        if str(draft.report_type) == "INVESTIGATION_STATUS"
        else "EXECUTIVE_SUMMARY"
    )
    limits = (
        "BLOCKING_GAPS_AND_LIMITATIONS"
        if summary == "EXECUTIVE_STATUS"
        else "LIMITATIONS_AND_RESEARCH_GAPS"
    )
    # Quant-only report replaces the empty-web boilerplate, not the web facts.
    content = {s.section_key: list(s.units) for s in draft.sections}
    quant_only = not any(
        u.claim_refs and not all(r.startswith("qclaim_") for r in u.claim_refs)
        for s in draft.sections
        for u in s.units
    )
    if quant_only:
        content[summary] = [
            NarrativeUnit(
                unit_key="quant-answer",
                section_key=summary,
                text=(
                    f"对{material.instrument_name}，冻结历史价格可用于离线核对区间表现。"
                    f"当前投研材料为“{material.completeness}”，不能据此确认估值与基本面匹配。"
                    "可复现不等于投资结论已验证，也不构成低估判断或未来收益预测。"
                ),
                content_class=ContentClass.GOVERNANCE_DISCLOSURE,
            )
        ]
        content["EVIDENCE_BASE"] = []
        content[limits] = []
        # Do not describe a web/model investigation that this branch did not run.
        content["RESEARCH_APPENDIX"] = [
            unit
            for unit in content.get("RESEARCH_APPENDIX", [])
            if not unit.text.startswith("方法：")
        ] + [
            NarrativeUnit(
                unit_key="quant-method",
                section_key="RESEARCH_APPENDIX",
                text=(
                    "方法：读取已绑定的冻结快照，子进程按固定公式计算，独立离线重算后"
                    "逐一核对产物、单元格和最新验证记录。供应商披露值不冒充独立复算值；"
                    "本支路不调用模型生成数值，也不执行交易或策略回测。"
                ),
                content_class=ContentClass.PRESENTATIONAL,
            )
        ]
        content["TECHNICAL_APPENDIX"] = [
            unit
            for unit in content.get("TECHNICAL_APPENDIX", [])
            if not unit.text.startswith("运行结束时状态为")
        ]
    content["EVIDENCE_BASE"].append(
        NarrativeUnit(
            unit_key="quant-source-basis",
            section_key="EVIDENCE_BASE",
            text=(
                f"量化输入覆盖 {material.source_count} 个供应商/上游组合，"
                f"已确认独立来源族 {material.family_count} 个；数值来自冻结快照与离线确定性计算，"
                "展示值保留四位小数，完整精度与口径见引用单元格，未由模型生成。"
            ),
            content_class=ContentClass.PRESENTATIONAL,
        )
    )
    content["EVIDENCE_BASE"].append(
        NarrativeUnit(
            unit_key="quant-data-definitions",
            section_key="EVIDENCE_BASE",
            text=(
                f"标的为{material.instrument_name}，资料截止 {material.asof}，"
                f"价格样本 {material.sample_count} 个完整交易日。"
                f"波动率年化假设为 {material.annual_sessions} 个交易日；"
                + (
                    "使用未复权价格收益，不包含现金分红，不代表总回报或策略收益。"
                    if material.return_basis == "price"
                    else "使用冻结锚点的复权价格代理，不等同于策略回测或完整公司行动账。"
                )
                + "年化价格收益仅按历史窗口的实际历时折算，不代表未来收益预测。"
            ),
            content_class=ContentClass.PRESENTATIONAL,
        )
    )
    for i, claim in enumerate(material.claims):
        status = "已验证" if claim.validation.status == "VERIFIED" else "尚未证实的可复现数值观察"
        content.setdefault("QUANTITATIVE_FINDINGS", []).append(
            NarrativeUnit(
                unit_key=f"quant-cell-{i}",
                section_key="QUANTITATIVE_FINDINGS",
                text=f"{status}：{claim.statement}",
                content_class=ContentClass.FACTUAL_ASSERTION,
                claim_refs=(claim.claim_id,),
            )
        )
    for key, reason in material.limitations:
        content.setdefault(limits, []).append(
            NarrativeUnit(
                unit_key=f"quant-gap-{key}",
                section_key=limits,
                text="局限与后续建议：" + reason,
                content_class=ContentClass.GOVERNANCE_DISCLOSURE,
            )
        )
    content.setdefault("TECHNICAL_APPENDIX", []).append(
        NarrativeUnit(
            unit_key="quant-manifest",
            section_key="TECHNICAL_APPENDIX",
            text=(
                f"artifact={material.artifact_id}; manifest={material.manifest_hash}; "
                f"output={material.output_hash}; "
            )
            + "; ".join(material.diagnostics),
            content_class=ContentClass.PRESENTATIONAL,
        )
    )
    order = [
        summary,
        "EVIDENCE_BASE",
        "CORE_FINDINGS",
        "QUANTITATIVE_FINDINGS",
        limits,
        "RESEARCH_APPENDIX",
        "TECHNICAL_APPENDIX",
    ]
    order += [key for key in content if key not in order]
    return draft.model_copy(
        update={
            "schema_version": "quant-report-writer-v2",
            "sections": tuple(
                DraftSection(
                    section_key=key, status=SectionStatus.CONTENT, units=tuple(content[key])
                )
                for key in order
                if content.get(key)
            ),
        }
    )
