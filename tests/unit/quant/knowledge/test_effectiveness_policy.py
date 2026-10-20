from uuid import uuid4

import pytest

from marketpulse.quant.knowledge.research_contracts import InvestmentRequest
from marketpulse.quant.knowledge.structured_contract import configure, verify_config


def test_component_requires_explicit_enhanced_structured_research():
    with pytest.raises(ValueError):
        InvestmentRequest(request_key=uuid4(), entities=["000333"], enhancement_stage="METHODS")


def test_component_request_never_leaks_into_calculation_or_legacy_payload():
    r = InvestmentRequest(request_key=uuid4(), entities=["000333"])
    assert "enhancement_stage" not in r.model_dump(mode="json")
    enhanced = r.model_copy(
        update=dict(
            enhanced_research=True,
            execution_contract="investment-structured-v2",
            enhancement_stage="METHODS",
        )
    )
    assert "enhancement_stage" not in enhanced.calculation_request()


@pytest.mark.parametrize("stage", ["METHODS", "TOOLS"])
def test_component_reasoning_is_disabled_and_cannot_be_switched_after_registration(stage):
    config = {"enhancement_stage": stage}
    configure(config, True)
    assert config["reasoning_profile"]["roles"]["Researcher"]["thinking_enabled"] is False
    verify_config(config, True)
    config["reasoning_profile"]["roles"]["Researcher"]["thinking_enabled"] = True
    with pytest.raises(ValueError):
        verify_config(config, True)
