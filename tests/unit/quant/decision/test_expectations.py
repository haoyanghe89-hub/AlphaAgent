from decimal import Decimal

from marketpulse.quant.decision.contracts import ExpectationsInputs
from marketpulse.quant.decision.expectations import evaluate_expectations


def inputs(**changes):
    values = dict(
        benchmark_kind="NONE",
        benchmark_refs=(),
        valuation_inputs=(("price", Decimal("100")),),
        fixed_assumptions=(("discount_rate", Decimal("0.10")), ("growth", Decimal("0"))),
        solve_variable="cash_flow",
        input_evidence_ids=("price-source", "assumption-source"),
    )
    return ExpectationsInputs(**(values | changes))


def test_perpetuity_inverse_and_independent_sensitivity():
    result = evaluate_expectations(inputs())
    assert result.implied_value == Decimal("10")
    assert dict(result.sensitivity)["discount_rate=0.11"] == Decimal("11")
    assert "市场真实预期" in "".join(result.limitations)


def test_missing_inputs_and_false_consensus_cannot_produce_implied_value():
    assert evaluate_expectations(inputs(input_evidence_ids=())).status == "UNAVAILABLE"
    assert evaluate_expectations(inputs(benchmark_kind="CONSENSUS")).status == "UNAVAILABLE"
    assert evaluate_expectations(inputs(valuation_inputs=())).implied_value is None
    assert (
        evaluate_expectations(inputs(fixed_assumptions=(("discount_rate", Decimal("0")),))).status
        == "UNAVAILABLE"
    )


def test_unknown_solver_does_not_invent_a_target_price():
    assert (
        evaluate_expectations(inputs(solve_variable="three_month_target")).status == "UNAVAILABLE"
    )


def test_explicit_historical_baseline_does_not_become_consensus():
    result = evaluate_expectations(
        inputs(
            benchmark_kind="HISTORICAL_TREND",
            benchmark_refs=("archived-same-period",),
            solve_variable=None,
            fixed_assumptions=(),
            valuation_inputs=(("observed_value", Decimal("0")), ("benchmark_value", Decimal("10"))),
        )
    )
    assert result.status == "AVAILABLE" and result.implied_value is None
    assert "低于同口径历史基准" in result.comparison
    assert "差值=-10" in result.comparison
