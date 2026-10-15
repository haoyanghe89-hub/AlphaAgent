"""Isolated forward-window arithmetic; these prices are synthetic, not realized returns."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from marketpulse.quant.decision.forecasts import observe_forecast


def mature_prices():
    days = []
    current = date(2026, 1, 5)
    while len(days) < 21:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return dict(
        window=20,
        observed_at=days[-1] + "T09:00:00+00:00",
        due_session=days[-1],
        sessions=days,
        security_prices=["100", *["90"] * 19, "110"],
        market_prices=[*["100"] * 20, "105"],
    )


def test_aligned_price_window_is_recomputable_without_full_return_upgrade():
    result = observe_forecast(SimpleNamespace(forecast_id="synthetic"), mature_prices())
    assert result.maturity == "MATURE"
    assert result.security_return == Decimal("0.10")
    assert result.market_return == Decimal("0.05")
    assert dict(result.relative_returns) == {"CSI300_PRICE": Decimal("0.05")}
    assert result.max_adverse_excursion == Decimal("-0.10")
    assert result.return_basis == "PRICE" and result.quality_status == "PARTIAL"


def test_incomplete_window_is_unavailable_and_duplicate_dates_are_rejected():
    inputs = mature_prices()
    record = SimpleNamespace(forecast_id="synthetic")
    result = observe_forecast(record, inputs | {"security_prices": inputs["security_prices"][:-1]})
    assert result.maturity == "UNAVAILABLE" and result.security_return is None
    inputs["sessions"][1] = inputs["sessions"][0]
    with pytest.raises(ValueError, match="dates do not align"):
        observe_forecast(record, inputs)


def test_new_observation_keeps_security_return_when_market_benchmark_is_missing():
    inputs = mature_prices() | {"protocol": "forecast-observe-2026-10-14-v2", "market_prices": []}
    result = observe_forecast(SimpleNamespace(forecast_id="synthetic"), inputs)
    assert result.maturity == "MATURE" and result.security_return == Decimal("0.1")
    assert result.market_return is None and not result.relative_returns
    assert result.return_basis == "PRICE" and result.industry_return is None
    legacy = observe_forecast(
        SimpleNamespace(forecast_id="synthetic"), inputs | {"protocol": "legacy"}
    )
    assert legacy.maturity == "UNAVAILABLE"  # Old saved calculation semantics stay intact.
