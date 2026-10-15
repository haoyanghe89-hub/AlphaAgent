"""Bounded medium-term workflow on P1 evidence. Every role/checkpoint is reusable."""

import asyncio
import copy
import time
from datetime import UTC, datetime

from marketpulse.infrastructure.storage.models import BlobError

from ..contracts import digest
from . import agents, flow_sources, forecasts, industry_sources, knowledge, policy_sources, routing
from .backup_model import open_backup
from .contracts import (
    EvidenceBundle,
    EvidenceItem,
    ForecastRecord,
    Locator,
    ResearchOpinion,
    VerifiedResearch,
)
from .evidence import retrieve
from .expectations import unavailable
from .methods import load_methods
from .next_session.agents import DailyRoles
from .next_session.calendar import load_calendar
from .service import collect
from .tools import SourceClient, Stopped


def funding_context(data):
    report = data["flow_report"]
    included = {
        "MARGIN_CHANGE",
        "MARGIN_BUY",
        "MARGIN_REPAY",
        "MARGIN_INCREASE_RATIO",
        "MARGIN_RECONCILIATION",
        "MARGIN_MAX_RESIDUAL",
        "SECURITY_RETURN",
        "RELATIVE_RETURN",
        "VENDOR_NET",
        "ETF_ESTIMATE",
        "INDUSTRY_SHARE",
    }
    return dict(
        end_date=report["end_date"],
        limitations=report["limitations"],
        metric_units={
            m["metric_id"]: m["unit"]
            for m in report["windows"][0]["metrics"]
            if m["metric_id"] in included
        },
        windows=[
            dict(
                window=w["window"],
                start=w["sessions"][0] if w["sessions"] else None,
                baseline_date=w["baseline_date"],
                divergence=w["divergence"],
                metrics={
                    m["metric_id"]: m["value"] for m in w["metrics"] if m["metric_id"] in included
                },
            )
            for w in report["windows"]
        ],
    )


def industry_input(data, evidence_ids, *, routed=False):
    result = industry_sources.model_context(data, evidence_ids)
    if routed:
        result["recent_event_count"] = len(data["attribution_report"]["timeline"])
        result["recent_events"] = result["recent_events"][:2]
        result["price_windows"] = [
            {
                k: w[k]
                for k in (
                    "window",
                    "baseline_date",
                    "security_return",
                    "market_return",
                    "proxy_return",
                    "relative_market",
                    "relative_proxy",
                )
                if k in w
            }
            for w in result["price_windows"]
        ]
    return result


def model_context(state, data, evidence, *, supplements_open=True):
    docs = {d["raw_ref"]: d for d in data["documents"] + data.get("industry_documents", [])}
    context = dict(
        subject={k: state["subject"][k] for k in ("name", "security_id", "industry")},
        question=state["request"]["question"],
        horizon_months=state["request"]["horizon_months"],
        evidence=[
            dict(
                evidence_id=e["evidence_id"],
                text=e["text"],
                kind=e["kind"],
                source_family=e["source_family"],
                value=e["value"],
                unit=e["unit"],
                period_start=e["period_start"],
                period_end=e["period_end"],
                title=docs.get(e["raw_ref"], {}).get("title"),
                original_source_id=e["original_source_id"],
            )
            for e in evidence
        ],
        expectations="没有可靠一致预期与规范化现金流输入；不得评价超预期、低估或求目标价。",
        method_scope="参考经营拆解和反证框架；M01规范化质量指标、M02逆向估值、M05行业传导缺输入，未完整启用。",
        conclusion_protocol=agents.CONCLUSION_PROTOCOL,
        qualitative_only=True,
        supplements_open=supplements_open,
        limitations=[
            "只有有限公司原文片段；不是完整财报审计或独立行业核查。",
            "资金持续性和股价归因本阶段未纳入；不能由经营改善推明日上涨。",
        ],
    )
    if data.get("flow_report"):
        context["flow_context"] = funding_context(data)
        if data.get("flow_protocol"):
            context["flow_assessment_protocol"] = data["flow_protocol"]
        context["limitations"][1] = (
            "本次只纳入有完整窗口的分渠道资金观察；行业广度、ETF申赎和事件因果仍未核定。"
        )
    if data.get("industry_report"):
        context["industry_context"] = industry_input(
            data,
            {e["evidence_id"] for e in evidence},
            routed=bool(data.get("method_routing") or data.get("bounded_industry_context")),
        )
        context["method_scope"] = (
            "M05半导体设备条件传导与M06事件窗口观察已接入；边均为待验证机制，因果贡献未识别；M01规范化、M02逆向估值仍缺输入。"
        )
        context["limitations"][0] = (
            "公司原文、行业原文与制造端比较均有封存；部分来源不可用，不是完整审计或因果识别。"
        )
    if data.get("method_routing"):
        for item in context["evidence"]:
            if item["kind"] != "disclosure":
                for key in ("period_start", "period_end", "title", "original_source_id"):
                    item.pop(key, None)
        context["routing_context"] = routing.model_context(data)
        context["policy_context"] = policy_sources.model_context(data)
        usable = {e["evidence_id"] for e in context["evidence"]}
        for policy in context["policy_context"]["policy"]["policies"]:
            policy["evidence_ids"] = [i for i in policy["evidence_ids"] if i in usable]
        note_ids = sorted(
            {i for m in context["routing_context"]["methods"] for i in m["knowledge_note_ids"]}
        )
        context["knowledge_context"] = knowledge.model_context(data, note_ids)
        context["method_scope"] = (
            "只执行routing_context已准入方法；完整质量、逆向估值和因子组合未启用。"
        )
    return context


def planner_context(context):
    # Planning selects questions and admitted methods, not a second financial analysis.
    return {
        k: v
        for k, v in context.items()
        if k
        in {
            "subject",
            "question",
            "horizon_months",
            "routing_context",
            "method_scope",
            "qualitative_only",
            "conclusion_protocol",
        }
    }


def initial_evidence(data, repo=None):
    periodic = [d for d in data["documents"] if d["category"] == "periodic"]
    latest = max(
        periodic, key=lambda d: (d["published_at"] or "", d["period_end"] or ""), default=None
    )
    items = data["bundle"]["items"]
    # Original finite excerpts, not manufactured facts; follow-up reads different originals.
    disclosures = [e for e in items if latest and e["raw_ref"] == latest["raw_ref"]][:2]
    if repo and latest and disclosures:
        pages = repo.value(latest["text_ref"])["pages"]
        # Read the actual summary table before asking about causes. Accounting-policy
        # pages often repeat income/cash keywords and are poor initial business input.
        table = next(
            (
                (i, text)
                for i, text in enumerate(pages, 1)
                if "主要会计数据" in text
                and "营业收入" in text
                and "净利润" in text
                and "上年同期" in text
                and "目录" not in text
            ),
            None,
        )
        if table:
            page, text = table
            original = EvidenceItem.model_validate(disclosures[0])
            disclosures = [
                original.model_copy(
                    update=dict(
                        evidence_id=f"EV-{latest['document_id']}-{page}-0",
                        text=text[:1100],
                        locator=Locator(
                            kind="pdf",
                            path=latest["text_ref"],
                            page=page,
                            start=0,
                            end=min(1100, len(text)),
                        ),
                    )
                ).model_dump(mode="json")
            ]
            by_id = {e["evidence_id"]: e for e in items + disclosures}
            data["bundle"]["items"] = list(by_id.values())
            data["evidence"] = list(by_id.values())
    facts = sorted([e for e in items if e["kind"] == "fact"], key=lambda e: e["period_end"] or "")[
        -1:
    ]
    return disclosures + facts


def industry_initial_evidence(data, subject_id):
    exposure_ids = set(data["industry_report"]["target_exposure_refs"][:1])
    own = {
        d["raw_ref"] for d in data.get("industry_documents", []) if d["subject_id"] == subject_id
    }
    return [
        e
        for e in data["bundle"]["items"]
        if (e["kind"] == "industry" and not e["evidence_id"].startswith(("EV-EVENT", "EV-ATTR")))
        or e["evidence_id"] in exposure_ids
        or (
            e["kind"] == "disclosure"
            and e["evidence_id"].startswith("EV-IND-DEMAND-")
            and e["subject_id"] == subject_id
            and e["raw_ref"] in own
        )
    ]


def supplement(repo, data, query, evidence):
    seen = {e["raw_ref"] for e in evidence}
    documents = [
        dict(d, pages=repo.value(d["text_ref"])["pages"])
        for d in data["documents"]
        if d["raw_ref"] not in seen
    ]
    found = retrieve(documents, query, datetime.fromisoformat(data["bundle"]["cutoff_at"]), limit=4)
    return [e.model_dump(mode="json") for e in found]


def publication_ready(proposal, review):
    claim_ids = {c.claim_id for c in proposal.claims}
    approved = {
        d.claim_id for d in review.decisions if d.verdict == "APPROVE" and d.claim_id in claim_ids
    }
    return (
        bool(proposal.claims)
        and approved == {c.claim_id for c in proposal.claims}
        and review.opinion_supported
        and review.scenarios_supported
        and len(proposal.scenarios) == 3
        and all(set(s.claim_ids) <= approved for s in proposal.scenarios)
        and (
            not isinstance(proposal, agents.DirectionalAnalysis)
            or isinstance(review, agents.DirectionalReview)
            and review.conclusion_verdict == "APPROVE"
        )
        and (
            not isinstance(proposal, agents.FlowAnalysis)
            or isinstance(review, agents.FlowReview)
            and {
                d.claim_id
                for d in review.decisions
                if d.claim_id.startswith("FLOW-") and d.verdict == "APPROVE"
            }
            == {"FLOW-5", "FLOW-20", "FLOW-60"}
        )
        and (
            not isinstance(proposal, agents.IndustryAnalysis)
            or {
                d.claim_id
                for d in review.decisions
                if d.verdict == "APPROVE" and d.claim_id in {"INDUSTRY", "ATTRIBUTION"}
            }
            == {"INDUSTRY", "ATTRIBUTION"}
        )
        and (
            not isinstance(proposal, agents.RoutedAnalysis)
            or {
                d.claim_id
                for d in review.decisions
                if d.verdict == "APPROVE" and d.claim_id in {"METHODS", "POLICY"}
            }
            == {"METHODS", "POLICY"}
        )
    )


def permitted_claims(proposal, review):
    approved = {d.claim_id for d in review.decisions if d.verdict == "APPROVE"}
    return [c.model_dump(mode="json") for c in proposal.claims if c.claim_id in approved]


def review_input(context, proposal):
    result = dict(context, proposal=proposal.model_dump(mode="json"))
    if isinstance(proposal, agents.FlowAnalysis):
        result.pop("previous_review", None)
        result.pop("previous_claims", None)
    if isinstance(proposal, agents.IndustryAnalysis):
        cited = {i for c in proposal.claims for i in c.support_ids + c.counter_ids}
        cited.update(proposal.industry_assessment.evidence_ids)
        if isinstance(proposal, agents.RoutedAnalysis):
            cited.update(proposal.method_assessment.evidence_ids)
            cited.update(proposal.policy_assessment.evidence_ids)
        for a in proposal.flow_assessments:
            cited.update(a.evidence_ids)
        # Review every cited original and all supplied company counter-material.
        # Keep complete numeric windows/scope; omit unused originals and planner prose.
        result = {
            k: result[k]
            for k in (
                "subject",
                "evidence",
                "qualitative_only",
                "flow_context",
                "flow_assessment_protocol",
                "industry_context",
                "proposal",
            )
        }
        result["evidence"] = [
            e for e in context["evidence"] if e["evidence_id"] in cited or e["kind"] == "disclosure"
        ]
        if isinstance(proposal, agents.RoutedAnalysis):
            for key in ("routing_context", "policy_context", "knowledge_context"):
                result[key] = context[key]
    return result


def guard_final(repo, proposal, context):
    """Bind the narrowed input to actual earlier role outputs, without editing facts."""
    schema = agents.FlowAnalysis if context.get("flow_context") else agents.DirectionalAnalysis
    review_schema = agents.FlowReview if context.get("flow_context") else agents.DirectionalReview
    if context.get("industry_context"):
        schema, review_schema = agents.IndustryAnalysis, agents.IndustryReview
    if context.get("routing_context"):
        schema, review_schema = agents.RoutedAnalysis, agents.RoutedReview
    prior = schema.model_validate(repo.value(context["prior_analysis_ref"]))
    review = review_schema.model_validate(repo.value(context["prior_review_ref"]))
    agents.guard_review(review, prior)
    allowed = permitted_claims(prior, review)
    if context["permitted_claims"] != allowed or any(
        c.model_dump(mode="json") not in allowed for c in proposal.claims
    ):
        raise ValueError("final reconciliation differs from approved claims")
    final_schema = (
        agents.FinalFlowAnalysis if context.get("flow_context") else agents.FinalDirectionalAnalysis
    )
    if context.get("industry_context"):
        final_schema = agents.FinalIndustryAnalysis
    if context.get("routing_context"):
        final_schema = agents.FinalRoutedAnalysis
    final_schema.model_validate(proposal.model_dump(mode="json"))
    agents.guard_analysis(proposal, context)


def approved_result(proposal, review, bundle, horizon):
    decisions = {d.claim_id: d.verdict for d in review.decisions}
    approved = tuple(
        c.model_copy(update={"verification_status": "APPROVE"})
        for c in proposal.claims
        if decisions[c.claim_id] == "APPROVE"
    )
    ids = {c.claim_id for c in approved}
    scenarios = tuple(s for s in proposal.scenarios if set(s.claim_ids) <= ids)
    opinion_ok = publication_ready(proposal, review)
    if isinstance(proposal, agents.DirectionalAnalysis) and not opinion_ok:
        raise ValueError("明确结论未通过复核，本次未签发；原记录保留，请补充取证后重新研究。")
    if not opinion_ok:
        scenarios = ()
    verified = VerifiedResearch(claims=approved, evidence=bundle, scenarios=scenarios)
    opinion = ResearchOpinion(
        stance=proposal.stance if opinion_ok else "INSUFFICIENT_EVIDENCE",
        outlook=proposal.outlook if opinion_ok else "UNDETERMINED",
        horizon_months=horizon,
        scenario_refs=tuple(s.scenario_id for s in scenarios),
        approved_claim_ids=tuple(c.claim_id for c in approved),
        supporting_reasons=tuple(c.text for c in approved),
        opposing_reasons=proposal.opposing_reasons
        if opinion_ok
        else ("现有材料尚不能支撑完整的前瞻判断。",),
        catalysts=proposal.catalysts if opinion_ok else (),
        invalidation_conditions=proposal.invalidation_conditions
        if opinion_ok
        else ("新披露与当前经营解释相矛盾时重新研究。",),
        next_checks=proposal.next_checks if opinion_ok else ("补齐经营原因、现金回收和估值约束。",),
        limitations=(
            "同一模型分角色复核，尚无外部独立验证。",
            "资金持续性、行业传导与估值位置尚未完整纳入，不能据此判断低估或超预期。",
            "中期情景属于条件性研究判断，不能继承为下一交易日方向。",
        )
        if not isinstance(proposal, agents.IndustryAnalysis)
        else (
            "同一模型分角色复核，尚无外部独立验证。",
            "行业连接为条件机制，价格观察不能识别公司与行业的独立贡献；估值基准仍有缺口。",
            "中期情景不能继承为下一交易日方向。",
        ),
    )
    return (
        verified,
        opinion,
        proposal.summary
        if opinion_ok
        else "现有资料还不足以判断未来方向，先核查经营原因与现金回收。",
    )


def directional_conclusion(proposal):
    """Only a reviewed model choice can supply direction; never default to a side."""
    if not isinstance(proposal, agents.DirectionalAnalysis):
        return None
    selected = [c for c in proposal.claims if c.claim_id in proposal.direction_claim_ids]
    return dict(
        protocol=agents.CONCLUSION_PROTOCOL,
        scope="MEDIUM_TERM_PRICE_BIAS",
        reason=proposal.direction_reason,
        claim_ids=list(proposal.direction_claim_ids),
        support_level="WEAK"
        if any(c.support_level in {"WEAK", "INSUFFICIENT"} for c in selected)
        else "MODERATE",
        limitations="这是现有证据下的方向取舍，估值、资金及行业核查仍有缺口；不是确定涨跌或校准概率。",
    )


def approved_payload(summary, opinion, conclusion=None):
    payload = dict(summary=summary, opinion=opinion.model_dump(mode="json"))
    if conclusion is not None:
        payload["conclusion"] = conclusion
    return payload


def verify_medium(repo, data):
    bundle = EvidenceBundle.model_validate(data["bundle"])
    context = repo.value(data["analysis_context_ref"])
    version = data["forecast"]["prompt_version"]
    if version == agents.LEGACY_PROMPT_VERSION:
        analysis_schema, review_schema = agents.Analysis, agents.Review
    elif version in agents.DIRECTION_PROMPT_VERSIONS:
        analysis_schema, review_schema = agents.DirectionalAnalysis, agents.DirectionalReview
    elif version in {
        *agents.FLOW_PROMPT_VERSIONS,
        agents.INDUSTRY_PROMPT_VERSION,
        agents.ROUTING_PROMPT_VERSION,
    }:
        analysis_schema, review_schema = (
            (agents.IndustryAnalysis, agents.IndustryReview)
            if version in {agents.INDUSTRY_PROMPT_VERSION, agents.ROUTING_PROMPT_VERSION}
            else (agents.FlowAnalysis, agents.FlowReview)
        )
        flow_sources.verify(repo, data)
        expected_context = funding_context(data)
        if context.get("flow_context") != expected_context:
            raise ValueError("funding model input differs from sealed calculations")
        if version in {
            agents.FLOW_PROMPT_VERSION,
            agents.INDUSTRY_PROMPT_VERSION,
            agents.ROUTING_PROMPT_VERSION,
        } and (
            data.get("flow_protocol") != agents.FLOW_PROTOCOL
            or context.get("flow_assessment_protocol") != agents.FLOW_PROTOCOL
        ):
            raise ValueError("required channel assessment protocol missing")
        if version in {agents.INDUSTRY_PROMPT_VERSION, agents.ROUTING_PROMPT_VERSION}:
            industry_sources.verify(repo, data)
            if context.get("industry_context") != industry_input(
                data,
                {e["evidence_id"] for e in context["evidence"]},
                routed=version == agents.ROUTING_PROMPT_VERSION
                or bool(data.get("bounded_industry_context")),
            ):
                raise ValueError("industry model input differs from sealed originals")
        if version == agents.ROUTING_PROMPT_VERSION:
            analysis_schema, review_schema = agents.RoutedAnalysis, agents.RoutedReview
            knowledge.verify(repo, data)
            policy_sources.verify(repo, data)
            expected_policy = policy_sources.model_context(data)
            usable = {e["evidence_id"] for e in context["evidence"]}
            for policy in expected_policy["policy"]["policies"]:
                policy["evidence_ids"] = [i for i in policy["evidence_ids"] if i in usable]
            if (
                context.get("routing_context") != routing.model_context(data)
                or context.get("policy_context") != expected_policy
            ):
                raise ValueError("method or policy context differs from sealed input")
            ids = sorted(
                {i for m in context["routing_context"]["methods"] for i in m["knowledge_note_ids"]}
            )
            if context.get("knowledge_context") != knowledge.model_context(data, ids):
                raise ValueError("method knowledge context differs")
            if repo.value(data["routing_plan_ref"]) != data["routing_plan"]:
                raise ValueError("actual selected methods differ from Planner output")
    else:
        raise ValueError("unknown medium-term conclusion version")
    if (
        version
        in {
            agents.PROMPT_VERSION,
            *agents.FLOW_PROMPT_VERSIONS,
            agents.INDUSTRY_PROMPT_VERSION,
            agents.ROUTING_PROMPT_VERSION,
        }
        and context.get("qualitative_only") is not True
    ):
        raise ValueError("required qualitative text guard missing")
    proposal = analysis_schema.model_validate(repo.value(data["analysis_ref"]))
    agents.guard_analysis(proposal, context)
    if context.get("final_reconciliation"):
        guard_final(repo, proposal, context)
    allowed = {e.evidence_id: e for e in bundle.items}
    for e in context["evidence"]:
        actual = allowed.get(e["evidence_id"])
        if not actual or any(
            e[k] != actual.model_dump(mode="json")[k]
            for k in (
                "text",
                "kind",
                "source_family",
                "value",
                "unit",
                "original_source_id",
                "period_start",
                "period_end",
            )
            if k in e or version != agents.ROUTING_PROMPT_VERSION
        ):
            raise ValueError("model input differs from sealed evidence")
    review = review_schema.model_validate(repo.value(data["review_ref"]))
    agents.guard_review(review, proposal)
    if isinstance(proposal, agents.RoutedAnalysis):
        routing.verify(repo, data, proposal, review)
        if data["report"].get("method_assessment") != proposal.method_assessment.model_dump(
            mode="json"
        ) or data["report"].get("policy_assessment") != proposal.policy_assessment.model_dump(
            mode="json"
        ):
            raise ValueError("method or policy result differs from approved output")
    if isinstance(proposal, agents.IndustryAnalysis) and repo.value(
        data["review_context_ref"]
    ) != review_input(context, proposal):
        raise ValueError("industry review input differs from cited originals and scope")
    if isinstance(proposal, agents.FlowAnalysis) and data["report"].get("flow_analysis") != [
        a.model_dump(mode="json") for a in proposal.flow_assessments
    ]:
        raise ValueError("published funding explanation differs from reviewed output")
    if isinstance(proposal, agents.IndustryAnalysis) and (
        data["report"].get("industry_analysis")
        != proposal.industry_assessment.model_dump(mode="json")
        or data["report"].get("attribution_analysis")
        != proposal.attribution_assessment.model_dump(mode="json")
    ):
        raise ValueError("published industry explanation differs from reviewed output")
    verified, opinion, summary = approved_result(
        proposal, review, bundle, data["forecast"]["horizon_months"]
    )
    if (
        data["verified_research"] != verified.model_dump(mode="json")
        or data["report"]["opinion"] != opinion.model_dump(mode="json")
        or data["report"]["summary"] != summary
    ):
        raise ValueError("published report differs from reviewed output")
    conclusion = directional_conclusion(proposal)
    if data["report"].get("conclusion") != conclusion:
        raise ValueError("published direction rationale differs from reviewed output")
    writing = agents.Writing.model_validate(repo.value(data["writer_ref"]))
    approved_hash = digest(approved_payload(summary, opinion, conclusion))
    if writing.summary != summary or writing.approved_hash != approved_hash:
        raise ValueError("writer introduced unapproved content")
    record = ForecastRecord.model_validate(data["forecast"])
    if (
        record.evidence_hash != digest(data["bundle"])
        or record.outlook != opinion.outlook
        or record.stance != opinion.stance
    ):
        raise ValueError("forecast does not bind reviewed report")


async def run(repo, run_id, stop, settings, *, port=None, collector=collect):
    state = repo.get(run_id, with_data=False)
    prior, started = state.get("elapsed_seconds", 0), time.monotonic()
    budget = state["request"]["budget_profile"]

    def check():
        if stop.is_set():
            raise Stopped("研究已停止")
        if prior + time.monotonic() - started >= budget["max_seconds"]:
            raise Stopped("研究达到累计时间上限")

    def save():
        check()
        state["elapsed_seconds"] = prior + time.monotonic() - started
        repo.save(state)

    def step(key, detail, status="RUNNING"):
        node = next((n for n in state["process"] if n["key"] == key), None)
        if node is None:
            node = dict(key=key)
            state["process"].append(node)
        node.update(detail=detail, status=status, updated_at=datetime.now(UTC).isoformat())
        save()

    try:
        async with asyncio.timeout(max(0, budget["max_seconds"] - prior)):
            if not state.get("evidence_ref"):
                await asyncio.to_thread(collector, repo, state, stop, publish=False)
            data = copy.deepcopy(repo.value(state["evidence_ref"]))
            repo.verify_data(data, state["evidence_sha256"], state["subject"]["security_id"])
            state.setdefault("charged_tokens", state.get("tokens", 0))
            flows_enabled = state["request"].get("include_flows", False)
            industry_enabled = state["request"].get("include_industry", False)
            methods_enabled = state["request"].get("include_methods", False)
            if industry_enabled and not methods_enabled:
                data["bounded_industry_context"] = "two-event-context-v2"
            prompt_version = agents.FLOW_PROMPT_VERSION if flows_enabled else agents.PROMPT_VERSION
            analysis_schema = agents.FlowAnalysis if flows_enabled else agents.DirectionalAnalysis
            review_schema = agents.FlowReview if flows_enabled else agents.DirectionalReview
            final_schema = (
                agents.FinalFlowAnalysis if flows_enabled else agents.FinalDirectionalAnalysis
            )
            if industry_enabled:
                prompt_version = agents.INDUSTRY_PROMPT_VERSION
                analysis_schema, review_schema, final_schema = (
                    agents.IndustryAnalysis,
                    agents.IndustryReview,
                    agents.FinalIndustryAnalysis,
                )
            if methods_enabled:
                prompt_version = agents.ROUTING_PROMPT_VERSION
                analysis_schema, review_schema, final_schema = (
                    agents.RoutedAnalysis,
                    agents.RoutedReview,
                    agents.FinalRoutedAnalysis,
                )
            state["config"] = dict(budget=budget, prompt_version=prompt_version)
            state.setdefault("rounds", [])
            state.setdefault("evidence_calls", 0)
            state.setdefault("evidence_queries", [])
            if not state.get("observation_calendar_ref"):
                step("calendar", "核对观察窗口交易日；缺年度日历不猜日期")
                try:
                    client = SourceClient(repo, state, save, stop, max_requests=24)
                    calendar = await asyncio.to_thread(
                        load_calendar, client, state["subject"]["exchange"]
                    )
                    state["observation_calendar_ref"] = repo.blob(
                        dict(calendar=calendar.model_dump(mode="json"))
                    )
                except ValueError:
                    state["observation_calendar_ref"] = repo.blob(None)
                step("calendar", "已记录实际日历可用范围", "COMPLETED")
            data["observation_calendar"] = repo.value(state["observation_calendar_ref"])
            if methods_enabled:
                step("methods", "读取作者公开方法片段与相关官方政策，记录实际覆盖")
                for key, module in (("knowledge", knowledge), ("policy", policy_sources)):
                    snapshot_key = key + "_snapshot_ref"
                    if not state.get(snapshot_key):
                        client = SourceClient(repo, state, save, stop, max_requests=52)
                        snapshot = await asyncio.to_thread(
                            module.collect, repo, client, state["subject"]
                        )
                        state[snapshot_key] = repo.blob(snapshot)
                        save()
            if industry_enabled and not state.get("industry_snapshot_ref"):
                step("industry", "读取行业原始发布、制造端披露与同窗其他公告")
                client = SourceClient(repo, state, save, stop, max_requests=52)
                snapshot = await asyncio.to_thread(
                    industry_sources.collect,
                    repo,
                    client,
                    state["subject"],
                    data["observation_calendar"],
                )
                state["industry_snapshot_ref"] = repo.blob(snapshot)
                save()
            if flows_enabled:
                step("flows", "取得真实融资、价格与分渠道资金观察，固定交易日窗口")
                if not state.get("flow_snapshot_ref"):
                    client = SourceClient(
                        repo, state, save, stop, max_requests=52 if industry_enabled else 40
                    )
                    snapshot = await asyncio.to_thread(
                        flow_sources.collect,
                        repo,
                        client,
                        state["subject"],
                        data["observation_calendar"],
                    )
                    state["flow_snapshot_ref"] = repo.blob(snapshot)
                    save()
                data = flow_sources.attach(repo, data, repo.value(state["flow_snapshot_ref"]))
                data["flow_protocol"] = agents.FLOW_PROTOCOL
                step("flows", "原始响应已封存；完整窗口才计算，缺项与冲突单独保留", "COMPLETED")
            if industry_enabled:
                initial_evidence(data, repo)
                data = industry_sources.attach(
                    repo, data, repo.value(state["industry_snapshot_ref"])
                )
                step(
                    "industry",
                    "行业连接与时间线已封存；日期公告从下一交易日观察，不推因果贡献",
                    "COMPLETED",
                )
            if methods_enabled:
                data = knowledge.attach(repo, data, repo.value(state["knowledge_snapshot_ref"]))
                data = policy_sources.attach(repo, data, repo.value(state["policy_snapshot_ref"]))
                data = routing.prepare(
                    repo, data, state["subject"], state["request"]["horizon_months"]
                )
                step("methods", "业务画像与方法准入已核对，政策资格及版本未知项保留", "COMPLETED")
            async with (
                open_backup(settings, enabled=port is None, repo=repo, state=state) as backup,
                DailyRoles(
                    repo,
                    state,
                    settings,
                    save,
                    check,
                    port=port,
                    prompts=agents.ROUTING_PROMPTS
                    if methods_enabled
                    else agents.INDUSTRY_PROMPTS
                    if industry_enabled
                    else agents.FLOW_PROMPTS
                    if flows_enabled
                    else agents.PROMPTS,
                    schema_version="decision-medium-method-policy-v1"
                    if methods_enabled
                    else "decision-medium-industry-v1"
                    if industry_enabled
                    else "decision-medium-flows-v1"
                    if flows_enabled
                    else "decision-medium-direction-v4",
                    output_tokens=3000 if methods_enabled else 3500,
                    reserve_limit=40000,
                    fallback_model=backup.model
                    if backup
                    else "deepseek-v4-pro"
                    if settings.model == "deepseek-flash" and port is None
                    else None,
                    fallback_port=backup.port if backup else None,
                    transport_attempts=3,
                ) as roles,
            ):

                async def call(role, schema, context, guard=None):
                    # Reuse completed exact input checkpoints; failures still charge budget.
                    for old in state["calls"]:
                        if old.get("role") == role and old["status"] == "COMPLETED":
                            request = repo.value(old["request_ref"])
                            if (
                                request.get("context") == context
                                and request.get("schema") == schema.model_json_schema()
                            ):
                                value = schema.model_validate(repo.value(old["response_ref"]))
                                if guard:
                                    guard(value)
                                return value, old
                    return await roles.call(role, schema, context, guard)

                evidence = initial_evidence(data, repo)
                if flows_enabled:
                    evidence += [
                        e
                        for e in data["bundle"]["items"]
                        if e["source_family"] == "DECISION_FLOW_CALC"
                        and e["evidence_id"].split("-")[-1]
                        in {"MARGIN_CHANGE", "SECURITY_RETURN", "VENDOR_NET"}
                    ]
                if industry_enabled:
                    chosen = industry_initial_evidence(data, state["subject"]["security_id"])
                    evidence = list({e["evidence_id"]: e for e in evidence + chosen}.values())
                if methods_enabled:
                    evidence += [
                        e
                        for e in data["bundle"]["items"]
                        if e["source_family"] in {"POLICY_CN", "POLICY_US"}
                    ]
                    evidence = list({e["evidence_id"]: e for e in evidence}.values())
                    evidence = [
                        e
                        for e in evidence
                        if not (
                            e["source_family"] in {"POLICY_CN", "POLICY_US"}
                            and e["locator"]["path"].split(":")[-1]
                            not in {"body", "advanced", "equipment"}
                        )
                    ]
                context = model_context(state, data, evidence)
                step("planner", "组织经营变化、反对理由与未来验证问题")
                plan, _ = await call(
                    "Planner",
                    routing.RoutingPlan if methods_enabled else agents.Plan,
                    planner_context(context),
                    lambda p: (
                        [agents.guard_text(q) for q in p.focus_questions],
                        routing.guard_plan(p, data) if methods_enabled else None,
                        agents.guard_text(p.selection_reason) if methods_enabled else None,
                    ),
                )
                state["plan"] = plan.model_dump(mode="json")
                if methods_enabled:
                    data = routing.select(data, plan)
                    data["routing_plan_ref"] = repo.blob(plan.model_dump(mode="json"))
                step("planner", "已固定问题与资料范围", "COMPLETED")
                for round_no in range(budget["max_rounds"] + 1):
                    checkpoint = next((r for r in state["rounds"] if r["round"] == round_no), None)
                    open_loop = (
                        round_no < budget["max_rounds"]
                        and state["evidence_calls"] < budget["max_evidence_calls"]
                    )
                    context = model_context(state, data, evidence, supplements_open=open_loop)
                    context["focus_questions"] = plan.focus_questions
                    # JSON-normalize tuples to make resume identity stable.
                    import json

                    from ..contracts import canonical

                    context = json.loads(canonical(context))
                    previous_round = next(
                        (r for r in state["rounds"] if r["round"] == round_no - 1), None
                    )
                    if previous_round:
                        context["previous_review"] = previous_round.get("review")
                        context["previous_claims"] = previous_round["proposal"]["claims"]
                        if data.get("bounded_industry_context"):
                            feedback = previous_round["review"]
                            context["previous_review"] = dict(
                                approved_ids=[
                                    d["claim_id"]
                                    for d in feedback["decisions"]
                                    if d["verdict"] == "APPROVE"
                                ],
                                decisions=[
                                    d for d in feedback["decisions"] if d["verdict"] != "APPROVE"
                                ],
                                conclusion_verdict=feedback.get("conclusion_verdict"),
                                conclusion_reason=feedback.get("conclusion_reason"),
                                opinion_supported=feedback["opinion_supported"],
                            )
                            context["previous_claims"] = [
                                {
                                    k: v
                                    for k, v in c.items()
                                    if k
                                    in (
                                        "claim_id",
                                        "text",
                                        "support_ids",
                                        "counter_ids",
                                        "mechanism",
                                    )
                                }
                                for c in previous_round["proposal"]["claims"]
                            ]
                    if checkpoint and checkpoint.get("context_ref"):
                        context = repo.value(checkpoint["context_ref"])
                    if round_no and flows_enabled:
                        # Optional loops must leave room for review, not spend the
                        # remaining budget on an analysis that cannot be checked.
                        def reserve(role, schema, value):
                            return (
                                len(
                                    (
                                        canonical(value)
                                        + roles.prompts[role]
                                        + canonical(schema.model_json_schema())
                                    ).encode("utf8")
                                )
                                + roles.output_tokens
                            )

                        analysis_reserve = reserve("Analyst", analysis_schema, context)
                        if previous_round is None:
                            raise ValueError("补证缺少前轮状态")
                        prior_proposal = analysis_schema.model_validate(previous_round["proposal"])
                        review_reserve = reserve(
                            "Verifier", review_schema, review_input(context, prior_proposal)
                        )
                        if (
                            analysis_reserve > roles.reserve_limit
                            or state["charged_tokens"] + analysis_reserve + review_reserve
                            > budget["max_tokens"]
                        ):
                            state["stop_reason"] = (
                                "补查后的分析与复核预留超出剩余预算，保留新增材料并收束已批准判断"
                            )
                            break
                    step("analysis", "分析经营原因并检查是否需要补查原文")
                    proposal, analysis_call = await call(
                        "Analyst",
                        analysis_schema,
                        context,
                        lambda p, ctx=context: agents.guard_analysis(p, ctx),
                    )
                    review_context = review_input(context, proposal)
                    review, review_call = await call(
                        "Verifier",
                        review_schema,
                        review_context,
                        lambda r, candidate=proposal: agents.guard_review(r, candidate),
                    )
                    round_record = dict(
                        round=round_no,
                        proposal=proposal.model_dump(mode="json"),
                        review=review.model_dump(mode="json"),
                        evidence_ids=[e["evidence_id"] for e in evidence],
                        analysis_ref=analysis_call["response_ref"],
                        review_ref=review_call["response_ref"],
                        context_ref=repo.blob(context),
                    )
                    old_round = next((r for r in state["rounds"] if r["round"] == round_no), None)
                    if old_round is None:
                        state["rounds"].append(round_record)
                    else:
                        old_round.update(round_record)
                        round_record = old_round
                    save()
                    if flows_enabled and publication_ready(proposal, review):
                        state["stop_reason"] = (
                            "当前判断和资金窗口均已复核；未来验证问题保留，不再消耗补查预算"
                        )
                        break
                    needs = {d.claim_id for d in review.decisions if d.verdict == "NEED_EVIDENCE"}
                    gaps = sorted(
                        proposal.gaps,
                        key=lambda g: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[g.materiality], g.gap_id),
                    )
                    if not open_loop or not gaps:
                        state["stop_reason"] = (
                            "补查预算/轮次已到" if not open_loop else "复核未要求进一步补查"
                        )
                        break
                    added = round_record.get("new_evidence", [])
                    for gap in [] if added else gaps:
                        if flows_enabled and added:
                            break
                        if state["evidence_calls"] >= budget["max_evidence_calls"]:
                            break
                        if gap.claim_id not in needs and gap.materiality != "HIGH":
                            continue
                        query = agents.gap_query(gap)
                        key = digest(
                            dict(
                                query=query,
                                subject=state["subject"]["security_id"],
                                input=state["evidence_sha256"],
                            )
                        )
                        if key in state["evidence_queries"]:
                            continue
                        state["evidence_queries"].append(key)
                        state["evidence_calls"] += 1
                        step("supplement", "补查目的：" + gap.need)
                        new = await asyncio.to_thread(
                            supplement, repo, data, query, evidence + added
                        )
                        if flows_enabled:
                            # Retrieval is relevance-ordered. Reserve room for reviewed closure
                            # inside the same budget rather than sending overlapping excerpts.
                            new = new[:1]
                        added.extend(new)
                        round_record["new_evidence"] = copy.deepcopy(added)
                        round_record.setdefault("supplements", []).append(
                            dict(
                                query=query,
                                purpose=gap.need,
                                new_evidence_ids=[e["evidence_id"] for e in new],
                                new_original_hashes=sorted({e["content_hash"] for e in new}),
                                note="定向读取本次基础取证已归档、此前未提供分析角色的其他原文；同文重复不算新增。",
                            )
                        )
                        save()
                    if not added:
                        state["stop_reason"] = "没有新增原文，停止无增益补查"
                        break
                    evidence.extend(added)
                    # Save original locators exactly; add new excerpts to the sealed input bundle.
                    by_id = {e["evidence_id"]: e for e in data["bundle"]["items"] + added}
                    data["bundle"]["items"] = list(by_id.values())
                    data["evidence"] = list(by_id.values())
                    step("supplement", "新增原文已提供分析角色，重新检验原因", "COMPLETED")
                if not publication_ready(proposal, review) and permitted_claims(proposal, review):
                    # One closing pass within the same cumulative budget; no new retrieval.
                    allowed = permitted_claims(proposal, review)
                    cited = {i for c in allowed for i in c["support_ids"] + c["counter_ids"]}
                    context = model_context(
                        state,
                        data,
                        [
                            e
                            for e in evidence
                            if e["evidence_id"] in cited
                            or e["source_family"] == "DECISION_FLOW_CALC"
                            and e["evidence_id"].split("-")[-1]
                            in {"MARGIN_CHANGE", "SECURITY_RETURN", "VENDOR_NET"}
                            or industry_enabled
                            and e["kind"] == "industry"
                        ],
                        supplements_open=False,
                    )
                    context.update(
                        final_reconciliation=True,
                        permitted_claims=allowed,
                        prior_analysis_ref=analysis_call["response_ref"],
                        prior_review_ref=review_call["response_ref"],
                    )
                    step("reconciliation", "删去未通过的解释，只用获准依据重新收束明确方向与情景")
                    proposal, analysis_call = await call(
                        "Analyst",
                        final_schema,
                        context,
                        lambda p, ctx=context: guard_final(repo, p, ctx),
                    )
                    review_context = review_input(context, proposal)
                    review, review_call = await call(
                        "Verifier",
                        review_schema,
                        review_context,
                        lambda r, candidate=proposal: agents.guard_review(r, candidate),
                    )
                    state["final_reconciliation"] = dict(
                        context_ref=repo.blob(context),
                        analysis_ref=analysis_call["response_ref"],
                        review_ref=review_call["response_ref"],
                        proposal=proposal.model_dump(mode="json"),
                        review=review.model_dump(mode="json"),
                    )
                    step("reconciliation", "收束结果已再次复核，沿用原发布门槛", "COMPLETED")
                bundle = EvidenceBundle.model_validate(data["bundle"])
                verified, opinion, summary = approved_result(
                    proposal, review, bundle, state["request"]["horizon_months"]
                )
                step("analysis", "已保留通过复核的判断，未通过的解释不进入结论", "COMPLETED")
                conclusion = directional_conclusion(proposal)
                approved_hash = digest(approved_payload(summary, opinion, conclusion))

                def guard_writer(w):
                    if w.summary != summary or w.approved_hash != approved_hash:
                        raise ValueError("Writer改变了批准内容")

                step("writer", "将批准结论整理为用户结果")
                _, writer_call = await call(
                    "Writer",
                    agents.Writing,
                    dict(summary=summary, approved_hash=approved_hash),
                    guard_writer,
                )
                issued = datetime.now(UTC)
                if methods_enabled:
                    data = routing.finish(data, proposal, review)
                parent = (
                    forecasts.forecast(repo, state["request"]["parent_run_id"])
                    if state["request"].get("parent_run_id")
                    else None
                )
                if parent and parent["subject_id"] != state["subject"]["security_id"]:
                    raise ValueError("更新研究与原证券不一致")
                record = ForecastRecord(
                    forecast_id="FC-" + run_id,
                    run_id=run_id,
                    subject_id=state["subject"]["security_id"],
                    issued_at=issued,
                    cutoff_at=bundle.cutoff_at,
                    method_versions=tuple(
                        c.method_id
                        + "@"
                        + c.version
                        + (
                            "(CONDITIONAL)"
                            if industry_enabled and c.method_id == "M05"
                            else "(OBSERVATION_ONLY)"
                            if industry_enabled and c.method_id == "M06"
                            else "(LIMITED_OR_DISABLED)"
                        )
                        for c in load_methods()
                        if c.method_id
                        in (
                            {"M01", "M02", "M04", "M05", "M06", "M08"}
                            if industry_enabled
                            else {"M01", "M02", "M04", "M08"}
                            if flows_enabled
                            else {"M01", "M02", "M08"}
                        )
                    ),
                    model_version=analysis_call["model"],
                    prompt_version=prompt_version,
                    outlook=opinion.outlook,
                    stance=opinion.stance,
                    horizon_months=opinion.horizon_months,
                    benchmark_refs=(
                        state["subject"]["security_id"] + ":RAW_CLOSE",
                        "sh000300:RAW_CLOSE",
                        "INDUSTRY:UNAVAILABLE",
                        *(
                            ("sh512480:RAW_CLOSE:THEME_PROXY_NOT_INDUSTRY",)
                            if industry_enabled
                            else ()
                        ),
                    ),
                    return_basis="PRICE",
                    catalysts=opinion.catalysts,
                    invalidation_conditions=opinion.invalidation_conditions,
                    evidence_hash=digest(data["bundle"]),
                    quality_status="PARTIAL",
                    supersedes_id=parent["forecast_id"] if parent else None,
                )
                if methods_enabled:
                    record = record.model_copy(
                        update={
                            "method_versions": tuple(
                                r["method_id"] + "@" + r["version"] + "(" + r["status"] + ")"
                                for r in data["method_routing"]["execution_records"]
                            )
                        }
                    )
                data.update(
                    analysis_ref=analysis_call["response_ref"],
                    review_ref=review_call["response_ref"],
                    writer_ref=writer_call["response_ref"],
                    analysis_context_ref=repo.blob(context),
                    verified_research=verified.model_dump(mode="json"),
                    forecast=record.model_dump(mode="json"),
                )
                data["report"] = dict(
                    stage="P4K"
                    if methods_enabled
                    else "P4"
                    if industry_enabled
                    else "P3"
                    if flows_enabled
                    else "P2",
                    title=state["subject"]["name"] + " · 中期研究",
                    summary=summary,
                    conclusion=conclusion,
                    opinion=opinion.model_dump(mode="json"),
                    scenarios=[s.model_dump(mode="json") for s in verified.scenarios],
                    claims=[c.model_dump(mode="json") for c in verified.claims],
                    expectations=unavailable(
                        "一致预期、规范化现金流及固定估值假设尚未齐备；本次方向不以超预期或估值高低作为依据。"
                    ).model_dump(mode="json"),
                    research_status="PARTIAL" if verified.claims else "INSUFFICIENT",
                    cutoff_at=bundle.cutoff_at.isoformat(),
                    gaps=data["report"]["gaps"],
                    review_identity="AI_METHOD_REVIEWED",
                    method_context=data["report"]["method_context"],
                    price_position="已取得有限未复权收盘观察，尚未计算价格分位。",
                    valuation_position="估值基准未齐，不以低估或高估支持本次方向。",
                    limitations=list(opinion.limitations),
                )
                if flows_enabled:
                    data["report"]["flow_analysis"] = [
                        a.model_dump(mode="json") for a in proposal.flow_assessments
                    ]
                if industry_enabled:
                    data["review_context_ref"] = repo.blob(review_context)
                    data["report"]["industry_analysis"] = proposal.industry_assessment.model_dump(
                        mode="json"
                    )
                    data["report"]["attribution_analysis"] = (
                        proposal.attribution_assessment.model_dump(mode="json")
                    )
                if methods_enabled:
                    data["report"]["method_assessment"] = proposal.method_assessment.model_dump(
                        mode="json"
                    )
                    data["report"]["policy_assessment"] = proposal.policy_assessment.model_dump(
                        mode="json"
                    )
                verify_medium(repo, data)
                step("writer", "用户结果与首次观点即将一起封存", "COMPLETED")
                result_ref = repo.blob(data)
                repo.verify_data(data, digest(data), state["subject"]["security_id"])
                check()
                state.update(
                    status="COMPLETED",
                    result_ref=result_ref,
                    result_sha256=digest(data),
                    forecast_id=record.forecast_id,
                    completed_at=issued.isoformat(),
                    error=None,
                    title=data["report"]["title"],
                    elapsed_seconds=prior + time.monotonic() - started,
                )
                forecasts.record_forecast(repo, record, state=state)
    except BaseException as error:
        stop.set()
        latest = repo.get(run_id, with_data=False)
        if latest["status"] == "RUNNING":
            interrupted = isinstance(error, (TimeoutError, asyncio.CancelledError, Stopped))
            latest.update(
                status="INTERRUPTED" if interrupted else "FAILED",
                error=str(error)[:250]
                if isinstance(error, (ValueError, BlobError))
                else f"研究执行中断（{type(error).__name__}）",
                elapsed_seconds=prior + time.monotonic() - started,
            )
            repo.save(latest)
        if not isinstance(error, Exception) and not isinstance(error, asyncio.CancelledError):
            raise
