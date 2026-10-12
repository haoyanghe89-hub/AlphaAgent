"""Independent FCFF scenario calculation, never imported from prototype code."""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

from ..domain import DcfAssumptions, MetricValue

FIELDS = (
    "profit_before_tax",
    "income_tax",
    "interest_expense",
    "interest_income",
    "investment_income",
    "fair_value_gain",
    "nonoperating_income",
    "nonoperating_expense",
    "depreciation",
    "capex",
    "inventory_decrease",
    "receivables_increase",
    "payables_increase",
    "cash",
    "short_debt",
    "long_debt",
    "bonds",
    "leases",
    "current_debt",
    "minority_equity",
    "issued_shares",
    "treasury_shares",
)
METHOD = (
    "FCFF=adjusted_EBIT*(1-effective_tax)+DA-capex-delta_NCWC;5+5_years;terminal_at_t10;"
    "equity=EV+cash-book_debt-book_minorities;shares=issued-company_buyback;scenario_not_target"
)


def present_value(flows, terminal, discount):
    with localcontext() as ctx:
        ctx.prec = 40
        return sum(f / (1 + discount) ** t for t, f in enumerate(flows, 1)) + terminal / (
            1 + discount
        ) ** len(flows)


def fcff_basis(inputs):
    with localcontext() as ctx:
        ctx.prec = 40
        try:
            x = {k: Decimal(str(inputs[k])) for k in FIELDS}
        except (KeyError, InvalidOperation, TypeError) as error:
            raise ValueError("missing or invalid DCF input") from error
        if not all(v.is_finite() for v in x.values()):
            raise ValueError("nonfinite DCF input")
        if x["profit_before_tax"] <= 0 or not 0 <= x["income_tax"] < x["profit_before_tax"]:
            raise ValueError("DCF requires positive PBT and meaningful effective tax")
        if any(
            x[k] < 0
            for k in (
                "depreciation",
                "capex",
                "cash",
                "short_debt",
                "long_debt",
                "bonds",
                "leases",
                "current_debt",
                "minority_equity",
                "treasury_shares",
            )
        ):
            raise ValueError("invalid DCF bridge/input sign")
        shares = x["issued_shares"] - x["treasury_shares"]
        if shares <= 0 or shares != shares.to_integral_value():
            raise ValueError("DCF share denominator invalid")
        ebit = (
            x["profit_before_tax"]
            + x["interest_expense"]
            - x["interest_income"]
            - x["investment_income"]
            - x["fair_value_gain"]
            - x["nonoperating_income"]
            + x["nonoperating_expense"]
        )
        tax = x["income_tax"] / x["profit_before_tax"]
        wc = -x["inventory_decrease"] + x["receivables_increase"] - x["payables_increase"]
        base = ebit * (1 - tax) + x["depreciation"] - x["capex"] - wc
        if base <= 0:
            raise ValueError("positive normalized FCFF required for perpetuity scenario")
        debt = sum(x[k] for k in ("short_debt", "long_debt", "bonds", "leases", "current_debt"))
        return dict(
            fcff=base,
            adjusted_ebit=ebit,
            effective_tax=tax,
            delta_ncwc=wc,
            debt=debt,
            cash=x["cash"],
            minority=x["minority_equity"],
            shares=shares,
        )


def scenario(basis, assumptions):
    with localcontext() as ctx:
        ctx.prec = 40
        flow, flows = basis["fcff"], []
        for t in range(1, 11):
            flow *= 1 + (assumptions.g1 if t <= 5 else assumptions.g2)
            flows.append(flow)
        terminal = (
            flows[-1] * (1 + assumptions.g_term) / (assumptions.discount - assumptions.g_term)
        )
        ev = present_value(flows, terminal, assumptions.discount)
        equity = ev + basis["cash"] - basis["debt"] - basis["minority"]
        return dict(
            enterprise=ev,
            equity=equity,
            per_share=equity / basis["shares"],
            terminal=terminal,
            terminal_pv=terminal / (1 + assumptions.discount) ** 10,
            forecast=flows,
        )


def dcf_cells(request):
    """Require a single explicitly reviewed annual industrial statement snapshot."""
    if request.spec.dcf is None:
        return []
    snapshots = [
        s for s in request.snapshots if s.result.request.normalizer_version == "dcf-official-v1"
    ]
    result = []

    def cell(name, value, unit, definition, row_keys=(), reason=None, period=None):
        end = period or request.asof.date()
        result.append(
            MetricValue(
                name=name,
                value=value,
                unit=unit,
                definition=definition,
                sample_count=1 if value is not None else 0,
                instrument_id=request.instrument_id,
                start=end,
                end=end,
                row_keys=row_keys,
                missing_reason=reason,
            )
        )

    if len(snapshots) != 1:
        cell(
            "dcf_per_share",
            None,
            "CNY/share",
            METHOD,
            reason="missing_or_ambiguous_official_DCF_inputs",
        )
        return result
    s = snapshots[0]
    records = json.loads(s.result.records_json)
    if len(records) != 1:
        raise ValueError("one annual DCF input row required")
    r = records[0]
    if (
        r.get("instrument_id") != request.instrument_id
        or r.get("model_scope") != "industrial_consolidated_proxy"
    ):
        raise ValueError("DCF model scope mismatch; banks require a separate model")
    period = date.fromisoformat(r["period_end"])
    if (
        r.get("period_start") != f"{period.year}-01-01"
        or period.month != 12
        or period.day != 31
        or period > request.asof.date()
        or datetime.fromisoformat(r["available_at"]) > request.asof
    ):
        raise ValueError("DCF annual/publication period mismatch")
    units = dict(s.result.units)
    if any(units.get(k) != ("share" if k.endswith("shares") else "CNY") for k in FIELDS):
        raise ValueError("DCF unit mismatch")
    if (
        not r.get("source_url", "").startswith("https://")
        or len(r.get("document_sha256", "")) != 64
    ):
        raise ValueError("DCF original document provenance missing")
    keys = (period.isoformat(), s.snapshot_id)
    try:
        basis = fcff_basis(r)
    except (KeyError, ValueError):
        cell(
            "dcf_per_share",
            None,
            "CNY/share",
            METHOD,
            keys,
            "incomplete_or_inapplicable_DCF_basis",
            period,
        )
        return result
    for k in FIELDS:
        cell(
            "dcf_input_" + k,
            Decimal(str(r[k])),
            units[k],
            f"annual_consolidated;PDF_page={r['source_pages'][k]};source={r['source_url']};sha256={r['document_sha256']}",
            keys,
            period=period,
        )
    for name, value in basis.items():
        cell(
            "dcf_" + name,
            value,
            "fraction" if name == "effective_tax" else "share" if name == "shares" else "CNY",
            METHOD,
            keys,
            period=period,
        )
    calc = scenario(basis, request.spec.dcf)
    for name in ("enterprise", "equity", "per_share", "terminal", "terminal_pv"):
        cell(
            "dcf_" + name,
            calc[name],
            "CNY/share" if name == "per_share" else "CNY",
            METHOD,
            keys,
            period=period,
        )
    raw_prices = [
        s
        for s in request.snapshots
        if s.result.request.dataset == "daily" and s.result.request.adjustment == "raw"
    ]
    if len(raw_prices) == 1:
        raw = raw_prices[0]
        last = max(json.loads(raw.result.records_json), key=lambda row: row["date"])
        reference = Decimal(str(last["close"])) if last.get("close") is not None else None
        if reference is not None and reference > 0:
            with localcontext() as ctx:
                ctx.prec = 40
                difference = calc["per_share"] / reference - 1
            cell(
                "dcf_reference_price",
                reference,
                "CNY/share",
                "raw_frozen_close;reference_date=" + last["date"],
                (last["date"], raw.snapshot_id),
                period=date.fromisoformat(last["date"]),
            )
            cell(
                "dcf_reference_difference",
                difference,
                "fraction",
                "scenario/reference_price-1;different_dates;not_expected_return",
                keys,
                period=period,
            )
    for t, flow in enumerate(calc["forecast"], 1):
        cell("chart_dcf_forecast", flow, "CNY", METHOD, (str(t), s.snapshot_id), period=period)
    for dg in ("-0.04", "-0.02", "0", "0.02", "0.04"):
        for dr in ("-0.02", "-0.01", "0", "0.01", "0.02"):
            with localcontext() as ctx:
                ctx.prec = 40
                g, rate = request.spec.dcf.g1 + Decimal(dg), request.spec.dcf.discount + Decimal(dr)
            value, reason = None, None
            try:
                a = DcfAssumptions(**{**request.spec.dcf.model_dump(), "g1": g, "discount": rate})
                value = scenario(basis, a)["per_share"]
            except ValueError:
                reason = "invalid_sensitivity_assumptions"
            cell(
                "chart_dcf_sensitivity",
                value,
                "CNY/share",
                METHOD,
                (str(g), str(rate), s.snapshot_id),
                reason,
                period,
            )
    return result
