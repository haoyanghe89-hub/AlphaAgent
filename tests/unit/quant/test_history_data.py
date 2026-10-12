from datetime import date

import pytest

from marketpulse.quant.backtest.history import annual_windows, query_segments, summarize


def packet(kind, rows, **params):
    return {
        "query": {"kind": kind, **params},
        "rows": rows,
        "retrieved_at": "2026-10-07T00:00:00Z",
        "provider": "baostock",
    }


def test_segments_are_contiguous_bounded_and_annual_dates_use_real_calendar():
    start, end = date(2018, 10, 6), date(2026, 10, 6)
    segments = query_segments(start, end)
    assert segments[0][0] == start and segments[-1][1] == end
    assert all((b - a).days <= 1096 for a, b in segments)
    assert all((c - b).days == 1 for (_, b), (c, _) in zip(segments, segments[1:], strict=False))
    sessions = ["2023-04-28", "2023-05-04", "2023-05-05", "2024-04-30", "2024-05-06"]
    assert annual_windows(sessions, date(2023, 5, 1), date(2024, 5, 10)) == [
        {"year": 2023, "signal_day": "2023-04-28", "execution_day": "2023-05-04"},
        {"year": 2024, "signal_day": "2024-04-30", "execution_day": "2024-05-06"},
    ]


def test_historical_delisted_and_suspended_members_retained_and_unknown_not_guessed():
    packets = [
        packet(
            "basic",
            [
                {
                    "code": "sh.600001",
                    "type": "1",
                    "ipoDate": "2000-01-01",
                    "outDate": "2025-01-01",
                    "status": "0",
                },
                {
                    "code": "sz.000001",
                    "type": "1",
                    "ipoDate": "2000-01-01",
                    "outDate": "",
                    "status": "1",
                },
                {
                    "code": "sh.000300",
                    "type": "2",
                    "ipoDate": "2005-01-01",
                    "outDate": "",
                    "status": "1",
                },
                {"code": "sz.399001", "type": "2", "ipoDate": "", "status": "1"},
            ],
        ),
        packet(
            "members",
            [
                {"code": "sh.600001", "tradeStatus": "1", "code_name": "past member"},
                {"code": "sz.000001", "tradeStatus": "0", "code_name": "suspended member"},
                {"code": "sh.000300", "tradeStatus": "1", "code_name": "index"},
                {"code": "sz.399001", "tradeStatus": "1", "code_name": "Shenzhen index"},
                {"code": "sz.300999", "tradeStatus": "1", "code_name": "unknown lifecycle"},
            ],
            day="2024-04-30",
        ),
        packet(
            "benchmark",
            [
                {
                    "date": "2024-04-30",
                    "code": "sh.000300",
                    "close": "3500",
                    "open": "3500",
                    "high": "3500",
                    "low": "3500",
                },
            ],
            start="2024-04-30",
            end="2024-04-30",
        ),
    ]
    result = summarize(packets, date(2024, 4, 30), date(2024, 4, 30))
    row = result["universes"][0]
    assert row["member_count"] == 2 and row["suspended_count"] == 1
    assert row["currently_delisted_count"] == 1 and row["unknown_count"] == 1
    assert row["pit_level"] == "PARTIAL"
    assert row["excluded_non_stock_count"] == 1
    legacy = summarize(packets, date(2024, 4, 30), date(2024, 4, 30), version="history-data-v1")
    assert legacy["universes"][0]["unknown_count"] == 2
    assert legacy["schema_version"] == "history-data-v1"
    assert (
        result["benchmark"]["row_count"] == 1
        and result["benchmark"]["return_basis"] == "price_index"
    )
    assert "rows" not in result["benchmark"]
    assert summarize(packets, date(2024, 4, 30), date(2024, 4, 30)) == result


def test_duplicate_members_outside_dates_and_missing_benchmark_reject():
    row = {"code": "sh.600001", "tradeStatus": "1"}
    with pytest.raises(ValueError, match="duplicate"):
        summarize(
            [packet("members", [row, row], day="2024-04-30")], date(2024, 4, 30), date(2024, 4, 30)
        )
    with pytest.raises(ValueError):
        summarize(
            [
                packet(
                    "benchmark",
                    [{"date": "2024-05-02", "code": "sh.000300", "close": "3500"}],
                    start="2024-04-30",
                    end="2024-04-30",
                )
            ],
            date(2024, 4, 30),
            date(2024, 4, 30),
        )
