"""Financial selection hand fixtures; not real page data or authoritative checks."""

from datetime import UTC, date, datetime
from decimal import Decimal

from marketpulse.quant.backtest.selection import Candidate, VersionedFact, select_at
from marketpulse.quant.domain import FinancialFact, PITLevel

CUTOFF = datetime(2024, 4, 30, 7, tzinfo=UTC)
BASIS = "total_issued_shares_parent_aggregate:CNY"


def version(
    metric, value, end, basis="point", start=None, known="2024-04-20T00:00:00Z", sha="a" * 64
):
    return VersionedFact(
        fact=FinancialFact(
            metric=metric,
            value=value,
            unit="share" if metric == "total_shares" else "CNY",
            period_end=end,
            period_start=start,
            available_at="2024-04-19T00:00:00Z",
            share_basis=BASIS,
            statement_basis="parent",
            period_basis=basis,
            revision=sha,
            source_hash=sha,
        ),
        version_known_at=known,
        pit_level="STRICT",
        locator="original filing page 1",
        source_hash=sha,
    )


def candidate():
    facts = (
        version("parent_net_profit", 100, "2023-12-31", "annual", "2023-01-01"),
        version("parent_net_profit", 20, "2023-03-31", "ytd", "2023-01-01"),
        version("parent_net_profit", 30, "2024-03-31", "ytd", "2024-01-01"),
        version("parent_equity", 200, "2023-03-31"),
        version("parent_equity", 220, "2024-03-31"),
        version("total_shares", 100, "2024-04-30"),
        version("revenue", 100, "2023-03-31", "ytd", "2023-01-01"),
        version("revenue", 120, "2024-03-31", "ytd", "2024-01-01"),
    )
    return Candidate(
        instrument_id="ins_synthetic_selection_001",
        historical_member=True,
        membership_pit="STRICT",
        membership_source_hash="b" * 64,
        price_session=date(2024, 4, 30),
        raw_close=10,
        price_known_at=CUTOFF,
        price_pit="STRICT",
        price_source_hash="c" * 64,
        financials=facts,
    )


def test_hand_ttm_pe_average_equity_roe_and_growth_are_unverified():
    result = select_at(
        (candidate(),), CUTOFF, {"pe_max": "10", "roe_min": "50", "rev_yoy_min": "15"}
    )
    assert result["eligible"] == ["ins_synthetic_selection_001"]
    item = result["candidates"][0]
    assert item["metrics"]["ttm_parent_profit"] == "110"
    assert abs(Decimal(item["metrics"]["pe"]) - Decimal(1000) / Decimal(110)) < Decimal("1e-25")
    assert abs(Decimal(item["metrics"]["roe"]) - Decimal(110) / Decimal(210)) < Decimal("1e-25")
    assert item["metrics"]["revenue_growth"] == "0.2"
    assert item["metric_status"] == "UNVERIFIED" and item["source_hashes"]
    assert (
        select_at((candidate(),), CUTOFF, {"pe_max": "8"})["candidates"][0]["status"] == "EXCLUDED"
    )


def test_future_revision_not_selected_conflicting_same_time_and_partial_are_missing():
    original = candidate()
    future = version(
        "parent_net_profit",
        300,
        "2024-03-31",
        "ytd",
        "2024-01-01",
        known="2024-05-02T00:00:00Z",
        sha="d" * 64,
    )
    first = select_at((original,), CUTOFF, {})
    assert (
        select_at(
            (original.model_copy(update={"financials": original.financials + (future,)}),),
            CUTOFF,
            {},
        )
        == first
    )
    conflict = future.model_copy(
        update={"version_known_at": original.financials[2].version_known_at}
    )
    out = select_at(
        (original.model_copy(update={"financials": original.financials + (conflict,)}),), CUTOFF, {}
    )
    assert out["candidates"][0]["status"] == "UNAVAILABLE"
    assert out["eligible_count"] is None
    for change in (
        {"membership_pit": PITLevel.PARTIAL},
        {"price_pit": PITLevel.PARTIAL},
        {"historical_member": None},
        {
            "financials": tuple(
                v.model_copy(update={"pit_level": PITLevel.PARTIAL}) for v in original.financials
            )
        },
    ):
        result = select_at((original.model_copy(update=change),), CUTOFF, {})
        assert result["candidates"][0]["status"] == "UNAVAILABLE"
        assert result["input_view_hash"] != first["input_view_hash"]


def test_missing_same_day_shares_negative_growth_base_and_hash_reproduction():
    original = candidate()
    no_shares = original.model_copy(
        update={
            "financials": tuple(v for v in original.financials if v.fact.metric != "total_shares")
        }
    )
    assert select_at((no_shares,), CUTOFF, {})["candidates"][0]["status"] == "UNAVAILABLE"
    zero = original.financials[-2].model_copy(
        update={"fact": original.financials[-2].fact.model_copy(update={"value": Decimal(0)})}
    )
    result = select_at(
        (
            original.model_copy(
                update={"financials": original.financials[:-2] + (zero, original.financials[-1])}
            ),
        ),
        CUTOFF,
        {"rev_yoy_min": "1"},
    )
    assert result["candidates"][0]["status"] == "UNAVAILABLE"
    assert select_at((original,), CUTOFF, {}) == select_at((original,), CUTOFF, {})


def test_wrong_units_and_period_basis_are_missing_not_threshold_failures():
    original = candidate()
    shares = original.financials[5]
    wrong = shares.model_copy(update={"fact": shares.fact.model_copy(update={"unit": "CNY"})})
    altered = original.model_copy(
        update={"financials": (*original.financials[:5], wrong, *original.financials[6:])}
    )
    result = select_at((altered,), CUTOFF, {})
    assert result["candidates"][0]["reason"] == "unit_mismatch"
    assert result["eligible_count"] is None
    revenue = original.financials[-2]
    wrong_period = revenue.model_copy(
        update={"fact": revenue.fact.model_copy(update={"period_basis": "quarter"})}
    )
    altered = original.model_copy(
        update={"financials": (*original.financials[:-2], wrong_period, original.financials[-1])}
    )
    result = select_at((altered,), CUTOFF, {"rev_yoy_min": "1"})
    assert result["candidates"][0]["reason"] == "incomparable_periods"
    assert result["eligible_count"] is None


def test_price_known_after_signal_is_unavailable_even_with_matching_close():
    original = candidate().model_copy(update={"price_known_at": datetime(2024, 5, 1, tzinfo=UTC)})
    result = select_at((original,), CUTOFF, {})
    assert result["candidates"][0]["reason"] == "signal_day_raw_price_unavailable"
    assert result["eligible_count"] is None
