"""Read a sealed local acceptance record, never infer passing from installed code."""

import json


def latest(repo):
    pointer = repo.base.legacy_root.parent / "decision-release.json"
    if not pointer.is_file():
        return dict(
            status="NOT_RUN",
            executed=0,
            predictive_validity="UNVERIFIED",
            user_acceptance="PENDING",
        )
    value = json.loads(pointer.read_text(encoding="utf8"))
    report = repo.value(value["report_ref"])
    if report.get("schema_version") != "decision-release-v1":
        raise ValueError("unknown acceptance report schema")
    return dict(report, report_ref=value["report_ref"])
