"""Synthetic source boundaries; never used for actual page results."""

import pytest

from marketpulse.quant.backtest.inputs import input_queries, inspect_inputs

TARGET = dict(
    instrument_id="ins_fixture_inputs_001",
    name="fixture",
    provider_code="sz.000001",
    start="2024-04-29",
    end="2024-06-01",
    signal_year=2024,
    signal_day="2024-04-30",
    execution_day="2024-05-06",
    expected_sessions=["2024-04-29", "2024-04-30"],
    calendar_hashes=["a" * 64],
)


def packet(kind, rows, **query):
    return dict(
        provider="baostock",
        retrieved_at="2026-10-07T00:00:00Z",
        query=dict(kind=kind, code="sz.000001", **query),
        rows=rows,
    )


def bar(day, **changes):
    return dict(
        date=day,
        code="sz.000001",
        open="10",
        high="11",
        low="9",
        close="10",
        preclose="10",
        volume="100",
        amount="1000",
        adjustflag="3",
        tradestatus="1",
        isST="0",
        **changes,
    )


def test_fixed_queries_prices_and_suspension_do_not_infer_buy_permissions():
    queries = input_queries(TARGET)
    assert len(queries) == 5
    assert queries[1] == dict(kind="dividends", code="sz.000001", year=2024)
    halted = {
        **bar("2024-04-30"),
        "tradestatus": "0",
        "isST": "",
        "open": "0",
        "close": "0",
        "low": "0",
        "high": "0",
    }
    result = inspect_inputs(
        [
            packet(
                "stock_daily", [bar("2024-04-29"), halted], start=TARGET["start"], end=TARGET["end"]
            )
        ],
        TARGET,
    )
    assert result["prices"]["coverage"] == "COVERED"
    assert result["prices"]["suspended_count"] == 1 and result["prices"]["unknown_st_count"] == 1
    assert result["execution_status"] == "BLOCKED"
    assert "buy_allowed" not in str(result) and "metrics" not in result
    with pytest.raises(ValueError):
        inspect_inputs(
            [
                packet(
                    "stock_daily",
                    [{**bar("2024-04-29"), "adjustflag": "2"}],
                    start=TARGET["start"],
                    end=TARGET["end"],
                )
            ],
            TARGET,
        )
    with pytest.raises(ValueError):
        inspect_inputs(
            [
                packet(
                    "stock_daily",
                    [{**bar("2024-04-29"), "high": "8"}],
                    start=TARGET["start"],
                    end=TARGET["end"],
                )
            ],
            TARGET,
        )


def test_dividend_dates_units_and_missing_components_are_not_imputed():
    event = dict(
        code="sz.000001",
        dividOperateDate="2024-05-20",
        dividRegistDate="2024-05-17",
        dividPayDate="2024-05-21",
        dividStockMarketDate="2024-05-22",
        dividCashPsBeforeTax="0.5",
        dividStocksPs="0.1",
        dividReserveToStockPs="",
    )
    result = inspect_inputs([packet("dividends", [event], year=2024)], TARGET)
    e = result["actions"]["events"][0]
    assert e["cash_per_share_before_tax"] == "0.5" and e["bonus_shares_per_share"] == "0.1"
    assert e["reserve_shares_per_share"] is None and e["status"] == "PARTIAL"
    assert e["ex_date"] != e["pay_date"] != e["stock_listing_date"]
    empty = inspect_inputs([packet("dividends", [], year=2024)], TARGET)
    assert empty["actions"]["empty_query_count"] == 1 and empty["actions"]["complete"] is False


def test_financial_future_or_same_day_publication_never_proves_original_version():
    rows = [
        dict(
            code="sz.000001",
            statDate="2024-03-31",
            pubDate="2024-04-30",
            netProfit="100",
            roeAvg="0.1",
        )
    ]
    same = inspect_inputs([packet("profit", rows, year=2024, quarter=1)], TARGET)
    assert same["financials"]["observations"][0]["date_observation"] == "SAME_DAY_TIME_UNKNOWN"
    assert same["financials"]["original_versions_available"] is False
    future = inspect_inputs(
        [packet("profit", [{**rows[0], "pubDate": "2024-05-01"}], year=2024, quarter=1)], TARGET
    )
    assert future["financials"]["observations"][0]["date_observation"] == "AFTER_SIGNAL"
    wrong = packet("profit", [{**rows[0], "code": "sz.000002"}], year=2024, quarter=1)
    with pytest.raises(ValueError):
        inspect_inputs([wrong], TARGET)


def test_duplicate_or_missing_sessions_and_unknown_status_are_explicit():
    p = packet(
        "stock_daily",
        [{**bar("2024-04-29"), "tradestatus": ""}],
        start=TARGET["start"],
        end=TARGET["end"],
    )
    result = inspect_inputs([p], TARGET)
    assert result["prices"]["coverage"] == "INCOMPLETE"
    assert result["prices"]["missing_session_count"] == 1
    assert result["prices"]["unknown_status_count"] == 1
    with pytest.raises(ValueError):
        inspect_inputs([p, p], TARGET)
    with pytest.raises(ValueError):
        inspect_inputs([{**p, "rows": [p["rows"][0], p["rows"][0]]}], TARGET)


@pytest.mark.parametrize(
    "query",
    [
        dict(kind="profit", code="sz.000001", year=2024, quarter=5),
        dict(kind="dividends", code="sz.000001", year=True),
        dict(kind="dividends", code="sz.000001", year=2100),
        dict(kind="dividends", code="us.AAPL", year=2024),
        dict(kind="dividends", code="sz.000001", year=2024, method="arbitrary"),
        dict(kind="stock_daily", code="sz.000001", start="2021-01-01", end="2025-01-01"),
    ],
)
def test_sdk_boundary_rejects_unbounded_queries_before_network(query):
    from marketpulse.quant.backtest.history_worker import execute

    with pytest.raises(ValueError):
        execute(query)
