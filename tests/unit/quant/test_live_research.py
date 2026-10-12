import copy
from decimal import Decimal

import pytest
from fastapi import HTTPException

from marketpulse.quant.domain import DcfAssumptions
from marketpulse.quant.live.api import digest, directory, load, public, write
from marketpulse.quant.live.calculate import (
    dcf_result,
    em_rows,
    field,
    performance,
    quarter,
    simulate,
    statement,
    ttm,
)


def reports():
    return {
        p: {"fields": {"profit": [str(v)]}}
        for p, v in {
            "20241231": 400,
            "20250331": 90,
            "20250630": 210,
            "20250930": 330,
            "20251231": 500,
            "20260331": 110,
            "20260630": 260,
        }.items()
    }


def test_ttm_uses_full_prior_year_plus_current_ytd_minus_prior_ytd():
    r = reports()
    assert ttm(r, "20260630", "profit") == 550
    assert ttm(r, "20251231", "profit") == 500
    assert quarter(r, "20260630", "profit") == 150
    assert quarter(r, "20251231", "profit") == 170
    del r["20250630"]
    with pytest.raises(KeyError):
        ttm(r, "20260630", "profit")


def test_ambiguous_duplicate_statement_names_fail_closed():
    with pytest.raises(ValueError, match="ambiguous"):
        field({"fields": {"利息收入": ["12", "45"]}}, "利息收入")
    assert field({"fields": {"profit": ["12", "12"]}}, "profit") == 12
    with pytest.raises(ValueError):
        field({"fields": {"profit": [None]}}, "profit")


def test_future_publication_and_wrong_currency_never_admitted():
    report = dict(publish_date="20260829", rCurrency="CNY", rType="合并期末", data=[])
    packet = {"result": {"data": {"report_list": {"20260630": report}}}}
    with pytest.raises(ValueError, match="no disclosed"):
        statement(packet, "2026-08-28")
    assert "20260630" in statement(packet, "2026-08-30")
    report["rCurrency"] = "USD"
    with pytest.raises(ValueError, match="CNY"):
        statement(packet, "2026-08-30")


def test_wrong_security_is_rejected_before_calculation():
    with pytest.raises(ValueError, match="wrong security"):
        em_rows({"result": {"data": [{"SECURITY_CODE": "600519"}]}})
    with pytest.raises(ValueError, match="no dataset"):
        em_rows({"success": True, "result": None})


def price_rows():
    from datetime import date, timedelta

    rows = []
    for i in range(180):
        price = 100 + i if i < 95 else 290 - i
        rows.append(
            dict(
                date=(date(2025, 1, 1) + timedelta(days=i)).isoformat(),
                open=float(price),
                close=float(price + 0.5),
            )
        )
    return rows


def test_simulation_has_lagged_signal_and_explicit_fee_cost():
    rows = price_rows()
    result = simulate(rows, rows, cost=0.0013)
    assert len(result["trades"]) == 2
    first = result["trades"][0]
    assert first["signal_date"] == rows[59]["date"]
    assert first["date"] == rows[60]["date"]
    assert first["price"] == rows[60]["open"]
    assert first["cost_nav"] == pytest.approx(0.0013)
    curves = result["chart"]["series"]
    assert curves[2]["data"][-1] > curves[0]["data"][-1] > curves[3]["data"][-1]
    assert result["annual"][0]["strategy"] == pytest.approx(result["metrics"]["total_ret"])
    assert result["benchmark_metrics"]["total_ret"] == pytest.approx(
        (rows[-1]["close"] / rows[59]["close"] - 1) * 100
    )
    changed = copy.deepcopy(rows)
    changed[60]["close"] *= 0.1
    # Changing today's close must not alter today's signal/execution side.
    assert simulate(changed, rows)["trades"][0]["side"] == first["side"]
    with pytest.raises(ValueError, match="benchmark"):
        simulate(rows, rows[:-1])
    with pytest.raises(ValueError):
        simulate(rows, rows, fast=60, slow=20)


def test_manual_performance_checkpoint():
    result = performance(
        [1, 1.2, 0.9, 1.08], ["2025-01-01", "2025-04-01", "2025-08-01", "2026-01-01"]
    )
    assert result["total_ret"] == pytest.approx(8)
    assert result["max_dd"] == pytest.approx(-25)


def test_dcf_independent_annuity_checkpoint_and_discount_sensitivity():
    # With zero growth and a perpetual constant FCFF, EV must equal FCFF / r.
    basis = dict(fcff="100", cash="20", debt="10", minority="5", shares="10")
    data = dict(basis=basis, period="2025-12-31")
    a = DcfAssumptions(g1=Decimal(0), g2=Decimal(0), g_term=Decimal(0), discount=Decimal("0.1"))
    result = dcf_result(data, a)
    assert result["per_share"] == pytest.approx(100.5)
    # A saved result is also a valid input to repeated scenario recalculation.
    repeated = dcf_result(result, a)
    assert repeated == result
    changed = dcf_result(repeated, a.model_copy(update={"g1": Decimal("0.05")}))
    assert changed["per_share"] > result["per_share"]
    assert result["assumptions"]["g1"] == "0"
    points = [r["per_share"] for r in result["sensitivity"]]
    assert all(a > b for a, b in zip(points[:-1], points[1:], strict=True))


def test_recorded_source_tampering_blocks_history_and_private_inputs_hidden(tmp_path):
    import hashlib

    run_id = "RUN-LIVE-" + "a" * 32
    path = directory(tmp_path, run_id)
    path.mkdir()
    raw = b'{"actual":12}'
    (path / "prices.raw").write_bytes(raw)
    data = dict(
        sources=[dict(key="prices", sha256=hashlib.sha256(raw).hexdigest())],
        calculation_inputs={"private": True},
    )
    write(path / "result.json", data)
    write(path / "state.json", dict(status="COMPLETED", result_sha256=digest(data)))
    state = load(tmp_path, run_id)
    assert "calculation_inputs" not in public(state)["data"]
    assert "calculation_inputs" in state["data"]
    (path / "prices.raw").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="source hash"):
        load(tmp_path, run_id)
    with pytest.raises(HTTPException):
        directory(tmp_path, "../../anything")
