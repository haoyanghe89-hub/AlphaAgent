"""Synthetic arithmetic fixtures prove definitions, not investment efficacy."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from marketpulse.quant.decision.contracts import FlowInputs, FlowPoint, FlowWindowSpec
from marketpulse.quant.decision.flows import (
    METHOD,
    compute,
    distinct_turnover,
    industry_observation,
)

DAYS = tuple(date(2026, 1, 1) + timedelta(days=i) for i in range(61))


def points(values, unit="CNY", days=DAYS):
    return tuple(
        FlowPoint(session=d, value=str(v), unit=unit, source_ref="raw-fixture")
        for d, v in zip(days, values, strict=False)
    )


def result(**changes):
    inputs = FlowInputs(membership_ref="UNAVAILABLE", definition_version=METHOD, **changes)
    spec = FlowWindowSpec(end_date=DAYS[-1], sessions=DAYS, definition_version=METHOD)
    return compute(inputs, spec, "688012.SH")


def metric(report, key, window=5):
    return next(
        m for w in report.windows if w.window == window for m in w.metrics if m.metric_id == key
    )


def test_margin_balance_delta_buy_repay_and_price_divergence_are_separate():
    r = result(
        margin_balance=points([100 + i * 10 for i in range(61)]),
        margin_buy=points([30] * 61),
        margin_repay=points([20] * 61),
        security_close=points([200 - i for i in range(61)], "CNY/share"),
    )
    assert metric(r, "MARGIN_CHANGE").value == 50
    assert metric(r, "MARGIN_BUY").value == 150
    assert metric(r, "MARGIN_REPAY").value == 100
    assert metric(r, "MARGIN_INCREASE_RATIO").value == 1
    assert r.windows[0].divergence == "PRICE_DOWN_MARGIN_UP"
    assert metric(r, "MARGIN_CHANGE", 60).value == 600


def test_one_missing_day_never_becomes_zero_or_shorter_window():
    source = points([10] * 61)
    r = result(vendor_estimate=source[:50] + source[51:])
    assert metric(r, "VENDOR_NET", 20).value is None
    assert metric(r, "VENDOR_NET", 20).actual_count == 19
    assert metric(r, "VENDOR_NET", 5).value == 50
    assert metric(result(vendor_estimate=points([0] * 61)), "VENDOR_NET", 60).value == 0


def test_missing_latest_day_does_not_roll_to_older_end():
    r = result(margin_balance=points([100] * 60))
    assert r.end_date == DAYS[-1]
    assert metric(r, "MARGIN_CHANGE").value is None


def test_margin_source_discrepancy_is_separate_and_never_forced_to_balance():
    r = result(
        margin_balance=points([100 + i for i in range(61)]),
        margin_buy=points([30] * 61),
        margin_repay=points([20] * 61),
    )
    assert metric(r, "MARGIN_CHANGE").value == 5
    assert metric(r, "MARGIN_RECONCILIATION").value == -45
    assert metric(r, "MARGIN_MAX_RESIDUAL").value == 9
    assert metric(r, "MARGIN_INCREASE_RATIO").value == 1


@pytest.mark.parametrize(
    "key,values,unit",
    [
        ("security_close", [0] * 61, "CNY/share"),
        ("etf_split_factor", [0] * 61, "ratio"),
        ("margin_balance", [-1] * 61, "CNY"),
        ("security_turnover", [-1] * 61, "CNY"),
    ],
)
def test_invalid_price_unit_or_negative_balance_is_rejected(key, values, unit):
    with pytest.raises(ValueError):
        result(**{key: points(values, unit)})


def test_currency_mismatch_duplicate_dates_and_short_calendar():
    with pytest.raises(ValueError, match="mismatched unit"):
        result(margin_balance=points([100] * 61, "shares"))
    p = points([100] * 61)
    with pytest.raises(ValueError, match="duplicate"):
        result(margin_balance=p + p[-1:])
    spec = FlowWindowSpec(end_date=DAYS[-1], sessions=DAYS[-5:], definition_version=METHOD)
    r = compute(
        FlowInputs(membership_ref="UNAVAILABLE", definition_version=METHOD, margin_balance=p),
        spec,
        "X",
    )
    assert metric(r, "MARGIN_CHANGE").value is None
    assert metric(r, "MARGIN_CHANGE", 20).value is None


def etf(shares, nav, factors):
    return result(
        etf_shares=points(shares, "shares"),
        etf_nav=points(nav, "CNY/share"),
        etf_split_factor=points(factors, "ratio"),
    )


def test_nav_rise_is_not_etf_subscription_and_share_change_uses_same_day_nav():
    r = etf([100] * 61, [1] * 60 + [D("1.1")], [1] * 61)
    assert metric(r, "ETF_CHANGE").value == 0
    assert metric(r, "ETF_ESTIMATE").value == 0
    r = etf([100] * 60 + [110], [1] * 60 + [D("1.1")], [1] * 61)
    assert metric(r, "ETF_CHANGE").value == 10
    assert metric(r, "ETF_ESTIMATE").value == 11


def test_unknown_split_blocks_calculation_and_known_split_is_not_inflow():
    r = result(
        etf_shares=points([100] * 60 + [200], "shares"), etf_nav=points([1] * 61, "CNY/share")
    )
    assert metric(r, "ETF_ESTIMATE").value is None
    r = etf([100] * 60 + [200], [1] * 60 + [D(".5")], [1] * 60 + [2])
    assert metric(r, "ETF_CHANGE").value == 0
    assert metric(r, "ETF_ESTIMATE").value == 0
    # Subscription before a split is converted to end-date share units.
    r = etf([100] * 59 + [110, 220], [1] * 60 + [D(".5")], [1] * 60 + [2])
    assert metric(r, "ETF_CHANGE").value == 20
    assert metric(r, "ETF_ESTIMATE").value == 10


def test_industry_denominator_breadth_concentration_and_overlapping_concepts():
    members = [
        dict(security_id="A", turnover=100, reference=10, close=11),
        dict(security_id="B", turnover=200, reference=None, close=None),
    ]
    r = industry_observation(members, ["A", "B"], 1000, same_scope=True)
    assert r == dict(share=D(".3"), breadth=1, concentration=1, eligible=1, excluded=1)
    assert industry_observation(members[:1], ["A", "B"], 1000, same_scope=True)["share"] is None
    assert industry_observation(members, ["A", "B"], 1000, same_scope=False)["share"] is None
    assert distinct_turnover([{"A": 100, "B": 200}, {"B": 200, "C": 300}]) == 600
    with pytest.raises(ValueError, match="conflicting"):
        distinct_turnover([{"A": 100}, {"A": 200}])
    with pytest.raises(ValueError, match="duplicate"):
        industry_observation(members + members[:1], ["A", "B"], 1000, same_scope=True)


def test_unverified_industry_membership_cannot_authorize_share():
    r = result(industry_turnover=points([300] * 61), market_turnover=points([1000] * 61))
    assert metric(r, "INDUSTRY_SHARE").value is None


def test_negative_institution_disclaimer_is_allowed_but_asserted_flow_is_rejected():
    from marketpulse.quant.decision import agents
    from tests.unit.quant.decision.test_directional_conclusion import directional

    def proposal(alternative):
        return agents.FlowAnalysis.model_validate(
            directional().model_dump(mode="json")
            | dict(
                flow_assessments=[
                    dict(
                        window=n,
                        summary="融资余额减少。",
                        persistence="期间变化有反复。",
                        breadth="行业广度缺项。",
                        divergence="价与融资同向。",
                        alternatives=[alternative],
                        evidence_ids=[f"EV-FLOW-{n}-MARGIN_CHANGE"],
                    )
                    for n in (5, 20, 60)
                ]
            )
        )

    context = dict(
        subject=dict(security_id="688012.SH"),
        qualitative_only=True,
        evidence=[
            dict(evidence_id=i)
            for i in ["E1", *[f"EV-FLOW-{n}-MARGIN_CHANGE" for n in (5, 20, 60)]]
        ],
    )
    agents.guard_analysis(proposal("融资变化不能等同于全市场净流入或机构意图。"), context)
    with pytest.raises(ValueError, match="机构意图"):
        agents.guard_analysis(proposal("全市场净流入，机构加仓。"), context)
    agents.guard_qualitative("相对沪深三百表现较弱。")


def test_new_flow_protocol_requires_vendor_channel_and_rejects_causal_summary():
    from marketpulse.quant.decision import agents
    from tests.unit.quant.decision.test_directional_conclusion import directional

    assessments = [
        dict(
            window=n,
            summary="融资余额减少，供应商大单估算方向不同。",
            persistence="期间有反复。",
            breadth="行业广度缺项。",
            divergence="价格与融资同向。",
            alternatives=["供应商按订单大小分类，不识别机构身份。"],
            evidence_ids=[f"EV-FLOW-{n}-MARGIN_CHANGE", f"EV-FLOW-{n}-VENDOR_NET"],
        )
        for n in (5, 20, 60)
    ]
    value = directional().model_dump(mode="json") | dict(flow_assessments=assessments)
    context = dict(
        subject=dict(security_id="688012.SH"),
        qualitative_only=True,
        flow_assessment_protocol=agents.FLOW_PROTOCOL,
        evidence=[
            dict(evidence_id=i)
            for i in ["E1", *[i for a in assessments for i in a["evidence_ids"]]]
        ],
    )
    agents.guard_analysis(agents.FlowAnalysis.model_validate(value), context)
    with pytest.raises(ValueError, match="同窗观察"):
        agents.guard_analysis(
            agents.FlowAnalysis.model_validate(
                value | dict(summary="倾向看空，资金压制经营增长。")
            ),
            context,
        )
    assessments[0]["evidence_ids"] = ["EV-FLOW-5-MARGIN_CHANGE"]
    with pytest.raises(ValueError, match="大单估算可用"):
        agents.guard_analysis(agents.FlowAnalysis.model_validate(value), context)
