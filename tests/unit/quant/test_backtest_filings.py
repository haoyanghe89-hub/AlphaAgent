"""Synthetic audit observations, never preview market evidence."""

from decimal import Decimal

import pytest

from marketpulse.quant.backtest.filings import compare_value, derive_financials
from marketpulse.quant.data.statement_official import extract_statement


def test_crosscheck_definitions_missing_precision_and_conflicts():
    assert compare_value("10", "10", "parent", "consolidated", "0")["status"] == "METHOD_DIFFERENCE"
    assert compare_value(None, "10", "parent", "parent", "0")["status"] == "UNAVAILABLE"
    assert compare_value("10.004", "10", "parent", "parent", "0.005")["status"] == "MATCH"
    assert compare_value("11", "10", "parent", "parent", "0.005")["status"] == "CONFLICT"
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="finite"):
            compare_value(bad, "10", "parent", "consolidated", "0")
    with pytest.raises(ValueError, match="nonnegative"):
        compare_value("10", "10", "parent", "parent", "-1")


def test_official_document_hash_rejects_wrong_or_unknown_document():
    with pytest.raises(ValueError):
        extract_statement(b"not the reviewed official PDF", "midea-2024q1-v1")
    with pytest.raises(KeyError):
        extract_statement(b"unknown document", "unreviewed-profile")


def observations():
    return [
        dict(period_end=p, parent_net_profit=n, revenue=r, source_hash=h * 64)
        for p, n, r, h in [
            ("2023-03-31", "20", "100", "a"),
            ("2023-12-31", "100", "500", "b"),
            ("2024-03-31", "30", "110", "c"),
        ]
    ]


def test_cumulative_ttm_hand_example_replay_and_missing_period():
    result = derive_financials(observations())
    assert Decimal(result["ttm_parent_profit"]["value"]) == 110  # 100 - 20 + 30
    assert Decimal(result["revenue_yoy"]["value"]) == Decimal("0.1")
    assert result["ttm_parent_profit"]["crosscheck_status"] == "UNAVAILABLE"
    assert result["metric_status"] == "UNVERIFIED"
    assert derive_financials(list(reversed(observations()))) == result
    assert derive_financials(observations()[1:])["ttm_parent_profit"]["value"] is None
