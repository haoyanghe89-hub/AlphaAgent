import copy
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from marketpulse.quant.knowledge import paired_evaluation as e


def test_cost_includes_measurement_and_known_failed_usage_and_unknown_reservation():
    calls = [
        dict(
            kind="model",
            model="deepseek-flash",
            input_tokens=1000,
            output_tokens=1,
            status="MEASURED",
            purpose="input_count",
        ),
        dict(
            kind="model",
            model="deepseek-flash",
            input_tokens=2000,
            output_tokens=200,
            status="FAILED",
        ),
        dict(kind="model", model="deepseek-flash", status="FAILED", reserved_tokens=100),
    ]
    result = e.usage(dict(calls=calls, charged_tokens=3301))
    assert result["model_calls"] == 3
    assert (result["input_tokens"], result["output_tokens"]) == (3000, 201)
    assert result["unknown_usage_calls"] == 1
    assert result["input_count_calls"] == 1
    assert result["failed_calls"] == 2
    assert Decimal(result["cost"]["lower_cny"]) == Decimal(".000864")
    assert Decimal(result["cost"]["upper_cny"]) == Decimal(".008408")


def test_unknown_model_price_never_looks_free():
    result = e.usage(
        dict(calls=[dict(kind="model", model="unknown", input_tokens=1, output_tokens=1)])
    )
    assert result["cost"]["upper_cny"] is None
    assert result["unknown_model_calls"] == 1


def test_matches_every_shared_source_context():
    a = dict(inputs_ref="a", context_snapshot_ref="b", context_report_ref="c")
    assert e.matched_inputs([a, a])
    for key in a:
        assert not e.matched_inputs([a, dict(a, **{key: "different"})])
    assert not e.matched_inputs([{}, {}])


def rows():
    return [
        dict(
            case_id=c["case_id"],
            arm=arm,
            matched_inputs=True,
            prediction=dict(stocks=[dict(security_id=sid, status="PENDING") for sid in e.ENTITIES]),
        )
        for c in e.CASES
        for arm in c["order"]
    ]


def test_pending_failures_and_flat_do_not_become_accuracy_or_drop_registration():
    data = rows()
    assert e.paired_outcomes(data)["accuracy_delta"] is None
    a, b = data[:2]
    a["prediction"]["stocks"][0].update(
        status="MATURED",
        actual_direction="UP",
        target_date="20261113",
        correct=True,
        baseline_correct=dict(ALWAYS_UP=True),
    )
    result = e.paired_outcomes(data)
    assert result["registered_pairs"] == 4 and result["paired_matured"] == 0
    b["prediction"]["stocks"][0] = copy.deepcopy(a["prediction"]["stocks"][0])
    b["prediction"]["stocks"][0]["correct"] = False
    result = e.paired_outcomes(data)
    assert result["paired_matured"] == 1 and result["accuracy_delta"] == -1
    b["prediction"]["stocks"][0]["target_date"] = "20261116"
    assert e.paired_outcomes(data)["paired_matured"] == 0
    b["prediction"]["stocks"][0]["target_date"] = "20261113"
    a["prediction"]["stocks"][0]["actual_direction"] = "FLAT"
    assert e.paired_outcomes(data)["paired_matured"] == 0


def test_request_rejects_private_inputs_and_caller_supplied_outcomes():
    with pytest.raises(ValidationError):
        e.EvaluationRequest(
            request_key="11111111-1111-1111-1111-111111111111", profile={"cash": 100}
        )
    with pytest.raises(ValidationError):
        e.EvaluationRequest(request_key="11111111-1111-1111-1111-111111111111", correct=True)


def test_preregistered_order_unchanged_ablation_v1():
    assert e.CASES[0]["order"] == list(reversed(e.CASES[1]["order"]))
    assert e.ablation.VERSION == "investment-method-ablation-v1"
    assert e.ablation.ARMS == ("NO_METHOD", "FIXED_TREND", "ADAPTIVE_ROUTE")


def test_historical_archive_deduplicates_before_reading_outcomes(monkeypatch):
    from marketpulse.quant.decision.next_session import store

    forecasts = [
        SimpleNamespace(
            prediction_id=f"p{i}",
            security_id="000333.SZ",
            issued_at=datetime(2026, 10, 12, 10 + i, tzinfo=UTC),
            base_session=date(2026, 10, 12),
            target_session=date(2026, 10, 13),
            prediction="UP",
            baselines=(("ALWAYS_UP", "UP"),),
        )
        for i in range(3)
    ]

    class Archive:
        def __init__(self, *a, **kw):
            pass

        def records(self, prefix):
            return [dict(prediction_ids=["p0", "p1", "p2"])]

        def predictions(self):
            return forecasts

        def outcome_versions(self, pid):
            assert pid == "p0"  # Later duplicates never select a more favorable score.
            return [dict(status="SCORABLE", return_direction="DOWN")]

    monkeypatch.setattr(store, "ExperimentStore", Archive)
    result = e.next_session_history(SimpleNamespace(base=None), "2026-10-12T11:30:00+00:00")
    original, enhanced = result["groups"]
    assert original["registered"] == 1
    assert original["accuracy"] == dict(numerator=0, denominator=1, value="0")
    assert original["independent_dates"] == 1
    assert enhanced["accuracy"]["value"] is None
    assert result["scope"] == "OBSERVATIONAL_ARCHIVE_NOT_MATCHED"


def test_overlapping_horizons_do_not_create_independent_origin_dates():
    data = rows()
    for row in data:
        row["registration_date"] = "20261019"
        for stock in row["prediction"]["stocks"]:
            stock.update(
                status="MATURED",
                actual_direction="UP",
                correct=True,
                target_date="20261116" if row["case_id"] == "SWING20" else "20270112",
                baseline_correct=dict(ALWAYS_UP=True),
            )
    result = e.paired_outcomes(data)
    assert result["independent_dates"] == 1
    assert result["unique_target_dates"] == 2
    assert result["paired_matured"] == 4


def test_failed_studies_stay_in_completion_denominator_and_cost():
    data = rows()
    for index, row in enumerate(data):
        row.update(
            status="COMPLETED" if index == 0 else "INTERRUPTED",
            model_calls=3,
            input_tokens=1000,
            output_tokens=2000,
            elapsed_seconds=10,
            cost=dict(lower_cny=".01", upper_cny=".02"),
        )
    result = e.round_summary(data, dict(report=dict(completed=3)))
    assert result["verdict"] == "COMPLETE_WITH_FAILURES"
    assert (
        result["declared_studies"],
        result["completed_studies"],
        result["unsuccessful_studies"],
    ) == (4, 1, 3)
    original, enhanced = result["arms"]
    assert (original["completed"], enhanced["completed"]) == (1, 0)
    assert original["model_calls"] == enhanced["model_calls"] == 6
    assert enhanced["cost"]["upper_cny"] == "0.04"


def test_sealed_failed_child_cannot_improve_engineering_scores_later(monkeypatch):
    data = rows()
    for row in data:
        row.update(status="INTERRUPTED", model_calls=3, cost=dict(lower_cny=".01", upper_cny=".02"))
    frozen = dict(
        rows=data, prediction=e.paired_outcomes(data), round_summary={"completed_studies": 0}
    )
    values = dict(manifest=dict(registered_at="2026-10-19T01:00:00+00:00"), report=frozen)

    class Store:
        def __init__(self, base):
            pass

        def get(self, rid):
            raise AssertionError("Must not import a later resumed child's improved scores")

    monkeypatch.setattr(e, "InvestmentStore", Store)
    monkeypatch.setattr(e, "next_session_history", lambda *_: {})
    result = e.report(
        SimpleNamespace(base=None, value=values.__getitem__),
        dict(manifest_ref="manifest", report_ref="report", status="COMPLETED"),
    )
    assert result["round_summary"]["completed_studies"] == 0
    assert all(r["model_calls"] == 3 and r["status"] == "INTERRUPTED" for r in result["rows"])
