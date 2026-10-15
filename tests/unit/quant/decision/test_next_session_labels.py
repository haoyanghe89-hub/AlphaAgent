"""E01/E02/E05/E06/E12: synthetic dates and prices, never real outcomes."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from marketpulse.quant.decision.next_session.labels import (
    classify_outcome,
    cohort_for,
    next_session,
)


def test_next_session_skips_weekend_and_verified_holiday():
    sessions = (date(2026, 9, 24), date(2026, 9, 28), date(2026, 9, 29))
    assert next_session(sessions, date(2026, 9, 24)) == date(2026, 9, 28)
    with pytest.raises(ValueError):
        next_session(sessions, date(2026, 9, 29))


def test_primary_window_cannot_be_backfilled_or_selected_after_open():
    base, target = date(2026, 10, 12), date(2026, 10, 13)
    assert (
        cohort_for(datetime.fromisoformat("2026-10-12T20:01:00+08:00"), base, target) == "PRIMARY"
    )
    assert (
        cohort_for(datetime.fromisoformat("2026-10-12T16:00:00+08:00"), base, target)
        == "EXPLORATORY"
    )
    assert (
        cohort_for(datetime.fromisoformat("2026-10-12T20:15:00+08:00"), base, target)
        == "EXPLORATORY"
    )
    with pytest.raises(ValueError):
        cohort_for(datetime.fromisoformat("2026-10-13T09:30:00+08:00"), base, target)


def test_dividend_reference_and_flat_are_exact_and_suspension_is_not_flat():
    assert classify_outcome("UP", Decimal("99.5"), Decimal("99")) == ("UP", True)
    assert classify_outcome("UP", Decimal("99"), Decimal("99")) == ("FLAT", False)
    with pytest.raises(ValueError):
        classify_outcome("UP", Decimal("0"), Decimal("99"))
