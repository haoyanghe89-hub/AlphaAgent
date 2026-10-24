"""Preregistered component ladder and evidence-grounded, identity-hidden answer review."""

import asyncio
import copy
import json
import re
import time
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from marketpulse.telemetry.tracing import workflow

from ..contracts import digest
from ..decision.next_session.agents import DailyRoles
from ..decision.tools import SourceClient
from . import quality_review as quality
from .context import collect, rebuild
from .inputs import capture_inputs
from .observations import ObservationStore
from .research_contracts import InvestmentRequest
from .research_service import run_with_recovery
from .research_store import BUDGET, InvestmentStore

VERSION = "research-effectiveness-v1"
ARMS = ("ORIGINAL", "METHODS", "TOOLS", "ENHANCED")
SHARED_FLAGS = dict(scoped_analysis=True, explicit_contract_v1=True, literal_text_contract_v1=True)
CASES = [
    dict(
        case_id="CONSUMER_FINANCE20",
        entities=["000333", "600036"],
        horizon="SWING",
        horizon_sessions=20,
    ),
    dict(
        case_id="ENERGY_TRANSPORT60",
        entities=["601088", "600009"],
        horizon="MEDIUM",
        horizon_sessions=60,
    ),
    dict(
        case_id="GROWTH_CONSUMPTION20",
        entities=["300750", "600519"],
        horizon="SWING",
        horizon_sessions=20,
    ),
]
LIMITATIONS = [
    "预先登记六只股票、三案例及运行顺序；样本小、同一日期、没有重复随机生成，不能证明总体预测或投资收益增益。",
    "组件按标准→动态方法→补充工具→低强度推理逐项增加；差异仍包含模型随机性、顺序和交互影响，不是已证实因果效果。",
    "同案例基础数据共用；底层审核知识版本相同，模型实际可见的方法片段及工具材料按处理组不同。",
    "回答质量为同模型隐藏流程身份评分，存在共同盲点，须人工核查；评分不是方向准确率或收益。",
    "全部失败与调用消耗保留；未成熟未来标签为空，不回填历史预测。旧5.2门禁不变。",
]


def children(rid):
    result = []
    for i, case in enumerate(CASES):
        order = list(ARMS[i:] + ARMS[:i])
        for arm in order:
            key = uuid5(NAMESPACE_URL, rid + ":" + case["case_id"] + ":" + arm)
            request = InvestmentRequest(
                schema_version="investment-research-v4",
                request_key=key,
                entities=case["entities"],
                horizon=case["horizon"],
                horizon_sessions=case["horizon_sessions"],
                enhanced_research=arm != "ORIGINAL",
                enhancement_stage=arm if arm in {"METHODS", "TOOLS"} else None,
                execution_contract="investment-structured-v2",
                input_reservation="deepseek-v4-tokenizer-v1",
            ).model_dump(mode="json")
            result.append(
                dict(case_id=case["case_id"], arm=arm, run_id="RUN-IR-" + key.hex, request=request)
            )
    return result


def create(repo, request, model):
    from .forward_pairs import schedule
    from .paired_evaluation import PRICING

    rid = repo.prefix + UUID(request["request_key"]).hex
    now = datetime.now(UTC)
    manifest = dict(
        version=VERSION,
        registered_at=now.isoformat(),
        cases=CASES,
        children=children(rid),
        model=model,
        per_study_budget=BUDGET,
        pricing=PRICING,
        quality_budget=dict(max_model_calls=8, max_tokens=40000, max_seconds=300),
        quality_protocol=quality.COMPLETE_VERSION,
        forward_schedule=schedule(repo.base, rid, now),
        shared_flags=SHARED_FLAGS,
        limitations=LIMITATIONS,
        default_admission="STANDARD;PILOT_NEVER_PROMOTES_ENHANCED_AUTOMATICALLY",
    )
    state = dict(
        run_id=rid,
        request=request,
        status="RUNNING",
        scenario="research_evaluation",
        page="evaluation",
        title="效果验收 · 组件成本与回答质量",
        created_at=now.isoformat(),
        version=VERSION,
        stage="REGISTERED",
        manifest_ref=repo.blob(manifest),
        calls=[],
        process=[],
        tool_results={},
        model_calls=0,
        tokens=0,
        elapsed_seconds=0,
        cases={},
    )
    repo.base.put(state)
    return state, True


def verify_manifest(manifest, rid):
    if (
        manifest["version"] != VERSION
        or manifest["cases"] != CASES
        or manifest["children"] != children(rid)
        or manifest["quality_protocol"] not in {quality.VERSION, quality.COMPLETE_VERSION}
        or manifest["per_study_budget"] != BUDGET
        or manifest["shared_flags"] != SHARED_FLAGS
    ):
        raise ValueError("效果验收固定协议不一致")


def quality_report(repo, state):
    from .active_research import verify_profile
    from .paired_evaluation import usage

    q = state.get("quality", {})
    results = []
    manifest = repo.value(state["manifest_ref"])
    quality_version = q.get("version", quality.VERSION)
    quality_prompt = (
        quality.COMPLETE_PROMPT if quality_version == quality.COMPLETE_VERSION else quality.PROMPT
    )
    ir = InvestmentStore(repo.base)
    for case_id, step in q.get("results", {}).items():
        context = repo.value(step["context_ref"])
        entries = {e["arm"]: e for e in manifest["children"] if e["case_id"] == case_id}
        if set(entries) != set(ARMS):
            raise ValueError("盲评案例不属于登记样本")
        a, b = [ir.get(entries[arm]["run_id"]) for arm in ("ORIGINAL", "ENHANCED")]
        reverse = bool(int(digest(state["run_id"] + case_id)[0], 16) % 2)
        mapping = dict(
            A="ENHANCED" if reverse else "ORIGINAL", B="ORIGINAL" if reverse else "ENHANCED"
        )
        call = q["calls"][step["call_index"]]
        request = repo.value(call["request_ref"])
        if (
            step["mapping"] != mapping
            or context != quality.packet(ir, a, b, reverse, version=quality_version)
            or call["status"] != "COMPLETED"
            or call["response_ref"] != step["response_ref"]
            or request["instructions"] != quality_prompt
            or request["context"] != context
            or request["schema"] != quality.Assessment.model_json_schema()
            or request["prompt_version"] != quality_version
            or call["model"] != manifest["model"]
        ):
            raise ValueError("质量评阅与固定匿名输入、映射或真实调用不一致")
        verify_profile(q, request, call, "Verifier", repo=repo)
        answer = quality.Assessment.model_validate(repo.value(step["response_ref"]))
        quality.guard(answer, context)
        results.append((answer, step["mapping"]))
    return dict(
        quality.summarize(results),
        status=q.get("status", "NOT_STARTED"),
        completed_cases=len(results),
        declared_cases=len(CASES),
        error=q.get("error"),
        **usage(q),
    )


def report(repo, state):
    from .forward_pairs import view as forward_view
    from .paired_evaluation import failed_checks, matched_inputs, paired_outcomes, usage

    manifest = repo.value(state["manifest_ref"])
    ir = InvestmentStore(repo.base)
    if state.get("report_ref") and state["status"] == "COMPLETED":
        result = copy.deepcopy(repo.value(state["report_ref"]))
        for row in result["rows"]:
            if row["status"] == "COMPLETED":
                child = ir.get(row["run_id"])
                if child.get("result_ref") != row["sealed_result_ref"]:
                    raise ValueError("效果验收封存的已发布研究发生变化")
                view = ObservationStore(ir).view(child)
                if view.get("latest"):
                    row["prediction"] = view["latest"]["evaluation"]
        result["prediction"] = paired_outcomes(result["rows"], CASES)
        result["forward_pairs"] = forward_view(repo.base, manifest["forward_schedule"])
        from ..decision.next_session.store import ExperimentStore

        audits = ExperimentStore(repo.base)
        review_id = "DEC-EFFECT-QUALITY-" + state["run_id"]
        if audits.exists(review_id):
            revision = audits.record(review_id)
            worker = repo.value(revision["worker_ref"])
            if (
                worker["run_id"] != state["run_id"]
                or worker["manifest_ref"] != state["manifest_ref"]
                or revision["original_report_sha256"] != state["report_sha256"]
            ):
                raise ValueError("完整输出补充复评与原封存记录不一致")
            result["quality_followup"] = dict(
                quality=quality_report(repo, worker),
                protocol=quality.COMPLETE_VERSION,
                original_report_sha256=state["report_sha256"],
            )
        return result
    states = {
        e["run_id"]: ir.get(e["run_id"]) if ir.exists(e["run_id"]) else dict(status="NOT_STARTED")
        for e in manifest["children"]
    }
    rows = []
    for entry in manifest["children"]:
        child = states[entry["run_id"]]
        if child["status"] != "NOT_STARTED" and any(
            child["config"].get(k) != v for k, v in SHARED_FLAGS.items()
        ):
            raise ValueError("组件对照的共同执行配置不一致")
        case = next(c for c in CASES if c["case_id"] == entry["case_id"])
        opinions = child.get("data", {}).get("research_opinions", [])
        context = repo.value(child["public_context_ref"]) if child.get("public_context_ref") else {}
        base = (
            repo.value(child["base_public_context_ref"])
            if child.get("base_public_context_ref")
            else context
        )
        same = [
            states[e["run_id"]] for e in manifest["children"] if e["case_id"] == entry["case_id"]
        ]
        prediction = dict(stocks=[])
        date = None
        if child.get("result_ref"):
            observed = ObservationStore(ir).view(child)
            date = observed["protocol"]["start_after"]
            prediction = (observed.get("latest") or {}).get(
                "evaluation",
                dict(
                    stocks=[
                        dict(security_id=o["security_id"], status="NOT_OBSERVED") for o in opinions
                    ]
                ),
            )
        rows.append(
            dict(
                case_id=entry["case_id"],
                arm=entry["arm"],
                run_id=entry["run_id"],
                horizon_sessions=case["horizon_sessions"],
                status=child["status"],
                error=child.get("error"),
                sealed_result_ref=child.get("result_ref"),
                record_ref=repo.blob(child),
                matched_inputs=matched_inputs(same),
                failed_checks=failed_checks(repo, child),
                directions=[
                    dict(
                        security_id=o["security_id"],
                        direction=o["direction"],
                        summary=o["summary"],
                        claims=len(o["claims"]),
                        opposing=len(o["opposing"]),
                        method_rule_ids=o["method_rule_ids"],
                        evidence_count=len(
                            {e for c in o["claims"] + o["opposing"] for e in c["evidence_ids"]}
                        ),
                    )
                    for o in opinions
                ],
                tool_calls=len(context.get("tool_results", [])),
                review_rounds=sum(k.startswith("Verifier-") for k in child.get("model_steps", {})),
                additional_evidence=max(
                    0, len(context.get("evidence", [])) - len(base.get("evidence", []))
                ),
                prediction=prediction,
                registration_date=date,
                **usage(child, manifest["pricing"]),
            )
        )
    groups = []
    for arm in ARMS:
        own = [r for r in rows if r["arm"] == arm]
        groups.append(
            dict(
                arm=arm,
                completed=sum(r["status"] == "COMPLETED" for r in own),
                declared=len(own),
                model_calls=sum(r["model_calls"] for r in own),
                tokens=sum(r["input_tokens"] + r["output_tokens"] for r in own),
                elapsed_seconds=sum(r["elapsed_seconds"] for r in own),
                unknown_usage_calls=sum(r["unknown_usage_calls"] for r in own),
                cost_lower=sum(
                    float(r["cost"]["lower_cny"]) for r in own if r["cost"]["lower_cny"] is not None
                ),
                cost_upper=sum(
                    float(r["cost"]["upper_cny"]) for r in own if r["cost"]["upper_cny"] is not None
                ),
            )
        )
    completed = sum(r["status"] == "COMPLETED" for r in rows)
    return dict(
        rows=rows,
        pairs=[],
        prediction=paired_outcomes(rows, CASES),
        ablation=None,
        quality=quality_report(repo, state),
        components=groups,
        round_summary=dict(
            completed_studies=completed,
            declared_studies=len(rows),
            completed_method_arms=0,
            declared_method_arms=0,
            arms=[],
            verdict="ALL_COMPLETED" if completed == len(rows) else "COMPLETE_WITH_FAILURES",
        ),
        forward_pairs=forward_view(repo.base, manifest["forward_schedule"]),
        shared_capture_seconds=sum(
            c.get("capture_seconds", 0) for c in state.get("cases", {}).values()
        ),
        limitations=LIMITATIONS,
    )


@workflow("alphaagent.effectiveness", id_parameter="rid")
async def execute(repo, rules, rid, stop, settings, *, port=None):
    state = repo.get(rid, with_data=False)
    if state["status"] == "COMPLETED":
        return state
    manifest = repo.value(state["manifest_ref"])
    started, previous = time.monotonic(), state.get("elapsed_seconds", 0)

    def save():
        state["elapsed_seconds"] = previous + time.monotonic() - started
        repo.save(state)

    try:
        if settings.model != manifest["model"]:
            raise ValueError("效果验收不能更换登记模型")
        state.update(status="RUNNING", error=None)
        save()
        ir = InvestmentStore(repo.base)
        for entry in manifest["children"]:
            child, fresh = ir.create(entry["request"])
            if fresh:
                child["config"].update(SHARED_FLAGS)
                ir.save(child)
        for case in CASES:
            if stop.is_set():
                raise ValueError("评测已停止，已取资料保留")
            state["stage"] = case["case_id"] + ":INPUTS"
            frozen = state["cases"].setdefault(case["case_id"], {})
            capture_started = time.monotonic()
            client = SourceClient(
                repo, state, save, stop, max_requests=220, budget_profile=dict(max_seconds=1800)
            )
            if "inputs_ref" not in frozen:
                packet = await asyncio.to_thread(
                    capture_inputs,
                    client,
                    tuple(case["entities"]),
                    datetime.fromisoformat(state["created_at"]),
                )
                frozen["inputs_ref"] = repo.blob(packet)
                save()
            packet = repo.value(frozen["inputs_ref"])
            if "context_snapshot_ref" not in frozen:
                snapshot = await asyncio.to_thread(collect, repo, client, packet)
                frozen.update(
                    context_snapshot_ref=repo.blob(snapshot),
                    context_report_ref=repo.blob(rebuild(repo, packet, snapshot)),
                    capture_seconds=time.monotonic() - capture_started,
                )
                save()
            for entry in [e for e in manifest["children"] if e["case_id"] == case["case_id"]]:
                child = ir.get(entry["run_id"], with_data=False)
                if child["status"] == "COMPLETED":
                    continue
                state["stage"] = case["case_id"] + ":" + entry["arm"]
                save()
                for key in ("inputs_ref", "context_snapshot_ref", "context_report_ref"):
                    if child.get(key) and child[key] != frozen[key]:
                        raise ValueError("组件对照基础输入不能改变")
                    child[key] = frozen[key]
                child["source_refs"] = sorted(
                    set(
                        re.findall(
                            r"blob://sha256/[0-9a-f]{64}",
                            json.dumps([packet, repo.value(frozen["context_snapshot_ref"])]),
                        )
                    )
                )
                ir.save(child)
                await run_with_recovery(ir, rules, child["run_id"], stop, settings, port=port)
        await evaluate_quality(repo, state, manifest, settings, save, stop, port)
        result = report(repo, state)
        state.update(
            status="COMPLETED",
            stage="COMPLETED",
            report_ref=repo.blob(result),
            report_sha256=digest(result),
            sealed_at=datetime.now(UTC).isoformat(),
        )
        save()
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240] if isinstance(error, ValueError) else type(error).__name__,
        )
        save()
    return state


async def evaluate_quality(repo, state, manifest, settings, save, stop, port):
    from ..decision.next_session.input_reservation import POLICY
    from .structured_contract import configure

    q = state.setdefault(
        "quality",
        dict(
            version=manifest["quality_protocol"],
            run_id=state["run_id"],
            calls=[],
            model_calls=0,
            tokens=0,
            charged_tokens=0,
            elapsed_seconds=0,
            results={},
            config=dict(budget=manifest["quality_budget"], input_reservation=POLICY),
        ),
    )
    if q.get("status") == "COMPLETED":
        return
    configure(q["config"], False)
    quality_version = q.get("version", quality.VERSION)
    quality_prompt = (
        quality.COMPLETE_PROMPT if quality_version == quality.COMPLETE_VERSION else quality.PROMPT
    )
    start, prior = time.monotonic(), q["elapsed_seconds"]

    def persist():
        q["elapsed_seconds"] = prior + time.monotonic() - start
        save()

    def check():
        if (
            stop.is_set()
            or prior + time.monotonic() - start >= q["config"]["budget"]["max_seconds"]
        ):
            raise ValueError("回答质量评阅达到固定时间上限")

    try:
        state["stage"] = "ANSWER_QUALITY"
        q["status"] = "RUNNING"
        persist()
        ir = InvestmentStore(repo.base)
        async with DailyRoles(
            repo,
            q,
            settings,
            persist,
            check,
            port=port,
            prompts={"Verifier": quality_prompt},
            schema_version="investment-research-v4",
            output_tokens=4200,
            min_output_tokens=1800,
            reserve_limit=45000,
        ) as roles:
            for case in CASES:
                if case["case_id"] in q["results"]:
                    continue
                entries = {
                    e["arm"]: e for e in manifest["children"] if e["case_id"] == case["case_id"]
                }
                a, b = [ir.get(entries[arm]["run_id"]) for arm in ("ORIGINAL", "ENHANCED")]
                if a["status"] != "COMPLETED" or b["status"] != "COMPLETED":
                    continue
                reverse = bool(int(digest(state["run_id"] + case["case_id"])[0], 16) % 2)
                context = quality.packet(ir, a, b, reverse, version=quality_version)
                value, call = await roles.call(
                    "Verifier",
                    quality.Assessment,
                    context,
                    lambda v, context=context: quality.guard(v, context),
                    prompt_version=quality_version,
                )
                q["results"][case["case_id"]] = dict(
                    context_ref=repo.blob(context),
                    response_ref=call["response_ref"],
                    call_index=len(q["calls"]) - 1,
                    mapping=dict(
                        A="ENHANCED" if reverse else "ORIGINAL",
                        B="ORIGINAL" if reverse else "ENHANCED",
                    ),
                )
                persist()
        q["status"] = "COMPLETED" if len(q["results"]) == len(CASES) else "INCOMPLETE_PAIRS"
    except Exception as error:
        q.update(status="INTERRUPTED", error=str(error)[:240])
    persist()


async def recheck_quality(repo, rid, settings, *, port=None):
    """Append one complete-output review; never rewrite the original scores or study runs."""
    import threading

    from ..decision.next_session.input_reservation import POLICY
    from ..decision.next_session.store import ExperimentStore

    original = repo.get(rid, with_data=False)
    if original["status"] != "COMPLETED":
        raise ValueError("原实验尚未封存")
    audit = ExperimentStore(repo.base)
    identity = "DEC-EFFECT-QUALITY-" + rid
    if audit.exists(identity):
        return audit.record(identity)
    legacy_id = "DEC-EFFECT-QUALITY-WORK-" + rid
    if repo.exists(legacy_id):
        prior = repo.base.get(legacy_id, with_data=False)
        audit.immutable(
            "DEC-EFFECT-QUALITY-WORK-FAILURE-" + rid,
            dict(
                worker_ref=repo.blob(prior),
                status="CHECKPOINT_FAILED",
                reason="进度记录缺少请求身份；原记录保留，模型调用前保存失败。",
            ),
        )
    work_id = "DEC-EFFECT-QUALITY-WORK-V2-" + rid
    worker = (
        repo.base.get(work_id, with_data=False)
        if repo.exists(work_id)
        else dict(
            run_id=rid,
            request=dict(
                parent_run_id=rid,
                protocol=quality.COMPLETE_VERSION,
                original_report_sha256=original["report_sha256"],
            ),
            manifest_ref=original["manifest_ref"],
            quality=dict(
                version=quality.COMPLETE_VERSION,
                run_id=work_id,
                status="PENDING",
                calls=[],
                model_calls=0,
                tokens=0,
                charged_tokens=0,
                elapsed_seconds=0,
                results={},
                config=dict(
                    budget=dict(max_model_calls=8, max_tokens=40000, max_seconds=300),
                    input_reservation=POLICY,
                ),
            ),
        )
    )
    worker["run_id"] = rid

    def save():
        repo.base.put(
            dict(
                worker,
                run_id=work_id,
                status=worker["quality"]["status"],
                created_at=original["created_at"],
            )
        )

    manifest = repo.value(original["manifest_ref"])
    await evaluate_quality(repo, worker, manifest, settings, save, threading.Event(), port)
    quality_report(repo, worker)
    return audit.immutable(
        identity,
        dict(
            worker_ref=repo.blob(worker),
            version=quality.COMPLETE_VERSION,
            original_report_sha256=original["report_sha256"],
            created_at=datetime.now(UTC).isoformat(),
        ),
    )
