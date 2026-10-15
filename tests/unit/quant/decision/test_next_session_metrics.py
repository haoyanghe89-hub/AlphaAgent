"""E04/E09/E10: known class imbalance must stay visible."""

from marketpulse.quant.decision.next_session.metrics import statistics


def test_always_up_does_not_beat_imbalance_and_days_are_not_stock_count():
    records = [
        dict(
            prediction="UP",
            actual="UP" if i < 8 else "DOWN",
            base_session="2026-10-12",
            baselines={"ALWAYS_UP": "UP", "ALWAYS_DOWN": "DOWN", "PERSISTENCE": "UP"},
        )
        for i in range(10)
    ]
    result = statistics(records)
    assert result["accuracy"] == dict(numerator=8, denominator=10, value="0.8")
    assert result["balanced_accuracy"] == "0.5"
    assert result["baselines"]["ALWAYS_UP"] == result["accuracy"]
    assert result["independent_dates"] == 1


def test_flat_stays_in_main_denominator_and_zero_samples_are_not_zero_accuracy():
    result = statistics(
        [dict(prediction="UP", actual="FLAT", base_session="2026-10-12", baselines={})]
    )
    assert result["accuracy"] == dict(numerator=0, denominator=1, value="0")
    assert result["nonflat_accuracy"]["value"] is None
    assert result["balanced_accuracy"] is None
    assert statistics([])["accuracy"]["value"] is None
