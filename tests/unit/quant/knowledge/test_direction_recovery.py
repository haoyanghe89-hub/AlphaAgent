# ruff: noqa: F811
import asyncio
from threading import Event
from uuid import uuid4

import pytest

from marketpulse.quant.knowledge import research_agents as agents
from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.research_service import run

from .test_research import SyntheticPort, runtime  # noqa: F401


class LegacyRejection(SyntheticPort):
    async def generate(self, request):
        if request.response_model is agents.Analysis and "Verifier" in self.roles:
            raise ValueError("SYNTHETIC restart before another analysis")
        response = await super().generate(request)
        if request.response_model is agents.Review:
            item = response.output.model_dump(mode="json")
            for r in item["reviews"]:
                r.update(
                    verdict="REJECT",
                    direction_supported=False,
                    reason="仅因入场尚未触发而拒绝方向。",
                )
            response.output = agents.Review.model_validate(item)
        return response


def test_legacy_direction_only_rejection_recovers_same_analysis_and_budget(runtime):
    repo, packet, settings = runtime
    state, _ = repo.create(
        InvestmentRequest(request_key=uuid4(), entities=["000333"], horizon="MEDIUM").model_dump(
            mode="json"
        )
    )
    first = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=LegacyRejection(),
            collector=lambda *_: packet,
        )
    )
    assert first["status"] == "INTERRUPTED" and first["review_round"] == 1
    old_analysis = first["model_steps"]["Analyst-0"].copy()
    old_review = first["model_steps"]["Verifier-0"].copy()
    used, count = first["charged_tokens"], first["model_calls"]
    repaired = SyntheticPort()
    result = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=repaired,
            collector=lambda *_: pytest.fail("no refetch"),
        )
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert repaired.roles == ["Verifier", "Writer"]
    assert result["model_steps"]["Analyst-0"] == old_analysis
    assert result["model_steps"]["Verifier-0"] == old_review
    assert result["approved_analysis_step"] == "Analyst-0"
    assert result["approved_review_step"] != "Verifier-0"
    assert result["charged_tokens"] == used + 400
    assert result["model_calls"] == count + 2
    assert repo.get(result["run_id"])["result_sha256"] == result["result_sha256"]


def test_new_review_distinguishes_direction_from_entry_and_repair_keeps_previous_proposal(runtime):
    repo, packet, settings = runtime
    state, _ = repo.create(
        InvestmentRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    )
    # Fresh technical research uses the corrected, separately pinned role protocol.
    assert "入场" in agents.prompts(agents.DIRECTION_PROMPT_VERSION)["Verifier"]
    state["config"]["prompt_version"] = agents.DIRECTION_PROMPT_VERSION
    repo.save(state)

    class Reconsider(SyntheticPort):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is agents.Review and self.roles.count("Verifier") == 1:
                value = response.output.model_dump(mode="json")
                for item in value["reviews"]:
                    item.update(
                        verdict="REJECT", direction_supported=False, reason="需重新权衡支持和反证。"
                    )
                response.output = agents.Review.model_validate(value)
            if request.response_model is agents.Analysis and self.roles.count("Analyst") > 1:
                context = self.contexts[-1]
                assert context["previous_analysis"]["opinions"][0]["security_id"] == "000333.SZ"
                assert context["review_feedback"][0]["direction_supported"] is False
            return response

    port = Reconsider()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["approved_analysis_step"] == "Analyst-1"


@pytest.mark.parametrize("blocked", ["fact", "conditions"])
def test_recovery_never_skips_rejected_facts_or_trade_conditions(runtime, blocked):
    repo, packet, settings = runtime
    state, _ = repo.create(
        InvestmentRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    )

    class RealRejection(LegacyRejection):
        async def generate(self, request):
            response = await super().generate(request)
            if request.response_model is agents.Review:
                value = response.output.model_dump(mode="json")
                for item in value["reviews"]:
                    if blocked == "fact":
                        item["decisions"][0]["verdict"] = "REJECT"
                    else:
                        item["conditions_respected"] = False
                response.output = agents.Review.model_validate(value)
            return response

    first = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=RealRejection(),
            collector=lambda *_: packet,
        )
    )
    assert first["status"] == "INTERRUPTED"
    port = SyntheticPort()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.roles == ["Analyst", "Verifier", "Writer"]
    assert "direction_review_recovery" not in result


def test_writer_failure_after_recovered_review_resumes_only_writer(runtime):
    repo, packet, settings = runtime
    state, _ = repo.create(
        InvestmentRequest(request_key=uuid4(), entities=["000333"]).model_dump(mode="json")
    )
    asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=LegacyRejection(),
            collector=lambda *_: packet,
        )
    )
    interrupted = asyncio.run(
        run(
            repo,
            None,
            state["run_id"],
            Event(),
            settings,
            port=SyntheticPort(fail_writer=True),
            collector=lambda *_: packet,
        )
    )
    assert interrupted["status"] == "INTERRUPTED"
    assert interrupted["approved_review_step"].endswith("-direction-check")
    port = SyntheticPort()
    result = asyncio.run(
        run(repo, None, state["run_id"], Event(), settings, port=port, collector=lambda *_: packet)
    )
    assert result["status"] == "COMPLETED", result.get("error")
    assert port.roles == ["Writer"]


@pytest.mark.parametrize("failed", [None, "fact", "direction", "conditions"])
def test_overall_review_is_derived_from_all_checks_without_model_overall(failed):
    item = dict(
        security_id="S",
        decisions=[
            dict(claim_id="C" + str(i), verdict="APPROVE", reason="合成事实核查通过。")
            for i in range(3)
        ],
        direction_supported=True,
        conditions_respected=True,
        reason="合成复核检查依据。",
    )
    if failed == "fact":
        item["decisions"][0]["verdict"] = "REJECT"
    if failed == "direction":
        item["direction_supported"] = False
    if failed == "conditions":
        item["conditions_respected"] = False
    raw = agents.ReviewChecks.model_validate(dict(reviews=[item]))
    assert agents.finalize_review(raw).reviews[0].verdict == ("REJECT" if failed else "APPROVE")
    with pytest.raises(ValueError):
        agents.ReviewChecks.model_validate(dict(reviews=[dict(item, verdict="APPROVE")]))


def test_direction_check_keeps_all_cited_numeric_evidence_and_cannot_approve_failed_fact():
    from marketpulse.quant.knowledge.research_context import direction_check_context

    review = agents.Review.model_validate(
        dict(
            reviews=[
                dict(
                    security_id="S",
                    verdict="REJECT",
                    decisions=[
                        dict(claim_id="C" + str(i), verdict="APPROVE", reason="合成事实核查通过。")
                        for i in range(3)
                    ],
                    direction_supported=False,
                    conditions_respected=True,
                    reason="合成方向需要再核查。",
                )
            ]
        )
    )
    context = dict(
        evidence=[dict(evidence_id="F-S-close", value="42.45", unit="CNY/share", window="W1")],
        windows=dict(W1=dict(start="20261016", end="20261016")),
        stocks=[dict(security_id="S", financial_scope="FINANCIAL_NOT_APPLICABLE")],
        proposal=dict(
            opinions=[
                dict(
                    security_id="S",
                    claims=[],
                    opposing=[],
                    invalidation=["旧条件"],
                    next_checks=["旧核查"],
                )
            ]
        ),
    )
    compact = direction_check_context(context, review)
    assert compact["evidence"] == context["evidence"] and compact["windows"] == context["windows"]
    unsupported = agents.DirectionCheck.model_validate(
        dict(
            reviews=[
                dict(security_id="S", decision="UNSUPPORTED", reason="合成测试仍不足以支持该方向。")
            ]
        )
    )
    assert agents.combine_direction_check(unsupported, review).reviews[0].verdict == "REJECT"
    changed = review.model_dump(mode="json")
    changed["reviews"][0]["decisions"][0]["verdict"] = "REJECT"
    with pytest.raises(ValueError, match="未获准事实"):
        agents.combine_direction_check(unsupported, agents.Review.model_validate(changed))
