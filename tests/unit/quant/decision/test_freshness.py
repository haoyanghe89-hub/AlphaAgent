from datetime import date, datetime

from marketpulse.quant.decision.capabilities import freshness
from marketpulse.quant.decision.contracts import SourceCapability


def source(**changes):
    data = dict(
        source_id="daily",
        name="fixture",
        url="https://example.com/",
        source_family="fixture",
        acquisition="fixture",
        authentication="none",
        license_note="test only",
        coverage="fixture",
        frequency="daily",
        status="AVAILABLE",
        reason="fixture",
        tested_at="2026-10-12T11:00:00+08:00",
        data_end="2026-10-09",
    )
    data.update(changes)
    return SourceCapability.model_validate(data)


def test_no_verified_publication_schedule_means_unknown_freshness():
    assert freshness(source(), datetime.fromisoformat("2026-10-12T12:00:00+08:00")) == (
        "FRESHNESS_UNKNOWN"
    )


def test_expected_complete_session_is_explicit_and_failed_source_is_not_fresh():
    at = datetime.fromisoformat("2026-10-12T12:00:00+08:00")
    assert freshness(source(), at, date(2026, 10, 9)) == "FRESH"
    assert freshness(source(status="UNAVAILABLE"), at, date(2026, 10, 9)) == ("FRESHNESS_UNKNOWN")
    assert freshness(source(data_end="20261012111156"), at, date(2026, 10, 9)) == (
        "FRESHNESS_UNKNOWN"
    )
