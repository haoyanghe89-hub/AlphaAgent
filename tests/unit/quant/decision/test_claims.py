import pytest

from marketpulse.quant.decision.agents import Analysis, guard_analysis


def analysis():
    return Analysis.model_validate(
        dict(
            claims=[
                dict(
                    claim_id="C1",
                    claim_type="hypothesis",
                    text="原材料上涨可能拖累利润。",
                    subject_ids=["688012.SH"],
                    horizon="MEDIUM_TERM",
                    support_ids=["E1"],
                    mechanism="待补查成本原因。",
                    support_level="WEAK",
                )
            ],
            gaps=[
                dict(
                    gap_id="G1",
                    claim_id="C1",
                    need="利润变化原因",
                    materiality="HIGH",
                    allowed_tool="disclosures",
                    arguments_json='{"query":"利润 产品结构 成本"}',
                    expected_benefit="区分成本与产品结构解释。",
                )
            ],
            outlook="UNDETERMINED",
            stance="WAIT_FOR_CONFIRMATION",
            summary="利润变化原因仍需核查。",
            opposing_reasons=["经营资料不足。"],
            catalysts=["后续经营披露"],
            invalidation_conditions=["补证否定原原因"],
            next_checks=["查阅经营讨论"],
            scenarios=[],
        )
    )


def test_claim_subject_citation_numbers_and_arbitrary_tool_are_rejected():
    proposal = analysis()
    context = dict(subject=dict(security_id="688012.SH"), evidence=[dict(evidence_id="E1")])
    guard_analysis(proposal, context)
    for change in [
        dict(support_ids=("FORGED",)),
        dict(subject_ids=("002371.SZ",)),
        dict(text="利润下降了99%。"),
        dict(verification_status="APPROVE"),
    ]:
        modified = proposal.model_copy(
            update={"claims": (proposal.claims[0].model_copy(update=change),)}
        )
        with pytest.raises(ValueError):
            guard_analysis(modified, context)
