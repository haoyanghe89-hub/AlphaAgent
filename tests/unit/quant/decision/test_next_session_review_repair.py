"""Synthetic role outputs verify correction and fail-closed publication gates."""

import asyncio

import pytest

from marketpulse.quant.decision.next_session import service
from marketpulse.quant.decision.next_session.agents import Proposal, Review


def proposal(summary):
    reason = dict(
        text="窗口方向不同", evidence_ids=["sealed"], next_session_mechanism="若延续则可能影响"
    )
    return Proposal(
        direction="UP",
        summary=summary,
        reasons=[reason],
        opposing_reasons=[reason],
        limitations=["隔夜事件未知"],
        support_level="WEAK",
    )


class Roles:
    def __init__(self, rejects):
        self.rejects, self.contexts, self.calls = rejects, [], []

    async def call(self, role, schema, context, guard=None):
        self.calls.append(role)
        self.contexts.append(context)
        if role == "Analyst":
            value = proposal("修正前" if self.calls.count(role) == 1 else "修正后")
        else:
            good = self.calls.count(role) > self.rejects
            value = Review(
                verdict="APPROVE" if good else "REJECT",
                citations_supported=good,
                next_session_relevant=True,
                no_invented_facts=True,
                reason="区分当日和五日方向",
            )
        if guard:
            guard(value)
        return value, {"response_ref": "synthetic-" + str(len(self.calls))}


def test_rejection_returns_exact_proposal_and_review_for_bounded_repair():
    roles = Roles(1)
    result = asyncio.run(
        service.analyze_verified(roles, {"evidence": [{"evidence_id": "sealed"}]}, repairs=2)
    )
    assert result[0].summary == "修正后"
    assert roles.calls == ["Analyst", "Verifier", "Analyst", "Verifier"]
    repaired = roles.contexts[2]
    assert repaired["previous_proposal"]["summary"] == "修正前"
    assert repaired["review_feedback"]["reason"] == "区分当日和五日方向"
    assert repaired["evidence"] == roles.contexts[0]["evidence"]


def test_repeated_rejections_never_approve_and_stop_at_limit():
    roles = Roles(9)
    with pytest.raises(ValueError, match="语义审核未通过"):
        asyncio.run(
            service.analyze_verified(roles, {"evidence": [{"evidence_id": "sealed"}]}, repairs=2)
        )
    assert roles.calls == ["Analyst", "Verifier"] * 3


def test_registered_fixed_protocol_does_not_add_unregistered_repair_rounds():
    roles = Roles(1)
    with pytest.raises(ValueError):
        asyncio.run(
            service.analyze_verified(roles, {"evidence": [{"evidence_id": "sealed"}]}, repairs=0)
        )
    assert roles.calls == ["Analyst", "Verifier"]
