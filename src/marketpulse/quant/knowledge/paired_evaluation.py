"""Preregistered matched public studies; live outcomes stay append-only in native stores."""

import asyncio
import copy
import json
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict

from marketpulse.telemetry.tracing import workflow

from ..contracts import digest
from ..decision.store import DecisionStore
from ..decision.tools import SourceClient
from . import ablation, grouped_ablation
from .context import collect as collect_context
from .context import rebuild
from .inputs import capture_inputs
from .observations import ObservationStore
from .research_contracts import InvestmentRequest
from .research_service import run_with_recovery
from .research_store import BUDGET, InvestmentStore

VERSION = "paired-public-research-v1"
REPAIRED_VERSION = "paired-public-research-v2"
GROUPED_VERSION = "paired-public-research-v3"
COMPACT_VERSION = "paired-public-research-v4"
TOKEN_VERSION = "paired-public-research-v5"
RESIZED_VERSION = "paired-public-research-v6"
INDIVIDUAL_VERSION = "paired-public-research-v7"
ENTITIES = ["000333", "600036"]
CASES = [
    dict(case_id="MEDIUM60", horizon="MEDIUM", horizon_sessions=60, order=["ORIGINAL", "ENHANCED"]),
    dict(case_id="SWING20", horizon="SWING", horizon_sessions=20, order=["ENHANCED", "ORIGINAL"]),
]
# Cache and peak-band details are absent in the adapter's usage contract: a range, never a bill.
PRICING = dict(
    version="deepseek-cny-20261019",
    checked_at="2026-10-19",
    source_url="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    currency="CNY",
    unit="per_million_tokens",
    models={
        "deepseek-flash": dict(input_min="0.02", input_max="2", output_min="4", output_max="8"),
        "deepseek-v4-pro": dict(
            input_min="0.15", input_max="9", output_min="13.5", output_max="27"
        ),
    },
    note="最低使用谷时缓存命中价，最高使用峰时缓存未命中价；缺少缓存量及账单，区间非实际扣费。",
)
LIMITATIONS = [
    "本轮只有两证券、一个登记日期及两个期限，是配对试验，不足以证明总体预测或投资收益提升。",
    "原/增强同时改变动态知识、工具、推理和发布流程；差异不能单独归因知识库或某个推理角色。",
    "基础输入与上下文完全相同；增强追加的工具证据另计，最终可见证据不保证相同。",
    "两组交错顺序减轻热身偏差，未盲审且模型仍有共同盲点；论据数量不等于质量或收益。",
    "实际输出token包含供应商计入的推理；费用按固定官网价格区间估算，不是平台账单。",
    "未到期、平盘、取数失败和研究未完成分别保留；不回填过去预测，不降低旧5.2门禁。",
]


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_key: UUID
    protocol_version: Literal[
        "paired-public-research-v1",
        "paired-public-research-v2",
        "paired-public-research-v3",
        "paired-public-research-v4",
        "paired-public-research-v5",
        "paired-public-research-v6",
        "paired-public-research-v7",
        "research-effectiveness-v1",
    ] = VERSION


class EvaluationStore(DecisionStore):
    prefix = "RUN-EV-"

    def create(self, request, model):
        rid = self.prefix + UUID(request["request_key"]).hex
        if self.exists(rid):
            return self.get(rid), False
        version = request.get("protocol_version", VERSION)
        if version == "research-effectiveness-v1":
            from .effectiveness import create

            return create(self, request, model)
        repaired = version in {
            REPAIRED_VERSION,
            GROUPED_VERSION,
            COMPACT_VERSION,
            TOKEN_VERSION,
            RESIZED_VERSION,
            INDIVIDUAL_VERSION,
        }
        children = []
        for case in CASES:
            for arm in case["order"]:
                key = uuid5(NAMESPACE_URL, rid + ":" + case["case_id"] + ":" + arm)
                child_request = InvestmentRequest(
                    schema_version="investment-research-v4",
                    request_key=key,
                    entities=tuple(ENTITIES),
                    horizon=case["horizon"],
                    horizon_sessions=case["horizon_sessions"],
                    enhanced_research=arm == "ENHANCED",
                    execution_contract="investment-structured-v2" if repaired else None,
                    input_reservation="deepseek-v4-tokenizer-v1"
                    if version in {TOKEN_VERSION, RESIZED_VERSION, INDIVIDUAL_VERSION}
                    else None,
                ).model_dump(mode="json")
                children.append(
                    dict(
                        case_id=case["case_id"],
                        arm=arm,
                        run_id="RUN-IR-" + key.hex,
                        request=child_request,
                    )
                )
        manifest = dict(
            version=version,
            registered_at=datetime.now(UTC).isoformat(),
            entities=ENTITIES,
            cases=CASES,
            children=children,
            model=model,
            per_study_budget=BUDGET,
            pricing=PRICING,
            limitations=LIMITATIONS,
            outcome="native-forward-v1;no-historical-rescore",
            knowledge_effect="separate-v1-method-context-ablation-not-accuracy",
            execution_contract="investment-structured-v2" if repaired else None,
        )
        if version in {
            GROUPED_VERSION,
            COMPACT_VERSION,
            TOKEN_VERSION,
            RESIZED_VERSION,
            INDIVIDUAL_VERSION,
        }:
            manifest["method_protocol"] = dict(
                version=ablation.TOKEN_VERSION
                if version == INDIVIDUAL_VERSION
                else grouped_ablation.TOKEN_VERSION
                if version in {TOKEN_VERSION, RESIZED_VERSION, INDIVIDUAL_VERSION}
                else grouped_ablation.COMPACT_VERSION
                if version == COMPACT_VERSION
                else grouped_ablation.VERSION,
                independent_analysts=True,
                shared_evidence_review=version != INDIVIDUAL_VERSION,
                repair_rounds=2,
                budget=BUDGET,
                feedback="own-arm-only",
            )
        if version in {RESIZED_VERSION, INDIVIDUAL_VERSION}:
            manifest["researcher_output_policy"] = dict(
                json_allowance=4096,
                reasoning_allowance=2048,
                total_output_limit=6144,
                cumulative_study_budget=BUDGET,
                note="供应商推理与JSON共用输出上限；余量不是已发生用量，实际回执累计。",
            )
        state = dict(
            run_id=rid,
            request=request,
            status="RUNNING",
            scenario="research_evaluation",
            page="evaluation",
            title="原流程与增强流程 · 真实数据配对评测",
            created_at=manifest["registered_at"],
            version=manifest["version"],
            stage="REGISTERED",
            manifest_ref=self.blob(manifest),
            calls=[],
            process=[],
            tool_results={},
            model_calls=0,
            tokens=0,
            elapsed_seconds=0,
        )
        self.base.put(state)
        return state, True

    def get(self, rid, *, with_data=True):
        if not re.fullmatch(r"RUN-EV-[0-9a-f]{32}", rid):
            raise KeyError(rid)
        state = self.base.get(rid, with_data=False)
        manifest = self.value(state["manifest_ref"])
        if (
            manifest["version"]
            not in {
                VERSION,
                REPAIRED_VERSION,
                GROUPED_VERSION,
                COMPACT_VERSION,
                TOKEN_VERSION,
                RESIZED_VERSION,
                INDIVIDUAL_VERSION,
                "research-effectiveness-v1",
            }
            or manifest["version"] != "research-effectiveness-v1"
            and manifest["cases"] != CASES
        ):
            raise ValueError("评测协议不一致")
        if manifest["version"] == "research-effectiveness-v1":
            from .effectiveness import verify_manifest

            verify_manifest(manifest, rid)
        if (
            state.get("report_ref")
            and digest(self.value(state["report_ref"])) != state["report_sha256"]
        ):
            raise ValueError("评测报告哈希不一致")
        if with_data:
            state["manifest"] = manifest
            state["data"] = report(self, state)
        return state

    def list(self):
        return self.base.list(prefix=self.prefix)


def usage(state, pricing=PRICING):
    calls = [c for c in state.get("calls", []) if c.get("kind") == "model"]
    low = high = Decimal(0)
    ins = outs = unknown = unsupported = 0
    model_seconds = 0.0
    for call in calls:
        rates = pricing["models"].get(call.get("model"))
        if rates is None:
            unsupported += 1
        known = all(
            type(call.get(k)) is int and call[k] >= 0 for k in ("input_tokens", "output_tokens")
        )
        if known:
            i, o = call["input_tokens"], call["output_tokens"]
            ins += i
            outs += o
            if rates:
                low += Decimal(i) * Decimal(rates["input_min"]) + Decimal(o) * Decimal(
                    rates["output_min"]
                )
                high += Decimal(i) * Decimal(rates["input_max"]) + Decimal(o) * Decimal(
                    rates["output_max"]
                )
        else:
            unknown += 1
            if rates:
                high += Decimal(call.get("reserved_tokens", 0)) * max(
                    Decimal(rates["input_max"]), Decimal(rates["output_max"])
                )
        if call.get("started_at") and call.get("finished_at"):
            model_seconds += max(
                0,
                (
                    datetime.fromisoformat(call["finished_at"])
                    - datetime.fromisoformat(call["started_at"])
                ).total_seconds(),
            )
    return dict(
        model_calls=len(calls),
        input_tokens=ins,
        output_tokens=outs,
        unknown_usage_calls=unknown,
        unknown_model_calls=unsupported,
        budget_charged_tokens=state.get("charged_tokens", 0),
        input_count_calls=sum(c.get("purpose") == "input_count" for c in calls),
        failed_calls=sum(c.get("status") in {"FAILED", "INTERRUPTED"} for c in calls),
        model_seconds=round(model_seconds, 3),
        elapsed_seconds=round(state.get("elapsed_seconds", 0), 3),
        cost=dict(
            lower_cny=str(low / 1000000) if not unsupported else None,
            upper_cny=str(high / 1000000) if not unsupported else None,
            basis="ESTIMATED_RANGE_NOT_BILL",
        ),
    )


def matched_inputs(states):
    return len(states) == 2 and all(
        states[0].get(k) and states[0].get(k) == states[1].get(k)
        for k in ("inputs_ref", "context_snapshot_ref", "context_report_ref")
    )


def next_session_history(repo, cutoff):
    """Describe existing forecasts, never treat unmatched archives as a causal trial."""
    from ..decision.next_session.metrics import statistics
    from ..decision.next_session.store import ExperimentStore

    archive = ExperimentStore(repo.base, request_cache=True)
    modes = {
        pid: "ENHANCED" if row.get("config", {}).get("agentic_research_v1") else "ORIGINAL"
        for row in archive.records("RUN-NS-")
        for pid in row.get("prediction_ids", [])
    }
    unique = {}
    forecasts = sorted(archive.predictions(), key=lambda p: p.issued_at)
    for p in forecasts:
        if p.issued_at > datetime.fromisoformat(cutoff):
            continue
        arm = modes.get(p.prediction_id, "ORIGINAL")
        key = (arm, p.security_id, str(p.target_session))
        if key in unique:
            continue
        versions = archive.outcome_versions(p.prediction_id)
        outcome = versions[-1] if versions else {}
        unique[key] = dict(
            arm=arm,
            prediction_id=p.prediction_id,
            security_id=p.security_id,
            base_session=str(p.base_session),
            target_session=str(p.target_session),
            prediction=p.prediction,
            actual=outcome.get("return_direction") if outcome.get("status") == "SCORABLE" else None,
            status=outcome.get("status", "PENDING"),
            baselines=dict(p.baselines),
        )
    return dict(
        scope="OBSERVATIONAL_ARCHIVE_NOT_MATCHED",
        cutoff=cutoff,
        deduplication="earliest-issued-per-arm-security-target;fixed-before-outcome",
        groups=[
            dict(arm=arm, registered=len(items), records=items, **statistics(items))
            for arm in ("ORIGINAL", "ENHANCED")
            for items in [[v for v in unique.values() if v["arm"] == arm]]
        ],
        limitations="只描述既有历史，输入与时间不匹配；不能用两组准确率差证明增强效果。",
    )


def paired_outcomes(rows, cases=None):
    cases = cases or CASES
    result = dict(
        registered_pairs=sum(len(c.get("entities", ENTITIES)) for c in cases),
        paired_matured=0,
        original_correct=0,
        enhanced_correct=0,
        accuracy_delta=None,
        independent_dates=0,
        unique_target_dates=0,
        baseline_correct={},
        status_counts={},
    )
    dates, origins = set(), set()
    for case in cases:
        pair = [
            r
            for r in rows
            if r["case_id"] == case["case_id"] and r["arm"] in {"ORIGINAL", "ENHANCED"}
        ]
        observations = {
            r["arm"]: {s["security_id"]: s for s in r["prediction"]["stocks"]} for r in pair
        }
        securities = sorted(set().union(*(set(v) for v in observations.values())))
        for sid in securities or case.get("entities", ENTITIES):
            a = observations.get("ORIGINAL", {}).get(sid, {})
            b = observations.get("ENHANCED", {}).get(sid, {})
            compatible = len(pair) == 2 and all(r["matched_inputs"] for r in pair)
            flat = a.get("actual_direction") == "FLAT" or b.get("actual_direction") == "FLAT"
            mature = (
                compatible
                and not flat
                and a.get("status") == b.get("status") == "MATURED"
                and a.get("target_date")
                and a.get("target_date") == b.get("target_date")
                and a.get("actual_direction") in {"UP", "DOWN"}
                and a.get("actual_direction") == b.get("actual_direction")
            )
            status = (
                "FLAT"
                if flat
                else "PAIRED_MATURED"
                if mature
                else " / ".join(
                    sorted({a.get("status", "NOT_OBSERVED"), b.get("status", "NOT_OBSERVED")})
                )
            )
            result["status_counts"][status] = result["status_counts"].get(status, 0) + 1
            if mature:
                result["paired_matured"] += 1
                result["original_correct"] += int(a["correct"])
                result["enhanced_correct"] += int(b["correct"])
                dates.add(a["target_date"])
                origins.update(r["registration_date"] for r in pair if r.get("registration_date"))
                for k, v in a["baseline_correct"].items():
                    result["baseline_correct"][k] = result["baseline_correct"].get(k, 0) + int(v)
    result["independent_dates"] = len(origins)
    result["unique_target_dates"] = len(dates)
    n = result["paired_matured"]
    if n:
        result["accuracy_delta"] = (result["enhanced_correct"] - result["original_correct"]) / n
    return result


def failed_checks(repo, child):
    from . import research_agents as agents
    from .research_context import analysis_basis

    if not child.get("public_context_ref") or "Planner" not in child.get("model_steps", {}):
        return []
    basis = analysis_basis(
        repo.value(child["public_context_ref"]),
        repo.value(child["model_steps"]["Planner"]["response_ref"]),
        scoped=child["config"].get("scoped_analysis", False),
    )
    issues = []
    for index, call in enumerate(child.get("calls", [])):
        if (
            call.get("kind") != "model"
            or call.get("role") != "Analyst"
            or call["status"] != "FAILED"
            or not call.get("response_ref")
        ):
            continue
        if call.get("contract_issue"):
            issues.append(dict(role="Analyst", call_index=index, issue=call["contract_issue"]))
            continue
        try:
            agents.guard_analysis(
                agents.Analysis.model_validate(repo.value(call["response_ref"])), basis
            )
        except ValueError as error:
            issues.append(dict(role="Analyst", call_index=index, issue=str(error)[:240]))
    return issues


def round_summary(rows, method):
    arms = []
    for arm in ("ORIGINAL", "ENHANCED"):
        group = [r for r in rows if r["arm"] == arm]
        priced = all(
            r["cost"]["lower_cny"] is not None and r["cost"]["upper_cny"] is not None for r in group
        )
        arms.append(
            dict(
                arm=arm,
                declared=len(group),
                completed=sum(r["status"] == "COMPLETED" for r in group),
                model_calls=sum(r["model_calls"] for r in group),
                input_tokens=sum(r["input_tokens"] for r in group),
                output_tokens=sum(r["output_tokens"] for r in group),
                cost={
                    k: str(sum(Decimal(r["cost"][k]) for r in group)) if priced else None
                    for k in ("lower_cny", "upper_cny")
                },
                elapsed_seconds=round(sum(r["elapsed_seconds"] for r in group), 3),
            )
        )
    completed = sum(r["status"] == "COMPLETED" for r in rows)
    methods = method["report"]["completed"] if method else 0
    return dict(
        declared_studies=len(rows),
        completed_studies=completed,
        unsuccessful_studies=len(rows) - completed,
        declared_method_arms=6,
        completed_method_arms=methods,
        verdict="ALL_COMPLETED"
        if completed == len(rows) and methods == 6
        else "COMPLETE_WITH_FAILURES",
        arms=arms,
    )


def report(repo, state):
    if state.get("version") == "research-effectiveness-v1":
        from .effectiveness import report as effectiveness_report

        return effectiveness_report(repo, state)
    ir = InvestmentStore(repo.base)
    manifest = repo.value(state["manifest_ref"])
    if state["status"] == "COMPLETED" and state.get("report_ref"):
        frozen = copy.deepcopy(repo.value(state["report_ref"]))
        for row in frozen["rows"]:
            # Resuming a failed child later must not rewrite this round's engineering score.
            if row["status"] != "COMPLETED":
                continue
            child = ir.get(row["run_id"])
            if child.get("result_ref") != row.get("sealed_result_ref"):
                raise ValueError("封存评测的已发布子研究发生变化")
            view = ObservationStore(ir).view(child)
            if view.get("latest"):
                row["prediction"] = dict(view["latest"]["evaluation"])
            attempt = view.get("last_attempt")
            row["prediction"]["last_attempt"] = (
                {k: attempt.get(k) for k in ("status", "created_at", "error")} if attempt else None
            )
            row["prediction"]["latest_valid_at"] = (
                view["latest"]["created_at"] if view.get("latest") else None
            )
        frozen["prediction"] = paired_outcomes(frozen["rows"])
        frozen["next_session_history"] = next_session_history(repo, manifest["registered_at"])
        return frozen
    rows, pairs = [], []
    by_case = {}
    for entry in manifest["children"]:
        child = (
            ir.get(entry["run_id"]) if ir.exists(entry["run_id"]) else dict(status="NOT_STARTED")
        )
        by_case.setdefault(entry["case_id"], []).append(child)
    for entry in manifest["children"]:
        case = next(c for c in CASES if c["case_id"] == entry["case_id"])
        child = (
            ir.get(entry["run_id"]) if ir.exists(entry["run_id"]) else dict(status="NOT_STARTED")
        )
        directions = []
        if child.get("approved_analysis_step"):
            from . import research_agents

            analysis = ir.output(
                child, child["approved_analysis_step"], research_agents.Analysis
            ).model_dump(mode="json")
            for opinion in analysis["opinions"]:
                facts = opinion["claims"] + opinion["opposing"]
                directions.append(
                    dict(
                        security_id=opinion["security_id"],
                        direction=opinion["direction"],
                        summary=opinion["summary"],
                        claims=len(opinion["claims"]),
                        opposing=len(opinion["opposing"]),
                        method_rule_ids=opinion["method_rule_ids"],
                        evidence_count=len({e for c in facts for e in c["evidence_ids"]}),
                    )
                )
        packet = repo.value(child["inputs_ref"]) if child.get("inputs_ref") else None
        prediction = (
            dict(
                stocks=[
                    dict(security_id=s["ts_code"], status="RESEARCH_" + child["status"])
                    for s in packet["stocks"]
                ]
            )
            if packet
            else dict(stocks=[])
        )
        registration_date = None
        if child.get("result_ref"):
            view = ObservationStore(ir).view(child)
            registration_date = view["protocol"]["start_after"]
            if view.get("latest"):
                prediction = dict(view["latest"]["evaluation"])
            else:
                prediction = dict(
                    stocks=[
                        dict(security_id=c["security_id"], status="NOT_OBSERVED")
                        for c in child["data"]["candidates"]
                    ]
                )
            attempt = view.get("last_attempt")
            prediction["last_attempt"] = (
                {k: attempt.get(k) for k in ("status", "created_at", "error")} if attempt else None
            )
            prediction["latest_valid_at"] = (
                view["latest"]["created_at"] if view.get("latest") else None
            )
        context = repo.value(child["public_context_ref"]) if child.get("public_context_ref") else {}
        base = (
            repo.value(child["base_public_context_ref"])
            if child.get("base_public_context_ref")
            else context
        )
        rows.append(
            dict(
                case_id=entry["case_id"],
                arm=entry["arm"],
                run_id=entry["run_id"],
                record_ref=repo.blob(child),
                sealed_result_ref=child.get("result_ref"),
                horizon_sessions=case["horizon_sessions"],
                status=child["status"],
                error=child.get("error"),
                failed_checks=failed_checks(repo, child),
                matched_inputs=matched_inputs(by_case[entry["case_id"]]),
                directions=directions,
                prediction=prediction,
                registration_date=registration_date,
                review_rounds=sum(k.startswith("Verifier-") for k in child.get("model_steps", {})),
                tool_calls=len(context.get("tool_results", [])),
                additional_evidence=max(
                    0, len(context.get("evidence", [])) - len(base.get("evidence", []))
                ),
                **usage(child, manifest["pricing"]),
            )
        )
    for case in CASES:
        arms = {r["arm"]: r for r in rows if r["case_id"] == case["case_id"]}
        a, b = arms["ORIGINAL"], arms["ENHANCED"]
        complete = a["status"] == b["status"] == "COMPLETED"
        costs = [
            a["cost"]["lower_cny"],
            a["cost"]["upper_cny"],
            b["cost"]["lower_cny"],
            b["cost"]["upper_cny"],
        ]
        pairs.append(
            dict(
                case_id=case["case_id"],
                matched_inputs=a["matched_inputs"],
                complete=complete,
                latency_delta_seconds=round(b["elapsed_seconds"] - a["elapsed_seconds"], 3)
                if complete
                else None,
                token_delta=(
                    b["input_tokens"] + b["output_tokens"] - a["input_tokens"] - a["output_tokens"]
                )
                if complete
                else None,
                cost_delta_lower=str(Decimal(costs[2]) - Decimal(costs[1]))
                if complete and all(v is not None for v in costs)
                else None,
                cost_delta_upper=str(Decimal(costs[3]) - Decimal(costs[0]))
                if complete and all(v is not None for v in costs)
                else None,
            )
        )
    method = None
    if state.get("ablation"):
        trial = state["ablation"]
        engine = grouped_ablation if trial["version"] in grouped_ablation.VERSIONS else ablation
        method = dict(
            report=engine.verify(repo.value, trial)
            if trial["status"] == "COMPLETED"
            else engine.summary(repo.value, trial),
            **usage(trial, manifest["pricing"]),
        )
    return dict(
        rows=rows,
        pairs=pairs,
        prediction=paired_outcomes(rows),
        ablation=method,
        round_summary=round_summary(rows, method),
        next_session_history=next_session_history(repo, manifest["registered_at"]),
        shared_capture_seconds=state.get("capture_seconds"),
        limitations=LIMITATIONS,
    )


def seal(repo, rid):
    """Finish the measurement, including bounded failures; never promote failed research."""
    state = repo.get(rid, with_data=False)
    if state["status"] == "COMPLETED":
        return repo.get(rid)
    if state["status"] != "INTERRUPTED":
        raise ValueError("只有已停止的评测可封存，执行中的任务请等待结束")
    result = report(repo, state)
    state.update(
        report_ref=repo.blob(result),
        report_sha256=digest(result),
        status="COMPLETED",
        stage="COMPLETED",
        sealed_at=datetime.now(UTC).isoformat(),
        completion_reason="BOUNDED_ROUND_FINISHED_INCLUDING_FAILURES",
        error=None,
    )
    repo.save(state)
    return repo.get(rid)


@workflow("alphaagent.evaluation", id_parameter="rid")
async def execute(repo, rules, rid, stop, settings, *, port=None):
    state = repo.get(rid, with_data=False)
    if state.get("version") == "research-effectiveness-v1":
        from .effectiveness import execute as execute_effectiveness

        return await execute_effectiveness(repo, rules, rid, stop, settings, port=port)
    manifest = repo.value(state["manifest_ref"])
    if state["status"] == "COMPLETED":
        return state
    ir = InvestmentStore(repo.base)
    started, prior = time.monotonic(), state.get("elapsed_seconds", 0)

    def save():
        state["elapsed_seconds"] = prior + time.monotonic() - started
        repo.save(state)

    try:
        if settings.model != manifest["model"]:
            raise ValueError("不能更换登记模型")
        state.update(status="RUNNING", error=None)
        save()
        for entry in manifest["children"]:
            ir.create(entry["request"])
        if "inputs_ref" not in state:
            state["stage"] = "SHARED_INPUTS"
            save()
            client = SourceClient(
                repo, state, save, stop, max_requests=100, budget_profile=dict(max_seconds=900)
            )
            packet = await asyncio.to_thread(
                capture_inputs, client, tuple(ENTITIES), datetime.fromisoformat(state["created_at"])
            )
            state["inputs_ref"] = repo.blob(packet)
            save()
        packet = repo.value(state["inputs_ref"])
        if "context_snapshot_ref" not in state:
            client = SourceClient(
                repo, state, save, stop, max_requests=100, budget_profile=dict(max_seconds=900)
            )
            snapshot = await asyncio.to_thread(collect_context, repo, client, packet)
            state["context_snapshot_ref"] = repo.blob(snapshot)
            state["context_report_ref"] = repo.blob(rebuild(repo, packet, snapshot))
            state["capture_seconds"] = time.monotonic() - started
            save()
        for entry in manifest["children"]:
            if stop.is_set():
                raise ValueError("评测已停止，输入与进度保留")
            state["stage"] = entry["case_id"] + ":" + entry["arm"]
            save()
            child = ir.get(entry["run_id"], with_data=False)
            if child["status"] == "COMPLETED":
                continue
            for key in ("inputs_ref", "context_snapshot_ref", "context_report_ref"):
                if child.get(key) and child[key] != state[key]:
                    raise ValueError("配对基础输入不能更换")
                child[key] = state[key]
            child["source_refs"] = sorted(
                set(
                    re.findall(
                        r"blob://sha256/[0-9a-f]{64}",
                        json.dumps([packet, repo.value(state["context_snapshot_ref"])]),
                    )
                )
            )
            ir.save(child)
            child = await run_with_recovery(ir, rules, child["run_id"], stop, settings, port=port)
            if child["status"] == "COMPLETED":
                await asyncio.to_thread(ObservationStore(ir).observe, ir.get(child["run_id"]))
        if "ablation" not in state:
            source = ir.get(manifest["children"][0]["run_id"])
            if source["status"] != "COMPLETED":
                raise ValueError("方法消融需要本轮已复核的原流程公开输入，失败组仍保留")
            frozen = (
                ablation.freeze(repo, source, settings.model, version=ablation.TOKEN_VERSION)
                if manifest["version"] == INDIVIDUAL_VERSION
                else grouped_ablation.freeze(
                    repo, source, settings.model, version=manifest["method_protocol"]["version"]
                )
                if manifest["version"]
                in {
                    GROUPED_VERSION,
                    COMPACT_VERSION,
                    TOKEN_VERSION,
                    RESIZED_VERSION,
                    INDIVIDUAL_VERSION,
                }
                else (
                    ablation.freeze(repo, source, settings.model, version=ablation.REPAIRED_VERSION)
                    if manifest["version"] == REPAIRED_VERSION
                    else ablation.freeze(repo, source, settings.model)
                )
            )
            state["ablation"] = dict(
                version=frozen["version"],
                run_id=rid,
                manifest_ref=repo.blob(frozen),
                status="FROZEN",
                config=(
                    grouped_ablation if frozen["version"] in grouped_ablation.VERSIONS else ablation
                ).config_for(frozen["version"]),
                model_calls=0,
                charged_tokens=0,
                tokens=0,
                calls=[],
                results={},
                elapsed_seconds=0,
            )
            save()
        state["stage"] = "METHOD_ABLATION"
        save()

        def persist(trial, audit_id):
            state["ablation"] = copy.deepcopy(trial)
            state["ablation_audit_id"] = audit_id
            save()

        engine = (
            grouped_ablation
            if state["ablation"]["version"] in grouped_ablation.VERSIONS
            else ablation
        )
        await engine.execute(repo, rules, state["ablation"], settings, persist, port=port)
        result = await asyncio.to_thread(report, repo, state)
        if (
            not all(r["status"] == "COMPLETED" for r in result["rows"])
            or state["ablation"]["status"] != "COMPLETED"
        ):
            raise ValueError("部分登记组未完成，失败与消耗已保留，可在原预算内继续")
        state.update(
            report_ref=repo.blob(result),
            report_sha256=digest(result),
            status="COMPLETED",
            stage="COMPLETED",
        )
        save()
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=str(error)[:240] if isinstance(error, ValueError) else type(error).__name__,
        )
        save()
    return state
