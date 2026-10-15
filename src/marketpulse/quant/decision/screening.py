"""Evidence-bound candidate opinions; one lease, source budget and four-role review."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ..contracts import digest
from ..domain import FrozenModel
from . import agents, candidate_sources
from .contracts import CandidateDecision, ScreeningResult
from .evidence import verify_excerpt
from .next_session.agents import DailyRoles
from .tools import Stopped

LEGACY_PROMPT_VERSION = "candidate-2026-10-13-v1"
V2_PROMPT_VERSION = "candidate-2026-10-13-v2"
PROMPT_VERSION = "candidate-2026-10-14-v3"
PROMPTS = dict(
    Planner=agents.COMMON
    + (
        "输出focus_questions，最多三个。候选名单和顺序由程序固定，不增删或按记忆推荐。"
        "核查经营、融资分歧、估值与同业可比性。"
    ),
    Analyst=agents.COMMON
    + (
        "只输出schema完整JSON。candidates按selected原顺序逐个返回，不增加/删除公司。每家公司必须明确outlook=FAVORABLE或UNFAVORABLE，"
        "summary以倾向看多或倾向看空开头，期限为输入的中期，非明日判断。reason比较支持与反证，不承诺未来。"
        "stance是研究优先级，独立于方向。supporting_reasons/opposing_reasons分别一至两条，verification_conditions/invalidation_conditions各一至两条。"
        "comparison一句说明与至少一个输入同业的差异，peer_ids引用实际同业代码；只有一候选则peer_ids为空并说明可比对象不足。"
        "仅同期间合并报表可以有限对照，产品不同不能简单混排ROE。现金流与利润不同，不由成交活跃推机构资金。"
        "funding仅融资余额窗口差，不代表逐日同向或全市场；未知渠道不能猜方向。动态PE仅供应商快照，缺项/负值不评价便宜。"
        "不能声称经营龙头、已确认行业第一或安全排雷；当前风险档案不完整。公司披露归公司称。"
        "evidence_ids只能引该公司提供的证据，须含本公司业务原文；counter_ids引该公司反证，缺少直接反证时写范围限制不编造。"
        "总summary解释当前取舍与覆盖，不能把未深研公司判为差公司；零候选时说明原文或核心资料不足，不填方向。"
    ),
    Verifier=agents.COMMON
    + (
        "输出schema完整JSON。decisions对proposal每个security_id恰一次；只有本公司原文、财务和渠道观察支持才APPROVE，"
        "错引/跨期/主体/因果错配REJECT，缺支持NEED_EVIDENCE。核对明确方向的正反取舍及条件，不要求保证未来。"
        "核查同业产品与报表期间限制、动态PE定义、融资非机构、未深研非差、风险未知非安全、规模非经营龙头。"
        "summary_supported核查整体摘要与资料范围。任一项未批准不能发布，这是同模型职责复核，不是独立审计。"
    ),
    Writer=agents.PROMPTS["Writer"],
)
V1_PROMPTS = PROMPTS
PROMPTS = dict(V1_PROMPTS)
VALUATION_INSTRUCTION = (
    "本轮没有历史估值或已核定估值水平基准。任何正文（含摘要、风险和条件）均不得断言估值偏高、偏低、"
    "便宜、昂贵、高估或低估；只能说明供应商动态市盈率快照不能判断贵便宜，需补齐比较基准。"
)
PROMPTS["Analyst"] += VALUATION_INSTRUCTION
PROMPTS["Analyst"] += (
    "保持简洁以免截断：总summary最多两句，各候选summary一句三十字左右；reason五十字以内，"
    "支持/反对/验证/失效各一条，各正文一句三十字左右，comparison一句五十字左右，引用最多五个加一个反证。"
    "不同细分产品没有直接可比对象时peer_ids可以为空，但comparison必须明确产品不同及可比对象有限；不能强行混排。"
)
PROMPTS["Verifier"] += VALUATION_INSTRUCTION
V2_PROMPTS = PROMPTS
PROMPTS = dict(V2_PROMPTS)
for _role in ("Planner", "Analyst", "Verifier"):
    PROMPTS[_role] += (
        "根据business_profile使用独立行业模板。制造企业围绕晶圆、工艺、产能利用率、资本开支、折旧与现金流；"
        "没有规范化利用率或资本开支数据必须写待核查，不能推断满产或扩产兑现。"
        "制造企业不套设备订单/验收传导，代工与IDM及设备不可混排，不能根据分类认定具体客户关系。"
    )
PROMPTS_BY_VERSION = {
    LEGACY_PROMPT_VERSION: V1_PROMPTS,
    V2_PROMPT_VERSION: V2_PROMPTS,
    PROMPT_VERSION: PROMPTS,
}
TUSHARE_V4_PROMPT_VERSION = "candidate-tushare-2026-10-14-v4"
TUSHARE_V4_PROMPTS = {
    role: text
    + (
        "本次数据来源为Tushare，行业为申万2021三级分类中的沪深A股成员。"
        "PE字段是过去十二个月市盈率pe_ttm，不是预测市盈率；总市值由万元换算为元。"
        "公司简介是供应商转录，业务判断仍必须引用本公司已核查的公告原文。"
        "报表是当前取得的供应商版本，不代表严格历史时点可知版本。"
    )
    for role, text in PROMPTS.items()
}
PROMPTS_BY_VERSION[TUSHARE_V4_PROMPT_VERSION] = TUSHARE_V4_PROMPTS
TUSHARE_PROMPT_VERSION = "candidate-tushare-2026-10-14-v5"
TUSHARE_PROMPTS = dict(TUSHARE_V4_PROMPTS)
TUSHARE_PROMPTS["Analyst"] += (
    "每家公司必须基于已封存证据作有条件的看多或看空研究取舍。"
    "总摘要应概括实际方向与分歧，不得写不构成方向判断、暂无法判断或不填方向来否定已给出的结论。"
    "缺少完整利用率、资金或资本开支序列时，明确列为限制、反证与验证条件；不得编造指标。"
    "估值正文按本次pe_ttm称过去十二月市盈率，不称预测或动态市盈率。"
)
TUSHARE_PROMPTS["Verifier"] += (
    "核验对象是有条件的研究取舍，不是已经证实的未来收益。逐条检查事实、主体、引用与口径，"
    "区分公司称、供应商数据和分析者判断。缺少完整利用率、资金或资本开支序列本身，"
    "在已明确限制且未据此作事实断言时，不自动构成NEED_EVIDENCE。"
    "若具体断言没有支持、结论与反证自相矛盾、把供应商数据当独立核验，仍须拒绝或要求补证；"
    "不能只因未来方向尚未验证而拒绝所有研究判断。总摘要必须与每家实际方向一致。"
)
PROMPTS_BY_VERSION[TUSHARE_PROMPT_VERSION] = TUSHARE_PROMPTS
TUSHARE_V5_PROMPT_VERSION = TUSHARE_PROMPT_VERSION
TUSHARE_PROMPT_VERSION = "candidate-tushare-2026-10-14-v6"
TUSHARE_PROMPTS = dict(TUSHARE_PROMPTS)
TUSHARE_PROMPTS["Analyst"] += (
    "严格字段约定：funding、valuation、comparison必须是字符串，未知时也不能填null或数组。"
    "valuation统一填写：过去十二月市盈率快照不能判断贵便宜，比较基准未核定。"
    "不得在此句前后追加偏高、偏低、高估、低估等估值水平断言。"
    "融资缺项时funding填写：融资余额窗口差未取得，不推断全市场或机构资金方向。"
)
PROMPTS_BY_VERSION[TUSHARE_PROMPT_VERSION] = TUSHARE_PROMPTS

TUSHARE_MARGIN_PROMPT_VERSION = "candidate-tushare-2026-10-14-dated-margin-v7"
TUSHARE_MARGIN_PROMPTS = dict(TUSHARE_PROMPTS)
TUSHARE_MARGIN_PROMPTS["Analyst"] += (
    "funding_window、group、metrics、risk_status仅是输入，不得添加到输出；"
    "逐字段遵循CandidateOpinion schema。"
    "融资日期与价格日期不同，按funding_window核对渠道余额变化，不宣称同日资金配合。"
    "funding写融资余额增加、减少或不变及局限，不能把余额减少叫全市场净流出。"
)
PROMPTS_BY_VERSION[TUSHARE_MARGIN_PROMPT_VERSION] = TUSHARE_MARGIN_PROMPTS


class CandidateOpinion(FrozenModel):
    security_id: str
    outlook: Literal["FAVORABLE", "UNFAVORABLE"]
    stance: Literal["PRIORITIZE_RESEARCH", "WAIT_FOR_CONFIRMATION", "RISK_FIRST"]
    summary: str = Field(max_length=150)
    reason: str = Field(max_length=300)
    supporting_reasons: tuple[str, ...] = Field(min_length=1, max_length=2)
    opposing_reasons: tuple[str, ...] = Field(min_length=1, max_length=2)
    funding: str = Field(max_length=180)
    valuation: str = Field(max_length=180)
    comparison: str = Field(max_length=220)
    peer_ids: tuple[str, ...] = Field(max_length=2)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    counter_ids: tuple[str, ...] = Field(max_length=4)
    verification_conditions: tuple[str, ...] = Field(min_length=1, max_length=2)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1, max_length=2)


class CandidateAnalysis(FrozenModel):
    summary: str = Field(max_length=250)
    candidates: tuple[CandidateOpinion, ...] = Field(max_length=3)


class CandidateVerdict(FrozenModel):
    security_id: str
    verdict: Literal["APPROVE", "REJECT", "NEED_EVIDENCE"]
    reason: str


class CandidateReview(FrozenModel):
    decisions: tuple[CandidateVerdict, ...] = Field(max_length=3)
    summary_supported: bool


def context(data, horizon, version=None):
    selected = [r for r in data["members"] if r["security_id"] in data["selected"]]
    allowed = {r["security_id"] for r in selected}
    result = dict(
        horizon_months=horizon,
        selected=data["selected"],
        coverage=data["coverage"],
        limitations=data["limitations"],
        companies=[
            {
                k: r[k]
                for k in (
                    "security_id",
                    "business_group",
                    "name",
                    "group",
                    "metrics",
                    "period",
                    "funding_change",
                    "risk_status",
                    "leader_type",
                    "business_evidence_ids",
                    "gaps",
                )
            }
            for r in selected
        ],
        evidence=[
            {k: e[k] for k in ("evidence_id", "subject_id", "kind", "text", "period_end")}
            for e in data["bundle"]["items"]
            if e["subject_id"] in allowed
        ],
    )
    for company in result["companies"]:
        original = next(r for r in selected if r["security_id"] == company["security_id"])
        if original.get("funding_window"):
            company["funding_window"] = original["funding_window"]
    if version in {
        V2_PROMPT_VERSION,
        PROMPT_VERSION,
        TUSHARE_V4_PROMPT_VERSION,
        TUSHARE_V5_PROMPT_VERSION,
        TUSHARE_PROMPT_VERSION,
        TUSHARE_MARGIN_PROMPT_VERSION,
    }:
        result["valuation_protocol"] = "NO_UNBENCHMARKED_LEVEL_V2"
    if version in {
        PROMPT_VERSION,
        TUSHARE_V4_PROMPT_VERSION,
        TUSHARE_V5_PROMPT_VERSION,
        TUSHARE_PROMPT_VERSION,
        TUSHARE_MARGIN_PROMPT_VERSION,
    }:
        from .sector_profiles import profile

        result["business_profile"] = profile(data["universe"]["business_scope"].split("供应商")[0])
    if version in {
        TUSHARE_V4_PROMPT_VERSION,
        TUSHARE_V5_PROMPT_VERSION,
        TUSHARE_PROMPT_VERSION,
        TUSHARE_MARGIN_PROMPT_VERSION,
    }:
        result["data_supply"] = data["data_supply"]
    if version in {
        TUSHARE_V5_PROMPT_VERSION,
        TUSHARE_PROMPT_VERSION,
        TUSHARE_MARGIN_PROMPT_VERSION,
    }:
        result["direction_protocol"] = "EXPLICIT_CONDITIONAL_OUTLOOK"
    return result


def guard_valuation(text):
    for match in re.finditer(
        r"(?:估值|市盈率).{0,10}(?:偏高|偏低|过高|过低|便宜|昂贵|低估|高估)", text
    ):
        scope = text[max(0, match.start() - 12) : match.end()]
        if not re.search(r"不能|不宜|无法|未核|尚无|不代表|不等于|不判断|不评价|不支持", scope):
            raise ValueError("valuation level needs a supplied comparable benchmark")


def guard_analysis(proposal, ctx):
    if [c.security_id for c in proposal.candidates] != ctx["selected"]:
        raise ValueError("candidate membership/order must match deterministic selection")
    by_id = {e["evidence_id"]: e for e in ctx["evidence"]}
    companies = {r["security_id"]: r for r in ctx["companies"]}
    agents.guard_text(proposal.summary)
    if (
        ctx.get("direction_protocol")
        and ctx["selected"]
        and re.search(r"不构成方向判断|无法给出方向|暂无法判断|不填方向", proposal.summary)
    ):
        raise ValueError("summary must retain the explicit conditional outlooks")
    if ctx.get("valuation_protocol"):
        guard_valuation(proposal.summary)
    for candidate in proposal.candidates:
        row = companies[candidate.security_id]
        allowed = {
            e["evidence_id"] for e in ctx["evidence"] if e["subject_id"] == candidate.security_id
        }
        if not set(candidate.evidence_ids + candidate.counter_ids) <= allowed:
            raise ValueError("candidate citation must bind its own actual source")
        if not set(candidate.evidence_ids) & set(row["business_evidence_ids"]):
            raise ValueError("candidate needs its own company business original")
        if len(set(candidate.peer_ids)) != len(candidate.peer_ids) or not set(
            candidate.peer_ids
        ) <= set(ctx["selected"]) - {candidate.security_id}:
            raise ValueError("peer identity must be another supplied candidate")
        if len(ctx["selected"]) > 1 and not candidate.peer_ids:
            same_group = any(
                companies[p]["business_group"] == row["business_group"]
                for p in set(ctx["selected"]) - {candidate.security_id}
            )
            if same_group or not re.search(
                r"可比.*(?:不足|有限)|(?:不同|不宜).*混排", candidate.comparison
            ):
                raise ValueError("candidate needs a peer or an explicit non-comparability limit")
        if any(
            companies[p]["business_group"] != row["business_group"] for p in candidate.peer_ids
        ) and not re.search(
            r"不同|不直接|仅|可比性|不能|不可|不宜|不应|避免", candidate.comparison
        ):
            raise ValueError("different equipment businesses need explicit comparison limits")
        prefix = "倾向看多" if candidate.outlook == "FAVORABLE" else "倾向看空"
        if not candidate.summary.startswith(prefix):
            raise ValueError("candidate must give a consistent explicit direction")
        for text in (
            candidate.summary,
            candidate.reason,
            *candidate.supporting_reasons,
            *candidate.opposing_reasons,
            candidate.funding,
            candidate.valuation,
            candidate.comparison,
            *candidate.verification_conditions,
            *candidate.invalidation_conditions,
        ):
            agents.guard_text(text)
            if ctx.get("valuation_protocol"):
                guard_valuation(text)
            for clause in re.split(r"[。；，]", text):
                if re.search(
                    r"经营龙头|行业第一|机构(?:加仓|抢筹)|已排除全部风险|估值低估", clause
                ) and not re.search(r"不|未|无|待", clause):
                    raise ValueError("unsupported leadership, institutional intent or safety")
        if row["funding_change"] is None and not re.search(r"缺|未|不足|待", candidate.funding):
            raise ValueError("missing financing must remain unknown")
        if "pe" not in row["metrics"] and not re.search(r"缺|未|不足|待", candidate.valuation):
            raise ValueError("missing valuation cannot become cheapness")
        if any(by_id[i]["subject_id"] != candidate.security_id for i in candidate.counter_ids):
            raise ValueError("counter-source subject mismatch")
        if ctx.get("business_profile", {}).get("role") == "MANUFACTURING":
            text = "；".join(
                (
                    candidate.reason,
                    *candidate.supporting_reasons,
                    *candidate.opposing_reasons,
                    *candidate.verification_conditions,
                )
            )
            if not re.search(r"晶圆|晶圓|工艺|产能|利用率|资本开支|折旧", text):
                raise ValueError("manufacturing needs its independent business framework")
            for clause in re.split(r"[。；，]", text):
                if re.search(
                    r"满产|满负荷|产能利用率(?:提升|改善|上升)|订单(?:增长|改善)|验收(?:增长|改善)",
                    clause,
                ) and not re.search(r"不|未|待|若|需|缺|假设|核查|验证", clause):
                    raise ValueError("missing manufacturing operating inputs cannot become facts")


def guard_review(review, proposal):
    if [d.security_id for d in review.decisions] != [c.security_id for c in proposal.candidates]:
        raise ValueError("review must cover each candidate exactly once")
    for decision in review.decisions:
        agents.guard_text(decision.reason)


def screening_result(data):
    decisions = []
    ref = digest(data["universe"])
    for row in data["members"]:
        decisions.append(
            CandidateDecision(
                security_id=row["security_id"],
                universe_ref=ref,
                leader_type=row["leader_type"],
                business_evidence_ids=tuple(row["business_evidence_ids"]),
                eligibility="EXCLUDED"
                if row["excluded"]
                else "ELIGIBLE"
                if row["security_id"] in data["selected"]
                else "MISSING_DATA",
                dimension_results=tuple(
                    (k, row[k]) for k in ("business_group", "group", "risk_status")
                ),
                exclusion_reasons=tuple(row["gaps"]),
                comparison_refs=tuple(row["evidence_ids"]),
                rule_version="candidate-v1",
            )
        )
    return ScreeningResult(
        selected=tuple(d for d in decisions if d.eligibility == "ELIGIBLE"),
        excluded=tuple(d for d in decisions if d.eligibility == "EXCLUDED"),
        insufficient=tuple(d for d in decisions if d.eligibility == "MISSING_DATA"),
        coverage=data["coverage"],
        rule_version="candidate-v1",
    ).model_dump(mode="json")


async def run(repo, run_id, stop, settings, *, port=None, collector=None):
    state = repo.get(run_id, with_data=False)
    pinned_prompt_version = state.get("config", {}).get("prompt_version")
    before, started = state.get("elapsed_seconds", 0), time.monotonic()
    budget = state["request"]["budget_profile"]
    state.setdefault("charged_tokens", 0)
    state["config"] = dict(budget=budget, prompt_version=PROMPT_VERSION)

    def check():
        if stop.is_set():
            raise Stopped("候选研究已停止")
        if before + time.monotonic() - started >= budget["max_seconds"]:
            raise Stopped("候选研究达到共享时间上限")

    def save():
        check()
        state["elapsed_seconds"] = before + time.monotonic() - started
        repo.save(state)

    def step(key, detail):
        state["process"].append(
            dict(key=key, detail=detail, status="RUNNING", updated_at=datetime.now(UTC).isoformat())
        )
        save()

    try:
        if not state.get("candidate_snapshot_ref"):
            snapshot = await asyncio.to_thread(
                collector or candidate_sources.collect, repo, state, stop, save, step
            )
            state["candidate_snapshot_ref"] = repo.blob(snapshot)
            save()
        snapshot = repo.value(state["candidate_snapshot_ref"])
        data = candidate_sources.rebuild(repo, snapshot)
        from .candidate_margin import VERSION as TUSHARE_MARGIN_VERSION
        from .tushare_candidates import LEGACY_VERSION as TUSHARE_LEGACY_SOURCE_VERSION
        from .tushare_candidates import VERSION as TUSHARE_SOURCE_VERSION

        prompt_version = (
            TUSHARE_MARGIN_PROMPT_VERSION
            if snapshot["version"] == TUSHARE_MARGIN_VERSION
            else TUSHARE_PROMPT_VERSION
            if snapshot["version"] == TUSHARE_SOURCE_VERSION
            else TUSHARE_V4_PROMPT_VERSION
            if snapshot["version"] == TUSHARE_LEGACY_SOURCE_VERSION
            else PROMPT_VERSION
        )
        if any(c.get("role") and c["status"] == "COMPLETED" for c in state["calls"]):
            if pinned_prompt_version not in PROMPTS_BY_VERSION:
                raise ValueError("completed role checkpoint has no supported pinned prompt")
            prompt_version = pinned_prompt_version
        state["config"]["prompt_version"] = prompt_version
        ctx = context(data, state["request"]["horizon_months"], prompt_version)
        from .backup_model import open_backup

        async with (
            open_backup(settings, enabled=port is None, repo=repo, state=state) as backup,
            DailyRoles(
                repo,
                state,
                settings,
                save,
                check,
                port=port,
                prompts=PROMPTS_BY_VERSION[prompt_version],
                schema_version="decision-candidates-v1",
                output_tokens=3000,
                reserve_limit=40000,
                fallback_model=backup.model if backup else None,
                fallback_port=backup.port if backup else None,
                transport_attempts=3,
            ) as roles,
        ):

            async def call(role, schema, value, guard=None):
                for c in state["calls"]:
                    if c.get("role") == role and c["status"] == "COMPLETED":
                        old = repo.value(c["request_ref"])
                        if old["context"] == value and old["schema"] == schema.model_json_schema():
                            output = schema.model_validate(repo.value(c["response_ref"]))
                            if guard:
                                guard(output)
                            return output
                output, _ = await roles.call(role, schema, value, guard)
                return output

            step("planner", "固定候选与比较问题，不由模型改名单或顺序")
            plan = await call(
                "Planner",
                agents.Plan,
                {k: v for k, v in ctx.items() if k != "evidence"},
                lambda p: [agents.guard_text(q) for q in p.focus_questions],
            )
            state["plan"] = plan.model_dump(mode="json")
            step("analyst", "结合公司原文、同期间财务和融资解释取舍")
            proposal = await call(
                "Analyst", CandidateAnalysis, ctx, lambda p: guard_analysis(p, ctx)
            )
            step("verifier", "逐一复核候选、同业差异、方向与反证")
            review = await call(
                "Verifier",
                CandidateReview,
                dict(ctx, proposal=proposal.model_dump(mode="json")),
                lambda r: guard_review(r, proposal),
            )
            if not review.summary_supported or any(
                d.verdict != "APPROVE" for d in review.decisions
            ):
                raise ValueError("候选意见未全部通过复核，未签发；失败输出保留")
            approved = dict(
                summary=proposal.summary, approved_hash=digest(proposal.model_dump(mode="json"))
            )
            step("writer", "只发布通过复核的候选意见与原始证据")
            written = await call(
                "Writer",
                agents.Writing,
                approved,
                lambda w: (
                    None
                    if w.model_dump(mode="json") == approved
                    else (_ for _ in ()).throw(ValueError("writer changed approved content"))
                ),
            )
        data.update(
            role_records=[
                c for c in state["calls"] if c.get("role") and c["status"] == "COMPLETED"
            ],
            report=dict(
                stage="P5",
                summary=written.summary,
                horizon_months=state["request"]["horizon_months"],
                cutoff_at=snapshot["cutoff_at"],
                candidates=proposal.model_dump(mode="json")["candidates"],
                review_identity="AI_METHOD_REVIEWED",
                prediction_validity="UNVERIFIED",
                prompt_version=prompt_version,
            ),
            screening_result=screening_result(data),
            evidence=data["bundle"]["items"],
            candidate_snapshot_ref=state["candidate_snapshot_ref"],
            analysis_context_ref=repo.blob(ctx),
            analysis_ref=repo.blob(proposal.model_dump(mode="json")),
            review_ref=repo.blob(review.model_dump(mode="json")),
            writer_ref=repo.blob(written.model_dump(mode="json")),
        )
        check()
        verify(repo, data)
        state.update(
            status="COMPLETED",
            title=state["request"]["entity"] + " · 候选研究",
            result_ref=repo.blob(data),
            result_sha256=digest(data),
            stop_reason="APPROVED",
            completed_at=datetime.now(UTC).isoformat(),
        )
        state["process"].append(
            dict(key="done", detail="候选意见已封存，更新需新建记录", status="COMPLETED")
        )
        save()
    except asyncio.CancelledError:
        if not stop.is_set():
            state.update(status="INTERRUPTED", error="服务中断，已完成步骤保留")
            repo.save(state)
        raise
    except Exception as error:
        if not stop.is_set():
            state.update(
                status="FAILED",
                error=str(error),
                elapsed_seconds=before + time.monotonic() - started,
            )
            repo.save(state)


def verify(repo, data):
    snapshot = repo.value(data["candidate_snapshot_ref"])
    rebuilt = candidate_sources.rebuild(repo, snapshot)
    for key in (
        "universe",
        "members",
        "selected",
        "coverage",
        "bundle",
        "documents",
        "sources",
        "limitations",
    ):
        if data[key] != rebuilt[key]:
            raise ValueError("candidate source or calculation changed: " + key)
    if data["screening_result"] != screening_result(rebuilt):
        raise ValueError("candidate disposition changed")
    if rebuilt.get("data_supply") != data.get("data_supply"):
        raise ValueError("candidate data supply declaration changed")
    version = data["report"].get("prompt_version", LEGACY_PROMPT_VERSION)
    if version not in PROMPTS_BY_VERSION:
        raise ValueError("unknown candidate opinion version")
    ctx = context(rebuilt, data["report"]["horizon_months"], version)
    if repo.value(data["analysis_context_ref"]) != ctx:
        raise ValueError("candidate model input changed")
    proposal = CandidateAnalysis.model_validate(repo.value(data["analysis_ref"]))
    guard_analysis(proposal, ctx)
    review = CandidateReview.model_validate(repo.value(data["review_ref"]))
    guard_review(review, proposal)
    if not review.summary_supported or any(d.verdict != "APPROVE" for d in review.decisions):
        raise ValueError("candidate report lacks approval")
    expected = dict(
        stage="P5",
        summary=proposal.summary,
        horizon_months=data["report"]["horizon_months"],
        cutoff_at=snapshot["cutoff_at"],
        candidates=proposal.model_dump(mode="json")["candidates"],
        review_identity="AI_METHOD_REVIEWED",
        prediction_validity="UNVERIFIED",
    )
    if version != LEGACY_PROMPT_VERSION:
        expected["prompt_version"] = version
    if data["report"] != expected or data["evidence"] != data["bundle"]["items"]:
        raise ValueError("candidate report changed after review")
    records = data["role_records"]
    if [r["role"] for r in records] != ["Planner", "Analyst", "Verifier", "Writer"]:
        raise ValueError("candidate role checkpoint set changed")
    expected_contexts = [
        {k: v for k, v in ctx.items() if k != "evidence"},
        ctx,
        dict(ctx, proposal=proposal.model_dump(mode="json")),
        dict(summary=proposal.summary, approved_hash=digest(proposal.model_dump(mode="json"))),
    ]
    expected_schemas = [agents.Plan, CandidateAnalysis, CandidateReview, agents.Writing]
    expected_responses = [None, data["analysis_ref"], data["review_ref"], data["writer_ref"]]
    for record, call_context, schema, response_ref in zip(
        records, expected_contexts, expected_schemas, expected_responses, strict=True
    ):
        request = repo.value(record["request_ref"])
        if (
            request["context"] != call_context
            or request["schema"] != schema.model_json_schema()
            or request["instructions"] != PROMPTS_BY_VERSION[version][record["role"]]
            or request["prompt_version"] != version
        ):
            raise ValueError("candidate actual role input changed")
        if response_ref is not None and record["response_ref"] != response_ref:
            raise ValueError("candidate actual role response changed")
    if repo.value(data["writer_ref"]) != dict(
        summary=proposal.summary, approved_hash=digest(proposal.model_dump(mode="json"))
    ):
        raise ValueError("candidate writer binding changed")
    by_ref = {s["blob_ref"]: s["sha256"] for s in data["sources"]}
    for source in data["sources"]:
        raw = repo.raw_bytes(source["blob_ref"])
        if candidate_sources.hashlib_of(raw) != source["sha256"]:
            raise ValueError("candidate original hash mismatch")
    for value in data["bundle"]["items"]:
        from .contracts import EvidenceItem

        item = EvidenceItem.model_validate(value)
        if by_ref.get(item.raw_ref) != item.content_hash:
            raise ValueError("candidate evidence not in sealed source set")
        if item.kind == "disclosure":
            if not any(
                d["subject_id"] == item.subject_id and d["raw_ref"] == item.raw_ref
                for d in data["documents"]
            ):
                raise ValueError("candidate disclosure subject mismatch")
            verify_excerpt(item, data["documents"], repo.value)
        else:
            if item.source_family == "TUSHARE":
                from . import candidate_margin
                from .tushare_candidates import verified_numeric

                if item.method_version == candidate_margin.VERSION:
                    verified_numeric = candidate_margin.verified_numeric

                if item.value != verified_numeric(repo, item):
                    raise ValueError("Tushare candidate numeric source mismatch")
                continue
            original = json.loads(repo.raw_bytes(item.raw_ref))
            for part in item.locator.path.strip("/").split("/"):
                original = original[int(part)] if isinstance(original, list) else original[part]
            if item.value != candidate_sources.number(original):
                raise ValueError("candidate numeric source mismatch")
