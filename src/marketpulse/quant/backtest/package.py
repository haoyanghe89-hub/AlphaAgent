"""Full-package admission and PIT selection, independent of network and execution.

Reviewed evidence is supplied by the internal evidence repository, never by an
HTTP client's completeness/STRICT/MATCH declarations. Synthetic registries are
only appropriate for tests; a package without actual reviews remains BLOCKED.
"""

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field

from marketpulse.infrastructure.storage.models import BlobNotFoundError

from ..contracts import digest
from ..domain import ExactDecimal, FrozenModel, InstrumentAlias
from .selection import Candidate, select_at

Hash = Field(pattern=r"^[0-9a-f]{64}$")


class Evidence(FrozenModel):
    source_hash: str = Hash
    source_family: str = Field(min_length=1, max_length=100)


class Review(FrozenModel):
    """Server-owned review binding actual content, semantic payload and scope."""

    domain: str
    payload_hash: str = Hash
    source_hash: str = Hash
    reference_hash: str = Hash
    reference_family: str = Field(min_length=1, max_length=100, pattern=r"\S")
    scope_hash: str = Hash
    outcome: Literal["MATCH", "CONFLICT", "METHOD_DIFFERENCE", "UNAVAILABLE"]
    strict_pit: bool = False
    complete_archive: bool = False


class Security(FrozenModel):
    instrument_id: str = Field(pattern=r"^ins_[a-zA-Z0-9_-]{8,}$")
    listed_at: date
    delisted_at: date | None = None
    aliases: tuple[InstrumentAlias, ...] = ()


class AnnualInput(FrozenModel):
    year: int
    signal_day: date
    execution_day: date
    cutoff: AwareDatetime
    members: tuple[str, ...]
    evidence: Evidence
    candidates: tuple[Candidate, ...] = Field(max_length=20000)


class DailyInput(FrozenModel):
    instrument_id: str
    session: date
    open: ExactDecimal | None = Field(default=None, gt=0)
    close: ExactDecimal | None = Field(default=None, gt=0)
    suspended: bool | None = None
    buy_allowed: bool | None = None
    sell_allowed: bool | None = None
    lot_size: int | None = Field(default=None, ge=1, le=10000)
    rule_version: str | None = None
    rule_effective_from: date | None = None
    rule_effective_to: date | None = None
    evidence: Evidence


class ActionArchive(FrozenModel):
    instrument_id: str
    start: date
    end: date
    # The reviewed canonical event archive is bound separately; an empty event
    # list only proves absence when an independent completeness review exists.
    events_hash: str = Hash
    evidence: Evidence


class DailyPartition(FrozenModel):
    instrument_id: str
    start: date
    end: date
    rows_hash: str = Hash
    evidence: Evidence


class BenchmarkInput(FrozenModel):
    basis: Literal["price_index", "total_return_index"]
    code: Literal["000300", "H00300"]
    points: tuple[tuple[date, ExactDecimal], ...]
    evidence: Evidence


class ActionEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    instrument_id: str
    kind: Literal["cash_dividend", "share_distribution", "rights_issue", "delisting"]
    known_at: AwareDatetime
    ex_day: date
    record_day: date | None = None
    settlement_day: date | None = None
    cash_per_share: ExactDecimal | None = Field(default=None, ge=0)
    shares_per_share: ExactDecimal | None = Field(default=None, ge=0)
    subscription_price: ExactDecimal | None = Field(default=None, ge=0)


class DataPackage(FrozenModel):
    schema_version: Literal["backtest-data-package-v1"] = "backtest-data-package-v1"
    start: date
    end: date
    sessions: tuple[date, ...] = Field(min_length=1, max_length=2200)
    calendar_evidence: Evidence
    securities: tuple[Security, ...] = Field(max_length=20000)
    lifecycle_evidence: Evidence
    annual: tuple[AnnualInput, ...] = Field(max_length=8)
    daily: tuple[DailyPartition, ...] = Field(max_length=100000)
    financial_sources: tuple[Evidence, ...] = Field(default=(), max_length=100000)
    actions: tuple[ActionArchive, ...] = Field(max_length=20000)
    benchmarks: tuple[BenchmarkInput, ...] = Field(max_length=2)


def scope_hash(package):
    return digest(dict(start=str(package.start), end=str(package.end), market="XSHG_XSHE_A"))


def review_subject(domain, payload, evidence, scope):
    return domain, digest(payload), evidence.source_hash, scope


def admit(package: DataPackage, reviewed_sources: tuple[Review, ...], conditions, resolve):
    """Return immutable-hash admission; never issue trades or calculate returns."""
    if package.end < package.start or (package.end - package.start).days > 2923:
        raise ValueError("ordered package scope of at most eight years required")
    sessions = package.sessions
    if sessions != tuple(sorted(set(sessions))) or not all(
        package.start <= day <= package.end for day in sessions
    ):
        raise ValueError("calendar must be unique, ordered and within scope")
    if len({s.instrument_id for s in package.securities}) != len(package.securities):
        raise ValueError("duplicate lifecycle identity")
    if len({a.year for a in package.annual}) != len(package.annual):
        raise ValueError("duplicate annual signal")
    if len({d.rows_hash for d in package.daily}) != len(package.daily):
        raise ValueError("duplicate daily partition")
    if len({a.instrument_id for a in package.actions}) != len(package.actions):
        raise ValueError("duplicate action archive")
    if len({b.basis for b in package.benchmarks}) != len(package.benchmarks):
        raise ValueError("duplicate benchmark basis")
    scope = scope_hash(package)
    reviews = {}
    for review in reviewed_sources:
        key = review.domain, review.payload_hash, review.source_hash, review.scope_hash
        if key in reviews:
            raise ValueError("ambiguous evidence review")
        reviews[key] = review
    gaps = []
    # Cache only within this admission. A later replay must re-read the files,
    # even if the server-owned review registry has not changed.
    evidence_files = {}

    def evidence_file(sha, domain, role, source_hash):
        if sha not in evidence_files:
            try:
                document = resolve(sha)
            except (BlobNotFoundError, FileNotFoundError, KeyError):
                document = None
            if document is not None and (
                not isinstance(document, bytes)
                or not 0 < len(document) <= 16 * 1024 * 1024
                or hashlib.sha256(document).hexdigest() != sha
            ):
                raise ValueError("review evidence hash, empty content or byte bound mismatch")
            evidence_files[sha] = dict(
                sha256=sha,
                status="AVAILABLE" if document is not None else "UNAVAILABLE",
                size_bytes=len(document) if document is not None else None,
            )
        available = evidence_files[sha]["status"] == "AVAILABLE"
        if not available:
            gaps.append(
                dict(
                    domain=domain,
                    source_hash=source_hash,
                    evidence_hash=sha,
                    reason=f"{role}_document_unavailable",
                )
            )
        return available

    def require(domain, payload, evidence, *, strict=False, archive=False):
        review = reviews.get(review_subject(domain, payload, evidence, scope))
        source_present = evidence_file(evidence.source_hash, domain, "source", evidence.source_hash)
        reference_present = (
            evidence_file(review.reference_hash, domain, "reference", evidence.source_hash)
            if review
            else False
        )
        reason = (
            "independent_review_missing"
            if not review
            else "independent_family_required"
            if review.reference_family == evidence.source_family
            or review.reference_hash == evidence.source_hash
            else review.outcome
            if review.outcome != "MATCH"
            else "strict_pit_unproven"
            if strict and not review.strict_pit
            else "archive_incomplete"
            if archive and not review.complete_archive
            else None
        )
        if reason:
            gaps.append(dict(domain=domain, source_hash=evidence.source_hash, reason=reason))
        return reason is None and source_present and reference_present

    require("calendar", list(map(str, sessions)), package.calendar_evidence)
    require(
        "lifecycles",
        [s.model_dump(mode="json") for s in package.securities],
        package.lifecycle_evidence,
        strict=True,
        archive=True,
    )
    securities = {s.instrument_id: s for s in package.securities}
    for s in securities.values():
        if s.delisted_at and s.delisted_at < s.listed_at:
            raise ValueError("invalid lifecycle interval")
        if any(
            not (a.code.startswith("6") if a.exchange == "XSHG" else a.code.startswith(("0", "3")))
            for a in s.aliases
        ):
            raise ValueError("package scope is Shanghai/Shenzhen A shares")
    # The requested annual windows are derived from the supplied *reviewed*
    # calendar; a caller cannot omit a difficult year from the manifest.
    from .history import annual_windows

    expected = annual_windows(list(map(str, sessions)), package.start, package.end)
    if [(a.year, str(a.signal_day), str(a.execution_day)) for a in package.annual] != [
        (a["year"], a["signal_day"], a["execution_day"]) for a in expected
    ]:
        gaps.append(dict(domain="annual", reason="annual_signal_scope_incomplete"))
    selections = []
    candidate_windows = defaultdict(list)
    financial_sources = {s.source_hash: s for s in package.financial_sources}
    if len(financial_sources) != len(package.financial_sources):
        raise ValueError("duplicate financial source identity")
    for annual in package.annual:
        if annual.cutoff != datetime.combine(
            annual.signal_day, time(15), ZoneInfo("Asia/Shanghai")
        ):
            raise ValueError("signal cutoff must be the signal-day Shanghai close")
        ids = {
            s.instrument_id
            for s in securities.values()
            if s.listed_at <= annual.signal_day
            and (s.delisted_at is None or annual.signal_day < s.delisted_at)
        }
        if len(set(annual.members)) != len(annual.members) or set(annual.members) != ids:
            gaps.append(
                dict(domain="universe", year=annual.year, reason="membership_lifecycle_mismatch")
            )
        candidate_ids = {c.instrument_id for c in annual.candidates}
        if candidate_ids != set(annual.members):
            gaps.append(
                dict(domain="financials", year=annual.year, reason="candidate_coverage_incomplete")
            )
        require("universe", sorted(annual.members), annual.evidence, strict=True, archive=True)
        for candidate in annual.candidates:
            candidate_windows[candidate.instrument_id].append((annual, candidate))
            if candidate.membership_source_hash != annual.evidence.source_hash:
                gaps.append(
                    dict(
                        domain="universe",
                        year=annual.year,
                        reason="candidate_membership_source_mismatch",
                    )
                )
            # Bind the entire visible version inventory and same-signal price;
            # STRICT strings inside Candidate alone never prove their provenance.
            visible = [
                v.model_dump(mode="json")
                for v in candidate.financials
                if v.version_known_at <= annual.cutoff
            ]
            payload = dict(
                instrument_id=candidate.instrument_id,
                cutoff=annual.cutoff.isoformat(),
                versions=sorted(visible, key=digest),
            )
            for source in {
                v.source_hash for v in candidate.financials if v.version_known_at <= annual.cutoff
            }:
                evidence = financial_sources.get(source)
                if evidence is None:
                    gaps.append(
                        dict(
                            domain="financial_versions",
                            reason="source_family_unavailable",
                            source_hash=source,
                        )
                    )
                else:
                    require("financial_versions", payload, evidence, strict=True, archive=True)
        selection = select_at(annual.candidates, annual.cutoff, conditions)
        selections.append(dict(year=annual.year, selection=selection))
        if selection["status"] == "BLOCKED":
            gaps.append(
                dict(domain="selection", year=annual.year, reason="candidate_inputs_unavailable")
            )
        for candidate, result in zip(
            sorted(annual.candidates, key=lambda c: c.instrument_id),
            selection["candidates"],
            strict=True,
        ):
            if any(
                result["metrics"].get(metric) is None
                for metric in ("pe", "roe", "revenue_growth", "ttm_parent_profit")
            ):
                gaps.append(
                    dict(
                        domain="metrics",
                        year=annual.year,
                        instrument_id=candidate.instrument_id,
                        reason="required_metric_missing",
                    )
                )
            if candidate.historical_member is not True:
                gaps.append(
                    dict(domain="universe", year=annual.year, reason="member_flag_not_confirmed")
                )
            for metric in ("pe", "roe", "revenue_growth", "ttm_parent_profit"):
                payload = dict(
                    instrument_id=candidate.instrument_id,
                    cutoff=annual.cutoff.isoformat(),
                    metric=metric,
                    selection_hash=selection["output_hash"],
                )
                require("metric_" + metric, payload, annual.evidence)
    partitions = defaultdict(list)
    for part in package.daily:
        if (
            part.instrument_id not in securities
            or part.end < part.start
            or (part.end - part.start).days > 1096
            or part.start < package.start
            or part.end > package.end
        ):
            raise ValueError("daily partition scope mismatch")
        partitions[part.instrument_id].append(part)
    missing_daily = unknown_execution = 0
    for security in securities.values():
        active = [
            d
            for d in sessions
            if d >= security.listed_at
            and (security.delisted_at is None or d < security.delisted_at)
        ]
        daily = {}
        if any(
            len(
                [
                    a
                    for a in security.aliases
                    if a.valid_from <= day and (a.valid_to is None or day <= a.valid_to)
                ]
            )
            != 1
            for day in active
        ):
            gaps.append(
                dict(
                    domain="lifecycles",
                    instrument_id=security.instrument_id,
                    reason="historical_alias_missing_or_ambiguous",
                )
            )
        for part in sorted(partitions[security.instrument_id], key=lambda p: p.start):
            document = resolve(part.rows_hash)
            if document is None:
                gaps.append(
                    dict(
                        domain="raw_prices",
                        reason="partition_unavailable",
                        source_hash=part.rows_hash,
                    )
                )
                continue
            if (
                len(document) > 16 * 1024 * 1024
                or hashlib.sha256(document).hexdigest() != part.rows_hash
            ):
                raise ValueError("daily partition hash or byte bound mismatch")
            raw = json.loads(document)
            if not isinstance(raw, list) or len(raw) > 20000:
                raise ValueError("daily partition row bound")
            rows = [DailyInput.model_validate(r) for r in raw]
            for row in rows:
                if (
                    row.instrument_id != security.instrument_id
                    or row.session not in active
                    or not part.start <= row.session <= part.end
                    or row.session in daily
                    or row.evidence != part.evidence
                ):
                    raise ValueError("daily input outside lifecycle, source or duplicate")
                daily[row.session] = row
            payload = part.model_dump(mode="json")
            require("raw_prices", payload, part.evidence, strict=True)
            require("execution_restrictions", payload, part.evidence, strict=True, archive=True)
        for day in active:
            row = daily.get(day)
            if not row:
                missing_daily += 1
                continue
            if (
                row.suspended is None
                or row.buy_allowed is None
                or row.sell_allowed is None
                or not row.rule_version
                or not row.lot_size
                or not row.rule_effective_from
                or row.rule_effective_from > day
                or (row.rule_effective_to and row.rule_effective_to < day)
                or row.close is None
                or (not row.suspended and row.open is None)
                or (row.suspended and (row.buy_allowed or row.sell_allowed))
            ):
                unknown_execution += 1
        for annual, candidate in candidate_windows[security.instrument_id]:
            row = daily.get(annual.signal_day)
            if (
                not row
                or candidate.raw_close != row.close
                or candidate.price_source_hash != row.evidence.source_hash
            ):
                gaps.append(
                    dict(
                        domain="signal_price",
                        year=annual.year,
                        instrument_id=security.instrument_id,
                        reason="candidate_daily_price_mismatch",
                    )
                )
        archive = next(
            (a for a in package.actions if a.instrument_id == security.instrument_id), None
        )
        if not archive or archive.start > package.start or archive.end < package.end:
            gaps.append(
                dict(
                    domain="corporate_actions",
                    instrument_id=security.instrument_id,
                    reason="full_window_archive_missing",
                )
            )
        else:
            document = resolve(archive.events_hash)
            if document is None:
                gaps.append(
                    dict(
                        domain="corporate_actions",
                        instrument_id=security.instrument_id,
                        reason="event_partition_unavailable",
                    )
                )
            else:
                if (
                    len(document) > 16 * 1024 * 1024
                    or hashlib.sha256(document).hexdigest() != archive.events_hash
                ):
                    raise ValueError("corporate action partition hash or byte bound mismatch")
                raw = json.loads(document)
                if not isinstance(raw, list) or len(raw) > 20000:
                    raise ValueError("corporate action row bound")
                events = [ActionEvent.model_validate(row) for row in raw]
                if len({e.event_id for e in events}) != len(events) or any(
                    e.instrument_id != security.instrument_id
                    or not archive.start <= e.ex_day <= archive.end
                    for e in events
                ):
                    raise ValueError("corporate action scope or duplicate mismatch")
                for event in events:
                    if (
                        not event.settlement_day
                        or event.settlement_day < event.ex_day
                        or (
                            event.kind != "delisting"
                            and (not event.record_day or event.record_day > event.ex_day)
                        )
                        or (
                            event.kind in {"cash_dividend", "delisting"}
                            and event.cash_per_share is None
                        )
                        or (
                            event.kind in {"share_distribution", "rights_issue"}
                            and event.shares_per_share is None
                        )
                        or (event.kind == "rights_issue" and event.subscription_price is None)
                    ):
                        gaps.append(
                            dict(
                                domain="corporate_actions",
                                instrument_id=security.instrument_id,
                                reason="event_effective_terms_missing",
                            )
                        )
            require(
                "corporate_actions",
                archive.model_dump(mode="json"),
                archive.evidence,
                strict=True,
                archive=True,
            )
    if any(a.instrument_id not in securities for a in package.actions):
        raise ValueError("action archive outside universe")
    if missing_daily or unknown_execution:
        gaps.append(
            dict(
                domain="daily",
                reason="missing_or_unknown_execution",
                missing_count=missing_daily,
                unknown_count=unknown_execution,
            )
        )
    for basis, code in (("price_index", "000300"), ("total_return_index", "H00300")):
        benchmark = next((b for b in package.benchmarks if b.basis == basis), None)
        if (
            not benchmark
            or benchmark.code != code
            or tuple(d for d, _ in benchmark.points) != sessions
            or any(v <= 0 for _, v in benchmark.points)
        ):
            gaps.append(
                dict(domain="benchmark", basis=basis, reason="same_calendar_benchmark_missing")
            )
        else:
            require("benchmark_" + basis, benchmark.model_dump(mode="json"), benchmark.evidence)
    result = dict(
        schema_version="backtest-package-admission-v1",
        status="BLOCKED" if gaps else "READY_FOR_5_3",
        package_hash=digest(package.model_dump(mode="json")),
        scope_hash=scope,
        review_hash=digest(
            sorted([r.model_dump(mode="json") for r in reviewed_sources], key=digest)
        ),
        conditions_hash=digest(conditions),
        evidence_files=[evidence_files[sha] for sha in sorted(evidence_files)],
        gaps=gaps,
        annual=selections,
        execution_performed=False,
    )
    # No potentially misleading executable weights or empty-universe cash decision
    # is emitted when a single required partition lacks admission evidence.
    result["output_hash"] = digest(result)
    return result
