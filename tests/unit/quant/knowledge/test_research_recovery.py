import asyncio
import copy
from threading import Event

import pytest

from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge import research_service as service


def state():
    return dict(
        status="INTERRUPTED",
        stage="ANALYST",
        error="Analyst 输出未通过契约检查",
        charged_tokens=10000,
        model_calls=3,
        config=dict(budget=dict(max_tokens=60000, max_model_calls=24)),
    )


def test_standalone_recovery_reuses_task_and_accumulated_budget(monkeypatch):
    current, calls = state(), []

    class Repo:
        def save(self, value):
            current.update(copy.deepcopy(value))

    async def run(repo, rules, rid, stop, settings, **options):
        calls.append((rid, current["charged_tokens"], current.get("automatic_continuations", 0)))
        current["charged_tokens"] += 1000
        if len(calls) == 2:
            current["status"] = "COMPLETED"
        return copy.deepcopy(current)

    monkeypatch.setattr(service, "run", run)
    result = asyncio.run(service.run_with_recovery(Repo(), None, "same-task", Event(), None))
    assert result["status"] == "COMPLETED"
    assert calls == [("same-task", 10000, 0), ("same-task", 11000, 1)]
    assert result["charged_tokens"] == 12000


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({}, 3),
        ({"charged_tokens": 50000}, 1),
        ({"error": "原文校验失败"}, 1),
        ({"stage": "INPUTS"}, 1),
    ],
)
def test_recovery_is_bounded_and_never_retries_source_integrity_failures(
    monkeypatch, changes, expected
):
    current, calls = dict(state(), **changes), []

    class Repo:
        def save(self, value):
            current.update(copy.deepcopy(value))

    async def run(*args, **options):
        calls.append(True)
        return copy.deepcopy(current)

    monkeypatch.setattr(service, "run", run)
    result = asyncio.run(service.run_with_recovery(Repo(), None, "same-task", Event(), None))
    assert result["status"] == "INTERRUPTED" and len(calls) == expected


def test_citation_feedback_identifies_stock_and_exact_required_ids():
    context = dict(
        protocol="public-investment-context-v3",
        stocks=[dict(security_id="000333.SZ")],
        methods=[dict(rule_id="R1")],
        evidence=[
            dict(evidence_id="F-one", security_id="000333.SZ"),
            dict(evidence_id="T-000333.SZ-macd", security_id="000333.SZ", kind="TECHNICAL"),
        ],
    )
    claim = dict(claim_id="C1", text="仅为合成测试", evidence_ids=["F-one"])
    opinion = dict(
        security_id="000333.SZ",
        direction="FAVORABLE",
        summary="倾向看多，仅为测试",
        claims=[claim, dict(claim, claim_id="C2")],
        opposing=[dict(claim, claim_id="C3")],
        direction_claim_ids=["C1"],
        method_rule_ids=["R1"],
        invalidation=["条件不成立时复核"],
        next_checks=["核对资料"],
    )
    with pytest.raises(ValueError, match=r"000333.SZ.*T-000333.SZ-macd"):
        agents.guard_analysis(agents.Analysis(opinions=[opinion]), context)
    opinion["claims"][0]["evidence_ids"] = ["X-000333SZ-misspelled"]
    with pytest.raises(ValueError, match=r"000333.SZ C1.*X-000333SZ-misspelled"):
        agents.guard_analysis(agents.Analysis(opinions=[opinion]), context)
