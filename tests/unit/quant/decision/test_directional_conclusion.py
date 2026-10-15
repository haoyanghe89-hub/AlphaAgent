"""Required choices remain grounded and separately reviewed; legacy output still parses."""

import pytest
from pydantic import ValidationError

from marketpulse.quant.decision import agents
from tests.unit.quant.decision.test_claims import analysis


def directional(**changes):
    return agents.DirectionalAnalysis.model_validate(
        analysis().model_dump(mode="json")
        | dict(
            outlook="UNFAVORABLE",
            summary="倾向看空，利润压力暂时更值得重视。",
            direction_reason="虽然存在经营恢复机会，当前利润压力仍更值得重视。",
            direction_claim_ids=["C1"],
        )
        | changes
    )


@pytest.mark.parametrize("outlook", ["UNDETERMINED", "MIXED"])
def test_new_direction_schema_cannot_abstain(outlook):
    with pytest.raises(ValidationError):
        directional(outlook=outlook)
    # The old schema remains available to replay immutable historical judgments.
    assert analysis().outlook == "UNDETERMINED"


@pytest.mark.parametrize(
    "changes",
    [
        dict(summary="倾向看多，利润压力暂时更值得重视。"),
        dict(summary="倾向看空，但目前无法判断方向。"),
        dict(direction_claim_ids=["FORGED"]),
        dict(direction_claim_ids=["C1", "C1"]),
    ],
)
def test_direction_needs_consistent_summary_and_bound_reason(changes):
    context = dict(subject=dict(security_id="688012.SH"), evidence=[dict(evidence_id="E1")])
    agents.guard_analysis(directional(), context)
    with pytest.raises(ValueError, match="结论必须明确择边"):
        agents.guard_analysis(directional(**changes), context)


def test_generic_claim_approval_is_not_an_approval_of_direction():
    proposal = directional()
    review = agents.Review(
        decisions=(agents.Decision(claim_id="C1", verdict="APPROVE", reason="原文支持事实"),),
        scenarios_supported=True,
        opinion_supported=True,
    )
    with pytest.raises(ValueError, match="单独复核"):
        agents.guard_review(review, proposal)


@pytest.mark.parametrize(
    "text",
    [
        "研发投入同比增加约五成半。",
        "营业收入增长三成半。",
        "利润增长一倍。",
        "增幅约百分之三十。",
        "收入约三点五亿元。",
        "研发增加五点五亿元。",
        "现金回收过半。",
    ],
)
def test_chinese_financial_quantities_cannot_bypass_qualitative_text_guard(text):
    context = dict(subject=dict(security_id="688012.SH"), evidence=[dict(evidence_id="E1")])
    candidate = directional(direction_reason=text)
    agents.guard_analysis(
        candidate, context
    )  # Earlier sealed versions keep their replay semantics.
    with pytest.raises(ValueError, match="中文数量"):
        agents.guard_analysis(candidate, context | dict(qualitative_only=True))
