"""Separate manufacturing applicability; synthetic business inputs only."""

from uuid import uuid4

import pytest

from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.decision.sector_profiles import profile


def test_manufacturing_scope_has_separate_chain_and_missing_inputs():
    request = ResearchRequest(
        request_key=uuid4(),
        scope="industry",
        research_mode="candidates",
        entity="集成电路制造",
        question="合成测试",
    )
    assert request.entity == "集成电路制造"
    manufacturing, equipment = profile(request.entity), profile("半导体设备")
    assert manufacturing["role"] == "MANUFACTURING"
    assert manufacturing["chain"] != equipment["chain"]
    assert any("利用率" in value for value in manufacturing["missing_inputs"])
    with pytest.raises(ValueError):
        ResearchRequest(
            request_key=uuid4(),
            scope="industry",
            research_mode="candidates",
            entity="其他合成行业",
            question="不可自动启用",
        )
