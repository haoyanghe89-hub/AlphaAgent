"""Five prototype dimensions with fixed arithmetic checkpoints and honest gaps."""

import copy
from datetime import date, timedelta

from marketpulse.quant.comparison_reader import present


def branches():
    start = date(2024, 1, 2)
    prices = [
        dict(date=str(start + timedelta(days=i)), close=100 + i / 364 * 20) for i in range(365)
    ]
    data = dict(
        subject=dict(company_type="4", industry="test"),
        revenue_basis="revenue",
        data_dates=dict(financial="20250630", price=prices[-1]["date"]),
        metrics=[
            dict(name="营收累计同比", value=5),
            dict(name="归母净利累计同比", value=3),
            dict(name="ROE（累计 / 平均归母权益）", value=10),
        ],
        financial=dict(x=["20240630", "20250630"], series=[dict(name="营收同比%", data=[10, 20])]),
        calculation_inputs=dict(prices=prices),
    )
    return [dict(data=copy.deepcopy(data), result_ref=key) for key in ("left", "right")]


def test_five_dimensions_use_single_quarter_and_dated_price_window():
    children = branches()
    original = copy.deepcopy(children)
    view = present(children)
    rows = {r["key"]: r for r in view["dimensions"]}
    assert len(rows) == 5
    assert rows["growth"]["cells"][0]["value"] == 20  # single quarter, not cumulative 5
    assert abs(rows["momentum"]["cells"][0]["value"] - 20) < 1e-10  # 120 / 100 - 1
    assert rows["momentum"]["alignment"] == "ALIGNED"
    assert rows["profitability"]["cells"] == [None, None]  # cumulative ROE cannot replace TTM
    assert rows["quality"]["cells"] == [None, None]
    assert children == original and present(children) == view


def test_different_quarter_and_sparse_window_do_not_force_comparison():
    children = branches()
    children[1]["data"]["data_dates"]["financial"] = "20250331"
    children[1]["data"]["calculation_inputs"]["prices"] = children[1]["data"]["calculation_inputs"][
        "prices"
    ][-20:]
    rows = {r["key"]: r for r in present(children)["dimensions"]}
    assert rows["growth"]["alignment"] == "UNAVAILABLE"
    assert rows["momentum"]["alignment"] == "UNAVAILABLE"
    assert rows["momentum"]["difference"] is None


def test_reserved_metrics_accept_complete_contract_and_reject_wrong_basis():
    children = branches()
    for ch in children:
        ch["data"]["sources"] = [dict(key="financial")]
        ch["data"]["comparison_metrics"] = dict(
            profitability=dict(
                basis="TTM_PARENT_ROE",
                value="20",
                unit="%",
                start="2023-07-01",
                end="2024-06-30",
                definition="TTM归母净利 / 期初期末平均归母权益",
                source=["financial"],
            )
        )
    row = present(children)["dimensions"][1]
    assert row["alignment"] == "ALIGNED" and row["cells"][0]["value"] == 20
    children[1]["data"]["comparison_metrics"]["profitability"]["basis"] = "YTD_PARENT_CASH_RATIO"
    assert present(children)["dimensions"][1]["alignment"] == "UNAVAILABLE"
