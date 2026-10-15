import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from marketpulse.quant.decision.next_session.contracts import (
    ExperimentConfig,
    NextSessionOutcome,
    NextSessionRunRequest,
)

ROOT = Path(__file__).resolve().parents[4]


def test_experiment_defaults_are_disabled_and_not_retrospectively_activated():
    config = ExperimentConfig.model_validate_json(
        (ROOT / "src/marketpulse/quant/decision/next_session/experiment_config.json").read_text()
    )
    assert not config.enabled and config.first_base_session is None
    assert config.securities == ("688012.SH", "002371.SZ")
    assert config.protocol_id == "NEXT_SESSION_EOD_V1"
    assert config.window_start.isoformat() == "20:00:00"
    changed = config.model_dump(mode="json")
    changed["window_start"] = "19:00:00"
    with pytest.raises(ValidationError, match="new protocol"):
        ExperimentConfig.model_validate(changed)


@pytest.mark.parametrize("extra", [{"prediction": "UP"}, {"correct": True}, {"cohort": "PRIMARY"}])
def test_client_cannot_upload_direction_actual_or_cohort(extra):
    with pytest.raises(ValidationError):
        NextSessionRunRequest(request_key=uuid4(), experiment_id="daily-v1", **extra)


def test_pending_outcome_has_no_scored_direction():
    with pytest.raises(ValidationError):
        NextSessionOutcome(
            outcome_id="o",
            version=1,
            prediction_id="p",
            observed_at="2026-10-12T20:00:00+08:00",
            status="PENDING",
            return_direction="UP",
            correct=True,
            limitations=(),
        )


def test_ex_dividend_reference_label_and_flat_are_exact():
    base = dict(
        outcome_id="o",
        version=1,
        prediction_id="p",
        observed_at="2026-10-13T16:00:00+08:00",
        status="SCORABLE",
        close="99.5",
        reference_price="99",
        return_direction="UP",
        correct=True,
        price_source_refs=("source-a", "source-b"),
        source_hashes=("a" * 64, "b" * 64),
        limitations=(),
    )
    assert NextSessionOutcome(**base).return_direction == "UP"
    base.update(close="99", return_direction="FLAT")
    with pytest.raises(ValidationError, match="FLAT"):
        NextSessionOutcome(**base)
    base["correct"] = False
    assert NextSessionOutcome(**base).correct is False


def test_registered_e_case_fixtures_are_parseable_and_not_real_accuracy():
    packet = json.loads((ROOT / "tests/fixtures/quant/decision/cases.json").read_text("utf8"))
    for case in packet:
        assert isinstance(json.loads(case["fixture_json"]), dict)
