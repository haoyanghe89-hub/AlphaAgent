"""Reader judgments use recorded, comparable inputs and preserve unknowns."""

import copy
from datetime import date, timedelta

import pytest

from marketpulse.quant.domain import DcfAssumptions
from marketpulse.quant.live.calculate import dcf_result
from marketpulse.quant.live.overview import build_overview


def report():
    days = [(date(2025, 10, 9) + timedelta(days=i)).isoformat() for i in range(366)]
    closes = [70] * 366
    closes[0], closes[1], closes[-2], closes[-1] = 1, 60, 100, 90
    return dict(
        subject=dict(name="测试公司", industry="制造业"),
        data_dates=dict(price=days[-1], financial="20260630", valuation=days[-1]),
        kline=dict(x=days, ohlc=[[p, p, p, p] for p in closes], ma20=[95] * 366, ma60=[96] * 366),
        quote=dict(price=999),
        metrics=[
            dict(name="营收累计同比", value=3.46, source="lrb", unit="%"),
            dict(name="归母净利累计同比", value=1.66, source="lrb", unit="%"),
            dict(name="自算 PE-TTM", value=10, source="valuation", unit="倍"),
        ],
        valuation=dict(x=list(days), series=[dict(name="自算 PE-TTM", data=[20] * 365 + [10])]),
        financial=dict(
            x=["20250630", "20260630"], series=[dict(name="营收同比%", data=[10.99, 4.49])]
        ),
        dcf=dict(status="NOT_APPLICABLE"),
        risks=[],
    )


def test_price_location_is_range_position_not_percentile_or_live_quote():
    data = report()
    before = copy.deepcopy(data)
    o = build_overview(data)
    p = o["price"]
    assert p["start"] == "2025-10-10"
    assert (p["low"], p["high"], p["close"]) == (60, 100, 90)
    assert p["range_position"] == 75  # Most daily observations are at 70.
    assert p["from_high"] == -10
    assert p["position"] == "区间偏上"
    assert p["trend"] == "近期偏弱"
    assert "收盘" in p["proof"]["text"]
    assert "364" in p["proof"]["text"]
    assert data == before


def test_future_dates_never_enter_price_or_valuation_context():
    d = report()
    d["kline"]["x"].append("2026-10-10")
    d["kline"]["ohlc"].append([10000] * 4)
    d["kline"]["ma20"].append(1)
    d["kline"]["ma60"].append(1)
    d["valuation"]["x"].append("2026-10-10")
    d["valuation"]["series"][0]["data"].append(10000)
    o = build_overview(d)
    assert o["price"]["high"] == 100
    assert o["price"]["trend"] == "近期偏弱"
    assert o["valuation"]["end"] == "2026-10-09"


def test_short_history_and_flat_prices_cannot_be_called_yearly_high_or_low():
    d = report()
    for key in ("x", "ohlc", "ma20", "ma60"):
        d["kline"][key] = d["kline"][key][-20:]
    p = build_overview(d)["price"]
    assert p["status"] == "PARTIAL"
    assert p["position"] == "一年位置待判断"
    d = report()
    d["kline"]["ohlc"] = [[80] * 4] * 366
    p = build_overview(d)["price"]
    assert p["range_position"] is None
    assert p["position"] == "样本收盘价相同"


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan")])
def test_bad_latest_close_is_unknown_not_an_older_close(bad):
    d = report()
    d["kline"]["ohlc"][-1][1] = bad
    assert build_overview(d)["price"]["status"] == "UNAVAILABLE"


def test_unordered_or_mismatched_kline_is_rejected():
    d = report()
    d["kline"]["x"][10] = d["kline"]["x"][9]
    assert build_overview(d)["price"]["status"] == "UNAVAILABLE"
    d = report()
    d["kline"]["ohlc"].pop()
    assert build_overview(d)["price"]["status"] == "UNAVAILABLE"


def test_growth_uses_same_period_and_does_not_invent_causation():
    d = report()
    g = build_overview(d)["growth"]
    assert g["judgment"] == "收入、利润均增长"
    assert "利润增速低于收入" in g["meaning"]
    assert "不能直接判断原因" in g["meaning"]
    assert g["quarter_comparison"]["previous"] == 10.99
    d["metrics"][1]["value"] = -2
    assert build_overview(d)["growth"]["judgment"] == "收入增长、利润下降"
    d["metrics"][0]["value"] = None
    assert build_overview(d)["growth"]["status"] == "PARTIAL"


def test_pe_percentile_is_scoped_and_not_upgraded_to_undervaluation():
    v = build_overview(report())["valuation"]
    assert v["status"] == "AVAILABLE"
    assert v["percentile"] == pytest.approx(100 / 366, abs=1e-5)
    assert v["judgment"] == "历史样本偏低，低估待确认"
    assert "最新查询版本" in v["proof"]["text"]
    assert "同行" in v["meaning"]
    d = report()
    d["valuation"]["series"][0]["data"][-1] = 11
    assert build_overview(d)["valuation"]["status"] == "UNAVAILABLE"
    assert build_overview({})["valuation"]["judgment"] == "贵便宜待判断"


def test_bank_dcf_and_missing_risk_data_stay_explicit():
    o = build_overview(report())
    assert o["dcf"]["status"] == "NOT_APPLICABLE"
    assert "per_share" not in o["dcf"]
    assert "风险" in o["risk"]["judgment"]
    assert "不能" in o["risk"]["meaning"]
    assert all("概率" not in w["condition"] for w in o["watch"])


def test_dcf_growth_stress_reuses_kernel_and_is_not_a_forecast():
    d = report()
    d["dcf"] = dcf_result(
        dict(basis=dict(fcff="100", cash="20", debt="10", minority="5", shares="10")),
        DcfAssumptions(g1="0", g2="0", g_term="0", discount=".1"),
    )
    dc = build_overview(d)["dcf"]
    assert dc["per_share"] == 100.5  # Independent perpetual-annuity checkpoint.
    rows = dc["growth_sensitivity"]
    assert rows[0]["g1"] < rows[1]["g1"] < rows[2]["g1"]
    assert rows[0]["per_share"] < 100.5 < rows[2]["per_share"]
    assert "压力测试" in dc["note"]
    assert "预测" in dc["note"]


def test_calculated_reader_facts_are_available_to_analyst_with_stable_existing_ids():
    from marketpulse.quant.live.agents import Analysis, validate_analysis
    from marketpulse.quant.live.evidence import facts

    evidence = facts(report())
    assert evidence[0]["locator"] == "metrics/营收累计同比"
    p = next(e for e in evidence if e["locator"] == "reader/price")
    assert "区间位置" in p["text"] and "不是" in p["text"]
    validate_analysis(
        Analysis(
            claims=[
                dict(text="价格观察：{{" + p["id"] + "}}", evidence_ids=[p["id"]]),
                dict(text="收入事实：{{F0}}", evidence_ids=["F0"]),
            ]
        ),
        evidence,
    )


def test_dcf_evidence_uses_readable_exact_rate_tokens_for_strict_number_bindings():
    from marketpulse.quant.live.agents import Analysis, validate_analysis
    from marketpulse.quant.live.evidence import facts

    d = report()
    d["dcf"] = dcf_result(
        dict(basis=dict(fcff="100", cash="20", debt="10", minority="5", shares="10")),
        DcfAssumptions(g1=".12"),
    )
    evidence = facts(d)
    e = next(e for e in evidence if e["locator"] == "reader/dcf")
    assert "8%" in e["text"] and "12%" in e["text"] and "16%" in e["text"]
    analysis = Analysis(
        claims=[
            dict(text="增长假设分别为8%、12%、16%，不是预测。", evidence_ids=[e["id"]]),
            dict(text="经营事实{{F0}}。", evidence_ids=["F0"]),
        ]
    )
    validate_analysis(analysis, evidence)
