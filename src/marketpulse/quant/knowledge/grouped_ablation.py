"""Independent method arms with shared-evidence review; never share analyst contexts."""

import time

from pydantic import Field

from ..contracts import digest
from ..decision.next_session.agents import DailyRoles
from . import ablation as base
from . import research_agents as agents
from .active_research import verify_profile
from .research_context import analyst_context
from .structured_contract import WireAnalysis, analysis, configure

VERSION = "investment-method-ablation-v3"
COMPACT_VERSION = "investment-method-ablation-v4"
TOKEN_VERSION = "investment-method-ablation-v5"
VERSIONS = {VERSION, COMPACT_VERSION, TOKEN_VERSION}
REVIEW_PROMPT = (
    "你是同模型的独立复核职责，非外部专家；只用封存事实，各组分别审核，不能跨组补方法或判断。"
    "common_evidence和windows为各组共用原始事实；arms逐组给定其自己可见的方法、路线、判断。"
    "逐项核对引用是否支持正文、数值单位、正负、大小、同窗与比较，decisions完整覆盖其全部判断ID。"
    "任何事实、引用、方向取舍错误都REJECT；direction_supported独立核对绝对价格、期限、支持反证。"
    "conditions_respected核对不得修改入退场条件，看多仍可等待；未触发本身不自动否决方向。"
    "资金代理非机构，日频分钟不合并；政策资格待核；技术辅助不等于独立确认，波动不判方向。"
    "方法只作参考，无方法组不能引入其他组的方法；全部判断、方向、条件通过才该组APPROVE。"
    "拒绝给具体证据或推断错误，不要求证明未来必然发生。简短中文、不写数字、概率或无条件交易指令。"
    "仅输出arms，每项key原样复制、review为该组完整reviews。"
)


class ArmReview(agents.FrozenModel):
    key: str
    review: agents.Review


class BatchReview(agents.FrozenModel):
    arms: tuple[ArmReview, ...] = Field(min_length=1, max_length=6)


def config_for(version=TOKEN_VERSION):
    if version not in VERSIONS:
        raise ValueError("未知分组复核版本")
    result = dict(prompt_version=version, budget=base.BUDGET)
    configure(result, False)
    if version == TOKEN_VERSION:
        from ..decision.next_session.input_reservation import POLICY

        result["input_reservation"] = dict(POLICY)
    return result


def prompts_for(version):
    config_for(version)
    prompts = dict(
        Analyst=base.prompts_for(base.REPAIRED_VERSION)["Analyst"], Verifier=REVIEW_PROMPT
    )
    if version in {COMPACT_VERSION, TOKEN_VERSION}:
        prompts["Analyst"] += (
            "本次每只仅两项claims与一项opposing。每项evidence_ids至多四个；"
            "技术、资金、政策引用分散到不同判断，不能把全部引用堆入一项。"
        )
        prompts["Verifier"] += (
            "common_methods和common_stocks是无损公共字典，逐组按method_refs、stock_refs展开；"
            "没有引用的方法不可用于该组。不得把其他组路线或结论用作本组证据。"
        )
    if version == TOKEN_VERSION:
        prompts["Analyst"] += (
            "必须逐一覆盖非空citation_requirements。先为每只安排技术、资金、政策引用再写正文，"
            "只覆盖其中部分不得输出。政策资格待核可作为反证；引用总数无需堆入同一判断。"
        )
    return prompts


def freeze(repo, source, model, *, version=TOKEN_VERSION):
    value = base.freeze(repo, source, model, version=base.REPAIRED_VERSION)
    value.update(
        version=version,
        prompt_version=version,
        prompts=prompts_for(version),
        review_protocol="independent-analyst-arms;common-evidence-review;isolated-feedback",
        repair_rounds=2,
    )
    return value


def key_for(scenario):
    return scenario["case_id"] + ":" + scenario["arm"]


def analysis_context(repo, scenario, feedback):
    return dict(
        analyst_context(
            repo.value(scenario["context_ref"]), agents.TECHNICAL_PROMPT_VERSION, structured=True
        ),
        plan=scenario["plan"],
        review_feedback=feedback,
    )


def review_context(repo, manifest, current):
    facts, windows, arms = {}, {}, []
    for scenario in manifest["scenarios"]:
        key = key_for(scenario)
        if key not in current:
            continue
        context = repo.value(scenario["context_ref"])
        opinion = repo.value(current[key]["analysis_ref"])
        evidence_ids = {
            i
            for o in opinion["opinions"]
            for c in o["claims"] + o["opposing"]
            for i in c["evidence_ids"]
        }
        for fact in context["evidence"]:
            if fact["evidence_id"] not in evidence_ids:
                continue
            item = {
                k: v
                for k, v in fact.items()
                if k in {"evidence_id", "value", "unit", "window", "text", "policy_state"}
            }
            window = item["window"]
            wid = digest(window)
            windows[wid] = window
            item["window"] = wid
            if item["evidence_id"] in facts and facts[item["evidence_id"]] != item:
                raise ValueError("不同组公共事实冲突")
            facts[item["evidence_id"]] = item
        ids = {i for o in opinion["opinions"] for i in o["method_rule_ids"]}
        arms.append(
            dict(
                key=key,
                horizon=context["horizon"],
                horizon_sessions=context["horizon_sessions"],
                stocks=context["stocks"],
                plan=scenario["plan"],
                methods=[m for m in context["methods"] if m["rule_id"] in ids],
                proposal=opinion,
                allowed_evidence_ids=sorted(evidence_ids),
            )
        )
    result = dict(common_evidence=[facts[k] for k in sorted(facts)], windows=windows, arms=arms)
    if manifest["version"] in {COMPACT_VERSION, TOKEN_VERSION}:
        result = intern_review(result)
    return result


def intern_review(value):
    """Intern whole unchanged objects; keep every fact and method condition."""
    methods, stocks = {}, {}
    for arm in value["arms"]:
        for field, mapping in (("methods", methods), ("stocks", stocks)):
            refs = []
            for item in arm.pop(field):
                key = next((k for k, v in mapping.items() if v == item), None)
                if key is None:
                    key = ("M" if field == "methods" else "S") + str(len(mapping) + 1)
                    mapping[key] = item
                refs.append(key)
            arm["method_refs" if field == "methods" else "stock_refs"] = refs
    ids = {k: "W" + str(i + 1) for i, k in enumerate(value["windows"])}
    value["windows"] = {ids[k]: v for k, v in value["windows"].items()}
    for fact in value["common_evidence"]:
        fact["window"] = ids[fact["window"]]
    value.update(common_methods=methods, common_stocks=stocks)
    return value


def guard_review(output, repo, current):
    if {a.key for a in output.arms} != set(current) or len(output.arms) != len(current):
        raise ValueError("分组复核必须逐组完整覆盖")
    for item in output.arms:
        proposal = base.ComparisonAnalysis.model_validate(
            repo.value(current[item.key]["analysis_ref"])
        )
        agents.guard_review(item.review, proposal)


def verify(value, state):
    from types import SimpleNamespace

    repo = SimpleNamespace(value=value)
    manifest = value(state["manifest_ref"])
    version = manifest["version"]
    expected_prompts = prompts_for(version)
    if (
        version not in VERSIONS
        or state["version"] != version
        or manifest["prompt_version"] != version
        or manifest["budget"] != base.BUDGET
        or manifest["repair_rounds"] != 2
        or manifest["prompts"] != expected_prompts
        or state["config"] != config_for(version)
    ):
        raise ValueError("分组消融协议不一致")
    scenarios = {key_for(s): s for s in manifest["scenarios"]}
    if len(manifest["scenarios"]) != 6 or set(scenarios) != {
        str(i) + ":" + arm for i in (1, 2) for arm in base.ARMS
    }:
        raise ValueError("分组消融必须保留六组")
    for scenario in scenarios.values():
        expected, plan = base.context_for(
            value(manifest["base_context_ref"]),
            scenario["security_id"],
            scenario["horizon"],
            scenario["sessions"],
            scenario["arm"],
        )
        if (
            expected != value(scenario["context_ref"])
            or scenario["plan"] != plan
            or digest(expected) != scenario["context_sha256"]
            or digest(expected["evidence"]) != scenario["evidence_sha256"]
        ):
            raise ValueError("分组公开证据或方法变化")
    current, approved, feedback = {}, {}, {}
    reviewed = 0
    for event in state.get("history", []):
        call = state["calls"][event["call_index"]]
        request = value(call["request_ref"])
        role = event["role"]
        if role not in expected_prompts:
            raise ValueError("未知分组职责")
        schema = WireAnalysis if role == "Analyst" else BatchReview
        if (
            call["role"] != role
            or request["role"] != role
            or call["status"] != "COMPLETED"
            or request["schema"] != schema.model_json_schema()
            or request["instructions"] != expected_prompts[role]
            or request["model"] != manifest["model"]
            or request["prompt_version"] != version
        ):
            raise ValueError("分组消融真实调用绑定失败")
        verify_profile(state, request, call, role, repo=repo)
        if role == "Analyst":
            key = event["key"]
            scenario = scenarios[key]
            if (
                key in approved
                or key in current
                or request["context"] != analysis_context(repo, scenario, feedback.get(key, []))
            ):
                raise ValueError("分析组泄漏其他组或未使用对应反馈")
            canonical = analysis(
                WireAnalysis.model_validate(value(call["response_ref"])), base.ComparisonAnalysis
            )
            base.guard_analysis(canonical, value(scenario["context_ref"]))
            if canonical.model_dump(mode="json") != value(event["analysis_ref"]):
                raise ValueError("分析与原模型正文不一致")
            current[key] = event
        else:
            if set(current) != set(scenarios) - set(approved):
                raise ValueError("每轮复核须覆盖全部未批准组")
            if request["context"] != review_context(repo, manifest, current):
                raise ValueError("公共证据复核输入不一致")
            output = BatchReview.model_validate(value(call["response_ref"]))
            guard_review(output, repo, current)
            for item in output.arms:
                feedback[item.key] = item.review.model_dump(mode="json")["reviews"]
                if all(r.verdict == "APPROVE" for r in item.review.reviews):
                    approved[item.key] = dict(current[item.key], review_ref=call["response_ref"])
            current = {}
            reviewed += 1
    if reviewed > manifest["repair_rounds"]:
        raise ValueError("分组消融超过固定纠错轮次")
    if state.get("results", {}) != approved:
        raise ValueError("分组消融批准结果与真实复核不一致")
    if (
        state["model_calls"] != len(state["calls"])
        or state["model_calls"] > 24
        or state["charged_tokens"] > 60000
        or state["elapsed_seconds"] > 900
    ):
        raise ValueError("分组消融超出原累计预算")
    if state["charged_tokens"] != sum(
        c.get("charged_tokens", c["reserved_tokens"]) for c in state["calls"]
    ):
        raise ValueError("分组消融预算核算不一致")
    if state["status"] == "COMPLETED" and len(approved) != 6:
        raise ValueError("分组消融尚未全部获批")
    return base.summary(value, state)


summary = base.summary


def progress(repo, state):
    """Restore only unreviewed analyst checkpoints and each arm's own feedback."""
    current, feedback, reviewed = {}, {}, 0
    for event in state.get("history", []):
        if event["role"] == "Analyst":
            current[event["key"]] = event
        else:
            output = BatchReview.model_validate(
                repo.value(state["calls"][event["call_index"]]["response_ref"])
            )
            feedback.update(
                {item.key: item.review.model_dump(mode="json")["reviews"] for item in output.arms}
            )
            current = {}
            reviewed += 1
    return current, feedback, reviewed


async def execute(repo, rules, state, settings, persist, *, port=None):
    manifest = repo.value(state["manifest_ref"])
    if state["status"] == "COMPLETED":
        verify(repo.value, state)
        return state
    started, prior = time.monotonic(), state.get("elapsed_seconds", 0)

    def save():
        state["elapsed_seconds"] = prior + time.monotonic() - started
        persist(state, rules.archive_audit(state))

    def check():
        if state.get("elapsed_seconds", 0) >= 900:
            raise ValueError("分组消融达到原时间上限")

    try:
        if settings.model != manifest["model"]:
            raise ValueError("登记模型不能更换")
        verify(repo.value, state)
        current, feedback_by_key, reviewed = progress(repo, state)
        state.update(status="RUNNING", error=None)
        state.setdefault("history", [])
        save()
        async with DailyRoles(
            repo,
            state,
            settings,
            save,
            check,
            port=port,
            prompts=manifest["prompts"],
            schema_version=manifest["version"],
            output_tokens=2400,
            reserve_limit=45000,
            transport_attempts=2,
        ) as roles:
            for _ in range(reviewed, manifest["repair_rounds"]):
                for scenario in manifest["scenarios"]:
                    key = key_for(scenario)
                    if key in state["results"] or key in current:
                        continue
                    feedback = feedback_by_key.get(key, [])
                    model_context = analysis_context(repo, scenario, feedback)
                    state["active_scenario"] = key
                    output, call = await roles.call(
                        "Analyst",
                        WireAnalysis,
                        model_context,
                        lambda output, scenario=scenario: base.guard_analysis(
                            analysis(output, base.ComparisonAnalysis),
                            repo.value(scenario["context_ref"]),
                        ),
                    )
                    event = dict(
                        role="Analyst",
                        key=key,
                        call_index=len(state["calls"]) - 1,
                        analysis_ref=repo.blob(
                            analysis(output, base.ComparisonAnalysis).model_dump(mode="json")
                        ),
                    )
                    state["history"].append(event)
                    current[key] = event
                    save()
                if not current:
                    break
                roles.output_tokens = 5200
                review, call = await roles.call(
                    "Verifier",
                    BatchReview,
                    review_context(repo, manifest, current),
                    lambda r, current=current: guard_review(r, repo, current),
                )
                state["history"].append(dict(role="Verifier", call_index=len(state["calls"]) - 1))
                for item in review.arms:
                    feedback_by_key[item.key] = item.review.model_dump(mode="json")["reviews"]
                    if all(r.verdict == "APPROVE" for r in item.review.reviews):
                        state["results"][item.key] = dict(
                            current[item.key], review_ref=call["response_ref"]
                        )
                current = {}
                roles.output_tokens = 2400
                save()
        if len(state["results"]) != 6:
            raise ValueError("仍有方法组未通过原事实与方向复核")
        state["status"] = "COMPLETED"
        state["elapsed_seconds"] = prior + time.monotonic() - started
        verify(repo.value, state)
        persist(state, rules.archive_audit(state))
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240] if isinstance(error, ValueError) else type(error).__name__,
        )
        save()
    return state
