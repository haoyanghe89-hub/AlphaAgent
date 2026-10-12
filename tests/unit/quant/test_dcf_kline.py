"""Hand samples, independently published methods, and missing-data boundaries."""

from datetime import date, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from marketpulse.quant.compute.dcf import FIELDS, fcff_basis, present_value, scenario
from marketpulse.quant.compute.exhibits import kline_cells
from marketpulse.quant.contracts import canonical
from marketpulse.quant.domain import DcfAssumptions, PricePoint, PriceSeries
from marketpulse.quant.evidence import build_computation_evidence, computation_evidence_cells
from tests.unit.quant.test_contracts_data import ID
from tests.unit.quant.test_reproduction_n import artifact


def inputs(**changes):
    return {
        **dict.fromkeys(FIELDS, "0"),
        "profit_before_tax": "100",
        "income_tax": "20",
        "depreciation": "10",
        "capex": "20",
        "receivables_increase": "5",
        "payables_increase": "3",
        "cash": "50",
        "long_debt": "20",
        "minority_equity": "10",
        "issued_shares": "12",
        "treasury_shares": "2",
        **changes,
    }


def test_hand_fcff_bridge_terminal_and_precision():
    # EBIT 100, tax 20%, DA 10, capex 20, WC +2 => 68.
    b = fcff_basis(inputs())
    assert b["fcff"] == 68 and b["shares"] == 10 and b["delta_ncwc"] == 2
    a = DcfAssumptions(g1="0", g2="0", g_term="0", discount="0.1")
    with localcontext() as ctx:
        ctx.prec = 6  # Caller rounding must not leak into the worker.
        r = scenario(b, a)
    assert abs(r["enterprise"] - Decimal(680)) < Decimal("1e-30")
    assert abs(r["per_share"] - Decimal(70)) < Decimal("1e-30")
    assert r["forecast"] == [Decimal(68)] * 10
    assert r["terminal"] == 680
    assert r["terminal_pv"] < r["terminal"]


def test_two_growth_stages_have_exactly_five_periods_each():
    b = {**fcff_basis(inputs()), "fcff": Decimal(100)}
    r = scenario(b, DcfAssumptions(g1="0.1", g2="0", g_term="0", discount="0.1"))
    assert r["forecast"][0] == 110
    assert r["forecast"][4] == Decimal("161.051")
    assert r["forecast"][5:] == [Decimal("161.051")] * 5


def test_batch_evidence_preserves_hashes_and_checks_integrity_once(monkeypatch):
    import hashlib
    import json

    import marketpulse.quant.evidence as evidence
    from marketpulse.quant.execution.service import check_bundle

    base = artifact()
    values = tuple(base.values[0].model_copy(update={"row_keys": (str(i),)}) for i in range(100))
    output = canonical([v.model_dump(mode="json") for v in values])
    output_hash = hashlib.sha256(output.encode()).hexdigest()
    manifest = canonical(
        {
            **json.loads(base.manifest_json),
            "output_hash": output_hash,
            "asof": "2024-06-15T00:00:00Z",
        }
    )
    manifest_hash = hashlib.sha256(manifest.encode()).hexdigest()
    b = base.model_copy(
        update=dict(
            values=values,
            output_json=output,
            output_hash=output_hash,
            manifest_json=manifest,
            manifest_hash=manifest_hash,
            artifact_id="qart_" + manifest_hash,
        )
    )
    calls = []

    def checked(bundle):
        calls.append(bundle.artifact_id)
        check_bundle(bundle)

    monkeypatch.setattr(evidence, "check_bundle", checked)
    batch = computation_evidence_cells(b)
    assert len(batch) == 100 and len(calls) == 1
    assert batch[0] == build_computation_evidence(b, metric_path="/values/0")
    assert batch[-1] == build_computation_evidence(b, metric_path="/values/99")
    assert len({cell.cell_hash for cell in batch}) == 100
    with pytest.raises(ValueError, match="output hash"):
        computation_evidence_cells(b.model_copy(update={"output_json": "[]"}))


def test_authoritative_nyu_worked_example_crosscheck():
    # NYU Damodaran, DCF Problems/Solutions, Firm Valuation Question 8 C.
    # Published flows rounded to 2 decimals, PV = 27.50 million at 12.49%.
    pv = present_value(
        list(map(Decimal, ["0.84", "1.01", "1.21", "1.45", "1.74"])),
        Decimal("41.85"),
        Decimal("0.1249"),
    )
    assert abs(pv - Decimal("27.50")) < Decimal("0.01")
    # Ryder 1993 in the same primary source: EBIT 972, PBT 800, tax 670,
    # DA 580, capex 850, WC 92 -> -370. Published FCFF = 349.95 million.
    b = fcff_basis(
        inputs(
            profit_before_tax="800",
            income_tax="670",
            interest_expense="172",
            depreciation="580",
            capex="850",
            receivables_increase="-462",
            payables_increase="0",
            issued_shares="77",
            treasury_shares="0",
        )
    )
    assert b["fcff"] == Decimal("349.95")


@pytest.mark.parametrize(
    "change",
    [
        dict(issued_shares="2"),
        dict(issued_shares="0"),
        dict(income_tax="101"),
        dict(profit_before_tax="0"),
        dict(long_debt="-1"),
        dict(capex=None),
    ],
)
def test_dcf_invalid_basis_never_returns_confident_value(change):
    with pytest.raises((ValueError, TypeError)):
        fcff_basis(inputs(**change))


def test_scenario_growth_discount_and_share_changes_are_directional():
    b = fcff_basis(inputs())
    r = scenario(b, DcfAssumptions())
    assert scenario(b, DcfAssumptions(g1="0.12"))["per_share"] > r["per_share"]
    assert scenario(b, DcfAssumptions(discount="0.11"))["per_share"] < r["per_share"]
    b["shares"] *= 2
    with localcontext() as ctx:
        ctx.prec = 40
        assert scenario(b, DcfAssumptions())["per_share"] == r["per_share"] / 2
    with pytest.raises(ValueError):
        DcfAssumptions(discount="0.04", g_term="0.04")


def candles(missing=None):
    rows = [
        dict(
            date=(date(2024, 1, 1) + timedelta(days=i)).isoformat(),
            open=str(i + 1),
            close=str(i + 2),
            low=str(i + 1),
            high=str(i + 3),
            volume=100 * (i + 1),
        )
        for i in range(60)
    ]
    if missing is not None:
        rows[missing]["close"] = None
    series = PriceSeries(
        instrument_id=ID,
        currency="CNY",
        adjustment="qfq",
        points=tuple(PricePoint(session_date=r["date"], close=r["close"]) for r in rows),
    )
    snapshot = SimpleNamespace(
        snapshot_id="FROZEN",
        result=SimpleNamespace(
            records_json=canonical(rows),
            units=tuple(
                (k, "share" if k == "volume" else "CNY/share")
                for k in ("open", "high", "low", "close", "volume")
            ),
        ),
    )
    return rows, series, snapshot


def test_real_ohlc_order_volume_units_and_complete_ma_windows():
    rows, s, snapshot = candles()
    cells = kline_cells(s, snapshot)

    def column(name):
        return [v.value for v in cells if v.name == "chart_kline_" + name + "_qfq"]

    assert column("open")[0] == 1 and column("close")[0] == 2
    assert column("volume")[0] == 100  # Not 1 lot in the frozen artifact.
    assert column("ma20")[:19] == [None] * 19
    assert column("ma20")[19] == Decimal("11.5")
    assert column("ma60")[:59] == [None] * 59
    assert column("ma60")[59] == Decimal("31.5")
    assert all(c.row_keys[-1] == "FROZEN" for c in cells)


def test_missing_ohlc_breaks_ma_and_invalid_candles_reject():
    rows, s, snapshot = candles(40)
    cells = kline_cells(s, snapshot)
    assert (
        next(
            v
            for v in cells
            if v.name == "chart_kline_ma20_qfq" and v.end == s.points[-1].session_date
        ).value
        is None
    )
    rows[0]["high"] = "0.5"
    snapshot.result.records_json = canonical(rows)
    with pytest.raises(ValueError, match="OHLC ordering"):
        kline_cells(s, snapshot)
    snapshot.result.records_json = canonical(
        [{"date": r["date"], "close": r["close"]} for r in rows]
    )
    assert kline_cells(s, snapshot) == []
