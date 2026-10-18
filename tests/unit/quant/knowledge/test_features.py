from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from marketpulse.quant.knowledge.features import compute_features, growth, ttm


def market_packet():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(140)]
    dates = [d.strftime("%Y%m%d") for d in days if d.weekday() < 5]
    daily = [
        dict(
            ts_code="000333.SZ",
            trade_date=d,
            close=100 + i,
            high=101 + i,
            low=99 + i,
            vol=100,
            amount=1000,
        )
        for i, d in enumerate(dates)
    ]
    stock = dict(
        ts_code="000333.SZ",
        name="SYNTHETIC",
        market="主板",
        industry="测试",
        daily=daily,
        adjustments=[dict(ts_code="000333.SZ", trade_date=d, adj_factor=1) for d in dates],
        industry_memberships=[
            dict(l1_code="SYNTHETIC", l1_name="测试", in_date="20200101", out_date=None, is_new="Y")
        ],
        financials={},
        financial_scope="NON_FINANCIAL_PENDING_CHECK",
    )
    return dict(
        market_date=dates[-1],
        benchmark_id="000300.SH",
        stocks=[stock],
        benchmark=[dict(ts_code="000300.SH", trade_date=d, close=100) for d in dates],
        calendars={"SZSE": [dict(cal_date=d, is_open=1) for d in dates]},
    )


def test_adjustment_cannot_be_silently_one():
    p = market_packet()
    p["stocks"][0]["adjustments"].pop()
    with pytest.raises(ValueError, match="adjustment"):
        compute_features(p)


def test_split_does_not_create_a_false_price_loss():
    p = market_packet()
    for i, row in enumerate(p["stocks"][0]["daily"]):
        price = 100 if i < 50 else 50
        row.update(close=price, high=price, low=price)
        p["stocks"][0]["adjustments"][i]["adj_factor"] = 1 if i < 50 else 2
    result = compute_features(p)["stocks"][0]
    assert Decimal(result["metrics"]["return_20"]["value"]) == 0
    assert Decimal(result["metrics"]["ma_60"]["value"]) == 50
    assert result["adjustment"] == "PROVIDER_FACTOR_NORMALIZED_TO_LAST_SESSION"


def test_missing_calendar_session_is_not_a_shortened_window():
    p = market_packet()
    p["stocks"][0]["daily"].pop(-10)
    with pytest.raises(ValueError, match="session"):
        compute_features(p)


def test_ttm_and_negative_comparison_base_are_explicit():
    rows = {"20260630": Decimal(60), "20251231": Decimal(100), "20250630": Decimal(40)}
    assert ttm(rows, "20260630") == Decimal(120)
    assert growth(Decimal(10), Decimal(-5)) is None
    assert growth(Decimal(10), Decimal(5)) == Decimal(1)
    assert ttm({}, "20260630") is None


def test_future_and_duplicate_prices_are_rejected():
    for mutation in ("future", "duplicate"):
        p = market_packet()
        row = deepcopy(p["stocks"][0]["daily"][-1])
        if mutation == "future":
            row["trade_date"] = "20990101"
        p["stocks"][0]["daily"].append(row)
        with pytest.raises(ValueError):
            compute_features(p)


def test_metric_windows_match_their_declared_formulas():
    packet = market_packet()
    result = compute_features(packet)["stocks"][0]
    dates = sorted(r["trade_date"] for r in packet["stocks"][0]["daily"])
    assert result["metrics"]["ma_60"]["window"]["start"] == dates[-60]
    assert result["metrics"]["ma_20"]["window"]["start"] == dates[-20]
    assert result["metrics"]["prior_high_20"]["window"]["end"] == dates[-2]
