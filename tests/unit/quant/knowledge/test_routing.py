from marketpulse.quant.knowledge.features import compute_features, statements
from marketpulse.quant.knowledge.routing import evaluate_routes

from .test_features import market_packet


def test_bank_keeps_trend_and_never_fakes_quality():
    packet = market_packet()
    packet["stocks"][0]["financial_scope"] = "FINANCIAL"
    feature = compute_features(packet)["stocks"][0]
    result = evaluate_routes(feature, [], "MEDIUM")
    assert result["selected_route"] == "TREND_SWING"
    assert result["routes"][1]["status"] == "NOT_APPLICABLE"
    assert result["parameters"]["validation"] == "UNVALIDATED"


def test_downtrend_cannot_become_an_immediate_entry():
    feature = compute_features(market_packet())["stocks"][0]
    feature["market_regime"] = "DOWNTREND"
    assert all(r["status"] != "CONDITIONS_MET" for r in evaluate_routes(feature, [])["routes"])


def test_financial_announcement_day_and_future_revision_are_not_usable():
    row = dict(
        ts_code="000333.SZ",
        report_type="1",
        comp_type="1",
        end_date="20260630",
        ann_date="20260801",
        f_ann_date="20260801",
        total_revenue=100,
    )
    assert not statements([row], "000333.SZ", "20260801", ["total_revenue"])
    assert (
        statements([row], "000333.SZ", "20260802", ["total_revenue"])["20260630"]["values"][
            "total_revenue"
        ]
        == 100
    )
    revision = dict(row, f_ann_date="20260803", total_revenue=200)
    assert (
        statements([row, revision], "000333.SZ", "20260802", ["total_revenue"])["20260630"][
            "values"
        ]["total_revenue"]
        == 100
    )
