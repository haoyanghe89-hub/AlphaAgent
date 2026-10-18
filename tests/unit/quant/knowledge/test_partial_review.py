import pytest

from marketpulse.quant.knowledge.review import validated_items


def response():
    return dict(
        rule_id="KR-SYNTHETIC",
        version="a" * 64,
        verdict="APPROVE",
        supported=True,
        parameters_supported=True,
        conditions_complete=True,
        counterexamples_useful=True,
        reason="这是合成模型分项契约测试，未调用真实服务。",
    )


def test_failed_batch_keeps_valid_items_but_never_strips_extra_fields():
    good = response()
    bad = dict(response(), rule_id="KR-INVALID", version_note="")
    request = dict(
        context=dict(
            rules=[dict(rule=dict(rule_id=r["rule_id"]), version=r["version"]) for r in (good, bad)]
        )
    )
    call = dict(
        status="FAILED", error="InvalidProviderResponseError", raw_model_response_ref="TEST"
    )
    assert [
        r.rule_id for r in validated_items(call, request, lambda _: dict(reviews=[good, bad]))
    ] == [good["rule_id"]]
    assert call["status"] == "FAILED"
    with pytest.raises(ValueError, match="REVIEW_COVERAGE_MISMATCH"):
        validated_items(call, request, lambda _: dict(reviews=[good, good]))


def test_transport_failure_without_response_cannot_supply_any_review():
    assert (
        validated_items(dict(status="FAILED", error="ProviderCallError"), {}, lambda _: None) == []
    )
