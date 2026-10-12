"""Worker-only composition of pure calculations over verified frozen input."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from ..contracts import canonical, digest
from ..domain import (
    ArtifactBundle,
    ComputeInput,
    FinancialFact,
    MetricValue,
    PricePoint,
    PriceSeries,
)
from .financials import yoy_growth
from .indicators import compute_indicators
from .metrics import compute_metrics, metric
from .valuation import valuation_check


def schema_hash() -> str:
    return digest(
        {
            model.__name__: model.model_json_schema()
            for model in (ComputeInput, MetricValue, ArtifactBundle)
        }
    )


def execute(request: ComputeInput) -> ArtifactBundle:
    snapshots = sorted(request.snapshots, key=lambda s: s.snapshot_id)
    desired = request.spec.price_adjustment or (
        "raw" if request.spec.return_basis == "price" else "qfq"
    )
    daily = [
        s
        for s in snapshots
        if s.result.request.dataset == "daily"
        and s.result.request.adjustment == desired
        and request.instrument_id in s.result.request.instruments
    ]
    if len(daily) != 1:
        raise ValueError("exactly one pinned price snapshot per computation")
    snapshot = daily[0]
    rows = sorted(json.loads(snapshot.result.records_json), key=lambda r: r["date"])
    if len({r["date"] for r in rows}) != len(rows) or any(
        r.get("instrument_id") != request.instrument_id for r in rows
    ):
        raise ValueError("duplicate sessions or mixed instruments")
    calendars = [s for s in snapshots if s.result.request.dataset == "calendar"]
    if len(calendars) > 1:
        raise ValueError("ambiguous pinned trading calendar")
    if len(calendars) == 1:
        days = {
            r["date"]
            for r in json.loads(calendars[0].result.records_json)
            if r["is_session"]
            and snapshot.result.request.start.isoformat()
            <= r["date"]
            <= snapshot.result.request.end.isoformat()
        }
        by_day = {r["date"]: r for r in rows}
        if set(by_day) - days:
            raise ValueError("price on unverified/closed session")
        # An absent session is a missing price, not a silently shortened series.
        rows = [by_day.get(day, {"date": day, "close": None}) for day in sorted(days)]
    series = PriceSeries(
        instrument_id=request.instrument_id,
        currency="CNY",
        adjustment=desired,
        asof=request.asof,
        points=tuple(
            PricePoint(
                session_date=r["date"],
                close=r.get("close"),
                suspended=r.get("trading_status") is False,
                provisional=r.get("provisional", False),
            )
            for r in rows
        ),
    )
    if dict(snapshot.result.units).get("close") != "CNY/share":
        raise ValueError("price unit mismatch")
    values = list(compute_metrics(series=series, spec=request.spec))
    for name in ("pe_ttm", "pb_mrq"):
        value = rows[-1].get(name)
        values.append(
            metric(
                series,
                f"disclosed_{name}",
                Decimal(str(value)) if value is not None else None,
                "ratio",
                "frozen_provider_disclosed;not_recomputed;raw_daily_close",
                reason="missing_disclosed_value" if value is None else None,
                row_keys=(rows[-1]["date"], snapshot.snapshot_id),
            )
        )
        values[-1] = values[-1].model_copy(
            update={
                "start": series.points[-1].session_date,
                "end": series.points[-1].session_date,
                "sample_count": 1,
            }
        )
    if "indicators" in request.spec.metrics:
        values.extend(compute_indicators(series=series, spec=request.spec))
    facts = []
    for s in snapshots:
        if s.result.request.dataset not in {"financial", "valuation"}:
            continue
        for row in json.loads(s.result.records_json):
            if row.get("instrument_id") != request.instrument_id:
                continue
            period = row.get("period_end") or row.get("date")
            available = row.get("available_at")
            # Only explicit provenance is admitted to computed valuation inputs.
            for name in (
                "parent_net_profit",
                "parent_equity",
                "parent_equity_begin",
                "total_shares",
                "net_profit",
                "revenue",
            ):
                if name in row:
                    facts.append(
                        FinancialFact(
                            metric=name,
                            period_end=period,
                            period_start=row.get("period_start"),
                            available_at=available,
                            value=row[name],
                            unit=dict(s.result.units).get(name, "unknown"),
                            share_basis=row.get("share_basis", "unknown"),
                            statement_basis=row.get("metric_basis", {}).get(
                                name, row.get("statement_basis", "unknown")
                            ),
                            period_basis=row.get("period_basis", "point"),
                            revision=row.get("revision", "unknown"),
                            source_hash=row.get("source_hash", s.semantic_hash),
                        )
                    )
            for name in ("net_profit", "revenue", "roe", "pe_ttm", "pb_mrq"):
                if name not in row:
                    continue
                if (
                    s.result.request.dataset == "valuation"
                    and period != series.points[-1].session_date.isoformat()
                ):
                    continue
                visible = available and datetime.fromisoformat(available) <= request.asof
                value = Decimal(str(row[name])) if row[name] is not None and visible else None
                values.append(
                    metric(
                        series,
                        f"disclosed_{name}",
                        value,
                        dict(s.result.units).get(name, "unknown"),
                        f"provider_disclosed;not_recomputed;period={period};"
                        f"revision={row.get('revision', 'unknown')}",
                        reason="missing_disclosed_value"
                        if row[name] is None
                        else "missing_publication_provenance"
                        if not visible
                        else None,
                        row_keys=(period, s.snapshot_id),
                    )
                )
                if s.result.request.dataset == "financial":
                    values[-1] = values[-1].model_copy(
                        update={
                            "start": date.fromisoformat(row.get("period_start") or period),
                            "end": date.fromisoformat(period),
                            "sample_count": 1,
                        }
                    )
                elif s.result.request.normalizer_version == "3-parent-2":
                    values[-1] = values[-1].model_copy(
                        update={
                            "start": date.fromisoformat(period),
                            "end": date.fromisoformat(period),
                            "sample_count": 1,
                        }
                    )
    raw = [
        s
        for s in snapshots
        if s.result.request.dataset == "daily" and s.result.request.adjustment == "raw"
    ]
    raw_series = series
    if desired != "raw" and len(raw) == 1:
        raw_rows = sorted(json.loads(raw[0].result.records_json), key=lambda r: r["date"])
        raw_series = PriceSeries(
            instrument_id=request.instrument_id,
            currency="CNY",
            adjustment="raw",
            asof=request.asof,
            points=tuple(
                PricePoint(session_date=r["date"], close=r.get("close")) for r in raw_rows
            ),
        )
    values.extend(
        v
        for v in valuation_check(series=raw_series, financials=tuple(facts), asof=request.asof)
        if v.name in request.spec.metrics
    )
    # Supplier ratios are observations, never substitutes for missing fundamentals.
    # A difference is preserved, not averaged; method/PIT gaps still prevent VERIFIED.
    for computed, disclosed in (("pe", "disclosed_pe_ttm"), ("pb", "disclosed_pb_mrq")):
        computed_cell = next((v for v in values if v.name == computed), None)
        for observed in [v for v in values if v.name == disclosed and v.value is not None]:
            if not computed_cell or computed_cell.value is None or observed.value == 0:
                continue
            from decimal import localcontext

            with localcontext() as context:
                context.prec = 40
                difference = computed_cell.value / observed.value - 1
            values.append(
                metric(
                    series,
                    f"{computed}_disclosed_relative_difference",
                    difference,
                    "fraction",
                    "independent_parent_aggregate_ratio/provider_disclosed_ratio-1;"
                    "method_difference_not_averaged;not_ordinary_eps_comparison",
                    row_keys=observed.row_keys,
                )
            )
            values[-1] = values[-1].model_copy(
                update={
                    "start": computed_cell.start,
                    "end": computed_cell.end,
                    "sample_count": computed_cell.sample_count,
                }
            )
    # Disclosed fundamentals remain observations, never inferred growth from missing periods.
    if "trend" in request.spec.metrics:
        for name in ("net_profit", "revenue"):
            available_facts = [
                f for f in facts if f.available_at and f.available_at <= request.asof
            ]
            metric_name = (
                "parent_net_profit"
                if name == "net_profit"
                and any(s.result.request.normalizer_version == "3-parent-2" for s in snapshots)
                else name
            )
            value, reason = yoy_growth(available_facts, metric_name)
            values.append(
                metric(
                    series,
                    f"{name}_growth",
                    value,
                    "fraction",
                    f"comparable_report_period_yoy;metric={metric_name};no_annualization",
                    reason=reason,
                )
            )
            if any(s.result.request.normalizer_version == "3-parent-2" for s in snapshots):
                periods = [f for f in available_facts if f.metric == metric_name]
                if periods:
                    current = max(periods, key=lambda f: f.period_end)
                    values[-1] = values[-1].model_copy(
                        update={
                            "start": current.period_start or current.period_end,
                            "end": current.period_end,
                            "sample_count": 1,
                        }
                    )
    if request.spec.include_exhibits:
        from .exhibits import kline_cells, price_cells

        for price_snapshot in snapshots:
            r = price_snapshot.result.request
            if r.dataset != "daily" or request.instrument_id not in r.instruments:
                continue
            chart_rows = sorted(
                json.loads(price_snapshot.result.records_json), key=lambda x: x["date"]
            )
            if calendars:
                chart_days = {
                    x["date"]
                    for x in json.loads(calendars[0].result.records_json)
                    if x["is_session"] and r.start.isoformat() <= x["date"] <= r.end.isoformat()
                }
                by_day = {x["date"]: x for x in chart_rows}
                if set(by_day) - chart_days:
                    raise ValueError("OHLC on unverified/closed session")
                chart_rows = [
                    by_day.get(day, {"date": day, "close": None}) for day in sorted(chart_days)
                ]
            chart_series = PriceSeries(
                instrument_id=request.instrument_id,
                currency="CNY",
                adjustment=r.adjustment,
                asof=request.asof,
                points=tuple(
                    PricePoint(
                        session_date=x["date"],
                        close=x.get("close"),
                        suspended=x.get("trading_status") is False,
                        provisional=x.get("provisional", False),
                    )
                    for x in chart_rows
                ),
            )
            if dict(price_snapshot.result.units).get("close") != "CNY/share":
                raise ValueError("exhibit price unit mismatch")
            values.extend(price_cells(chart_series, request.spec))
            values.extend(kline_cells(chart_series, price_snapshot))
        for fact in facts:
            if (
                fact.metric not in {"parent_net_profit", "revenue"}
                or not fact.available_at
                or fact.available_at > request.asof
            ):
                continue
            available_facts = [
                f
                for f in facts
                if f.available_at
                and f.available_at <= request.asof
                and f.period_end <= fact.period_end
            ]
            growth, missing = yoy_growth(available_facts, fact.metric)
            values.append(
                metric(
                    series,
                    "chart_" + fact.metric + "_growth",
                    growth,
                    "fraction",
                    f"frozen_report_period;basis={fact.statement_basis};period={fact.period_basis};no_annualization",
                    reason=missing,
                    count=1,
                    row_keys=(fact.period_end.isoformat(), fact.source_hash),
                ).model_copy(
                    update={"start": fact.period_start or fact.period_end, "end": fact.period_end}
                )
            )
    from .dcf import dcf_cells

    values.extend(dcf_cells(request))
    # Ordinals are stable within this frozen output; duplicate cell identities are rejected.
    if len({(v.name, v.row_keys) for v in values}) != len(values):
        raise ValueError("duplicate metric cells")
    output = canonical([v.model_dump(mode="json") for v in values])
    output_hash = hashlib.sha256(output.encode()).hexdigest()
    manifest = {
        "schema": "quant-artifact-v1",
        "schema_hash": schema_hash(),
        "financial_inputs": [f.model_dump(mode="json") for f in facts],
        "inputs": [
            {
                "snapshot_id": s.snapshot_id,
                "semantic_hash": s.semantic_hash,
                "manifest_ref": s.manifest_ref,
                "partitions": [p.model_dump(mode="json") for p in s.partitions],
                "request": s.result.request.model_dump(mode="json"),
                "normalizer": s.result.request.normalizer_version,
                "units": s.result.units,
                "rights": s.result.rights.model_dump(mode="json"),
                "pit": s.result.pit,
                "stale": s.stale,
                "provider": s.result.provider,
                "upstream": s.result.upstream,
                "family": s.result.source_family,
                "lineage": s.result.lineage_status,
                "quality": s.result.quality_flags,
            }
            for s in snapshots
        ],
        "instrument": json.loads(request.instrument_definition_json),
        "environment": json.loads(request.environment_json),
        "spec": request.spec.model_dump(mode="json"),
        "asof": request.asof.isoformat(),
        "output_hash": output_hash,
        "seed": request.spec.seed,
        "sampling": {
            "start": series.points[0].session_date.isoformat(),
            "end": series.points[-1].session_date.isoformat(),
            "count": len(series.points),
            "complete_sessions_only": True,
        },
        "benchmark_snapshot": request.spec.benchmark_snapshot_id,
        "risk_free_snapshot": request.spec.risk_free_snapshot_id,
        "fx_snapshot": request.spec.fx_snapshot_id,
    }
    manifest_json = canonical(manifest)
    manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
    return ArtifactBundle(
        artifact_id=f"qart_{manifest_hash}",
        manifest_hash=manifest_hash,
        manifest_json=manifest_json,
        output_hash=output_hash,
        output_json=output,
        input_snapshot_ids=tuple(s.snapshot_id for s in snapshots),
        values=tuple(values),
    )
