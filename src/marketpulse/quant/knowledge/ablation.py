"""Frozen, bounded method-context comparison; no historical prediction scoring."""

import copy
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from pydantic import Field

from ..contracts import digest
from ..decision.next_session.agents import DailyRoles
from . import research_agents as agents
from .research_context import analyst_context, review_context
from .research_store import BUDGET

VERSION = "investment-method-ablation-v1"
REPAIRED_VERSION = "investment-method-ablation-v2"
TOKEN_VERSION = "investment-method-ablation-v6"
ARMS = ("NO_METHOD", "FIXED_TREND", "ADAPTIVE_ROUTE")
COMMON_RULES = {"KR-CAPACITY-LIQUIDITY", "KR-AVOID-RUIN", "KR-EXPECTATION-EXIT"}
ROUTE_RULES = {
    "TREND_SWING": {"KR-TREND-MARKET", "KR-TREND-ENTRY-DISCIPLINE"},
    "QUALITY_GROWTH": {"KR-QUALITY-ECONOMICS", "KR-GROWTH-RETURN", "KR-DCF-DISTRESS"},
}
PROMPT_VERSION = VERSION
PROMPTS = {
    role: agents.prompts(agents.TECHNICAL_PROMPT_VERSION)[role].replace(
        "method_rule_ids引用输入提供的方法规则；不能用方法原文替代股票事实。",
        "method_rule_ids只引用输入方法；methods为空必须返回空数组，不能发明方法ID。",
    )
    + "这是同一封存输入的论证功能比较，不是历史预测测验。只比较本次可见信息。"
    "没有路线定义时仅据公开证据分析，不能暗自补路线。有路线时遵守其条件，不增加交易参数。"
    for role in ("Analyst", "Verifier")
}


class ComparisonOpinion(agents.Opinion):
    method_rule_ids: tuple[str, ...] = Field(min_length=0, max_length=3)


class ComparisonAnalysis(agents.Analysis):
    opinions: tuple[ComparisonOpinion, ...] = Field(min_length=1, max_length=4)


def config_for(version):
    result = dict(prompt_version=version, budget=BUDGET)
    if version in {REPAIRED_VERSION, TOKEN_VERSION}:
        from .structured_contract import configure

        configure(result, False)
    if version == TOKEN_VERSION:
        from ..decision.next_session.input_reservation import POLICY

        result["input_reservation"] = dict(POLICY)
        result["json_minimum_tokens"] = 1000
    return result


def prompts_for(version):
    if version == VERSION:
        return PROMPTS
    if version not in {REPAIRED_VERSION, TOKEN_VERSION}:
        raise ValueError("未知方法对照版本")
    result = dict(PROMPTS)
    result["Analyst"] = agents.role_prompts(
        dict(
            prompt_version=agents.CHECKS_PROMPT_VERSION,
            execution_contract="investment-structured-v2",
        )
    )["Analyst"]
    result["Analyst"] += (
        "methods为空时method_rule_ids必须空；有方法时只引用输入规则。单次封存输入论证对照，不是历史预测测验。"
    )
    if version == TOKEN_VERSION:
        result["Analyst"] += (
            "evidence_ids每项最多四个；逐只覆盖citation_requirements非空的技术、资金、政策类别。"
            "先分配各类引用到支持和反证，再写定性判断；不得遗漏类别。"
        )
        result["Verifier"] += (
            "verdict必须和decisions、direction_supported、conditions_respected一致；"
            "全部事实APPROVE且方向、条件均真时必须APPROVE，否则说明对应拒绝项。"
        )
    return result


def guard_review(review, analysis, version):
    agents.guard_review(review, analysis)
    if version == TOKEN_VERSION and any(
        r.verdict == "REJECT"
        and r.direction_supported
        and r.conditions_respected
        and all(d.verdict == "APPROVE" for d in r.decisions)
        for r in review.reviews
    ):
        raise ValueError("复核结论与逐项事实、方向及条件检查矛盾，必须重新复核，不能代写批准")


def context_for(base, security_id, horizon, sessions, arm):
    if arm not in ARMS:
        raise ValueError("未知方法对照组")
    result = copy.deepcopy(base)
    stock = next(s for s in result["stocks"] if s["security_id"] == security_id)
    result.update(horizon=horizon, horizon_sessions=sessions, stocks=[stock])
    result["evidence"] = [f for f in result["evidence"] if f["security_id"] == security_id]
    if arm == "NO_METHOD":
        selected = None
        stock.update(routes=[], eligible_routes=[], preferred_route=None)
        result["methods"] = []
    else:
        selected = "TREND_SWING"
        if (
            arm == "ADAPTIVE_ROUTE"
            and horizon == "MEDIUM"
            and "QUALITY_GROWTH" in stock["eligible_routes"]
        ):
            selected = "QUALITY_GROWTH"
        if selected not in stock["eligible_routes"]:
            raise ValueError("对照路线未准入")
        stock.update(
            routes=[r for r in stock["routes"] if r["route_id"] == selected],
            eligible_routes=[selected],
            preferred_route=selected,
        )
        rules = COMMON_RULES | ROUTE_RULES[selected]
        result["methods"] = [m for m in result["methods"] if m["rule_id"] in rules]
        if not result["methods"]:
            raise ValueError("对照路线缺少固定方法")
    plan = dict(
        selections=[
            dict(
                security_id=security_id,
                route_id=selected,
                rationale="按事先冻结的对照协议选择，非模型自由改变。",
                focus=["核查支持、反证与路线条件。"],
            )
        ]
    )
    return result, plan


def freeze(repo, state, model, *, version=VERSION):
    if state["status"] != "COMPLETED":
        raise ValueError("需要已核验完成的公开研究输入")
    base = repo.value(state["public_context_ref"])
    cases = [
        dict(security_id="000333.SZ", horizon="MEDIUM", sessions=60),
        dict(security_id="600036.SH", horizon="SWING", sessions=20),
    ]
    scenarios = []
    for index, case in enumerate(cases):
        # Counterbalance order; every case and arm is declared before the first call.
        order = ARMS if index == 0 else tuple(reversed(ARMS))
        for arm in order:
            context, plan = context_for(base, **case, arm=arm)
            scenarios.append(
                dict(
                    case_id=str(index + 1),
                    arm=arm,
                    **case,
                    context_ref=repo.blob(context),
                    context_sha256=digest(context),
                    evidence_sha256=digest(context["evidence"]),
                    plan=plan,
                )
            )
    return dict(
        version=version,
        frozen_at=datetime.now(UTC).isoformat(),
        source_run_id=state["run_id"],
        source_result_sha256=state["result_sha256"],
        base_context_ref=state["public_context_ref"],
        source_input_ref=state["inputs_ref"],
        source_refs=state["source_refs"],
        knowledge_ref=state["knowledge_ref"],
        market_date=state["data"]["market_date"],
        model=model,
        prompt_version=version,
        prompts=prompts_for(version),
        budget=BUDGET,
        scenarios=scenarios,
        purpose="PUBLIC_INPUT_REPLAY_NOT_PROSPECTIVE;METHOD_AND_ROUTE_CONTEXT_ABLATION",
        controls="same_public_evidence_horizon_model_and_guardrails;no_in_context_answers_or_future_outcomes",
        limitations=[
            "无方法组仍有模型预训练知识、共同技术定义和风控约束；消融对象仅为入库方法片段及路线定义。",
            "路线组同时改变方法片段与路线条件，不将差异拆解为单一来源因果。",
            "两证券单次对照，无独立专家或盲法审核；不能据此推断预测准确率、策略盈利或知识增益。",
        ],
    )


def guard_analysis(output, context):
    agents.guard_analysis(output, context)
    for opinion in output.opinions:
        if bool(opinion.method_rule_ids) != bool(context["methods"]):
            raise ValueError("方法引用与该对照组不一致")


def summary(value, state):
    manifest = value(state["manifest_ref"])
    rows = []
    for scenario in manifest["scenarios"]:
        key = scenario["case_id"] + ":" + scenario["arm"]
        record = state.get("results", {}).get(key)
        if not record:
            rows.append(
                dict(
                    case_id=scenario["case_id"],
                    security_id=scenario["security_id"],
                    arm=scenario["arm"],
                    status="NOT_COMPLETED",
                )
            )
            continue
        analysis = value(record["analysis_ref"])
        opinion = analysis["opinions"][0]
        rows.append(
            dict(
                case_id=scenario["case_id"],
                security_id=scenario["security_id"],
                arm=scenario["arm"],
                status="APPROVED",
                direction=opinion["direction"],
                summary=opinion["summary"],
                method_rule_ids=opinion["method_rule_ids"],
                claims=len(opinion["claims"]),
                opposing=len(opinion["opposing"]),
                evidence_ids=sorted(
                    {i for c in opinion["claims"] + opinion["opposing"] for i in c["evidence_ids"]}
                ),
                route_id=scenario["plan"]["selections"][0]["route_id"],
            )
        )
    return dict(
        version=manifest["version"],
        rows=rows,
        completed=sum(r["status"] == "APPROVED" for r in rows),
        declared=len(rows),
        model_calls=state["model_calls"],
        charged_tokens=state["charged_tokens"],
        elapsed_seconds=state["elapsed_seconds"],
        review_identity="SAME_MODEL_SEPARATE_RESPONSIBILITY",
        future_accuracy=None,
        profitability=None,
        causal_knowledge_gain="NOT_ESTABLISHED",
        limitations=manifest["limitations"],
    )


def verify(value, state):
    manifest = value(state["manifest_ref"])
    version = manifest["version"]
    prompts = prompts_for(version)
    repaired = version in {REPAIRED_VERSION, TOKEN_VERSION}
    if (
        state["version"] != version
        or manifest["prompts"] != prompts
        or manifest["budget"] != BUDGET
        or manifest["prompt_version"] != version
    ):
        raise ValueError("对照协议或提示版本不一致")
    if state["config"] != config_for(version):
        raise ValueError("对照运行预算不一致")
    if len(manifest["scenarios"]) != 6 or {
        (s["case_id"], s["arm"]) for s in manifest["scenarios"]
    } != {(str(i), arm) for i in (1, 2) for arm in ARMS}:
        raise ValueError("必须保留全部冻结对照组")
    base = value(manifest["base_context_ref"])
    expected_keys = set()
    for scenario in manifest["scenarios"]:
        key = scenario["case_id"] + ":" + scenario["arm"]
        if key in expected_keys:
            raise ValueError("对照样本重复")
        expected_keys.add(key)
        expected, plan = context_for(
            base,
            scenario["security_id"],
            scenario["horizon"],
            scenario["sessions"],
            scenario["arm"],
        )
        context = value(scenario["context_ref"])
        if (
            context != expected
            or plan != scenario["plan"]
            or digest(context) != scenario["context_sha256"]
            or digest(context["evidence"]) != scenario["evidence_sha256"]
        ):
            raise ValueError("对照组改变了固定公开输入")
        if key not in state.get("results", {}):
            continue
        result = state["results"][key]
        analysis = ComparisonAnalysis.model_validate(value(result["analysis_ref"]))
        review = agents.Review.model_validate(value(result["review_ref"]))
        guard_analysis(analysis, context)
        guard_review(review, analysis, version)
        if any(r.verdict != "APPROVE" for r in review.reviews):
            raise ValueError("未通过复核的对照结果")
        for index, role, schema in (
            (result["analysis_index"], "Analyst", ComparisonAnalysis),
            (result["review_index"], "Verifier", agents.Review),
        ):
            call = state["calls"][index]
            request = value(call["request_ref"])
            if repaired and role == "Analyst":
                from .structured_contract import WireAnalysis
                from .structured_contract import analysis as convert

                schema = WireAnalysis
                if (
                    convert(schema.model_validate(value(call["response_ref"])), ComparisonAnalysis)
                    != analysis
                ):
                    raise ValueError("方法对照结构分析与原输出不一致")
            if (
                call["status"] != "COMPLETED"
                or request["instructions"] != prompts[role]
                or request["schema"] != schema.model_json_schema()
                or request["model"] != manifest["model"]
                or request["prompt_version"] != version
            ):
                raise ValueError("对照模型调用绑定失败")
            if repaired:
                from .active_research import verify_profile

                verify_profile(
                    state,
                    request,
                    call,
                    role,
                    repo=SimpleNamespace(value=value),
                )
            if role == "Analyst":
                expected_context = dict(
                    analyst_context(context, agents.TECHNICAL_PROMPT_VERSION, structured=repaired),
                    plan=plan,
                )
                # Retry feedback is separately bound to its rejected review below.
                feedback = []
                if result["review_round"]:
                    prior = state["calls"][result["prior_review_index"]]
                    prior_request = value(prior["request_ref"])
                    rejected = agents.Review.model_validate(value(prior["response_ref"]))
                    if (
                        prior["status"] != "COMPLETED"
                        or prior_request["role"] != "Verifier"
                        or prior_request["context"]["plan"] != plan
                        or all(r.verdict == "APPROVE" for r in rejected.reviews)
                    ):
                        raise ValueError("对照反馈未绑定真实拒绝记录")
                    feedback = rejected.model_dump(mode="json")["reviews"]
                expected_context["review_feedback"] = feedback
                if request["context"] != expected_context or (
                    call["response_ref"] != result.get("wire_ref")
                    if repaired
                    else call["response_ref"] != result["analysis_ref"]
                ):
                    raise ValueError("对照分析输入不一致")
            else:
                expected_context = review_context(
                    context,
                    plan,
                    analysis.model_dump(mode="json"),
                    result["review_round"],
                    compact=True,
                )
                if (
                    request["context"] != expected_context
                    or call["response_ref"] != result["review_ref"]
                ):
                    raise ValueError("对照复核输入不一致")
    if set(state.get("results", {})) - expected_keys:
        raise ValueError("对照多出未登记样本")
    if (
        state["model_calls"] != len(state["calls"])
        or state["model_calls"] > 24
        or state["charged_tokens"] > 60000
        or state["elapsed_seconds"] > 900
    ):
        raise ValueError("对照超出总预算")
    expected_tokens = sum(c.get("charged_tokens", c["reserved_tokens"]) for c in state["calls"])
    if state["charged_tokens"] != expected_tokens:
        raise ValueError("对照计费不一致")
    if state["status"] == "COMPLETED" and (
        set(state["results"]) != expected_keys or state["report"] != summary(value, state)
    ):
        raise ValueError("最终对照报告不一致")
    return summary(value, state)


async def execute(repo, rules, state, settings, persist, *, port=None):
    if state["status"] == "COMPLETED":
        verify(repo.value, state)
        return state
    prior = state.get("elapsed_seconds", 0)
    started = time.monotonic()

    def save():
        state["elapsed_seconds"] = prior + time.monotonic() - started
        # Root every context, source, failed output and checkpoint in append-only audit storage.
        audit_id = rules.archive_audit(state)
        persist(state, audit_id)

    def check():
        if prior + time.monotonic() - started >= 900:
            raise ValueError("对照达到时间上限")

    manifest = repo.value(state["manifest_ref"])
    repaired = manifest["version"] in {REPAIRED_VERSION, TOKEN_VERSION}
    prompts = prompts_for(manifest["version"])
    if settings.model != manifest["model"]:
        raise ValueError("不能变更冻结对照模型")
    state.update(status="RUNNING", error=None)
    save()
    try:
        async with DailyRoles(
            repo,
            state,
            settings,
            save,
            check,
            port=port,
            prompts=prompts,
            schema_version=manifest["version"],
            output_tokens=2400,
            min_output_tokens=state["config"].get("json_minimum_tokens"),
            reserve_limit=22000,
            transport_attempts=2,
        ) as roles:

            async def step(role, schema, context, guard):
                from .structured_contract import WireAnalysis
                from .structured_contract import analysis as convert

                wired = repaired and role == "Analyst"
                original_guard = guard
                if wired:
                    schema = WireAnalysis

                    def guard(output):
                        original_guard(convert(output, ComparisonAnalysis))

                for index, call in reversed(list(enumerate(state["calls"]))):
                    if call["status"] != "COMPLETED" or call["role"] != role:
                        continue
                    request = repo.value(call["request_ref"])
                    if (
                        request["context"] == context
                        and request["instructions"] == prompts[role]
                        and request["schema"] == schema.model_json_schema()
                    ):
                        output = schema.model_validate(repo.value(call["response_ref"]))
                        guard(output)
                        return convert(output, ComparisonAnalysis) if wired else output, call, index
                output, call = await roles.call(role, schema, context, guard)
                return (
                    convert(output, ComparisonAnalysis) if wired else output,
                    call,
                    len(state["calls"]) - 1,
                )

            for scenario in manifest["scenarios"]:
                key = scenario["case_id"] + ":" + scenario["arm"]
                if key in state["results"]:
                    continue
                context = repo.value(scenario["context_ref"])
                feedback = []
                prior_review_index = None
                for round_index in range(2):
                    check()
                    state["active_scenario"] = key
                    save()
                    model_context = dict(
                        analyst_context(
                            context, agents.TECHNICAL_PROMPT_VERSION, structured=repaired
                        ),
                        plan=scenario["plan"],
                        review_feedback=feedback,
                    )
                    analysis, acall, analysis_index = await step(
                        "Analyst",
                        ComparisonAnalysis,
                        model_context,
                        lambda a, context=context: guard_analysis(a, context),
                    )
                    review_input = review_context(
                        context,
                        scenario["plan"],
                        analysis.model_dump(mode="json"),
                        round_index,
                        compact=True,
                    )
                    roles.output_tokens = 2000
                    review, rcall, review_index = await step(
                        "Verifier",
                        agents.Review,
                        review_input,
                        lambda r, analysis=analysis: guard_review(r, analysis, manifest["version"]),
                    )
                    roles.output_tokens = 2400
                    if all(r.verdict == "APPROVE" for r in review.reviews):
                        state["results"][key] = dict(
                            analysis_ref=repo.blob(analysis.model_dump(mode="json"))
                            if repaired
                            else acall["response_ref"],
                            **(dict(wire_ref=acall["response_ref"]) if repaired else {}),
                            review_ref=rcall["response_ref"],
                            analysis_index=analysis_index,
                            review_index=review_index,
                            review_round=round_index,
                            prior_review_index=prior_review_index,
                        )
                        save()
                        break
                    feedback = review.model_dump(mode="json")["reviews"]
                    prior_review_index = review_index
                else:
                    raise ValueError("对照解释未获批准；原记录和失败保留")
        state["status"] = "COMPLETED"
        state["elapsed_seconds"] = prior + time.monotonic() - started
        state["report"] = summary(repo.value, state)
        # Avoid report elapsed drift after the final seal.
        verify(repo.value, state)
        persist(state, rules.archive_audit(state))
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240] if isinstance(error, ValueError) else type(error).__name__,
        )
        save()
    return state
