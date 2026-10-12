"""Synthetic universe tests, not production admission evidence."""

from copy import deepcopy

import pytest

from marketpulse.quant.backtest.market_plan import all_queries, build_plan, coverage, inspect_packet
from marketpulse.quant.contracts import digest


def fixture():
    parent = dict(
        run_id="RUN-HIST-test",
        investigation_id="INV-test",
        meta=dict(date_start="2021-10-06", date_end="2026-10-06"),
        historical_data=dict(annual=[dict(year=y) for y in range(2022, 2027)]),
    )
    basics = [
        dict(code="sh.600001", type="1", ipoDate="2000-01-01", outDate=""),
        dict(code="sz.000002", type="1", ipoDate="2023-06-01", outDate="2023-07-01"),
        dict(code="sz.000003", type="1", ipoDate="2000-01-01", outDate="2020-07-01"),
        dict(code="sz.000005", type="2", ipoDate="2000-01-01", outDate=""),
        dict(code="bj.800001", type="1", ipoDate="2000-01-01", outDate=""),
    ]
    packets = [
        dict(query=dict(kind="basic"), rows=basics),
        dict(query=dict(kind="members", day="2024-04-30"), rows=[dict(code="sz.000004")]),
        dict(
            query=dict(kind="calendar"), rows=[dict(calendar_date="2024-04-30", is_trading_day="1")]
        ),
    ]
    return parent, packets


def test_full_scope_includes_delisted_between_signals_and_unknown_not_only_master():
    parent, packets = fixture()
    plan = build_plan(parent, packets)
    stocks = {s["code"]: s for s in plan["securities"]}
    assert set(stocks) == {"sh.600001", "sz.000002", "sz.000004"}
    assert stocks["sz.000002"]["delisted_observed"]
    assert stocks["sz.000004"]["identity_status"] == "UNAVAILABLE"
    assert stocks["sz.000004"]["start"] == plan["start"]
    assert stocks["sz.000004"]["end"] == plan["end"]
    assert plan == build_plan(parent, packets)
    queries = all_queries(plan)
    assert len(queries) == len({digest(q) for q in queries})
    assert len([q for q in queries if q["kind"] == "profit" and q["code"] == "sh.600001"]) == 11
    counts = coverage(plan, {})
    assert counts["stock_count"] == 3 and counts["observed_stock_count"] == 0
    assert counts["frozen_count"] == 0 and counts["query_count"] == len(queries)
    assert all(s["missing_sessions"] is None for s in counts["stocks"])


def test_official_only_and_conflicting_lifecycle_not_silently_excluded():
    parent, packets = fixture()
    rows = [
        dict(code="sh.600009", name="synthetic", listed_at="2021-11-01", delisted_at="2022-01-01"),
        dict(code="sz.000003", name="synthetic", listed_at="2000-01-01", delisted_at="2025-01-01"),
    ]
    plan = build_plan(parent, packets, rows)
    assert {s["code"] for s in plan["securities"]} >= {"sh.600009", "sz.000003"}


def test_cross_stock_pollution_empty_financial_and_missing_sessions():
    parent, packets = fixture()
    plan = build_plan(parent, packets)
    stock = plan["securities"][0]
    query = next(q for q in all_queries(plan) if q["kind"] == "profit")
    packet = dict(provider="baostock", query=query, rows=[], retrieved_at="2026-10-10T00:00:00Z")
    result = inspect_packet(packet, query, plan, stock)
    assert result["empty"] and result["status"] == "UNAVAILABLE"
    bad = deepcopy(packet)
    bad["rows"] = [dict(code="sh.600099")]
    with pytest.raises(ValueError, match="security mismatch"):
        inspect_packet(bad, query, plan, stock)
    daily = next(q for q in all_queries(plan) if q["kind"] == "stock_daily")
    result = inspect_packet({**packet, "query": daily}, daily, plan, stock)
    assert result["missing_sessions"] == 1


def test_symbol_transition_clips_prices_without_rewriting_old_plan():
    parent, packets = fixture()
    packets[0]["rows"] += [
        dict(
            code=c, type="1", ipoDate="2010-08-27", outDate="2025-02-17" if c == "sz.300114" else ""
        )
        for c in ("sz.300114", "sz.302132")
    ]
    original = build_plan(parent, packets)
    before = digest(original)
    parent["notice_audit"] = dict(
        complete=True,
        events=[
            dict(
                status="MATCH",
                old_code="sz.300114",
                new_code="sz.302132",
                effective_day="2025-02-17",
                source_hashes=["synthetic-issuer", "synthetic-cni"],
            )
        ],
    )
    plan = build_plan(parent, packets)
    stocks = {s["code"]: s for s in plan["securities"]}
    old, new = stocks["sz.300114"], stocks["sz.302132"]
    assert old["end"] == "2025-02-16" and new["start"] == "2025-02-17"
    assert old["observed_identity_id"] == new["observed_identity_id"]
    assert plan["schema_version"] == "market-observation-plan-v2"
    assert digest(original) == before and original["schema_version"].endswith("v1")
    assert all(
        q["end"] < "2025-02-17"
        for q in all_queries(plan)
        if q["kind"] == "stock_daily" and q["code"] == old["code"]
    )
    assert all(
        q["start"] >= "2025-02-17"
        for q in all_queries(plan)
        if q["kind"] == "stock_daily" and q["code"] == new["code"]
    )
    current = coverage(plan, {})
    assert current["observed_identity_count"] == len(stocks) - 1
    assert current["symbol_change_exit_count"] == 1
    assert current["delisted_count"] == 1  # Short-lived fixture, not the old code.
    parent["notice_audit"]["events"][0]["status"] = "UNAVAILABLE"
    partial_plan = build_plan(parent, packets)
    partial = {s["code"]: s for s in partial_plan["securities"]}
    original_old = next(s for s in original["securities"] if s["code"] == old["code"])
    assert partial[old["code"]]["end"] == original_old["end"]
    assert coverage(partial_plan, {})["symbol_change_exit_count"] == 0
    assert coverage(partial_plan, {})["delisted_count"] == 2
    assert (
        partial[old["code"]]["observed_identity_id"] != partial[new["code"]]["observed_identity_id"]
    )
    parent["notice_audit"]["events"][0]["status"] = "MATCH"
    parent["notice_audit"]["events"][0]["source_hashes"] = ["only-one"]
    with pytest.raises(ValueError, match="continuity"):
        build_plan(parent, packets)
