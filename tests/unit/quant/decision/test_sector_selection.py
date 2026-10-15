from datetime import date, datetime, timedelta

import pytest

from marketpulse.quant.decision.contracts import SectorHeatInputs
from marketpulse.quant.decision.sector_selection import rank_sectors


def inputs():
    days = [date(2026, 8, 3) + timedelta(days=i) for i in range(28)]
    days = [d for d in days if d.weekday() < 5]
    return dict(
        classification_version="fixture-industry-v1",
        classification_level="INDUSTRY",
        source_family="fixture",
        sessions=days,
        sectors=[
            dict(
                industry_id=s,
                membership_ref="fixture-members",
                days=[
                    dict(session=d, turnover=v, members_total=2, members_available=2) for d in days
                ],
            )
            for s, v in [("B", "300"), ("A", "300"), ("C", "100")]
        ],
        market_turnover=[
            dict(session=d, value="1000", unit="CNY", source_ref="fixture") for d in days
        ],
        market_coverage_complete=True,
        universe_ref="fixture-full-market",
        cutoff_at="2026-08-31T20:00:00+08:00",
    )


def test_rank_uses_identical_market_denominator_and_deterministic_ties():
    result = rank_sectors(
        SectorHeatInputs.model_validate(inputs()),
        datetime.fromisoformat("2026-09-01T20:00:00+08:00"),
    )
    assert [r["industry_id"] for r in result["ranking"]] == ["A", "B", "C"]
    assert result["ranking"][0]["mean_20_share"] == "0.3"
    assert result["selection"].provisional is False


def test_missing_one_of_twenty_days_cannot_rank_or_fill_zero():
    data = inputs()
    data["sectors"][0]["days"].pop(7)
    result = rank_sectors(
        SectorHeatInputs.model_validate(data), datetime.fromisoformat("2026-09-01T20:00:00+08:00")
    )
    assert [r["industry_id"] for r in result["ranking"]] == ["A", "C"]
    assert result["excluded"][0]["industry_id"] == "B"
    assert result["coverage_label"] == "已覆盖范围的排序"


def test_incomplete_market_denominator_blocks_all_rankings():
    data = inputs()
    data["market_coverage_complete"] = False
    result = rank_sectors(
        SectorHeatInputs.model_validate(data), datetime.fromisoformat("2026-09-01T20:00:00+08:00")
    )
    assert result["ranking"] == []
    assert "分母" in result["reason"]


def test_future_session_is_rejected_even_when_cutoff_field_looks_historical():
    data = inputs()
    data["cutoff_at"] = "2026-08-20T20:00:00+08:00"
    with pytest.raises(ValueError, match="future"):
        rank_sectors(
            SectorHeatInputs.model_validate(data),
            datetime.fromisoformat("2026-09-01T20:00:00+08:00"),
        )
