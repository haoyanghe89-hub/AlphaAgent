from copy import deepcopy

import pytest
from pydantic import ValidationError

from marketpulse.quant.api import QuantComparisonIn
from marketpulse.quant.comparison import alignment, compose


def cell(**changes):
    return dict(
        name="revenue_growth",
        value="0.125",
        unit="fraction",
        start="2024-01-01",
        end="2024-03-31",
        definition="comparable_report_period_yoy;metric=revenue;no_annualization",
        status="UNVERIFIED",
        **changes,
    )


def test_alignment_does_not_upgrade_status_or_mix_periods_units_formulas():
    a = cell()
    assert alignment([a, deepcopy(a)])[0] == "MATCH"
    assert a["status"] == "UNVERIFIED"
    for field, value in (
        ("start", "2023-01-01"),
        ("end", "2024-06-30"),
        ("unit", "%"),
        ("definition", "single_quarter_yoy"),
    ):
        b = {**a, field: value}
        assert alignment([a, b])[0] == "METHOD_DIFFERENCE"
    assert alignment([a, {**a, "value": None}])[0] == "UNAVAILABLE"
    assert alignment([a, None])[0] == "UNAVAILABLE"
    assert alignment([a, {**a, "value": "0"}])[0] == "MATCH"


def test_revision_identity_is_preserved_without_changing_common_formula():
    a = {**cell(), "definition": "raw*shares/TTM;revision=AAA;period=2024-03-31"}
    b = {**a, "definition": "raw*shares/TTM;revision=BBB;period=2024-03-31"}
    assert alignment([a, b])[0] == "MATCH"
    assert "AAA" in a["definition"] and "BBB" in b["definition"]


def test_five_prototype_dimensions_preserve_missing_and_do_not_substitute_ytd_or_short_returns():
    subjects = [
        dict(
            data={
                "values": [
                    cell(),
                    {**cell(), "name": "return", "start": "2024-06-01", "end": "2024-06-14"},
                ]
            }
        )
        for _ in range(2)
    ]
    result = compose(subjects)
    assert len(result["dimensions"]) == 5
    rows = {r["key"]: r for r in result["dimensions"]}
    assert rows["growth"]["alignment"] == "MATCH"
    assert all(rows[k]["alignment"] == "UNAVAILABLE" for k in ("quality", "valuation", "momentum"))
    assert "winner" not in str(result)
    for subject in subjects:
        subject["data"]["values"][0]["end"] = "2024-06-30"
    assert compose(subjects)["dimensions"][0]["cells"] == [None, None]


def test_comparison_contract_rejects_duplicate_misaligned_or_unfrozen_inputs():
    plan = dict(
        instrument="ins_a",
        date_start="2024-06-01",
        date_end="2024-06-14",
        asof="2024-06-15T00:00:00Z",
        frozen_snapshot_ids=["qds_a"],
    )
    other = {**plan, "instrument": "ins_b", "frozen_snapshot_ids": ["qds_b"]}
    assert len(QuantComparisonIn(question="a vs b", subjects=[plan, other]).subjects) == 2
    for bad in (
        plan,
        {**other, "asof": "2024-06-16T00:00:00Z"},
        {**other, "adjustment": "qfq"},
        {**other, "date_end": "2024-06-13"},
        {**other, "frozen_snapshot_ids": []},
    ):
        with pytest.raises(ValidationError):
            QuantComparisonIn(question="a vs b", subjects=[plan, bad])
