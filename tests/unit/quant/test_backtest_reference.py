"""Independent checkpoint trust and numerical disagreement, synthetic inputs only."""

import copy
import hashlib

import pytest

from marketpulse.quant.backtest.reference import check, parameter_hash
from marketpulse.quant.contracts import digest


def examples():
    blobs = {}
    for raw in (b"independent external engine export", b"auditor original output"):
        blobs[hashlib.sha256(raw).hexdigest()] = raw
    sha, other = list(blobs)
    metrics = dict(total_ret="10", cagr="10", vol="2", sharpe="1", max_dd="0")
    result = dict(
        admission=dict(package_hash="a" * 64, conditions_hash="b" * 64, scope_hash="c" * 64),
        assumptions=dict(initial_cash="1000", risk_free_annual=".02"),
        conditions=dict(cost_pct_per_side=".13"),
        nav=dict(dates=["2023-12-29", "2024-12-29"], strategy=["1", "1.1"], benchmark=["1", "1.1"]),
        metrics=metrics,
        benchmark_metrics=metrics,
    )
    refs = []
    for basis in ("strategy", "benchmark"):
        ref = dict(
            basis=basis,
            parameter_hash=parameter_hash(result),
            points=[
                [d, v] for d, v in zip(result["nav"]["dates"], result["nav"][basis], strict=True)
            ],
            metrics=copy.deepcopy(metrics),
            source_hash=sha,
            source_family="external-engine",
        )
        ref["review"] = dict(
            domain="strategy_result",
            payload_hash=digest(ref),
            source_hash=sha,
            reference_hash=other,
            reference_family="external-audit",
            scope_hash="c" * 64,
            outcome="MATCH",
        )
        refs.append(ref)
    return result, refs, blobs


def reseal(ref):
    ref["review"]["payload_hash"] = digest({k: v for k, v in ref.items() if k != "review"})


def test_both_independent_checkpoints_must_match_and_no_auto_upgrade():
    result, refs, blobs = examples()
    original = copy.deepcopy(result)
    assert check(result, refs, blobs.get)["status"] == "MATCH"
    assert check(result, refs[:1], blobs.get)["status"] == "UNAVAILABLE"
    assert result == original


def test_certain_but_wrong_nav_or_metric_is_a_conflict():
    result, refs, blobs = examples()
    refs[0]["metrics"]["total_ret"] = "11"
    reseal(refs[0])
    assert check(result, refs, blobs.get)["status"] == "CONFLICT"
    refs[0]["metrics"]["total_ret"] = "10"
    refs[0]["points"][-1][1] = "1.1001"
    reseal(refs[0])
    assert check(result, refs, blobs.get)["checks"][0]["status"] == "CONFLICT"


@pytest.mark.parametrize(
    "change", ["parameters", "period", "metrics", "self-source", "raw-missing", "payload"]
)
def test_incompatible_or_untrusted_checkpoints_cannot_pass(change):
    result, refs, blobs = examples()
    ref = refs[0]
    if change == "parameters":
        ref["parameter_hash"] = "d" * 64
    if change == "period":
        ref["points"].pop()
    if change == "metrics":
        ref["metrics"].pop("cagr")
    if change == "self-source":
        ref["review"]["reference_family"] = ref["source_family"]
    if change == "raw-missing":
        blobs.pop(ref["source_hash"])
    if change != "payload":
        reseal(ref)
    else:
        ref["metrics"]["total_ret"] = "99"
    outcome = check(result, refs, blobs.get)
    assert outcome["status"] == "UNAVAILABLE"
    assert outcome["checks"][0]["status"] == (
        "METHOD_DIFFERENCE" if change in {"parameters", "period", "metrics"} else "UNAVAILABLE"
    )


def test_tampered_raw_reference_rejected():
    result, refs, blobs = examples()
    blobs[refs[0]["source_hash"]] = b"tampered"
    with pytest.raises(ValueError, match="hash mismatch"):
        check(result, refs, blobs.get)
