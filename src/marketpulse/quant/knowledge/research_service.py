"""Four real model responsibilities, with recoverable public-only checkpoints."""

import asyncio
import copy
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from ..contracts import digest
from ..decision.next_session.agents import DailyRoles
from ..decision.tools import SourceClient, Stopped
from . import research_agents as agents
from . import technicals
from .context import collect as collect_context
from .context import enrich, rebuild
from .inputs import capture_inputs
from .planning_service import build_report
from .playbook import allocate_plans
from .research_context import (
    analysis_basis,
    analysis_plan,
    direction_check_context,
    knowledge_snapshot,
    public_context,
    review_context,
)
from .research_context import analyst_context as prepare_analysis
from .research_contracts import InvestmentRequest
from .research_store import BUDGET
from .trading_rules import verify_trading_rules


async def run_with_recovery(repo, rules, run_id, stop, settings, **options):
    """Continue correctable role failures on the same sealed task and original budget."""
    while True:
        state = await run(repo, rules, run_id, stop, settings, **options)
        count = state.get("automatic_continuations", 0)
        correctable = (
            not stop.is_set()
            and state["status"] == "INTERRUPTED"
            and state.get("stage") in {"PLANNER", "ANALYST", "VERIFIER", "WRITER"}
            and any(s in (state.get("error") or "") for s in ("输出未通过", "解释仍未通过"))
            and count < 2
            and state["charged_tokens"] < state["config"]["budget"]["max_tokens"] - 15000
            and state["model_calls"] < state["config"]["budget"]["max_model_calls"] - 4
        )
        if not correctable:
            return state
        state["automatic_continuations"] = count + 1
        repo.save(state)


async def run(
    repo,
    rules,
    run_id,
    stop,
    settings,
    *,
    port=None,
    collector=capture_inputs,
    context_collector=collect_context,
):
    state = repo.get(run_id, with_data=False)
    state.setdefault(
        "config",
        dict(
            prompt_version=agents.prompt_version(state["request"]["schema_version"]),
            budget=BUDGET,
        ),
    )
    for name in ("model_calls", "charged_tokens", "tokens"):
        state.setdefault(name, 0)
    state.setdefault("model_steps", {})
    started, previous_elapsed = time.monotonic(), state.get("elapsed_seconds", 0)

    def save():
        state["elapsed_seconds"] = previous_elapsed + time.monotonic() - started
        repo.save(state)

    def check():
        if stop.is_set() or previous_elapsed + time.monotonic() - started >= 900:
            raise Stopped("本次研究已停止或达到时间上限；已取得资料保留。")

    try:
        state.update(status="RUNNING", error=None, stage="INPUTS")
        state.setdefault(
            "implementation",
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(__file__).parent.glob("*.py")
            },
        )
        save()
        request = InvestmentRequest.model_validate(state["request"])
        prompts = agents.role_prompts(state["config"])
        if "inputs_ref" not in state:
            client = SourceClient(
                repo, state, save, stop, max_requests=80, budget_profile=dict(max_seconds=900)
            )
            work = asyncio.create_task(
                asyncio.to_thread(
                    collector, client, request.entities, datetime.fromisoformat(state["created_at"])
                )
            )
            try:
                packet = await asyncio.shield(work)
            except asyncio.CancelledError:
                stop.set()
                await work
                raise
            state["inputs_ref"] = repo.blob(packet)
            state["source_refs"] = sorted(
                set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(packet)))
            )
            save()
        packet = repo.value(state["inputs_ref"])
        if (
            request.schema_version == "investment-research-v4"
            and "forward_protocol_ref" not in state
        ):
            from .observations import ObservationStore, calendars

            spec = ObservationStore(repo).register(state)
            client = SourceClient(
                repo, state, save, stop, max_requests=80, budget_profile=dict(max_seconds=900)
            )
            work = asyncio.create_task(asyncio.to_thread(calendars, client, packet, spec))
            try:
                schedule = await asyncio.shield(work)
            except asyncio.CancelledError:
                stop.set()
                await work
                raise
            state["forward_protocol_ref"] = repo.blob(dict(spec, **schedule))
            state["source_refs"] += sorted(
                set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(schedule)))
            )
            save()
        if (
            request.schema_version
            in {"investment-research-v2", "investment-research-v3", "investment-research-v4"}
            and "context_snapshot_ref" not in state
        ):
            state["stage"] = "CONTEXT"
            save()
            client = SourceClient(
                repo, state, save, stop, max_requests=80, budget_profile=dict(max_seconds=900)
            )
            work = asyncio.create_task(asyncio.to_thread(context_collector, repo, client, packet))
            try:
                snapshot = await asyncio.shield(work)
            except asyncio.CancelledError:
                stop.set()
                await work
                raise
            context_report = rebuild(repo, packet, snapshot)
            state.update(
                context_snapshot_ref=repo.blob(snapshot),
                context_report_ref=repo.blob(context_report),
            )
            state["source_refs"] += sorted(
                set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(snapshot)))
            )
            save()
        if "core_ref" not in state:
            check()
            knowledge = (
                knowledge_snapshot(rules, datetime.fromisoformat(state["created_at"]), dynamic=True)
                if state["config"].get("agentic_research_v1")
                else knowledge_snapshot(rules, datetime.fromisoformat(state["created_at"]))
            )
            bindings = [
                dict(
                    rule_id=r["rule_id"],
                    version=r["version"],
                    title=r["rule"]["title"],
                    source_families=r["source_families"],
                    execution_status="REFERENCE_ONLY",
                )
                for r in knowledge["rules"]
            ]
            trading = verify_trading_rules(packet, repo.raw_bytes)
            core = build_report(request.calculation_request(), packet, bindings, trading)
            context, evidence = public_context(core, packet, knowledge, request.horizon_sessions)
            if request.analysis_focus:
                context["user_analysis_focus"] = list(request.analysis_focus)
            if state.get("context_report_ref"):
                context, evidence = enrich(
                    context, evidence, repo.value(state["context_report_ref"])
                )
            if request.schema_version in {"investment-research-v3", "investment-research-v4"}:
                technical = technicals.compute(packet)
                context, evidence = technicals.enrich(context, evidence, technical, packet)
                state["technicals_ref"] = repo.blob(technical)
            state.update(
                core_ref=repo.blob(core),
                knowledge_ref=repo.blob(knowledge),
                public_context_ref=repo.blob(context),
                evidence_ref=repo.blob(evidence),
                features_ref=repo.blob(core["features"]),
            )
            state["source_refs"] += sorted(
                set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(knowledge)))
            )
            save()
        core, knowledge, context = (
            repo.value(state[k]) for k in ("core_ref", "knowledge_ref", "public_context_ref")
        )
        async with DailyRoles(
            repo,
            state,
            settings,
            save,
            check,
            port=port,
            prompts=prompts,
            schema_version=request.schema_version,
            output_tokens=5200,
            reserve_limit=45000,
            transport_attempts=2,
        ) as roles:
            if state["config"].get("agentic_research_v1"):
                from . import active_research

                if "base_public_context_ref" not in state:
                    state["base_public_context_ref"] = state["public_context_ref"]
                    state["base_evidence_ref"] = state["evidence_ref"]
                    save()
                context = await active_research.execute(
                    repo,
                    state,
                    save,
                    roles,
                    repo.value(state["base_public_context_ref"]),
                    knowledge,
                    SourceClient(
                        repo,
                        state,
                        save,
                        stop,
                        max_requests=80,
                        budget_profile=dict(max_seconds=900),
                    ),
                )
                state["public_context_ref"] = repo.blob(context)
                state["evidence_ref"] = repo.blob(
                    repo.value(state["base_evidence_ref"])
                    + active_research.additional_evidence(context["tool_results"])
                )
                state["source_refs"] += sorted(
                    set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(context)))
                    - set(state["source_refs"])
                )
                save()

            async def step(
                key,
                role,
                schema,
                model_context,
                guard,
                *,
                version=None,
                output_kind=None,
                previous_review_step=None,
            ):
                check()
                from . import structured_contract as structured

                structured_analysis = schema is agents.Analysis and bool(
                    state["config"].get("execution_contract")
                )
                canonical_schema = schema
                canonical_guard = guard
                if structured_analysis:
                    schema = structured.WireAnalysis

                    def guard(output):
                        canonical_guard(structured.analysis(output))

                    output_kind = "structured_analysis"
                version = version or state["config"]["prompt_version"]
                active_prompts = agents.role_prompts(dict(state["config"], prompt_version=version))
                roles.prompts = active_prompts
                roles.output_tokens = {
                    "Planner": 1800,
                    "Analyst": 4300,
                    "Verifier": 3200,
                    "Writer": 1200,
                }[role]
                if role == "Analyst" and len(context["stocks"]) == 1:
                    # One opinion needs less output than a four-stock batch; budgets stay charged.
                    roles.output_tokens = 2400
                count = len(context["stocks"])
                roles.min_output_tokens = {
                    "Planner": 600 + 200 * count,
                    "Analyst": 700 + 600 * count,
                    "Verifier": 400 + 400 * count,
                    "Writer": 200 + 150 * count,
                }[role]
                if role == "Verifier" and state["config"].get("portfolio_compact_v1"):
                    roles.min_output_tokens = 300 + 100 * count
                if schema is agents.DirectionCheck:
                    roles.output_tokens = 200 + 100 * count
                    roles.min_output_tokens = 120 + 80 * count
                state["stage"] = role.upper()
                save()
                if key in state["model_steps"]:
                    output = repo.output(state, key, canonical_schema)
                    canonical_guard(output)
                    return output
                for index, prior in reversed(list(enumerate(state["calls"]))):
                    if prior.get("role") != role or prior["status"] != "COMPLETED":
                        continue
                    recorded = repo.value(prior["request_ref"])
                    if (
                        recorded.get("context") == model_context
                        and recorded.get("instructions") == active_prompts[role]
                        and recorded.get("prompt_version") == version
                        and recorded.get("schema") == schema.model_json_schema()
                        and roles.matches_request_profile(recorded, role)
                    ):
                        output = schema.model_validate(repo.value(prior["response_ref"]))
                        guard(output)
                        state["model_steps"][key] = dict(
                            role=role,
                            call_index=index,
                            context_ref=repo.blob(model_context),
                            response_ref=prior["response_ref"],
                            prompt_version=version,
                            output_kind=output_kind,
                            previous_review_step=previous_review_step,
                        )
                        save()
                        return structured.analysis(output) if structured_analysis else output
                output, call = await roles.call(
                    role, schema, model_context, guard, prompt_version=version
                )
                state["model_steps"][key] = dict(
                    role=role,
                    call_index=len(state["calls"]) - 1,
                    context_ref=repo.blob(model_context),
                    response_ref=call["response_ref"],
                    prompt_version=version,
                    output_kind=output_kind,
                    previous_review_step=previous_review_step,
                )
                save()
                return structured.analysis(output) if structured_analysis else output

            planner_context = {
                k: context[k]
                for k in ("horizon", "horizon_sessions", "stocks", "methods", "limitations")
            }
            if context.get("user_analysis_focus"):
                planner_context["user_analysis_focus"] = context["user_analysis_focus"]
            plan = await step(
                "Planner",
                "Planner",
                agents.Plan,
                planner_context,
                lambda p: agents.guard_plan(p, context),
            )
            first_round = state.get("review_round", 0)
            feedback = []
            analysis = review = None
            if (
                state.get("approved_analysis_step") in state["model_steps"]
                and state.get("approved_review_step") in state["model_steps"]
            ):
                analysis = repo.output(state, state["approved_analysis_step"], agents.Analysis)
                agents.guard_analysis(
                    analysis,
                    analysis_basis(
                        context,
                        plan.model_dump(mode="json"),
                        scoped=state["config"].get("scoped_analysis", False),
                    ),
                )
                review = repo.output(state, state["approved_review_step"], agents.Review)
                agents.guard_review(review, analysis)
            if first_round and "Verifier-" + str(first_round - 1) in state["model_steps"]:
                feedback = repo.output(
                    state, "Verifier-" + str(first_round - 1), agents.Review
                ).model_dump(mode="json")["reviews"]
            # Re-review legacy entry/direction disagreement without regenerating approved facts.
            if (
                first_round
                and not state.get("approved_analysis_step")
                and state["config"]["prompt_version"]
                not in {agents.DIRECTION_PROMPT_VERSION, agents.CHECKS_PROMPT_VERSION}
            ):
                prior_review_key, prior_analysis_key = (
                    "Verifier-" + str(first_round - 1),
                    "Analyst-" + str(first_round - 1),
                )
                if (
                    prior_review_key in state["model_steps"]
                    and prior_analysis_key in state["model_steps"]
                ):
                    full_direction_key = prior_review_key + "-direction"
                    if full_direction_key in state["model_steps"]:
                        prior_review_key = full_direction_key
                    prior_review = repo.output(state, prior_review_key, agents.Review)
                    if agents.direction_only_rejection(prior_review):
                        analysis = repo.output(state, prior_analysis_key, agents.Analysis)
                        recovery_key = "Verifier-" + str(first_round - 1) + "-direction-check"
                        if (
                            state.get("direction_review_recovery")
                            and state["direction_review_recovery"]["review_step"] != recovery_key
                        ):
                            state.setdefault("prior_direction_reviews", []).append(
                                state["direction_review_recovery"]
                            )
                        state["direction_review_recovery"] = dict(
                            analysis_step=prior_analysis_key,
                            previous_review_step=prior_review_key,
                            review_step=recovery_key,
                            prompt_version=agents.DIRECTION_CHECK_PROMPT_VERSION,
                            previous_charged_tokens=state["charged_tokens"],
                            previous_model_calls=state["model_calls"],
                        )
                        save()
                        previous_context = repo.value(
                            state["model_steps"][prior_review_key]["context_ref"]
                        )
                        model_context = direction_check_context(previous_context, prior_review)
                        direction = await step(
                            recovery_key,
                            "Verifier",
                            agents.DirectionCheck,
                            model_context,
                            lambda d: agents.guard_review(
                                agents.combine_direction_check(d, prior_review), analysis
                            ),
                            version=agents.DIRECTION_CHECK_PROMPT_VERSION,
                            output_kind="direction_check",
                            previous_review_step=prior_review_key,
                        )
                        review = agents.combine_direction_check(direction, prior_review)
                        if all(r.verdict == "APPROVE" for r in review.reviews):
                            state.update(
                                approved_analysis_step=prior_analysis_key,
                                approved_review_step=recovery_key,
                            )
                            save()
            recovered = review is not None and all(r.verdict == "APPROVE" for r in review.reviews)
            for attempt in [] if recovered else range(first_round, min(first_round + 3, 8)):
                state["review_round"] = attempt
                analyst_context = dict(
                    prepare_analysis(
                        context,
                        state["config"]["prompt_version"],
                        plan.model_dump(mode="json"),
                        scoped=state["config"].get("scoped_analysis", False),
                        compact=state["config"].get("portfolio_compact_v1", False),
                        structured=bool(state["config"].get("execution_contract")),
                    ),
                    plan=analysis_plan(
                        plan.model_dump(mode="json"),
                        state["config"].get("portfolio_compact_v1", False),
                    ),
                    review_feedback=feedback,
                    review_round=attempt,
                )
                if (
                    state["config"]["prompt_version"]
                    in {agents.DIRECTION_PROMPT_VERSION, agents.CHECKS_PROMPT_VERSION}
                    and attempt
                ):
                    analyst_context["previous_analysis"] = repo.output(
                        state, "Analyst-" + str(attempt - 1), agents.Analysis
                    ).model_dump(mode="json")
                analysis_key, review_key = "Analyst-" + str(attempt), "Verifier-" + str(attempt)
                analysis = await step(
                    analysis_key,
                    "Analyst",
                    agents.Analysis,
                    analyst_context,
                    lambda a: agents.guard_analysis(
                        a,
                        analysis_basis(
                            context,
                            plan.model_dump(mode="json"),
                            scoped=state["config"].get("scoped_analysis", False),
                        ),
                    ),
                )
                verifier_context = review_context(
                    context,
                    plan.model_dump(mode="json"),
                    analysis.model_dump(mode="json"),
                    attempt,
                    compact=agents.compact_review(state["config"]["prompt_version"]),
                    portfolio_compact=state["config"].get("portfolio_compact_v1", False),
                )
                checked_review = state["config"]["prompt_version"] == agents.CHECKS_PROMPT_VERSION
                review = agents.finalize_review(
                    await step(
                        review_key,
                        "Verifier",
                        agents.ReviewChecks if checked_review else agents.Review,
                        verifier_context,
                        lambda r, analysis=analysis: agents.guard_review(
                            agents.finalize_review(r), analysis
                        ),
                        output_kind="review_checks" if checked_review else None,
                    )
                )
                if all(r.verdict == "APPROVE" for r in review.reviews):
                    state.update(
                        approved_analysis_step=analysis_key, approved_review_step=review_key
                    )
                    save()
                    break
                feedback = review.model_dump(mode="json")["reviews"]
                state["review_round"] = attempt + 1
                save()
            if not state.get("approved_analysis_step"):
                raise ValueError("解释仍未通过事实与方向复核，已有分析及反证保留，可继续核查。")
            approved = analysis.model_dump(mode="json")
            writing_context = dict(
                approved_hash=digest(approved),
                items=[
                    dict(
                        security_id=o["security_id"], direction=o["direction"], summary=o["summary"]
                    )
                    for o in approved["opinions"]
                ],
            )
            if state["config"].get("agentic_research_v1") or state["config"].get(
                "execution_contract"
            ):
                writing = agents.Writing.model_validate(writing_context)
                agents.guard_writing(writing, approved)
                state["model_steps"]["Writer"] = dict(
                    role="Writer",
                    context_ref=repo.blob(writing_context),
                    response_ref=repo.blob(writing.model_dump(mode="json")),
                    output_kind="deterministic_publication",
                )
                save()
            else:
                await step(
                    "Writer",
                    "Writer",
                    agents.Writing,
                    writing_context,
                    lambda w: agents.guard_writing(w, approved),
                )
        report = copy.deepcopy(core)
        selections = {s.security_id: s for s in plan.selections}
        opinions = {o["security_id"]: o for o in approved["opinions"]}
        for c in report["candidates"]:
            selection = selections[c["security_id"]]
            if selection.route_id is not None:
                c["selected_route"] = selection.route_id
                c["action"] = next(
                    r["action"] for r in c["routes"] if r["route_id"] == selection.route_id
                )
            c["route_rationale"] = selection.rationale
            c["research_direction"] = opinions[c["security_id"]]["direction"]
            if c["research_direction"] == "UNFAVORABLE":
                c["action"] = "倾向看空，暂不安排新增仓位"
        report["conclusion"] = (
            "按条件等待，保留现金"
            if all(c.get("action") != "条件已满足，先核对风险预算" for c in report["candidates"])
            else "部分股票满足研究条件，按组合预算逐项复核"
        )
        report.update(
            schema_version="investment-research-result-v1",
            request=request.model_dump(mode="json"),
            research_opinions=approved["opinions"],
            approved_hash=digest(approved),
            horizon_sessions=request.horizon_sessions,
            knowledge_usage=knowledge,
            evidence=repo.value(state["evidence_ref"]),
            portfolio=allocate_plans(report["candidates"], request.profile),
            verification=dict(
                kind="SAME_MODEL_SEPARATE_RESPONSIBILITIES", external_verification=False
            ),
            privacy=dict(profile_sent_to_model=False, holdings_sent_to_model=False),
            caveats=[
                "方向是封存数据下的条件性判断，入场须另满足路线与组合约束。",
                *context["limitations"],
            ],
        )
        if state.get("context_report_ref"):
            external = repo.value(state["context_report_ref"])
            report["external_context"] = external
            report["sources"] += [s for s in external["sources"] if s not in report["sources"]]
        if state.get("technicals_ref"):
            report["technicals"] = repo.value(state["technicals_ref"])
        if state["config"].get("agentic_research_v1"):
            report["sources"] += [
                t["source"]
                for t in context["tool_results"]
                if t.get("source") and t["source"] not in report["sources"]
            ]
        if state.get("forward_protocol_ref"):
            report["forward_protocol"] = repo.value(state["forward_protocol_ref"])
            report["issued_at"] = datetime.now(UTC).isoformat()
        check()
        state.update(
            status="COMPLETED",
            stage="COMPLETED",
            result_ref=repo.blob(report),
            result_sha256=digest(report),
        )
        save()
    except asyncio.CancelledError:
        stop.set()
        state.update(status="INTERRUPTED", error="服务停止，已完成材料与分析步骤保留。")
        save()
        raise
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240]
            if isinstance(error, ValueError)
            else "资料或分析服务未完成，已完成步骤保留，可继续。",
        )
        save()
    return repo.get(run_id)
