"""Point-in-time candidate selection; no live queries or provider PE shortcuts."""

from datetime import date, datetime
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator

from ..compute.financials import ttm_profit, yoy_growth
from ..compute.valuation import valuation_check
from ..contracts import digest
from ..domain import (
    ExactDecimal,
    FinancialFact,
    FrozenModel,
    PITLevel,
    PricePoint,
    PriceSeries,
    decimal_text,
)


class VersionedFact(FrozenModel):
    fact: FinancialFact
    version_known_at: AwareDatetime
    pit_level: PITLevel
    locator: str = Field(min_length=1, max_length=1000)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def dates(self):
        if self.fact.available_at and self.version_known_at < self.fact.available_at:
            raise ValueError("version cannot be available before original disclosure")
        if self.fact.source_hash != self.source_hash or self.fact.revision == "unknown":
            raise ValueError("explicit original source/version binding required")
        return self


class Candidate(FrozenModel):
    instrument_id: str = Field(pattern=r"^ins_[a-zA-Z0-9_-]{8,}$")
    historical_member: bool | None
    membership_pit: PITLevel
    membership_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    price_session: date
    raw_close: ExactDecimal | None = Field(gt=0)
    price_known_at: AwareDatetime
    price_pit: PITLevel
    price_source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    financials: tuple[VersionedFact, ...] = Field(max_length=200)


def visible_versions(financials, cutoff):
    groups = {}
    for v in financials:
        if (
            v.version_known_at > cutoff
            or not v.fact.available_at
            or v.fact.available_at > cutoff
            or v.fact.period_end > cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date()
        ):
            continue
        key = (v.fact.metric, v.fact.period_start, v.fact.period_end, v.fact.period_basis)
        groups.setdefault(key, []).append(v)
    selected = []
    for values in groups.values():
        known = max(v.version_known_at for v in values)
        latest = [v for v in values if v.version_known_at == known]
        if len(latest) != 1:
            return (), "ambiguous_visible_revision"
        if latest[0].pit_level != PITLevel.STRICT:
            return (), "financial_version_not_strict_pit"
        selected.append(latest[0])
    return tuple(sorted(selected, key=lambda v: (v.fact.metric, v.fact.period_end))), None


def select_at(candidates, cutoff: datetime, conditions):
    if cutoff.tzinfo is None or len(candidates) > 20000:
        raise ValueError("aware cutoff and bounded candidate universe required")
    if len({c.instrument_id for c in candidates}) != len(candidates):
        raise ValueError("duplicate selection candidates")
    thresholds = {"pe_max": "30", "roe_min": "15", "rev_yoy_min": None, **conditions}
    if set(thresholds) != {"pe_max", "roe_min", "rev_yoy_min"}:
        raise ValueError("unsupported selection condition")
    for key, value in thresholds.items():
        if value is not None:
            parsed = Decimal(value)
            if not parsed.is_finite():
                raise ValueError("nonfinite condition")
            thresholds[key] = decimal_text(parsed)
    results, inputs = [], []
    day = cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date()
    for c in sorted(candidates, key=lambda c: c.instrument_id):
        known_versions = tuple(v for v in c.financials if v.version_known_at <= cutoff)
        inputs.append(
            {
                **c.model_dump(mode="json", exclude={"financials"}),
                "financials": sorted(
                    [v.model_dump(mode="json") for v in known_versions], key=digest
                ),
            }
        )
        metrics, sources, reason = {}, [c.membership_source_hash, c.price_source_hash], None
        sources.extend(v.source_hash for v in known_versions)
        if c.historical_member is None or c.membership_pit != PITLevel.STRICT:
            reason = "historical_membership_unconfirmed"
        elif c.historical_member is False:
            results.append(
                dict(
                    instrument_id=c.instrument_id,
                    status="EXCLUDED",
                    reason="not_historical_member",
                    metrics={},
                    metric_status="UNVERIFIED",
                    source_hashes=[c.membership_source_hash],
                )
            )
            continue
        elif c.price_session != day or c.price_known_at > cutoff or c.raw_close is None:
            reason = "signal_day_raw_price_unavailable"
        elif c.price_pit != PITLevel.STRICT:
            reason = "raw_price_not_strict_pit"
        visible, version_error = visible_versions(c.financials, cutoff)
        if not reason:
            reason = version_error
        if not reason:
            series = PriceSeries(
                instrument_id=c.instrument_id,
                currency="CNY",
                adjustment="raw",
                asof=cutoff,
                points=(PricePoint(session_date=day, close=c.raw_close),),
            )
            values = {
                v.name: v
                for v in valuation_check(
                    series=series, financials=tuple(v.fact for v in visible), asof=cutoff
                )
            }
            growth, growth_error = yoy_growth(tuple(v.fact for v in visible), "revenue")
            profit = ttm_profit(tuple(v.fact for v in visible))
            with localcontext() as context:
                context.prec = 40
                metrics = {
                    name: decimal_text(values[name].value)
                    if values[name].value is not None
                    else None
                    for name in ("pe", "roe")
                }
                metrics["revenue_growth"] = decimal_text(growth) if growth is not None else None
                metrics["ttm_parent_profit"] = (
                    decimal_text(profit.value) if profit and profit.value is not None else None
                )
            reasons = [
                values[name].missing_reason
                for name in ("pe", "roe")
                if thresholds[name + "_max" if name == "pe" else "roe_min"] is not None
                and values[name].value is None
            ]
            if thresholds["rev_yoy_min"] is not None and growth_error:
                reasons.append(growth_error)
            reason = ";".join(reasons) if reasons else None
            sources.extend(v.source_hash for v in visible)
        selected = False
        if not reason:
            with localcontext() as context:
                context.prec = 40
                selected = all(
                    thresholds[key] is None
                    or (
                        Decimal(metrics[metric]) <= Decimal(thresholds[key])
                        if key == "pe_max"
                        else Decimal(metrics[metric]) * 100 >= Decimal(thresholds[key])
                    )
                    for key, metric in (
                        ("pe_max", "pe"),
                        ("roe_min", "roe"),
                        ("rev_yoy_min", "revenue_growth"),
                    )
                )
        results.append(
            dict(
                instrument_id=c.instrument_id,
                status="UNAVAILABLE" if reason else "ELIGIBLE" if selected else "EXCLUDED",
                reason=reason,
                metrics=metrics,
                metric_status="UNVERIFIED",
                source_hashes=sorted(set(sources)),
            )
        )
    incomplete = any(r["status"] == "UNAVAILABLE" for r in results)
    eligible = [r["instrument_id"] for r in results if r["status"] == "ELIGIBLE"]
    payload = dict(
        schema_version="pit-selection-v1",
        cutoff=cutoff.isoformat(),
        conditions=thresholds,
        candidates=results,
        eligible=eligible,
        eligible_count=None if incomplete else len(eligible),
        status="BLOCKED" if incomplete else "COMPUTED_UNVERIFIED",
        input_view_hash=digest(inputs),
    )
    payload["output_hash"] = digest(payload)
    return payload
