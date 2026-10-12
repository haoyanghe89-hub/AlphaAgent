"""Fully synthetic package and internal review registry, never preview data."""

import hashlib
import json

import pytest

from marketpulse.quant.backtest.package import DataPackage, Review, admit, scope_hash
from marketpulse.quant.backtest.selection import select_at
from marketpulse.quant.contracts import canonical, digest
from tests.unit.quant.test_backtest_selection import CUTOFF, candidate, version


def fixture():
    c = candidate()
    # Real bytes and content identities, still entirely synthetic. A registry
    # containing MATCH strings without its source documents must never suffice.
    originals = {key * 64: canonical({"synthetic_source": key}).encode() for key in "abc4ef98"}
    hashes = {key: hashlib.sha256(value).hexdigest() for key, value in originals.items()}
    blobs = {hashes[key]: value for key, value in originals.items()}

    def bind(value):
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        return hashes.get(value, value) if isinstance(value, str) else value

    c = type(c).model_validate(bind(c.model_dump(mode="json")))
    evidence = dict(source_hash=hashes["c" * 64], source_family="synthetic_feed")
    days = ["2024-04-30", "2024-05-06", "2024-05-07"]
    rows = [
        dict(
            instrument_id=c.instrument_id,
            session=d,
            open="10",
            close="10",
            suspended=False,
            buy_allowed=True,
            sell_allowed=True,
            lot_size=100,
            rule_version="synthetic-rule",
            rule_effective_from="2020-01-01",
            evidence=evidence,
        )
        for d in days
    ]
    document = canonical(rows).encode()
    raw = dict(
        start=days[0],
        end=days[-1],
        sessions=days,
        calendar_evidence=dict(source_hash="4" * 64, source_family="synthetic_calendar"),
        securities=[
            dict(
                instrument_id=c.instrument_id,
                listed_at="2020-01-01",
                aliases=[dict(exchange="XSHG", code="600000", valid_from="2020-01-01")],
            )
        ],
        lifecycle_evidence=dict(source_hash="b" * 64, source_family="synthetic_members"),
        annual=[
            dict(
                year=2024,
                signal_day=days[0],
                execution_day=days[1],
                cutoff=CUTOFF,
                members=[c.instrument_id],
                evidence=dict(source_hash="b" * 64, source_family="synthetic_members"),
                candidates=[c],
            )
        ],
        daily=[
            dict(
                instrument_id=c.instrument_id,
                start=days[0],
                end=days[-1],
                rows_hash=digest(rows),
                evidence=evidence,
            )
        ],
        financial_sources=[dict(source_hash="a" * 64, source_family="synthetic_financials")],
        actions=[
            dict(
                instrument_id=c.instrument_id,
                start=days[0],
                end=days[-1],
                events_hash=digest([]),
                evidence=dict(source_hash="e" * 64, source_family="synthetic_actions"),
            )
        ],
        benchmarks=[
            dict(
                code=code,
                basis=basis,
                points=[(d, "100") for d in days],
                evidence=dict(source_hash=sha * 64, source_family="synthetic_index"),
            )
            for code, basis, sha in [
                ("000300", "price_index", "f"),
                ("H00300", "total_return_index", "9"),
            ]
        ],
    )
    package = DataPackage.model_validate(
        bind(DataPackage.model_validate(raw).model_dump(mode="json"))
    )
    proofs = []

    def review(domain, payload, source):
        proofs.append(
            Review(
                domain=domain,
                payload_hash=digest(payload),
                source_hash=source.source_hash,
                reference_hash=hashes["8" * 64],
                reference_family="synthetic_independent",
                scope_hash=scope_hash(package),
                outcome="MATCH",
                strict_pit=True,
                complete_archive=True,
            )
        )

    review("calendar", days, package.calendar_evidence)
    review(
        "lifecycles",
        [s.model_dump(mode="json") for s in package.securities],
        package.lifecycle_evidence,
    )
    annual = package.annual[0]
    review("universe", sorted(annual.members), annual.evidence)
    visible = [v.model_dump(mode="json") for v in c.financials]
    review(
        "financial_versions",
        dict(
            instrument_id=c.instrument_id,
            cutoff=annual.cutoff.isoformat(),
            versions=sorted(visible, key=digest),
        ),
        package.financial_sources[0],
    )
    selected = select_at((c,), CUTOFF, {})
    for metric in ("pe", "roe", "revenue_growth", "ttm_parent_profit"):
        review(
            "metric_" + metric,
            dict(
                instrument_id=c.instrument_id,
                cutoff=annual.cutoff.isoformat(),
                metric=metric,
                selection_hash=selected["output_hash"],
            ),
            annual.evidence,
        )
    for domain in ("raw_prices", "execution_restrictions"):
        review(domain, package.daily[0].model_dump(mode="json"), package.daily[0].evidence)
    review(
        "corporate_actions", package.actions[0].model_dump(mode="json"), package.actions[0].evidence
    )
    for benchmark in package.benchmarks:
        review(
            "benchmark_" + benchmark.basis, benchmark.model_dump(mode="json"), benchmark.evidence
        )
    return (
        package,
        tuple(proofs),
        {**blobs, digest(rows): document, digest([]): canonical([]).encode()},
    )


@pytest.mark.parametrize("role", ["source_hash", "reference_hash"])
@pytest.mark.parametrize(
    "domain",
    [
        "calendar",
        "financial_versions",
        "execution_restrictions",
        "corporate_actions",
        "benchmark_total_return_index",
    ],
)
def test_review_requires_its_actual_source_and_reference_bytes(domain, role):
    p, reviews, blobs = fixture()
    review = next(r for r in reviews if r.domain == domain)
    missing = getattr(review, role)
    blobs.pop(missing)
    out = admit(p, reviews, {}, blobs.get)
    assert out["status"] == "BLOCKED"
    assert any(g["domain"] == domain and g.get("evidence_hash") == missing for g in out["gaps"])


@pytest.mark.parametrize("role", ["source_hash", "reference_hash"])
def test_corrupt_review_document_cannot_pass_with_matching_registry(role):
    p, reviews, blobs = fixture()
    blobs[getattr(reviews[0], role)] = b"tampered"
    with pytest.raises(ValueError, match="evidence.*hash"):
        admit(p, reviews, {}, blobs.get)


def test_review_files_are_read_once_per_admission_and_checked_again_on_replay():
    from collections import Counter

    p, reviews, blobs = fixture()
    reads = Counter()

    def resolve(sha):
        reads[sha] += 1
        return blobs.get(sha)

    result = admit(p, reviews, {}, resolve)
    assert result["status"] == "READY_FOR_5_3"
    assert len(result["evidence_files"]) == 8
    assert all(
        row["status"] == "AVAILABLE" and row["size_bytes"] > 0 for row in result["evidence_files"]
    )
    assert reads[reviews[0].reference_hash] == 1
    blobs[reviews[0].reference_hash] = b"changed after first validation"
    with pytest.raises(ValueError, match="evidence.*hash"):
        admit(p, reviews, {}, resolve)


@pytest.mark.parametrize("size", [0, 16 * 1024 * 1024 + 1], ids=["empty", "over_limit"])
def test_empty_or_oversized_reference_is_rejected_even_with_its_correct_hash(size):
    p, reviews, blobs = fixture()
    content = b"x" * size
    sha = hashlib.sha256(content).hexdigest()
    revised = (reviews[0].model_copy(update={"reference_hash": sha}), *reviews[1:])
    blobs[sha] = content
    with pytest.raises(ValueError, match="evidence.*byte bound"):
        admit(p, revised, {}, blobs.get)


def test_blob_store_missing_error_is_reported_as_a_gap():
    from marketpulse.infrastructure.storage.models import BlobNotFoundError, BlobRef

    p, reviews, blobs = fixture()
    missing = reviews[0].reference_hash

    def resolve(sha):
        if sha == missing:
            raise BlobNotFoundError(BlobRef(sha))
        return blobs.get(sha)

    result = admit(p, reviews, {}, resolve)
    assert result["status"] == "BLOCKED"
    assert any(g["reason"] == "reference_document_unavailable" for g in result["gaps"])


def test_full_synthetic_package_needs_internal_reviews_and_is_reproducible():
    p, reviews, blobs = fixture()
    blocked = admit(p, (), {}, blobs.get)
    assert blocked["status"] == "BLOCKED" and blocked["gaps"]
    result = admit(p, reviews, {}, blobs.get)
    assert result["status"] == "READY_FOR_5_3", result["gaps"]
    assert result["execution_performed"] is False and "targets" not in result
    assert admit(p, reviews, {}, blobs.get) == result


@pytest.mark.parametrize(
    "domain",
    [
        "calendar",
        "lifecycles",
        "universe",
        "financial_versions",
        "metric_pe",
        "metric_roe",
        "metric_revenue_growth",
        "metric_ttm_parent_profit",
        "raw_prices",
        "execution_restrictions",
        "corporate_actions",
        "benchmark_price_index",
        "benchmark_total_return_index",
    ],
)
def test_every_missing_review_blocks_admission(domain):
    p, reviews, blobs = fixture()
    out = admit(p, tuple(r for r in reviews if r.domain != domain), {}, blobs.get)
    assert out["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "CONFLICT"},
        {"outcome": "METHOD_DIFFERENCE"},
        {"outcome": "UNAVAILABLE"},
        {"reference_family": "synthetic_financials"},
        {"strict_pit": False},
        {"complete_archive": False},
    ],
)
def test_financial_review_conflict_basis_same_family_or_incomplete_version_blocks(change):
    p, reviews, blobs = fixture()
    revised = tuple(
        r.model_copy(update=change) if r.domain == "financial_versions" else r for r in reviews
    )
    assert admit(p, revised, {}, blobs.get)["status"] == "BLOCKED"


def test_missing_partition_and_corruption_cannot_be_zero_or_complete():
    p, reviews, blobs = fixture()
    assert admit(p, reviews, {}, lambda _: None)["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="hash"):
        admit(p, reviews, {}, lambda _: b"corrupted")
    with pytest.raises(ValueError, match="duplicate daily"):
        admit(p.model_copy(update={"daily": p.daily + p.daily}), reviews, {}, blobs.get)


def test_omitted_annual_member_candidate_actions_or_benchmark_blocks():
    p, reviews, blobs = fixture()
    for change in (
        {"annual": ()},
        {"actions": ()},
        {"benchmarks": p.benchmarks[:1]},
        {"annual": (p.annual[0].model_copy(update={"candidates": ()}),)},
        {"annual": (p.annual[0].model_copy(update={"members": ()}),)},
    ):
        assert admit(p.model_copy(update=change), reviews, {}, blobs.get)["status"] == "BLOCKED"


def test_future_revision_keeps_historical_selection_and_does_not_require_future_review():
    p, reviews, blobs = fixture()
    original = p.annual[0].candidates[0]
    future = version(
        "parent_net_profit",
        300,
        "2024-03-31",
        "ytd",
        "2024-01-01",
        known="2024-05-02T00:00:00Z",
        sha="d" * 64,
    )
    annual = p.annual[0].model_copy(
        update={
            "candidates": (
                original.model_copy(update={"financials": original.financials + (future,)}),
            )
        }
    )
    revised = p.model_copy(update={"annual": (annual,)})
    first = admit(p, reviews, {}, blobs.get)
    second = admit(revised, reviews, {}, blobs.get)
    assert second["status"] == "READY_FOR_5_3", second["gaps"]
    assert first["annual"] == second["annual"]
    assert first["package_hash"] != second["package_hash"]


def test_action_partition_cannot_be_missing_corrupt_or_effective_terms_unknown():
    p, reviews, blobs = fixture()
    no_actions = {k: v for k, v in blobs.items() if k != digest([])}
    out = admit(p, reviews, {}, no_actions.get)
    assert any(g["reason"] == "event_partition_unavailable" for g in out["gaps"])
    corrupt = {**blobs, digest([]): b"corrupt actions"}
    with pytest.raises(ValueError, match="corporate action partition"):
        admit(p, reviews, {}, corrupt.get)
    events = [
        dict(
            event_id="synthetic-dividend",
            instrument_id=p.securities[0].instrument_id,
            kind="cash_dividend",
            known_at="2024-04-01T00:00:00Z",
            ex_day="2024-05-06",
            record_day="2024-04-30",
            cash_per_share="1",
        )
    ]
    archive = p.actions[0].model_copy(update={"events_hash": digest(events)})
    revised = p.model_copy(update={"actions": (archive,)})
    out = admit(revised, reviews, {}, {**blobs, digest(events): canonical(events).encode()}.get)
    assert any(g["reason"] == "event_effective_terms_missing" for g in out["gaps"])


def test_missing_historical_alias_and_unknown_execution_never_pass():
    p, reviews, blobs = fixture()
    revised = p.model_copy(
        update={"securities": (p.securities[0].model_copy(update={"aliases": ()}),)}
    )
    out = admit(revised, reviews, {}, blobs.get)
    assert any(g["reason"] == "historical_alias_missing_or_ambiguous" for g in out["gaps"])
    rows = json.loads(blobs[p.daily[0].rows_hash])
    rows[0]["buy_allowed"] = None
    part = p.daily[0].model_copy(update={"rows_hash": digest(rows)})
    out = admit(
        p.model_copy(update={"daily": (part,)}),
        reviews,
        {},
        {**blobs, digest(rows): canonical(rows).encode()}.get,
    )
    assert any(
        g["reason"] == "missing_or_unknown_execution" and g["unknown_count"] == 1
        for g in out["gaps"]
    )


@pytest.mark.parametrize("domain", ["raw_prices", "execution_restrictions", "corporate_actions"])
def test_strict_strings_cannot_override_partial_source_review(domain):
    p, reviews, blobs = fixture()
    revised = tuple(
        r.model_copy(update={"strict_pit": False}) if r.domain == domain else r for r in reviews
    )
    out = admit(p, revised, {}, blobs.get)
    assert out["status"] == "BLOCKED"
    assert any(g["domain"] == domain and g["reason"] == "strict_pit_unproven" for g in out["gaps"])
