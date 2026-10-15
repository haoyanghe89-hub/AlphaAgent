"""Verified source reuse and safe progress messages; never a synthetic opinion."""

import copy
from datetime import UTC, datetime

SOURCE_CHECKPOINTS = (
    "subject",
    "evidence_ref",
    "evidence_sha256",
    "sealed_at",
    "observation_calendar_ref",
    "knowledge_snapshot_ref",
    "policy_snapshot_ref",
    "industry_snapshot_ref",
    "flow_snapshot_ref",
    "candidate_snapshot_ref",
    "market_snapshot_ref",
)


def prepare_retry(repo, previous, request_key, implementation):
    definition = dict(previous["request"], request_key=str(request_key))
    if previous.get("evidence_ref"):
        data = repo.value(previous["evidence_ref"])
        repo.verify_data(data, previous["evidence_sha256"], previous["subject"]["security_id"])
    for key in SOURCE_CHECKPOINTS[4:]:
        if previous.get(key):
            repo.value(previous[key])
    state, fresh = repo.create(definition, implementation)
    if not fresh:
        if state.get("recovery_of") != previous["run_id"]:
            raise ValueError("重试标识已绑定其他任务")
        return state, False
    state.update(
        recovery_of=previous["run_id"],
        recovery_created_at=datetime.now(UTC).isoformat(),
        title=previous.get("subject", {}).get("name", definition["entity"]) + " · 继续研究",
    )
    for key in SOURCE_CHECKPOINTS:
        if key in previous:
            state[key] = copy.deepcopy(previous[key])
    if any(previous.get(k) for k in SOURCE_CHECKPOINTS if k.endswith("_ref")):
        state["calls"] = [copy.deepcopy(c) for c in previous["calls"] if c.get("kind") != "model"]
    repo.save(state)
    return state, True


def failure(state):
    calls = [
        c for c in state.get("calls", []) if c.get("kind") == "model" and c["status"] == "FAILED"
    ]
    last = calls[-1] if calls else {}
    diagnostics = last.get("diagnostics", {})
    provider = last.get("error") in {"ProviderCallError", "TimeoutError", "RateLimitedError"}
    if provider:
        return dict(
            code="MODEL_UNAVAILABLE",
            retryable=True,
            message="分析服务暂时无法响应，已取得的公司资料和行情完整保留。",
            detail="点击继续研究会复用资料，另存新的研究记录，不覆盖本次记录。",
            diagnostics=diagnostics,
        )
    if "预算" in state.get("error", "") or "上限" in state.get("error", ""):
        return dict(
            code="BUDGET_EXHAUSTED",
            retryable=True,
            message="本次分析已达到执行上限，已取得的资料保留。",
            detail="可复用资料另存研究，不会覆盖原记录或放宽复核标准。",
        )
    return dict(
        code="REVIEW_INCOMPLETE",
        retryable=True,
        message="本次分析未完成事实复核，已取得的公司资料保留。",
        detail="继续研究会重新复核理由和反证，原记录保留。",
    )


def present(repo, state):
    if state["status"] == "COMPLETED":
        return state
    result = copy.deepcopy(state)
    if state["status"] in {"FAILED", "INTERRUPTED"}:
        result["failure"] = failure(state)
    if state.get("evidence_ref"):
        data = repo.value(state["evidence_ref"])
        repo.verify_data(data, state["evidence_sha256"], state["subject"]["security_id"])
        result["available_materials"] = dict(
            name=state["subject"]["name"],
            sealed_at=state.get("sealed_at"),
            disclosures=[
                dict(title=d["title"], published_at=d.get("published_at"))
                for d in data["documents"][:3]
            ],
            facts=[
                {k: e.get(k) for k in ("text", "value", "unit", "observed_at")}
                for e in sorted(
                    (e for e in data["bundle"]["items"] if e["kind"] == "fact"),
                    key=lambda e: e.get("observed_at") or "",
                    reverse=True,
                )[:3]
            ],
        )
    return result
