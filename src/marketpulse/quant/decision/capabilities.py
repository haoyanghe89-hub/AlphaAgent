"""P0 support matrix reports observed probes, never promises a live feed."""

import hashlib
from datetime import UTC, date, datetime

from .contracts import SourceCapability
from .methods import load_cases, load_config, load_methods
from .next_session.contracts import ExperimentConfig


def freshness(source, asof, expected_session=None):
    # No invented release schedule or default 'fresh'. A caller supplies the
    # verified last complete session for daily data, not a weekday heuristic.
    if source.tested_at is None or source.tested_at > asof or source.status != "AVAILABLE":
        return "FRESHNESS_UNKNOWN"
    if expected_session is not None and source.data_end:
        try:
            last = date.fromisoformat(source.data_end)
        except ValueError:
            return "FRESHNESS_UNKNOWN"
        if last > expected_session:
            return "FRESHNESS_UNKNOWN"
        return "FRESH" if last == expected_session else "STALE"
    # Fetch time does not establish the release deadline or reporting period.
    # Monthly/disclosure adapters must first verify their publication schedule.
    return "FRESHNESS_UNKNOWN"


def source_audit(root=None):
    audit = load_config("source_audit.json")
    sources = []
    for item in audit["sources"]:
        source = SourceCapability.model_validate(item)
        row = source.model_dump(mode="json")
        row["archive_status"] = "NOT_ARCHIVED_BY_POLICY"
        if source.raw_ref:
            if root is None:
                row["archive_status"] = "NOT_CHECKED"
            else:
                target = (root / source.raw_ref).resolve()
                if not target.is_relative_to((root / ".phase3-quant-o").resolve()):
                    raise ValueError("audit archive path outside private root")
                if not target.is_file():
                    row["archive_status"] = "MISSING_ON_THIS_HOST"
                elif hashlib.sha256(target.read_bytes()).hexdigest() != source.content_hash:
                    raise ValueError("source audit archive hash mismatch")
                else:
                    row["archive_status"] = "HASH_CHECKED"
        sources.append(row)
    return dict(audit, sources=sources)


def capabilities(*, model_configured=False, model=None, root=None):
    from .sector_profiles import PROFILES, profile

    cards, cases = load_methods(), load_cases()
    audit = source_audit(root)
    experiment = ExperimentConfig.model_validate(load_config("next_session/experiment_config.json"))
    return dict(
        schema_version="decision-v1",
        stage="P7",
        generated_at=datetime.now(UTC).isoformat(),
        model_configured=model_configured,
        model=model,
        roles=["Planner", "Data", "Analyst", "Verifier", "Writer"],
        features=dict(
            single_evidence=True,
            next_session=True,
            medium_term=True,
            industry_screening=True,
            market_scan=True,
            market_scope="设备与制造固定联合池，非全市场",
            refresh_versions=True,
        ),
        audit=audit,
        methods=[c.model_dump(mode="json") for c in cards],
        evaluation=dict(
            total=len(cases),
            development=sum(c.split == "development" for c in cases),
            holdout=sum(c.split == "holdout" for c in cases),
            executed=0,
            status="REGISTERED",
            predictive_validity="UNVERIFIED",
        ),
        experiment=experiment.model_dump(mode="json"),
        industry_templates=load_config("industry_templates.json"),
        candidate_business_profiles=[profile(name) for name in PROFILES],
        selection=load_config("sector_selection_record.json"),
        candidate_rules=load_config("screening_rules.json"),
        freshness_rules=load_config("freshness_rules.json"),
        limitations=[
            "P2接通有限公司原文研究、补查和首次观点台账；一致预期、完整质量指标及逆向估值实源仍缺，明确降级。",
            "来源表是有时间戳的实测快照；刷新本页面只重读台账，不冒充重新联网取数。",
            "八张方法卡为AI方法审核，未做外部专家审核；48例实际执行状态以封存验收记录为准。",
            "P1-N已接通真实取数、封存、模型预测和手动核对；实际开启状态在实验接口查看，预测价值尚未验证。",
            "两证券是实验篮子；开发选题不代表当前全市场热点排名。",
            "日行情活跃度、融资、ETF份额、供应商估算分别处理；缺Level-2则禁用订单簿方法。",
            "5.2历史档案与独立全收益门禁继续保留。",
        ],
    )
