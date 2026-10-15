"""P1 evidence report deliberately makes no research opinion or forecast."""

from .disclosures import LABELS


def build(subject, bundle, documents, catalog_coverage, gaps, methods):
    counts = {kind: sum(d["category"] == kind for d in documents) for kind in LABELS}
    return dict(
        stage="P1",
        title=subject["name"] + " · 取证报告",
        headline=f"取得{len(documents)}份原文，整理{len(bundle.items)}条可定位证据",
        summary="已整理本次取得的资料与缺口，尚未生成经营判断或涨跌预测。",
        research_status="PARTIAL" if bundle.items else "INSUFFICIENT",
        material_counts=counts,
        cutoff_at=bundle.cutoff_at.isoformat(),
        gaps=gaps,
        catalog_coverage=catalog_coverage,
        model_execution="NOT_RUN_IN_P1",
        review_identity="DETERMINISTIC_CHECKED",
        scope_note="单股有限取证，不代表全行业覆盖、独立审计或完整历史回放。",
        limitations=[
            "片段为原文摘取，公司解释不等于独立证明。",
            "定期报告、业绩材料和调研分别检索，未取得的类型明确列缺口。",
            "没有生成投资建议、涨跌概率或真实预测成绩。",
        ],
        method_context=methods,
    )
