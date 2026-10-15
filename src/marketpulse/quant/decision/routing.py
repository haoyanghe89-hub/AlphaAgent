"""Evidence-bound research admission and actual-use records; legacy cards stay unchanged."""

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import Field

from ..contracts import digest
from ..domain import FrozenModel
from .methods import load_methods

VERSION = "method-routing-2026-10-13-v1"
ACTIVE = {"AVAILABLE", "CONDITIONAL", "OBSERVATION_ONLY"}
ROLE_LABELS = {
    "EQUIPMENT": "半导体设备",
    "MANUFACTURING": "晶圆制造",
    "MIXED": "多业务待区分",
    "UNKNOWN": "专业业务待核定",
}


class ResearchProfile(FrozenModel):
    subject_id: str
    name: str
    industry: str
    primary_role: Literal["EQUIPMENT", "MANUFACTURING", "MIXED", "UNKNOWN"]
    business_roles: tuple[str, ...]
    role_label: str
    horizon_months: int = Field(ge=1, le=3)
    evidence_ids: tuple[str, ...]
    profile_source_ref: str
    geographic_exposure: Literal["NOT_NORMALIZED"] = "NOT_NORMALIZED"
    lifecycle: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    limitations: tuple[str, ...]


class RoutingPlan(FrozenModel):
    focus_questions: tuple[str, ...] = Field(min_length=1, max_length=3)
    primary_method_id: str
    auxiliary_method_ids: tuple[str, ...] = Field(max_length=4)
    selection_reason: str = Field(min_length=1, max_length=220)


def _profile(repo, data, snapshot):
    subject = snapshot["subject"]
    source = next((s for s in data["sources"] if s.get("key") == "profile"), None)
    if not source:
        raise ValueError("routing needs the sealed issuer identity profile")
    raw = repo.raw_bytes(source["blob_ref"])
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("business profile source hash differs")
    rows = json.loads(raw).get("jbzl", [])
    if len(rows) != 1:
        raise ValueError("business profile has ambiguous issuer")
    row = rows[0]
    if (
        row.get("SECURITY_CODE") != subject["security_id"].split(".")[0]
        or row.get("SECURITY_NAME_ABBR") != subject["name"]
        or str(row.get("ORG_PROFILE") or "")[:1500] != subject.get("description", "")
        or (row.get("EM2016") or row.get("INDUSTRYCSRC1")) != subject["industry"]
    ):
        raise ValueError("business profile does not match sealed subject")
    cutoff = datetime.fromisoformat(data["bundle"]["cutoff_at"])
    if datetime.fromisoformat(source["fetched_at"]) > cutoff:
        raise ValueError("identity profile was fetched after the sealed cutoff")
    selected = set(snapshot["target_evidence_ids"])
    originals = [e for e in data["bundle"]["items"] if e["evidence_id"] in selected]
    if len(originals) != len(selected) or any(
        e["subject_id"] != subject["security_id"]
        or e["kind"] != "disclosure"
        or datetime.fromisoformat(e["available_at"]) > cutoff
        for e in originals
    ):
        raise ValueError("routing disclosure identity or cutoff differs")
    description = subject.get("description", "")
    current = " ".join(e["text"] for e in originals)
    equipment = bool(
        re.search(
            r"(?:主营|从事|专注|研发|开发|研制|生产|销售|供应|提供).{0,35}(?:刻蚀设备|薄膜沉积设备|半导体设备)|(?:刻蚀设备|薄膜沉积设备|半导体设备).{0,25}(?:研发|生产|销售)",
            description,
        )
    )
    # Buying equipment is a manufacturing input, not a supplier business.
    if re.search(
        r"(?:采购|购置|购买).{0,12}(?:半导体设备|刻蚀设备)", description
    ) and not re.search(r"销售|供应|设备业务", description):
        equipment = False
    manufacturing = bool(re.search(r"晶圆代工|晶圓代工", description))
    if re.search(r"(?:剥离|退出|不再从事|停止).{0,18}(?:刻蚀|薄膜沉积|设备业务)", current):
        equipment = False
    if re.search(
        r"不(?:从事|研发|生产|供应|销售).{0,18}(?:半导体设备|刻蚀设备)", description + current
    ):
        equipment = False
    if re.search(r"(?:当前|现今|目前|主营).{0,12}(?:仅|转向)?.{0,6}(?:晶圆代工|代工)", current):
        manufacturing = True
    roles = (["EQUIPMENT"] if equipment else []) + (["MANUFACTURING"] if manufacturing else [])
    role = roles[0] if len(roles) == 1 else "MIXED" if roles else "UNKNOWN"
    return ResearchProfile(
        subject_id=subject["security_id"],
        name=subject["name"],
        industry=subject["industry"],
        primary_role=role,
        business_roles=tuple(roles),
        role_label=ROLE_LABELS[role],
        horizon_months=snapshot["horizon_months"],
        evidence_ids=tuple(sorted(selected)),
        profile_source_ref=source["blob_ref"],
        limitations=(
            "业务识别以封存身份资料和本公司原文为边界；供应商简介未代替完整分部审核。",
            "成长、周期、成熟阶段和地区收入敞口尚未规范化，未凭名称或价格贴标签。",
        ),
    ).model_dump(mode="json")


def _candidate(method_id, title, family, status, reason, refs=(), missing=(), notes=()):
    return dict(
        method_id=method_id,
        version=VERSION,
        title=title,
        family=family,
        implementation_status="REFERENCE_ONLY"
        if method_id in {f"M0{i}" for i in range(1, 9)}
        else "EXECUTABLE_LIMITED",
        status=status,
        reason=reason,
        input_refs=sorted(set(refs)),
        missing_inputs=list(missing),
        knowledge_note_ids=list(notes),
        horizon="MEDIUM_TERM",
        validation_status="FUNCTIONAL_ONLY",
    )


def _candidates(data, profile):
    items = {e["evidence_id"]: e for e in data["bundle"]["items"]}
    originals = profile["evidence_ids"]
    text = " ".join(items[i]["text"] for i in originals)
    notes = data.get("knowledge_notes", [])

    def note_ids(family):
        return [
            n["note_id"]
            for n in notes
            if family in n.get("method_ids", [])
            and n.get("implementation_status") != "DEFINITION_ONLY"
        ]

    candidates = []
    for card in load_methods():
        candidates.append(
            _candidate(
                card.method_id,
                card.title,
                card.method_id,
                "WRONG_SCOPE" if card.method_id == "M07" else "MISSING_INPUTS",
                "完整方法输入未齐；本次仅能选择已具备执行合同的有限变体。",
                missing=card.input_requirements,
                notes=note_ids(card.method_id),
            )
        )
    candidates.append(
        _candidate(
            "Q-DISCLOSURE",
            "经营原文与现金回收核查",
            "M01",
            "CONDITIONAL" if originals else "MISSING_INPUTS",
            "只解释本公司已取得原文，不等同标准化质量评分或完整审计。",
            originals,
            () if originals else ("company_disclosures",),
            note_ids("M01"),
        )
    )
    role = profile["primary_role"]
    exposure = [i for i in originals if re.search(r"刻蚀|薄膜沉积|MOCVD", items[i]["text"])]
    eq_allowed = (
        role == "EQUIPMENT"
        and bool(exposure)
        and data.get("industry_report", {}).get("template_status") == "CONDITIONAL_ENABLED"
    )
    candidates.append(
        _candidate(
            "EQ-TRANSMISSION",
            "设备需求到验收的条件传导",
            "M05",
            "WRONG_ROLE"
            if role != "EQUIPMENT"
            else "CONDITIONAL"
            if eq_allowed
            else "MISSING_INPUTS",
            "须有设备业务原文、行业背景与制造端比较；全部连接仍是待验证机制。",
            exposure,
            () if eq_allowed else ("equipment_exposure_and_industry_context",),
            note_ids("M05"),
        )
    )
    manufacturing_refs = [
        i for i in originals if re.search(r"产能|利用率|资本开支|资本支出|折旧", items[i]["text"])
    ]
    candidates.append(
        _candidate(
            "MFG-OPERATIONS",
            "制造产能与投入的有限核查",
            "MFG01",
            "WRONG_ROLE"
            if role != "MANUFACTURING"
            else "CONDITIONAL"
            if manufacturing_refs
            else "MISSING_INPUTS",
            "按制造业务核查利用率、资本支出及折旧线索；没有规范化序列，不启用完整制造模板。",
            manufacturing_refs,
            () if manufacturing_refs else ("manufacturer_operating_originals",),
        )
    )
    for method_id, title, family, prefix, reason in (
        (
            "FLOW-CHECK",
            "分渠道资金与经营分歧",
            "M04",
            "EV-FLOW",
            "融资和供应商估算分别观察，不推机构身份或相加。",
        ),
        (
            "EVENT-CHECK",
            "同期价格与事件竞争解释",
            "M06",
            "EV-ATTR",
            "仅同窗观察，主题代理不是行业指数，不认定事件因果贡献。",
        ),
        (
            "POLICY-CHECK",
            "政策条款与公司连接核查",
            "POLICY01",
            "EV-POL",
            "正式条款、状态和资格分别核查；未知资格只能条件讨论。",
        ),
    ):
        refs = [i for i in items if i.startswith(prefix)]
        candidates.append(
            _candidate(
                method_id,
                title,
                family,
                "OBSERVATION_ONLY" if refs else "MISSING_INPUTS",
                reason,
                refs,
                () if refs else ("sealed_" + family.lower(),),
                note_ids(family),
            )
        )
    # A company name, generic header, or price move cannot establish growth style.
    if not text and originals:
        raise ValueError("empty business originals")
    return candidates


def _build(repo, data, snapshot):
    if snapshot["schema_version"] != VERSION or not 1 <= snapshot["horizon_months"] <= 3:
        raise ValueError("unsupported routing version or horizon")
    profile = _profile(repo, data, snapshot)
    return dict(schema_version=VERSION, profile=profile, candidates=_candidates(data, profile))


def prepare(repo, data, subject, horizon):
    data = copy.deepcopy(data)
    snapshot = dict(
        schema_version=VERSION,
        subject=copy.deepcopy(subject),
        horizon_months=horizon,
        target_evidence_ids=sorted(
            e["evidence_id"]
            for e in data["bundle"]["items"]
            if e["kind"] == "disclosure" and e["subject_id"] == subject["security_id"]
        ),
    )
    data["routing_snapshot"] = snapshot
    data["method_routing"] = _build(repo, data, snapshot)
    return data


def allowed_primary(data):
    candidates = {c["method_id"]: c for c in data["method_routing"]["candidates"]}
    for method in ("EQ-TRANSMISSION", "MFG-OPERATIONS", "Q-DISCLOSURE"):
        if candidates[method]["status"] in ACTIVE:
            return [method]
    return []


def guard_plan(plan, data):
    plan = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
    if plan["primary_method_id"] not in allowed_primary(data):
        raise ValueError("主方法不符合业务类型或封存输入；从allowed_primary选择")
    ids = [plan["primary_method_id"], *plan["auxiliary_method_ids"]]
    candidates = {c["method_id"]: c for c in data["method_routing"]["candidates"]}
    if (
        len(ids) != len(set(ids))
        or len(ids) > 5
        or any(i not in candidates or candidates[i]["status"] not in ACTIVE for i in ids)
    ):
        raise ValueError("方法重复、未实现或必要输入不足，不能启用")
    required = {
        c["method_id"]
        for c in candidates.values()
        if c["method_id"] in {"Q-DISCLOSURE", "FLOW-CHECK", "EVENT-CHECK", "POLICY-CHECK"}
        and c["status"] in ACTIVE
    }
    if not required <= set(ids):
        raise ValueError("必须保留已有经营、资金、事件及可用政策的交叉核查，不得省略反对视角")


def select(data, plan):
    guard_plan(plan, data)
    data = copy.deepcopy(data)
    plan = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else copy.deepcopy(plan)
    data["routing_plan"] = plan
    data["method_routing"].update(
        {k: plan[k] for k in ("primary_method_id", "auxiliary_method_ids", "selection_reason")}
    )
    return data


def model_context(data):
    routing = data["method_routing"]
    candidates = routing["candidates"]
    if data.get("routing_plan"):
        ids = {
            data["routing_plan"]["primary_method_id"],
            *data["routing_plan"]["auxiliary_method_ids"],
        }
        candidates = [c for c in candidates if c["method_id"] in ids]
    else:
        candidates = [c for c in candidates if c["status"] in ACTIVE]
    return dict(
        version=VERSION,
        profile={
            k: v
            for k, v in routing["profile"].items()
            if k not in {"evidence_ids", "limitations", "profile_source_ref"}
        },
        allowed_primary=allowed_primary(data),
        methods=[
            {
                k: c[k]
                for k in ("method_id", "title", "family", "status", "reason", "knowledge_note_ids")
            }
            for c in candidates
        ],
        plan=data.get("routing_plan"),
        disabled_summary="规范化质量、逆向估值、完整因子组合缺输入，未启用。制造仅原文有限核查。",
    )


def _execution_records(data, proposal, review):
    plan = data["routing_plan"]
    approved = {d.claim_id for d in review.decisions if d.verdict == "APPROVE"}
    candidates = {c["method_id"]: c for c in data["method_routing"]["candidates"]}
    output = proposal.model_dump(mode="json")
    result = []
    for method_id in [plan["primary_method_id"], *plan["auxiliary_method_ids"]]:
        candidate = candidates[method_id]
        if method_id == "Q-DISCLOSURE":
            paths = ["claims"]
            reviews = [c.claim_id for c in proposal.claims]
            refs = sorted({i for c in proposal.claims for i in c.support_ids + c.counter_ids})
        elif method_id in {"EQ-TRANSMISSION", "MFG-OPERATIONS"}:
            paths, reviews, refs = (
                ["industry_assessment", "method_assessment"],
                ["INDUSTRY", "METHODS"],
                sorted(
                    set(proposal.method_assessment.evidence_ids)
                    | set(proposal.industry_assessment.evidence_ids)
                ),
            )
        elif method_id == "FLOW-CHECK":
            paths, reviews = ["flow_assessments"], ["FLOW-5", "FLOW-20", "FLOW-60"]
            refs = sorted({i for a in proposal.flow_assessments for i in a.evidence_ids})
        elif method_id == "EVENT-CHECK":
            paths, reviews, refs = (
                ["attribution_assessment"],
                ["ATTRIBUTION"],
                candidate["input_refs"],
            )
        else:
            paths, reviews, refs = (
                ["policy_assessment"],
                ["POLICY"],
                list(proposal.policy_assessment.evidence_ids),
            )
        if not set(reviews) <= approved or any(path not in output for path in paths):
            raise ValueError("selected method lacks approved actual output")
        result.append(
            dict(
                method_id=method_id,
                title=candidate["title"],
                version=VERSION,
                status="EXECUTED_OBSERVATION"
                if candidate["status"] == "OBSERVATION_ONLY"
                else "EXECUTED_LIMITED",
                input_evidence_ids=refs,
                admitted_input_evidence_ids=candidate["input_refs"],
                output_paths=paths,
                review_ids=reviews,
                source_note_ids=candidate["knowledge_note_ids"],
                limitations=[candidate["reason"]],
                output_hash=digest({p: output[p] for p in paths}),
            )
        )
    return result


def finish(data, proposal, review):
    data = copy.deepcopy(data)
    data["method_routing"]["execution_records"] = _execution_records(data, proposal, review)
    return data


def verify(repo, data, proposal=None, review=None):
    actual = data["method_routing"]
    core = _build(repo, data, data["routing_snapshot"])
    if any(actual.get(k) != v for k, v in core.items()):
        raise ValueError("method admission differs from sealed identity or input")
    selected = select({**data, "method_routing": core}, data["routing_plan"])["method_routing"]
    if any(actual.get(k) != v for k, v in selected.items()):
        raise ValueError("method selection differs from sealed plan")
    if proposal is not None and actual.get("execution_records") != _execution_records(
        data, proposal, review
    ):
        raise ValueError("actual method use differs from approved role output")
