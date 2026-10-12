"""Allowlisted evidence exhibits over integrity-checked immutable output cells."""

import json

from sqlalchemy import select

from marketpulse.investigation.persistence.models import ClaimRow, ValidationResultRow

from .evidence import computation_evidence_cells
from .execution.service import check_bundle, check_input_binding
from .reporting import load_material
from .storage.models import ComputeJobRow, SnapshotOwnershipRow
from .validation import verify_cell


def presentation(service, run_id):
    with service.sessions() as session:
        material = load_material(session, run_id)
        if not material:
            return {}
        if not material.claims:
            return dict(gaps=material.limitations, completeness=material.completeness)
        job = session.get(ComputeJobRow, material.job_id)
        if (
            not job
            or job.run_id != run_id
            or job.status != "COMPLETED"
            or job.artifact_id != material.artifact_id
        ):
            raise ValueError("job/material mismatch")
        bundle = service.bundle_for(job.job_id)
        check_bundle(bundle)
        checked_input = service.input_for(job.job_id, require_current_environment=False)
        check_input_binding(bundle, checked_input)
        if (
            bundle.output_hash != material.output_hash
            or bundle.manifest_hash != material.manifest_hash
        ):
            raise ValueError("material artifact hash mismatch")
        from marketpulse.investigation.persistence.models import InvestigationRunRow, ReportRow

        owner = session.get(InvestigationRunRow, run_id).investigation_id
        for snapshot_id in bundle.input_snapshot_ids:
            if not session.get(SnapshotOwnershipRow, (owner, snapshot_id)):
                raise PermissionError("input ownership missing")
        statuses = {}
        for claim in material.claims:
            persisted = session.scalar(
                select(ClaimRow).where(
                    ClaimRow.run_id == run_id, ClaimRow.statement == claim.statement
                )
            )
            validation = (
                session.get(ValidationResultRow, persisted.latest_validation_id)
                if persisted
                else None
            )
            if (
                not validation
                or validation.validation_basis_payload.get("computation")
                != claim.validation.model_dump(mode="json")
                or str(persisted.validation_status) != claim.validation.status
            ):
                raise ValueError("latest validation drift")
            verify_cell(bundle, claim.evidence)
            if not claim.validation.integrity or claim.validation.reproduction != "REPRODUCIBLE":
                raise ValueError("unvalidated artifact")
            statuses[claim.evidence.metric_path] = claim.validation.status
        from .storage.models import QuantCitationRow

        # Identical followups may share a computation artifact. Their report
        # citations remain bound to the original run/version, never its sibling.
        citations = session.scalars(
            select(QuantCitationRow)
            .join(ReportRow, QuantCitationRow.report_id == ReportRow.report_id)
            .where(ReportRow.run_id == run_id)
            .order_by(ReportRow.version)
        ).all()
        citation_by_path = {
            c.payload.get("canonical_locator", {}).get("metric_path"): c.citation_id
            for c in citations
            if c.payload.get("canonical_locator", {}).get("artifact_id") == bundle.artifact_id
        }
    values = []
    manifest = json.loads(bundle.manifest_json)
    providers = {s["snapshot_id"]: s["provider"] for s in manifest["inputs"]}
    proofs = computation_evidence_cells(bundle)
    for index, (value, ev) in enumerate(zip(bundle.values, proofs, strict=True)):
        path = f"/values/{index}"
        values.append(
            dict(
                **value.model_dump(mode="json"),
                metric_path=path,
                cell_hash=ev.cell_hash,
                artifact_id=bundle.artifact_id,
                status=statuses.get(path, "UNVERIFIED"),
                citation_id=citation_by_path.get(path),
                provider=providers.get(value.row_keys[-1]) if value.row_keys else None,
            )
        )

    def series(names):
        result = []
        for name in names:
            cells = [
                v
                for v in values
                if v["name"] == name and (name.startswith("chart_") or v["value"] is not None)
            ]
            if cells and any(v["value"] is not None for v in cells):
                result.append(
                    dict(
                        name=name,
                        unit=cells[0]["unit"],
                        points=[
                            dict(
                                x=v["row_keys"][0] if v["row_keys"] else v["end"],
                                value=v["value"],
                                missing_reason=v["missing_reason"],
                                metric_path=v["metric_path"],
                                cell_hash=v["cell_hash"],
                                citation_id=v["citation_id"],
                                label=(v["name"] + " · " + (v["provider"] or "independent")),
                            )
                            for v in cells
                        ],
                    )
                )
        return result

    charts = []
    for key, title, names, note in (
        (
            "price",
            "价格与回撤",
            [f"chart_{kind}_{a}" for a in ("raw", "qfq", "hfq") for kind in ("price", "drawdown")],
            "价格而非策略净值；复权使用冻结 anchor，回撤为价格/历史最高价格−1；不含未收盘 bar。",
        ),
        (
            "valuation",
            "估值口径对照",
            ["pe", "pb", "disclosed_pe_ttm", "disclosed_pb_mrq"],
            "独立计算为raw价格×同日总发行股数/TTM归母净利或归母总权益；披露值分列，不平均、不排名。",
        ),
        (
            "financial",
            "财务报告期趋势",
            ["chart_parent_net_profit_growth", "chart_revenue_growth"],
            "fraction；同报告期累计金额同比，年报与年报、相同累计季度与上年同期比较，不年化。"
            "当前回溯版本并非STRICT PIT。",
        ),
    ):
        data = series(names)
        if data:
            charts.append(
                dict(
                    exhibit_id=f"EXHIBIT-Q·{len(charts) + 1}",
                    kind=key,
                    title=title,
                    note=note,
                    artifact_id=bundle.artifact_id,
                    output_hash=bundle.output_hash,
                    asof=material.asof,
                    status="UNVERIFIED",
                    series=data,
                    render_version="quant-chart-v1",
                )
            )
    drawdown = next((v for v in values if v["name"] == "drawdown"), None)
    if charts and charts[0]["kind"] == "price" and drawdown:
        charts[0]["drawdown"] = {
            k: drawdown[k] for k in ("peak_date", "trough_date", "recovery_date", "metric_path")
        }
    klines = []
    for adjustment in ("qfq", "raw", "hfq"):
        by_kind = {
            kind: [v for v in values if v["name"] == f"chart_kline_{kind}_{adjustment}"]
            for kind in ("open", "close", "low", "high", "volume", "ma20", "ma60")
        }
        if not by_kind["open"]:
            continue

        def column(kind, by_kind=by_kind):
            return [float(v["value"]) if v["value"] is not None else None for v in by_kind[kind]]

        klines.append(
            dict(
                kind="kline",
                adjustment=adjustment,
                title="日K线（冻结历史，"
                + {"qfq": "前复权", "raw": "未复权", "hfq": "后复权"}[adjustment]
                + "）+ MA20/MA60",
                x=[v["row_keys"][0] for v in by_kind["open"]],
                ohlc=[
                    list(row)
                    for row in zip(
                        *(column(k) for k in ("open", "close", "low", "high")), strict=True
                    )
                ],
                ma20=column("ma20"),
                ma60=column("ma60"),
                volume=[v / 100 if v is not None else None for v in column("volume")],
                cells=by_kind,
                artifact_id=bundle.artifact_id,
                note="开高低收取自冻结原始行情；均线由离线计算器计算。成交量原值为股，图中转换为手（100股）；缺项与均线预热期留空。",
            )
        )
    dcf_values = {v["name"].removeprefix("dcf_"): v for v in values if v["name"].startswith("dcf_")}
    sensitivities = [v for v in values if v["name"] == "chart_dcf_sensitivity"]
    dcf = dict(
        cells=dcf_values,
        sensitivity=sensitivities,
        assumptions=manifest["spec"].get("dcf"),
        note=(
            "5+5年 FCFF 增长情景与永续终值；以年报期末为估值基准，非当前目标价。"
            "营运资本使用现金流附注代理，债务与少数权益用账面值；未剥离财务子公司、"
            "未归一化营运资本释放，未加入非经营性投资公允价值。每股分母为年末发行股数"
            "减公司回购专户股数，未模拟未来稀释。与参考价的数据时点不同，不代表当前低估或预期收益。"
        ),
        source_url=None,
        input_check="UNAVAILABLE",
    )
    source = next(
        (
            s
            for s in checked_input.snapshots
            if s.result.request.normalizer_version == "dcf-official-v1"
        ),
        None,
    )
    if source:
        row = json.loads(source.result.records_json)[0]
        dcf.update(
            source_url=row["source_url"],
            document_sha256=row["document_sha256"],
            base_date=row["period_end"],
            published_at=row["available_at"],
            input_check=json.loads(source.result.crosscheck_json).get("status", "UNAVAILABLE"),
        )
    from .execution.environment import environment

    pinned_environment = json.loads(checked_input.environment_json)
    current_environment = json.loads(environment(service.root))
    same_environment = all(
        pinned_environment[k] == current_environment[k] for k in ("source_tree_hash", "lock_hash")
    )
    return dict(
        environment=dict(
            recorded=pinned_environment,
            matches_current=same_environment,
            note="历史产物与冻结输入已通过完整性校验；复算需要生成时环境。",
        ),
        values=values,
        charts=charts[:3],
        klines=klines,
        dcf=dcf,
        gaps=material.limitations,
        evidence_count=len(material.claims),
        citation_count=len(citation_by_path),
        completeness=material.completeness,
        manifest_hash=bundle.manifest_hash,
        output_hash=bundle.output_hash,
        input_snapshot_ids=bundle.input_snapshot_ids,
        asof=material.asof,
        methodology=manifest["spec"],
    )
