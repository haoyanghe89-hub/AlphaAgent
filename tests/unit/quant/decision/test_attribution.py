"""Synthetic counterexamples check time/price logic, not prediction effectiveness."""

from decimal import Decimal

import pytest

from marketpulse.quant.decision.attribution import build, effective_session, observed_return

DAYS = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


@pytest.mark.parametrize(
    "stamp,precision,expected",
    [
        ("2026-10-12T18:00:00+08:00", "minute", "2026-10-13"),
        ("2026-10-12T14:00:00+08:00", "minute", "2026-10-12"),
        ("2026-10-12T00:00:00+08:00", "day", "2026-10-13"),
        ("2026-10-09T16:00:00+00:00", "day", "2026-10-12"),
        (None, "unknown", None),
    ],
)
def test_only_observe_after_publication(stamp, precision, expected):
    assert effective_session(stamp, precision, DAYS) == expected


def test_manual_returns_missing_day_and_zero_not_filled():
    prices = {DAYS[0]: "100", DAYS[1]: "95", DAYS[2]: "110"}
    assert Decimal(observed_return(prices, DAYS[0], DAYS[1:3])) == Decimal(".1")
    assert observed_return(prices, DAYS[0], DAYS[1:]) is None
    with pytest.raises(ValueError):
        observed_return({**prices, DAYS[2]: "0"}, DAYS[0], DAYS[1:3])


def test_market_same_direction_multiple_events_and_raw_exdiv_cannot_identify():
    series = {
        k: [dict(session=d, value=str(v)) for d, v in zip(DAYS, vals, strict=True)]
        for k, vals in {
            "security_close": [100, 99, 102, 105, 108, 110],
            "market_close": [100, 105, 107, 108, 109, 110],
        }.items()
    }
    # Two simultaneous synthetic titles remain competitors; after-close future title excluded.
    events = [dict(event_id=str(i), published_at="2026-10-09T00:00:00+08:00") for i in range(2)]
    events.append(dict(event_id="future", published_at="2026-10-16T18:00:00+08:00"))
    report = build(
        dict(series=series, windows=[dict(window=5, sessions=DAYS[1:], baseline_date=DAYS[0])]),
        dict(calendar=dict(sessions=DAYS)),
        events,
        {DAYS[0]: "1", DAYS[1]: "1.1", DAYS[2]: "1.2"},
    )
    w = report["windows"][0]
    assert Decimal(w["relative_market"]) == 0 and len(w["events"]) == 2
    assert w["industry_return"] is None and w["identification"] == "NOT_IDENTIFIED"
    assert "除权息" in "".join(w["limitations"])
    assert not any("contribution" in key for key in w)


def test_short_calendar_cannot_be_labeled_a_complete_window():
    series = {
        k: [dict(session=d, value="100") for d in DAYS] for k in ("security_close", "market_close")
    }
    report = build(
        dict(series=series, windows=[dict(window=20, sessions=DAYS[1:], baseline_date=DAYS[0])]),
        None,
        [],
        {},
    )
    assert report["windows"][0]["security_return"] is None
