from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RightsScope(StrEnum):
    INTERNAL_RESEARCH = "internal_research"
    LICENSED_SERVICE = "licensed_service"


class PITLevel(StrEnum):
    STRICT = "STRICT"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class DataRights(FrozenModel):
    policy_id: str
    provider: str
    upstream: str
    entitlement: str = "unknown"
    attribution: str
    scope: RightsScope = RightsScope.INTERNAL_RESEARCH
    expires_at: AwareDatetime | None = None
    raw_export: bool = False
    external_share: bool = False
    license_reference: str | None = None


class DataRightsPolicy:
    @staticmethod
    def allows(
        rights: DataRights,
        action: Literal["raw_export", "external_share"],
        now: datetime,
        scope: RightsScope,
    ) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone aware")
        return bool(
            rights.entitlement != "unknown"
            and rights.license_reference
            and rights.scope == scope
            and (rights.expires_at is None or now < rights.expires_at)
            and getattr(rights, action)
        )


class InstrumentAlias(FrozenModel):
    exchange: Literal["XSHG", "XSHE"]
    code: str = Field(pattern=r"^\d{6}$")
    valid_from: date
    valid_to: date | None = None

    @model_validator(mode="after")
    def valid_interval(self):
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("invalid alias interval")
        return self


class InstrumentStatus(FrozenModel):
    effective_from: date
    effective_to: date | None = None
    suspended: bool = False
    is_st: bool = False
    source: str


class TradingRule(FrozenModel):
    version: str
    exchange: Literal["XSHG", "XSHE"]
    board: str
    status: str
    effective_from: date
    effective_to: date | None = None
    settlement_days: int = 1
    source: str


class Instrument(FrozenModel):
    instrument_id: str = Field(pattern=r"^ins_[a-zA-Z0-9_-]{8,}$")
    name: str
    market: Literal["CN"] = "CN"
    board: str
    listed_at: date
    delisted_at: date | None = None
    aliases: tuple[InstrumentAlias, ...]
    statuses: tuple[InstrumentStatus, ...] = ()
    rules: tuple[TradingRule, ...] = ()
    lifecycle_source: str
    lifecycle_pit: PITLevel = PITLevel.PARTIAL

    @model_validator(mode="after")
    def lifecycle(self):
        if not self.aliases:
            raise ValueError("instrument needs an effective alias")
        if self.delisted_at is not None and self.delisted_at < self.listed_at:
            raise ValueError("invalid lifecycle")
        for intervals in (self.statuses, self.rules):
            for interval in intervals:
                if interval.effective_to is not None:
                    if interval.effective_to < interval.effective_from:
                        raise ValueError("invalid effective interval")
        return self


class DataRequest(FrozenModel):
    instruments: tuple[str, ...]
    dataset: Literal[
        "daily", "financial", "adjustment", "valuation", "calendar", "instrument_master"
    ]
    start: date
    end: date
    asof: AwareDatetime
    adjustment: Literal["raw", "qfq", "hfq"] = "raw"
    adjustment_anchor: date | None = None
    fields: tuple[str, ...] = ()
    scope: RightsScope = RightsScope.INTERNAL_RESEARCH
    schema_version: str = "2"
    normalizer_version: str = "2"
    complete_sessions_only: bool = True
    snapshot_id: str | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.end < self.start or (self.end - self.start).days > 1096:
            raise ValueError("range must be ordered and at most three years")
        if not self.instruments or len(set(self.instruments)) != len(self.instruments):
            raise ValueError("instrument IDs must be nonempty and unique")
        if any(not re.fullmatch(r"ins_[a-zA-Z0-9_-]{8,}", value) for value in self.instruments):
            raise ValueError("stable internal instrument IDs required")
        cutoff = self.asof.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if self.end > cutoff:
            raise ValueError("end cannot exceed asof")
        if self.adjustment != "raw" and self.adjustment_anchor is None:
            raise ValueError("adjusted data needs a frozen anchor")
        if self.adjustment_anchor is not None and self.adjustment_anchor > cutoff:
            raise ValueError("anchor cannot exceed asof")
        if self.adjustment_anchor is not None and self.adjustment_anchor < self.end:
            raise ValueError("anchor must cover the end of the requested range")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("duplicate requested fields")
        return self


class DataResult(FrozenModel):
    provider: str
    upstream: str
    source_family: str = "unknown_market_feed"
    lineage_status: str = "unverified"
    request: DataRequest
    retrieved_at: AwareDatetime
    # Canonical JSON is immutable (a frozen model containing dicts would not be).
    records_json: str
    raw_records_json: str
    units: tuple[tuple[str, str], ...] = ()
    quality_flags: tuple[str, ...] = ()
    pit: PITLevel = PITLevel.UNAVAILABLE
    rights: DataRights
    adjustment_factor_hash: str | None = None
    crosscheck_json: str = "{}"
    provenance_json: str = "{}"

    @model_validator(mode="after")
    def validate_content(self):
        for field in ("records_json", "raw_records_json"):
            value = json.loads(getattr(self, field))
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError("record streams must be JSON arrays of objects")
        for field in ("crosscheck_json", "provenance_json"):
            if not isinstance(json.loads(getattr(self, field)), dict):
                raise ValueError("metadata must be JSON objects")
        if (self.rights.provider, self.rights.upstream, self.rights.scope) != (
            self.provider,
            self.upstream,
            self.request.scope,
        ):
            raise ValueError("result rights provenance/scope mismatch")
        return self


class Partition(FrozenModel):
    layer: Literal["raw", "normalized"]
    market: str = "CN"
    dataset: str
    year: int
    blob_ref: str
    sha256: str
    row_count: int


class DatasetSnapshot(FrozenModel):
    snapshot_id: str
    semantic_hash: str
    request_hash: str
    result: DataResult
    partitions: tuple[Partition, ...]
    manifest_ref: str
    row_count: int
    frozen_at: AwareDatetime
    stale: bool = False


def decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("nonfinite numeric value")
    # Do not use normalize(): it rounds under the caller's Decimal context.
    text = format(value, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return "0" if value == 0 else text


ExactDecimal = Annotated[Decimal, Field(allow_inf_nan=False), PlainSerializer(decimal_text)]


class DcfAssumptions(FrozenModel):
    """User assumptions, not forecasts inferred from a language model."""

    g1: ExactDecimal = Field(default=Decimal("0.08"), ge=Decimal("-0.2"), le=Decimal("0.4"))
    g2: ExactDecimal = Field(default=Decimal("0.04"), ge=0, le=Decimal("0.15"))
    g_term: ExactDecimal = Field(default=Decimal("0.025"), ge=0, le=Decimal("0.04"))
    discount: ExactDecimal = Field(default=Decimal("0.09"), ge=Decimal("0.05"), le=Decimal("0.15"))

    @model_validator(mode="after")
    def valid_terminal(self):
        if self.discount <= self.g_term:
            raise ValueError("DCF discount must exceed terminal growth")
        return self


class MetricSpec(FrozenModel):
    version: str = "quant-compute-v1"
    metrics: tuple[
        Literal[
            "return",
            "cagr",
            "volatility",
            "drawdown",
            "sharpe",
            "beta",
            "correlation",
            "pe",
            "pb",
            "roe",
            "trend",
            "indicators",
        ],
        ...,
    ] = ("return", "cagr", "volatility", "drawdown", "pe", "pb", "roe", "trend", "indicators")
    return_basis: Literal["price", "adjusted_proxy"] = "price"
    price_adjustment: Literal["raw", "qfq", "hfq"] | None = None
    include_exhibits: bool = False
    dcf: DcfAssumptions | None = None
    annual_sessions: int = Field(default=252, ge=1, le=366)
    elapsed_year_days: ExactDecimal = Decimal("365.2425")
    suspension_policy: Literal["carry_forward", "reject"] = "carry_forward"
    missing_policy: Literal["propagate"] = "propagate"
    benchmark_snapshot_id: str | None = None
    risk_free_snapshot_id: str | None = None
    fx_snapshot_id: str | None = None
    explicit_zero_risk_free: bool = False
    sma_window: int = Field(default=20, ge=2, le=252)
    ema_window: int = Field(default=20, ge=2, le=252)
    rsi_window: int = Field(default=14, ge=2, le=252)
    macd_windows: tuple[int, int, int] = (12, 26, 9)
    seed: int = 0
    absolute_tolerance: ExactDecimal = Field(default=Decimal("1e-10"), ge=0, le=Decimal("1e-10"))
    relative_tolerance: ExactDecimal = Field(default=Decimal("1e-8"), ge=0, le=Decimal("1e-8"))
    minimum_observations: int = Field(default=3, ge=3, le=5000)

    @model_validator(mode="after")
    def valid_spec(self):
        if self.price_adjustment is not None and (
            (self.price_adjustment == "raw") != (self.return_basis == "price")
        ):
            raise ValueError("price adjustment must match return basis")
        if not self.metrics or len(set(self.metrics)) != len(self.metrics):
            raise ValueError("nonempty unique metric whitelist required")
        fast, slow, signal = self.macd_windows
        if not 1 < fast < slow <= 252 or not 1 < signal <= 252:
            raise ValueError("invalid MACD windows")
        if (
            self.elapsed_year_days <= 0
            or self.absolute_tolerance < 0
            or self.relative_tolerance < 0
        ):
            raise ValueError("invalid numeric assumptions")
        if self.explicit_zero_risk_free and self.risk_free_snapshot_id:
            raise ValueError("choose frozen rf or explicit zero, not both")
        return self


class PricePoint(FrozenModel):
    session_date: date
    close: ExactDecimal | None
    suspended: bool = False
    provisional: bool = False


class PriceSeries(FrozenModel):
    instrument_id: str = Field(pattern=r"^ins_[a-zA-Z0-9_-]{8,}$")
    currency: str
    adjustment: Literal["raw", "qfq", "hfq"]
    points: tuple[PricePoint, ...]
    asof: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered(self):
        days = [p.session_date for p in self.points]
        if not days or len(days) > 5000 or days != sorted(set(days)):
            raise ValueError("nonempty unique ordered sessions required")
        if any(p.close is not None and p.close <= 0 for p in self.points):
            raise ValueError("prices must be positive")
        if any(p.provisional for p in self.points):
            raise ValueError("provisional prices cannot enter computation")
        if self.asof and days[-1] > self.asof.astimezone(ZoneInfo("Asia/Shanghai")).date():
            raise ValueError("future price")
        if self.asof:
            close_at = datetime.combine(days[-1], time(15), tzinfo=ZoneInfo("Asia/Shanghai"))
            if self.asof < close_at:
                raise ValueError("incomplete session cannot enter computation")
        return self


class FinancialFact(FrozenModel):
    metric: str
    period_end: date
    period_start: date | None = None
    available_at: AwareDatetime | None
    value: ExactDecimal | None
    unit: str
    share_basis: str = "unknown"
    statement_basis: str = "unknown"
    period_basis: Literal["point", "quarter", "ytd", "ttm", "annual", "disclosed"] = "point"
    revision: str = "unknown"
    source_hash: str
    disclosed: bool = False


class MetricValue(FrozenModel):
    name: str
    value: ExactDecimal | None
    unit: str
    definition: str
    sample_count: int = Field(ge=0)
    instrument_id: str
    start: date
    end: date
    row_keys: tuple[str, ...] = ()
    missing_reason: str | None = None
    peak_date: date | None = None
    trough_date: date | None = None
    recovery_date: date | None = None

    @model_validator(mode="after")
    def missing(self):
        if (self.value is None) != bool(self.missing_reason):
            raise ValueError("null requires reason; present value cannot have missing reason")
        if self.end < self.start:
            raise ValueError("invalid metric range")
        return self


class ComputeInput(FrozenModel):
    snapshots: tuple[DatasetSnapshot, ...]
    instrument_id: str
    spec: MetricSpec
    asof: AwareDatetime
    instrument_definition_json: str
    environment_json: str

    @model_validator(mode="after")
    def bound_inputs(self):
        ids = [s.snapshot_id for s in self.snapshots]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("nonempty unique frozen inputs required")
        for pinned in (
            self.spec.benchmark_snapshot_id,
            self.spec.risk_free_snapshot_id,
            self.spec.fx_snapshot_id,
        ):
            if pinned is not None and pinned not in ids:
                raise ValueError("referenced snapshot must be included in frozen inputs")
        definition = json.loads(self.instrument_definition_json)
        if definition.get("instrument_id") != self.instrument_id:
            raise ValueError("frozen instrument identity mismatch")
        if not isinstance(json.loads(self.environment_json), dict):
            raise ValueError("frozen environment required")
        return self


class ArtifactBundle(FrozenModel):
    artifact_id: str
    manifest_hash: str
    manifest_json: str
    output_hash: str
    output_json: str
    input_snapshot_ids: tuple[str, ...]
    values: tuple[MetricValue, ...]
