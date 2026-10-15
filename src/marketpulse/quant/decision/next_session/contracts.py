"""Frozen daily protocol and internal records, separate from public run requests."""

from datetime import date, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator

from ...domain import ExactDecimal, FrozenModel
from ..contracts import BudgetProfile, EvidenceBundle

Direction = Literal["UP", "DOWN"]
Cohort = Literal["PRIMARY", "EXPLORATORY", "COMPARATOR"]


class ExperimentConfig(FrozenModel):
    schema_version: Literal["next-session-v1"] = "next-session-v1"
    experiment_id: str
    protocol_id: str
    universe_version: str
    securities: tuple[str, ...] = Field(min_length=1, max_length=3)
    enabled: bool = False
    enabled_at: AwareDatetime | None = None
    first_base_session: date | None = None
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    window_start: time = time(20)
    window_end: time = time(20, 15)
    minimum_inputs: tuple[str, ...] = (
        "security_identity",
        "verified_exchange_calendar",
        "complete_daily_prices",
        "base_reference_price",
        "market_prices",
        "price_precision",
        "suspension_status",
    )
    method_version: str
    prompt_version: str
    model_version: str | None = None
    budget: BudgetProfile = BudgetProfile()
    persistence_baseline_flat: Literal["UP"] = "UP"
    comparator_enabled: bool = False

    @model_validator(mode="after")
    def registered(self):
        import re

        if len(set(self.securities)) != len(self.securities) or any(
            not re.fullmatch(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ)", s) for s in self.securities
        ):
            raise ValueError("unique Shanghai/Shenzhen securities required")
        if not self.window_start < self.window_end:
            raise ValueError("invalid issue window")
        if self.protocol_id == "NEXT_SESSION_EOD_V1" and (
            self.window_start != time(20) or self.window_end != time(20, 15)
        ):
            raise ValueError("changed window requires a new protocol_id")
        if self.enabled and (self.enabled_at is None or self.first_base_session is None):
            raise ValueError("enabled experiment needs a recorded activation and first session")
        if not self.enabled and (
            self.enabled_at is not None or self.first_base_session is not None
        ):
            raise ValueError("disabled template must not imply historical planned slots")
        return self


class NextSessionRunRequest(FrozenModel):
    request_key: UUID
    experiment_id: str
    entity: str | None = Field(default=None, min_length=1, max_length=80)
    # No direction, actual, reviewed flag, retrospective cutoff or cohort input.
    exploratory: bool = False

    @model_validator(mode="after")
    def selected_stock(self):
        if self.entity is not None and not self.entity.strip():
            raise ValueError("请输入股票名称或代码")
        return self


class VerifiedSessionCalendar(FrozenModel):
    exchange: Literal["SSE", "SZSE"]
    coverage_start: date
    coverage_end: date
    sessions: tuple[date, ...] = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    verified_at: AwareDatetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid(self):
        if self.sessions != tuple(sorted(set(self.sessions))):
            raise ValueError("calendar must contain ordered unique market sessions")
        if self.coverage_end < self.coverage_start or any(
            not self.coverage_start <= d <= self.coverage_end for d in self.sessions
        ):
            raise ValueError("calendar outside verified coverage")
        return self


class DailyPrice(FrozenModel):
    security_id: str
    session: date
    close: ExactDecimal = Field(gt=0)
    reference_price: ExactDecimal | None = Field(default=None, gt=0)
    price_tick: ExactDecimal = Field(gt=0)
    turnover: ExactDecimal = Field(ge=0)
    source_ref: str
    reference_basis: Literal["EXCHANGE_QUOTE_PREVCLOSE", "UNAVAILABLE"]
    suspended_all_day: bool

    @model_validator(mode="after")
    def precision(self):
        if (
            self.close % self.price_tick
            or self.reference_price is not None
            and self.reference_price % self.price_tick
        ):
            raise ValueError("prices must match the recorded raw price precision")
        if (self.reference_price is None) != (self.reference_basis == "UNAVAILABLE"):
            raise ValueError("missing historical reference must stay explicit")
        return self


class NextSessionSnapshot(FrozenModel):
    schema_version: Literal["next-session-v1"] = "next-session-v1"
    security_id: str
    base_session: date
    target_session: date
    requested_at: AwareDatetime
    evidence_sealed_at: AwareDatetime
    calendar: VerifiedSessionCalendar
    daily_prices: tuple[DailyPrice, ...] = Field(min_length=2)
    evidence: EvidenceBundle
    indicator_refs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_run_id: str | None = None
    sources: tuple[dict, ...] = ()
    documents: tuple[dict, ...] = ()
    indicators: tuple[dict, ...] = ()
    model_context: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def sealed_inputs(self):
        if self.evidence_sealed_at < self.requested_at:
            raise ValueError("seal precedes request")
        if self.evidence.cutoff_at > self.evidence_sealed_at:
            raise ValueError("evidence cutoff exceeds seal")
        sessions = self.calendar.sessions
        if self.base_session not in sessions or self.target_session not in sessions:
            raise ValueError("unverified base/target session")
        idx = sessions.index(self.base_session)
        if idx + 1 >= len(sessions) or sessions[idx + 1] != self.target_session:
            raise ValueError("target must be the next market session")
        days = [p.session for p in self.daily_prices]
        if days != sorted(set(days)) or days[-1] != self.base_session:
            raise ValueError("daily input must end at the base session")
        if any(p.security_id != self.security_id for p in self.daily_prices):
            raise ValueError("daily security mismatch")
        if self.daily_prices[-1].reference_price is None:
            raise ValueError("base session requires a verified quote reference")
        return self


class PredictionReason(FrozenModel):
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    next_session_mechanism: str = Field(min_length=1, max_length=1000)


class NextSessionPrediction(FrozenModel):
    prediction_id: str
    experiment_id: str
    protocol_id: str
    universe_version: str
    security_id: str
    base_session: date
    target_session: date
    requested_at: AwareDatetime
    evidence_sealed_at: AwareDatetime
    issued_at: AwareDatetime
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    method_versions: tuple[str, ...] = Field(min_length=1)
    model_version: str
    prompt_version: str
    prediction: Direction
    summary: str = ""
    timing_group: str = ""
    support_ids: tuple[str, ...] = Field(min_length=1)
    counter_ids: tuple[str, ...] = Field(min_length=1)
    reasons: tuple[PredictionReason, ...] = Field(min_length=1, max_length=3)
    opposing_reasons: tuple[PredictionReason, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)
    support_level: Literal["STRONG", "MODERATE", "WEAK"]
    review_status: Literal["APPROVE"]
    review_refs: tuple[str, ...] = Field(min_length=1)
    cohort: Cohort
    baselines: tuple[tuple[Literal["ALWAYS_UP", "ALWAYS_DOWN", "PERSISTENCE"], Direction], ...]
    supersedes_id: str | None = None

    @model_validator(mode="after")
    def issue(self):
        if not self.requested_at <= self.evidence_sealed_at <= self.issued_at:
            raise ValueError("prediction must follow sealed input")
        local = self.issued_at.astimezone(ZoneInfo("Asia/Shanghai"))
        sealed = self.evidence_sealed_at.astimezone(ZoneInfo("Asia/Shanghai"))
        requested = self.requested_at.astimezone(ZoneInfo("Asia/Shanghai"))
        if local.date() >= self.target_session or self.target_session <= self.base_session:
            raise ValueError("cannot issue after target session begins")
        if (
            self.cohort == "PRIMARY"
            and self.protocol_id == "NEXT_SESSION_EOD_V1"
            and (
                local.date() != self.base_session
                or requested.date() != self.base_session
                or not time(20) <= requested.time() < time(20, 15)
                or sealed.date() != self.base_session
                or not time(20) <= sealed.time() <= local.time() < time(20, 15)
            )
        ):
            raise ValueError("primary seal and issue must both fall in the protocol window")
        if (
            dict(self.baselines).get("ALWAYS_UP") != "UP"
            or (dict(self.baselines).get("ALWAYS_DOWN") != "DOWN")
            or len(self.baselines) != 3
            or "PERSISTENCE" not in dict(self.baselines)
        ):
            raise ValueError("all three preregistered baselines required")
        ids = set(self.support_ids + self.counter_ids)
        if any(not set(r.evidence_ids) <= ids for r in self.reasons + self.opposing_reasons):
            raise ValueError("reason has unbound references")
        return self

    @property
    def primary_key(self):
        return self.experiment_id, self.security_id, self.target_session


class NextSessionOutcome(FrozenModel):
    outcome_id: str
    version: int = Field(ge=1)
    supersedes_id: str | None = None
    prediction_id: str
    observed_at: AwareDatetime
    status: Literal["PENDING", "PENDING_DATA", "SCORABLE", "UNSCORABLE", "CONFLICT"]
    close: ExactDecimal | None = Field(default=None, gt=0)
    reference_price: ExactDecimal | None = Field(default=None, gt=0)
    return_direction: Literal["UP", "DOWN", "FLAT"] | None = None
    price_source_refs: tuple[str, ...] = ()
    price_basis: Literal["EXCHANGE_REFERENCE_DIRECTION"] = "EXCHANGE_REFERENCE_DIRECTION"
    source_hashes: tuple[str, ...] = ()
    correct: bool | None = None
    limitations: tuple[str, ...]
    revision_reason: str | None = None

    @model_validator(mode="after")
    def score(self):
        if self.status == "SCORABLE":
            if None in (self.close, self.reference_price, self.return_direction, self.correct):
                raise ValueError("scorable outcome requires precise prices and label")
            direction = (
                "UP"
                if self.close > self.reference_price
                else ("DOWN" if self.close < self.reference_price else "FLAT")
            )
            if self.return_direction != direction or (direction == "FLAT" and self.correct):
                raise ValueError("wrong exact-price direction or FLAT marked correct")
            if len(set(self.price_source_refs)) < 2 or len(set(self.source_hashes)) < 2:
                raise ValueError("scoring needs two archived price sources")
        elif self.return_direction is not None or self.correct is not None:
            raise ValueError("pending/unscorable/conflict outcome cannot be scored")
        if self.version > 1 and (not self.supersedes_id or not self.revision_reason):
            raise ValueError("revised outcome must preserve and explain the previous version")
        return self


class ExperimentRunSlot(FrozenModel):
    experiment_id: str
    security_id: str
    base_session: date
    target_session: date
    window_start: AwareDatetime
    window_end: AwareDatetime
    state: Literal["PLANNED", "ISSUED", "MISSED", "FAILED_INPUT", "FAILED_REVIEW", "TIMEOUT"]
    prediction_id: str | None = None
    reason: str
    attempt_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid(self):
        if self.window_start >= self.window_end or self.target_session <= self.base_session:
            raise ValueError("invalid planned slot window/session")
        if (self.state == "ISSUED") != bool(self.prediction_id):
            raise ValueError("only issued slots reference a successful prediction")
        return self


class PredictionAttempt(FrozenModel):
    attempt_id: str
    slot_key: tuple[str, str, date]
    started_at: AwareDatetime
    finished_at: AwareDatetime
    state: Literal["ISSUED", "FAILED_INPUT", "FAILED_REVIEW", "TIMEOUT", "AUXILIARY"]
    reason: str
    model_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    prediction_id: str | None = None
