"""Descriptive direction metrics; flat and missing samples never disappear."""

from collections import Counter
from decimal import Decimal


def ratio(n, d):
    return dict(numerator=n, denominator=d, value=str(Decimal(n) / Decimal(d)) if d else None)


def statistics(records):
    scored = [r for r in records if r.get("actual") in ("UP", "DOWN", "FLAT")]
    counts = Counter(r["actual"] for r in scored)
    correct = sum(r["prediction"] == r["actual"] for r in scored)
    recalls = {
        v: ratio(sum(r["actual"] == v and r["prediction"] == v for r in scored), counts[v])
        for v in ("UP", "DOWN")
    }
    balanced = (
        str((Decimal(recalls["UP"]["value"]) + Decimal(recalls["DOWN"]["value"])) / 2)
        if all(recalls[v]["value"] is not None for v in recalls)
        else None
    )
    baselines = {}
    for name in ("ALWAYS_UP", "ALWAYS_DOWN", "PERSISTENCE"):
        common = [r for r in scored if name in r.get("baselines", {})]
        baselines[name] = ratio(
            sum(r["baselines"][name] == r["actual"] for r in common), len(common)
        )
    return dict(
        accuracy=ratio(correct, len(scored)),
        nonflat_accuracy=ratio(correct, counts["UP"] + counts["DOWN"]),
        recalls=recalls,
        balanced_accuracy=balanced,
        actual_distribution={v: counts[v] for v in ("UP", "DOWN", "FLAT")},
        baselines=baselines,
        independent_dates=len({r["base_session"] for r in scored}),
        uncertainty="DESCRIPTIVE_ONLY",
        predictive_validity="UNVERIFIED",
    )
