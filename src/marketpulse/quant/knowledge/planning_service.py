"""Bounded real-data collection and deterministic route/portfolio computation."""

import asyncio
import hashlib
import re
import time
from datetime import datetime
from pathlib import Path

from marketpulse.telemetry.tracing import stage, workflow

from ..contracts import digest
from ..decision.tools import SourceClient
from .features import compute_features
from .inputs import capture_inputs
from .planning_contracts import PlanRequest
from .playbook import allocate_plans
from .routing import CATALOG, RULE_IDS, VERSION, evaluate_routes
from .trading_rules import verify_trading_rules


@stage("deterministic.compute", as_type="span")
def build_report(request, packet, knowledge, trading_rules):
    request = PlanRequest.model_validate(request).model_dump(mode="json")
    features = compute_features(packet, strict=False)
    candidates = [evaluate_routes(f, knowledge, request["horizon"]) for f in features["stocks"]]
    holdings_review = []
    for h in (request["profile"] or {}).get("holdings", []):
        candidate = next(
            (
                c
                for c in candidates
                if c["security_id"] == h["security_id"] and c["status"] == "COMPUTED"
            ),
            None,
        )
        if candidate and candidate["industry_code"] != h["industry_code"]:
            raise ValueError("已声明持仓行业与当前申万一级行业不符，请核对画像。")
        route = (
            next((r for r in candidate["routes"] if r["route_id"] == h["route_id"]), None)
            if candidate
            else None
        )
        holdings_review.append(
            dict(
                **h,
                action="按原路线复核，不能切换路线掩盖失效"
                if route
                else "未纳入本次行情研究，仅计入已有暴露",
                invalidation=route["invalidation"] if route else None,
            )
        )
    return dict(
        schema_version="investment-plan-result-v1",
        request=request,
        input_digest=digest(packet),
        market_date=packet["market_date"],
        captured_at=packet["captured_at"],
        features=features,
        candidates=candidates,
        portfolio=allocate_plans(candidates, request["profile"]),
        trading_rules=trading_rules,
        sources=[s["source"] for s in packet["sources"]],
        knowledge=knowledge,
        route_version=VERSION,
        route_catalog=CATALOG,
        validation="RESEARCH_CONDITIONS_UNVALIDATED",
        holdings_review=holdings_review,
        conclusion="按条件等待，保留现金"
        if all(c.get("action") != "条件已满足，先核对风险预算" for c in candidates)
        else "部分股票满足研究条件，按组合预算逐项复核",
        caveats=[
            "这是日频条件计划，行情日期见页面；盘中价格改变后须重新试算。",
            "本计划评估价格、增长和资金条件；政策影响与完整估值仍需进一步核查。",
        ],
    )


def execute(repo, rule_store, run_id, stop, *, collector=capture_inputs):
    state = repo.get(run_id, with_data=False)
    start = time.monotonic()

    def save():
        state["elapsed_seconds"] = time.monotonic() - start + elapsed
        repo.save(state)

    elapsed = state.get("elapsed_seconds", 0)
    try:
        state.update(status="RUNNING", error=None)
        state.setdefault(
            "implementation",
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path(__file__).parent.glob("*.py"))
            },
        )
        save()
        if state.get("inputs_ref"):
            packet = repo.value(state["inputs_ref"])
        else:
            client = SourceClient(
                repo, state, save, stop, max_requests=80, budget_profile=dict(max_seconds=900)
            )
            packet = collector(
                client, state["request"]["entities"], datetime.fromisoformat(state["created_at"])
            )
            state["inputs_ref"] = repo.blob(packet)
            # Root all provider descriptors and nested refs for shared GC and paired backup.
            state["source_refs"] = sorted(
                set(re.findall(r"blob://sha256/[0-9a-f]{64}", str(packet)))
            )
            save()
        current = rule_store.current(datetime.fromisoformat(state["created_at"]))
        knowledge = [
            dict(
                rule_id=r["rule_id"],
                version=r["version"],
                title=r["rule"]["title"],
                source_families=r["source_families"],
                execution_status=r["rule"]["execution_status"],
            )
            for r in current
            if r["rule_id"] in RULE_IDS
        ]
        if {r["rule_id"] for r in knowledge} != set(RULE_IDS):
            raise ValueError("required published method versions unavailable")
        trading = verify_trading_rules(packet, repo.raw_bytes)
        report = build_report(state["request"], packet, knowledge, trading)
        state["features_ref"] = repo.blob(report["features"])
        ref = repo.blob(report)
        state.update(status="COMPLETED", result_ref=ref, result_sha256=digest(report))
        save()
    except Exception as error:
        state.update(
            status="INTERRUPTED",
            error=(
                str(error)[:240]
                if isinstance(error, ValueError)
                else "数据读取或计算失败，调用材料已保留，可重试。"
            ),
        )
        save()
    return repo.get(run_id)


@workflow("alphaagent.investment_plan", id_parameter="run_id")
async def run(repo, rules, run_id, stop, **kwargs):
    work = asyncio.create_task(asyncio.to_thread(execute, repo, rules, run_id, stop, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        stop.set()
        await work
        raise
