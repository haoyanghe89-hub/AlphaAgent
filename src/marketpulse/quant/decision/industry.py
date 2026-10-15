"""Method/template retrieval is metadata, never contemporary business evidence."""

from .methods import load_config, select_methods

DOMAIN_POLICY = "semiconductor-domain-2026-10-13-v1"


def business_role(subject):
    import re

    profile = subject.get("description", "")
    if re.search(r"晶圆代工|晶圓代工", profile):
        return "MANUFACTURING"
    if re.search(
        r"(?:研发|研制|生产|制造|销售|供应).{0,55}(?:刻蚀|薄膜沉积|半导体设备)|(?:半导体设备|刻蚀设备).{0,55}(?:研发|研制|生产|制造|销售)",
        profile,
    ):
        return "EQUIPMENT"
    return "UNCONFIGURED"


def metric_priority(business_role, metric_scope):
    primary = {
        "EQUIPMENT": {"EQUIPMENT_BILLINGS", "MANUFACTURER_CAPEX"},
        "MANUFACTURING": {"UTILIZATION", "WAFER_SHIPMENTS", "MANUFACTURER_CAPEX"},
    }
    if business_role not in primary:
        raise ValueError("unconfigured business role")
    return "PRIMARY_CONTEXT" if metric_scope in primary[business_role] else "BACKGROUND_ONLY"


def same_identity(expected, actual):
    return all(expected.get(k) and expected[k] == actual.get(k) for k in ("security_id", "name"))


def direct_transmission(source_scope, exposure):
    # Missing scope NEVER becomes an implicit match.
    return all(
        source_scope.get(k) and source_scope[k] == exposure.get(k)
        for k in ("geography", "product", "process")
    )


def order_supported(disclosure_type):
    return disclosure_type == "VERIFIED_ORDER_DISCLOSURE"


def independent_statistics(records):
    return sorted({r["underlying_statistics"] for r in records})


def manufacturing_chain(original_refs):
    steps = [
        (
            "客户需求与产品结构",
            "产能利用率与晶圆出货",
            "若客户需求匹配工艺与产能，才可能转为利用率及出货改善。",
            "核查晶圆尺寸、工艺、客户类型与实际利用率",
        ),
        (
            "产能利用率与售价",
            "毛利及折旧负担",
            "利用率变化可能改变固定成本分摊，售价与产品结构也会影响利润。",
            "分别核查售价、结构、折旧与费用，不把相关性写成因果",
        ),
        (
            "资本开支与扩产",
            "未来产能与现金支出",
            "投入不等于立即可用产能，设备交期、建设与需求会影响兑现。",
            "核查资本开支币种、建设进度、融资和新增折旧",
        ),
        (
            "利润与营运资金",
            "经营现金回收",
            "利润、营运资金与资本支出共同影响现金，不以收入增长替代回款。",
            "区分经营现金流、自由现金流和资本开支",
        ),
    ]
    return [
        dict(
            edge_id="MFG-EDGE-" + str(i + 1),
            from_label=a,
            to_label=b,
            status="HYPOTHESIS",
            evidence_ids=sorted(set(original_refs))[:4],
            mechanism=m,
            next_check=c,
        )
        for i, (a, b, m, c) in enumerate(steps)
    ]


def available_versions(records, cutoff):
    from datetime import datetime

    return [
        r
        for r in records
        if datetime.fromisoformat(r["available_at"]) <= datetime.fromisoformat(cutoff)
    ]


def context(subject, documents, items, cutoff):
    inputs = set()
    if any(d["category"] == "periodic" for d in documents):
        inputs.add("business_disclosures")
    if any(d.get("period_start") and d.get("period_end") for d in documents):
        inputs.add("period_identity")
    if any(e.kind == "fact" for e in items):
        inputs.add("daily_prices")
    if any(e.kind == "flow" for e in items):
        inputs.add("margin_series")
    # An extracted PDF does not imply audited, normalized financial statements.
    return dict(
        subject_industry=subject["industry"],
        available_inputs=sorted(inputs),
        methods=[
            m.model_dump(mode="json")
            for m in select_methods("single", "MEDIUM_TERM", inputs, cutoff)
        ],
        templates=load_config("industry_templates.json"),
        note="方法配置与行业模板不是当期事实；必要输入不足的方法保持停用。",
    )
