"""Decision v1 contracts. Public inputs cannot supply review or result fields."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, model_serializer, model_validator

from ..domain import ExactDecimal, FrozenModel

Scope = Literal["single", "industry", "market"]
Outlook = Literal["FAVORABLE", "UNFAVORABLE", "MIXED", "UNDETERMINED"]
Stance = Literal[
    "PRIORITIZE_RESEARCH", "WAIT_FOR_CONFIRMATION", "RISK_FIRST", "INSUFFICIENT_EVIDENCE"
]
Quality = Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "CONFLICT", "UNVERIFIED"]
Hash = str


class BudgetProfile(FrozenModel):
    max_model_calls: int = Field(default=24, ge=1, le=24)
    max_tokens: int = Field(default=60000, ge=1, le=60000)
    max_seconds: int = Field(default=900, ge=1, le=900)
    max_rounds: int = Field(default=2, ge=0, le=2)
    max_evidence_calls: int = Field(default=8, ge=0, le=8)
    max_candidates: int = Field(default=3, ge=0, le=3)


class ResearchRequest(FrozenModel):
    schema_version: Literal["decision-v1"] = "decision-v1"
    request_key: UUID
    question: str = Field(min_length=1, max_length=2000)
    scope: Scope = "single"
    entity: str = Field(min_length=1, max_length=80)
    horizon_months: int = Field(default=3, ge=1, le=3)
    research_mode: Literal["evidence", "medium_term", "candidates", "market_scan"] = "evidence"
    include_flows: bool = False
    include_industry: bool = False
    include_methods: bool = False
    mode: Literal["latest", "historical"] = "latest"
    cutoff_at: AwareDatetime | None = None
    parent_run_id: str | None = Field(default=None, pattern=r"^RUN-DEC-[0-9a-f]{32}$")
    budget_profile: BudgetProfile = BudgetProfile()
    enhanced_research: bool = False

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if not self.enhanced_research:
            value.pop("enhanced_research", None)
        return value

    @model_validator(mode="after")
    def explicit_historical_cutoff(self):
        if self.enhanced_research and self.research_mode not in {"candidates", "market_scan"}:
            raise ValueError("增强市场研究仅适用于市场观察和行业候选。")
        if self.research_mode == "candidates" and (
            self.scope != "industry"
            or self.entity not in {"半导体设备", "集成电路制造"}
            or self.mode != "latest"
            or self.cutoff_at is not None
        ):
            raise ValueError("candidate research requires latest semiconductor-equipment scope")
        if self.research_mode == "market_scan" and (
            self.scope != "market"
            or self.entity != "设备与制造观察范围"
            or self.mode != "latest"
            or self.cutoff_at is not None
        ):
            raise ValueError("market scan requires the declared current two-industry scope")
        if self.mode == "historical" and self.cutoff_at is None:
            raise ValueError("historical research requires an explicit cutoff")
        if self.include_flows and (self.mode != "latest" or self.research_mode != "medium_term"):
            raise ValueError(
                "flow research requires latest medium-term mode; no historical backfill"
            )
        if self.include_industry and not self.include_flows:
            raise ValueError("industry research requires latest medium-term flow observations")
        if self.include_methods and not self.include_industry:
            raise ValueError("method routing requires latest medium-term industry observations")
        return self


class ResearchScope(FrozenModel):
    scope: Scope
    subject_ids: tuple[str, ...] = Field(min_length=1)
    industry_id: str | None = None
    horizon_months: int = Field(ge=1, le=3)
    cutoff_at: AwareDatetime
    mode: Literal["latest", "historical"]
    start: date
    end: date

    @model_validator(mode="after")
    def bounds(self):
        if self.end < self.start or len(set(self.subject_ids)) != len(self.subject_ids):
            raise ValueError("invalid research interval or duplicate subjects")
        return self


class Locator(FrozenModel):
    kind: Literal["pdf", "text", "json", "calculation"]
    path: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def position(self):
        if self.kind == "pdf" and self.page is None:
            raise ValueError("PDF evidence requires a page")
        if (self.start is None) != (self.end is None):
            raise ValueError("character start and end must be paired")
        if self.start is not None and self.end <= self.start:
            raise ValueError("empty character span")
        if self.kind == "text" and self.start is None:
            raise ValueError("text evidence requires a character locator")
        return self


class EvidenceItem(FrozenModel):
    schema_version: Literal["decision-v1"] = "decision-v1"
    evidence_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    kind: Literal["fact", "calculation", "disclosure", "industry", "flow"]
    source_family: str = Field(min_length=1)
    original_source_id: str = Field(min_length=1)
    url: HttpUrl
    raw_ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: Locator
    text: str = Field(min_length=1)
    published_at: AwareDatetime | None = None
    observed_at: AwareDatetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    fetched_at: AwareDatetime
    available_at: AwareDatetime
    availability_basis: Literal["FIRST_FETCH", "DATED_SOURCE_VERSION"] = "FIRST_FETCH"
    availability_proof_ref: str | None = None
    time_precision: Literal["second", "minute", "day", "unknown"]
    method_version: str = Field(min_length=1)
    quality_status: Quality
    limitations: tuple[str, ...] = ()
    value: ExactDecimal | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def provenance(self):
        if self.availability_basis == "FIRST_FETCH" and self.available_at < self.fetched_at:
            raise ValueError("first-fetch evidence cannot backdate availability")
        if self.availability_basis == "DATED_SOURCE_VERSION" and not self.availability_proof_ref:
            raise ValueError("historical availability needs archived version proof")
        if self.published_at and self.available_at < self.published_at:
            raise ValueError("evidence cannot be available before publication")
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("invalid evidence period")
        if self.value is not None and self.unit not in {
            "CNY",
            "USD",
            "CNY/share",
            "shares",
            "ratio",
            "percent",
            "count",
            "wafer/month",
        }:
            raise ValueError("numeric evidence needs a canonical unit")
        if self.quality_status != "AVAILABLE" and not self.limitations:
            raise ValueError("limited evidence needs an explanation")
        return self


class SourceCapability(FrozenModel):
    source_id: str
    name: str
    url: HttpUrl
    source_family: str
    acquisition: str
    authentication: str
    license_note: str
    coverage: str
    frequency: str
    publication_schedule: str | None = None
    grace_seconds: int | None = Field(default=None, ge=0)
    units: tuple[str, ...] = ()
    estimated: bool = False
    historical_versions: bool = False
    tested_at: AwareDatetime | None = None
    status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNTESTED", "LINK_ONLY"]
    reason: str
    data_start: str | None = None
    data_end: str | None = None
    sample_count: int = Field(default=0, ge=0)
    fields: tuple[str, ...] = ()
    http_status: int | None = None
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    raw_ref: str | None = None
    freshness: Literal["FRESH", "STALE", "FRESHNESS_UNKNOWN"] = "FRESHNESS_UNKNOWN"


class CoverageReport(FrozenModel):
    universe_total: int = Field(ge=0)
    attempted: int = Field(ge=0)
    available: int = Field(ge=0)
    missing: int = Field(ge=0)
    excluded: int = Field(ge=0)
    universe_ref: str
    member_refs: tuple[str, ...]
    source_dates: tuple[str, ...]
    method: str
    status: Literal["COMPLETE", "PARTIAL", "UNAVAILABLE"]

    @model_validator(mode="after")
    def denominator(self):
        if self.available + self.missing + self.excluded != self.universe_total:
            raise ValueError("coverage must account for the full universe")
        if not self.available <= self.attempted <= self.universe_total:
            raise ValueError("invalid attempted/available coverage")
        if self.status == "COMPLETE" and (self.missing or self.excluded):
            raise ValueError("complete coverage cannot hide missing/excluded members")
        return self


class EvidenceBundle(FrozenModel):
    items: tuple[EvidenceItem, ...]
    sources: tuple[SourceCapability, ...]
    coverage: CoverageReport
    cutoff_at: AwareDatetime

    @model_validator(mode="after")
    def references(self):
        ids = [e.evidence_id for e in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence identity")
        if any(e.available_at > self.cutoff_at for e in self.items):
            raise ValueError("future evidence in bundle")
        families = {s.source_family for s in self.sources}
        if any(e.source_family not in families for e in self.items):
            raise ValueError("unknown evidence source family")
        return self


class ResearchClaim(FrozenModel):
    claim_id: str
    claim_type: Literal["observation", "association", "hypothesis", "scenario"]
    text: str = Field(min_length=1)
    subject_ids: tuple[str, ...] = Field(min_length=1)
    horizon: str
    support_ids: tuple[str, ...] = Field(min_length=1)
    counter_ids: tuple[str, ...] = ()
    mechanism: str
    alternative_claim_ids: tuple[str, ...] = ()
    gap_ids: tuple[str, ...] = ()
    support_level: Literal["STRONG", "MODERATE", "WEAK", "INSUFFICIENT"]
    verification_status: Literal["PENDING", "APPROVE", "REJECT", "NEED_EVIDENCE"] = "PENDING"
    supersedes_id: str | None = None


class EvidenceGap(FrozenModel):
    gap_id: str
    claim_id: str
    need: str
    materiality: Literal["HIGH", "MEDIUM", "LOW"]
    allowed_tool: Literal["disclosures", "daily", "financial", "margin", "industry"]
    arguments_json: str
    expected_benefit: str
    status: Literal["OPEN", "FILLED", "UNAVAILABLE", "DUPLICATE"] = "OPEN"


class Scenario(FrozenModel):
    scenario_id: str
    kind: Literal["BASE", "IMPROVEMENT", "DETERIORATION"]
    text: str
    claim_ids: tuple[str, ...] = Field(min_length=1)
    conditions: tuple[str, ...] = Field(min_length=1)


class ResearchOpinion(FrozenModel):
    stance: Stance
    outlook: Outlook
    horizon_months: int = Field(ge=1, le=3)
    scenario_refs: tuple[str, ...]
    approved_claim_ids: tuple[str, ...]
    supporting_reasons: tuple[str, ...]
    opposing_reasons: tuple[str, ...] = Field(min_length=1)
    catalysts: tuple[str, ...]
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    next_checks: tuple[str, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def direction_support(self):
        if self.outlook != "UNDETERMINED" and (
            not self.approved_claim_ids or len(self.scenario_refs) != 3
        ):
            raise ValueError("direction needs approved claims and three scenarios")
        return self


class VerifiedResearch(FrozenModel):
    claims: tuple[ResearchClaim, ...]
    evidence: EvidenceBundle
    scenarios: tuple[Scenario, ...]

    @model_validator(mode="after")
    def approved_only(self):
        ids = {e.evidence_id for e in self.evidence.items}
        for c in self.claims:
            if c.verification_status != "APPROVE" or not set(c.support_ids + c.counter_ids) <= ids:
                raise ValueError("writer may only consume approved, bound claims")
        claim_ids = {c.claim_id for c in self.claims}
        if any(not set(s.claim_ids) <= claim_ids for s in self.scenarios):
            raise ValueError("scenario references unapproved claim")
        return self


class ReviewRecord(FrozenModel):
    identity: Literal["AI_METHOD_REVIEWED", "DETERMINISTIC_CHECKED", "EXTERNAL_REVIEWED"]
    reviewer: str
    reviewed_at: AwareDatetime
    reference: str = Field(min_length=1)
    note: str

    @model_validator(mode="after")
    def honest_identity(self):
        if self.identity == "EXTERNAL_REVIEWED" and self.reviewer.upper() in {
            "CODEX",
            "AI",
            "LLM",
            "DEEPSEEK",
            "DETERMINISTIC",
        }:
            raise ValueError("AI/program review is not external review")
        return self


class MethodCard(FrozenModel):
    method_id: str = Field(pattern=r"^M0[1-8]$")
    version: str
    title: str
    source_refs: tuple[str, ...] = Field(min_length=1)
    applicable_scopes: tuple[Scope, ...] = Field(min_length=1)
    horizon: Literal["MEDIUM_TERM", "DAILY_PROXY", "SHORT_TERM_OBSERVATION"]
    input_requirements: tuple[str, ...] = Field(min_length=1)
    procedure: tuple[str, ...] = Field(min_length=1)
    counterexamples: tuple[str, ...] = Field(min_length=1)
    allowed_outputs: tuple[str, ...] = Field(min_length=1)
    forbidden_inferences: tuple[str, ...] = Field(min_length=1)
    review_records: tuple[ReviewRecord, ...] = Field(min_length=1)
    validation_status: Literal["UNVALIDATED", "FUNCTIONAL_ONLY", "PROSPECTIVE_VALIDATED"]


class MethodSelection(FrozenModel):
    method_id: str
    version: str
    selected_at: AwareDatetime
    applicability: Literal["APPLICABLE", "MISSING_INPUTS", "WRONG_SCOPE", "WRONG_HORIZON"]
    missing_inputs: tuple[str, ...]
    reason: str


class CandidateDecision(FrozenModel):
    security_id: str
    universe_ref: str
    leader_type: Literal["BUSINESS", "SUBSECTOR", "TRADING", "LARGE_REPRESENTATIVE", "UNKNOWN"]
    business_evidence_ids: tuple[str, ...]
    eligibility: Literal["ELIGIBLE", "EXCLUDED", "MISSING_DATA"]
    dimension_results: tuple[tuple[str, str], ...]
    exclusion_reasons: tuple[str, ...]
    comparison_refs: tuple[str, ...]
    rule_version: str


class CandidateUniverse(FrozenModel):
    universe_id: str
    members: tuple[str, ...]
    classification_version: str
    membership_at: AwareDatetime
    source_refs: tuple[str, ...] = Field(min_length=1)
    business_scope: str


class ScreeningInputs(FrozenModel):
    decisions: tuple[CandidateDecision, ...]
    evidence: EvidenceBundle
    rule_version: str


class ScreeningResult(FrozenModel):
    selected: tuple[CandidateDecision, ...] = Field(max_length=3)
    excluded: tuple[CandidateDecision, ...]
    insufficient: tuple[CandidateDecision, ...]
    coverage: CoverageReport
    rule_version: str


class FlowPoint(FrozenModel):
    session: date
    value: ExactDecimal | None
    source_ref: str
    unit: Literal["CNY", "CNY/share", "shares", "ratio"]
    missing_reason: str | None = None

    @model_validator(mode="after")
    def missing(self):
        if (self.value is None) != bool(self.missing_reason):
            raise ValueError("missing flow is not zero and needs a reason")
        return self


class FlowInputs(FrozenModel):
    # Separate series; their values must never be added into a total money flow.
    industry_turnover: tuple[FlowPoint, ...] = ()
    market_turnover: tuple[FlowPoint, ...] = ()
    vendor_estimate: tuple[FlowPoint, ...] = ()
    margin_balance: tuple[FlowPoint, ...] = ()
    etf_shares: tuple[FlowPoint, ...] = ()
    etf_nav: tuple[FlowPoint, ...] = ()
    etf_split_factor: tuple[FlowPoint, ...] = ()
    margin_buy: tuple[FlowPoint, ...] = ()
    margin_repay: tuple[FlowPoint, ...] = ()
    security_turnover: tuple[FlowPoint, ...] = ()
    security_close: tuple[FlowPoint, ...] = ()
    market_close: tuple[FlowPoint, ...] = ()
    membership_ref: str
    definition_version: str


class FlowWindowSpec(FrozenModel):
    end_date: date
    sessions: tuple[date, ...]
    windows: tuple[Literal[5, 20, 60], ...] = (5, 20, 60)
    definition_version: str

    @model_validator(mode="after")
    def ordered(self):
        if tuple(sorted(set(self.sessions))) != self.sessions or any(
            s > self.end_date for s in self.sessions
        ):
            raise ValueError("flow calendar must be unique, ordered and bounded")
        if self.windows != (5, 20, 60):
            raise ValueError("flow windows are fixed at five, twenty and sixty sessions")
        return self


class FlowMetric(FrozenModel):
    metric_id: str
    label: str
    value: ExactDecimal | None
    unit: Literal["CNY", "shares", "ratio"]
    status: Literal["AVAILABLE", "UNAVAILABLE", "CONFLICT"]
    actual_count: int = Field(ge=0)
    expected_count: int = Field(ge=1)
    source_refs: tuple[str, ...]
    formula: str
    note: str

    @model_validator(mode="after")
    def available_value(self):
        if (self.status == "AVAILABLE") != (self.value is not None):
            raise ValueError("missing/conflicting flow calculation cannot carry a value")
        if self.status == "AVAILABLE" and self.actual_count != self.expected_count:
            raise ValueError("complete window requires every expected session")
        return self


class FlowWindowReport(FrozenModel):
    window: Literal[5, 20, 60]
    sessions: tuple[date, ...]
    baseline_date: date | None
    metrics: tuple[FlowMetric, ...]
    divergence: Literal[
        "PRICE_UP_MARGIN_DOWN",
        "PRICE_DOWN_MARGIN_UP",
        "ALIGNED_UP",
        "ALIGNED_DOWN",
        "MIXED",
        "UNAVAILABLE",
    ]


class FlowReport(FrozenModel):
    method_version: str
    subject_id: str
    end_date: date
    status: Literal["PARTIAL", "UNAVAILABLE"]
    windows: tuple[FlowWindowReport, ...]
    series: dict[str, tuple[FlowPoint, ...]]
    limitations: tuple[str, ...]


class EventRecord(FrozenModel):
    event_id: str
    subject_id: str
    published_at: AwareDatetime | None
    published_date: date
    time_precision: Literal["second", "minute", "day", "unknown"]
    available_at: AwareDatetime
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    expectation_basis_refs: tuple[str, ...] = ()


class PriceWindow(FrozenModel):
    sessions: tuple[date, ...]
    security_prices: tuple[ExactDecimal, ...]
    industry_prices: tuple[ExactDecimal, ...]
    market_prices: tuple[ExactDecimal, ...]
    adjustment: Literal["raw", "qfq", "hfq"]
    source_refs: tuple[str, ...]


class AttributionObservation(FrozenModel):
    event_id: str
    effective_session: date
    observation: str
    security_return: ExactDecimal | None
    industry_return: ExactDecimal | None
    market_return: ExactDecimal | None
    alternative_explanations: tuple[str, ...]
    limitations: tuple[str, ...]


class ExpectationsInputs(FrozenModel):
    benchmark_kind: Literal["GUIDANCE", "HISTORICAL_TREND", "CONSENSUS", "NONE"]
    benchmark_refs: tuple[str, ...]
    valuation_inputs: tuple[tuple[str, ExactDecimal], ...]
    fixed_assumptions: tuple[tuple[str, ExactDecimal], ...]
    solve_variable: str | None
    input_evidence_ids: tuple[str, ...]


class ExpectationsReport(FrozenModel):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    comparison: str
    implied_value: ExactDecimal | None
    sensitivity: tuple[tuple[str, ExactDecimal], ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]


class ForecastRecord(FrozenModel):
    schema_version: Literal["decision-v1"] = "decision-v1"
    forecast_id: str
    run_id: str
    subject_id: str
    issued_at: AwareDatetime
    cutoff_at: AwareDatetime
    method_versions: tuple[str, ...]
    model_version: str
    prompt_version: str
    outlook: Outlook
    stance: Stance
    horizon_months: int = Field(ge=1, le=3)
    observation_windows: tuple[Literal[20, 40, 60], ...] = (20, 40, 60)
    benchmark_refs: tuple[str, ...] = Field(min_length=1)
    return_basis: Literal["PRICE", "ADJUSTED_PROXY", "TOTAL_RETURN_VERIFIED"]
    catalysts: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_status: Quality
    supersedes_id: str | None = None

    @model_validator(mode="after")
    def times(self):
        if self.issued_at < self.cutoff_at:
            raise ValueError("forecast precedes its evidence cutoff")
        if self.observation_windows != (20, 40, 60):
            raise ValueError("v1 fixes 20/40/60 trading-day observations")
        return self


class OutcomeInputs(FrozenModel):
    observed_at: AwareDatetime
    calendar_ref: str
    prices: PriceWindow
    corporate_actions_complete: bool
    return_basis: Literal["PRICE", "ADJUSTED_PROXY", "TOTAL_RETURN_VERIFIED"]
    independent_reference_ref: str | None = None


class OutcomeRecord(FrozenModel):
    forecast_id: str
    observed_at: AwareDatetime
    window: Literal[20, 40, 60]
    maturity: Literal["PENDING", "MATURE", "UNAVAILABLE"]
    actual_dates: tuple[date, ...]
    return_basis: Literal["PRICE", "ADJUSTED_PROXY", "TOTAL_RETURN_VERIFIED"]
    security_return: ExactDecimal | None = None
    industry_return: ExactDecimal | None = None
    market_return: ExactDecimal | None = None
    relative_returns: tuple[tuple[str, ExactDecimal], ...] = ()
    max_adverse_excursion: ExactDecimal | None = None
    catalyst_observations: tuple[str, ...] = ()
    invalidation_observations: tuple[str, ...] = ()
    quality_status: Quality

    @model_validator(mode="after")
    def no_immature_returns(self):
        if self.maturity != "MATURE" and (
            any(
                v is not None
                for v in (
                    self.security_return,
                    self.industry_return,
                    self.market_return,
                    self.max_adverse_excursion,
                )
            )
            or self.relative_returns
        ):
            raise ValueError("immature/missing outcome cannot have realised returns")
        return self


class SectorDaily(FrozenModel):
    session: date
    turnover: ExactDecimal | None
    members_total: int = Field(ge=1)
    members_available: int = Field(ge=0)

    @model_validator(mode="after")
    def valid(self):
        if self.members_available > self.members_total:
            raise ValueError("invalid member coverage")
        if self.turnover is not None and self.turnover < 0:
            raise ValueError("negative turnover")
        return self


class SectorSeries(FrozenModel):
    industry_id: str
    membership_ref: str
    days: tuple[SectorDaily, ...]


class SectorHeatInputs(FrozenModel):
    classification_version: str
    classification_level: Literal["INDUSTRY"]
    source_family: str
    sessions: tuple[date, ...] = Field(min_length=20)
    sectors: tuple[SectorSeries, ...]
    market_turnover: tuple[FlowPoint, ...]
    market_coverage_complete: bool
    universe_ref: str
    cutoff_at: AwareDatetime


class SectorSelectionRecord(FrozenModel):
    selected_at: AwareDatetime
    data_cutoff: AwareDatetime
    classification_version: str
    coverage_ref: str
    ranking_method: str
    metric_refs: tuple[str, ...]
    chosen_scope: str
    selection_reason: str
    provisional: bool


class EvaluationCase(FrozenModel):
    case_id: str = Field(pattern=r"^(?:B(?:0[1-9]|1[0-9]|2[0-4])|[DE](?:0[1-9]|1[0-2]))$")
    split: Literal["development", "holdout"]
    family: str
    material_kind: Literal["SYNTHETIC_GATE_FIXTURE", "LICENSED_SOURCE"]
    material_ref: str
    fixture_json: str
    allowed: tuple[str, ...] = Field(min_length=1)
    forbidden: tuple[str, ...] = Field(min_length=1)
    expected_gaps: tuple[str, ...]
    review_identity: Literal["AI_METHOD_REVIEWED", "DETERMINISTIC_CHECKED"]
    status: Literal["REGISTERED", "EXECUTED", "REVIEWED"] = "REGISTERED"
