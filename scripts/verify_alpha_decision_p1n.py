"""Read-only acceptance evidence plus an optional manual pre-close observation."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--run-id", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(exist_ok=False)
root = "http://127.0.0.1:8839/api/quant/decision/experiments/next-session"


def get(path):
    response = requests.get(root + path, timeout=60)
    response.raise_for_status()
    return response.json()


value = get("/runs/" + args.run_id + "/export")
assert value["status"] == "COMPLETED" and len(value["predictions"]) == 2
assert value["cohort"] == "EXPLORATORY"
for item in value["predictions"]:
    p = item["prediction"]
    assert p["prediction"] in ("UP", "DOWN")
    assert p["reasons"] and p["opposing_reasons"]
    assert p["requested_at"] <= p["evidence_sealed_at"] <= p["issued_at"]
    assert item["outcome"]["status"] == "PENDING"
board = get("/scoreboard")
assert len(board["groups"]) == 1 and board["groups"][0]["statistics"]["accuracy"]["value"] is None
assert all(
    v["denominator"] == 0 and v["value"] is None
    for v in board["groups"][0]["statistics"]["baselines"].values()
)
baseline = json.loads(
    Path(".phase3-quant-o/decision-p1n-service-01/baseline.json").read_text("utf8")
)
with sqlite3.connect(
    "file:"
    + Path(".phase3-quant-o/alpha-history-real-02/metadata.sqlite").resolve().as_posix()
    + "?mode=ro",
    uri=True,
) as s:
    current = {
        rid: dict(status=status, payload_hash=hashlib.sha256(payload.encode()).hexdigest())
        for rid, status, payload in s.execute(
            "select run_id,status,payload from inv_quant_research_run"
        )
    }
assert all(current.get(rid) == saved for rid, saved in baseline.items())
again = get("/runs/" + args.run_id)
assert again["predictions"] == value["predictions"]
calls = [c for c in value["development"]["calls"] if c.get("kind") == "model"]
result = dict(
    run_id=args.run_id,
    old_records_unchanged=len(baseline),
    predictions=[
        dict(
            name=r["name"],
            security=r["prediction"]["security_id"],
            direction=r["prediction"]["prediction"],
            target=r["prediction"]["target_session"],
            issued_at=r["prediction"]["issued_at"],
            input_hash=r["prediction"]["input_hash"],
            outcome=r["outcome"]["status"],
        )
        for r in value["predictions"]
    ],
    model_calls=len(calls),
    roles=[c["role"] for c in calls],
    tokens=value["development"]["tokens"],
    charged_tokens=value["development"]["charged_tokens"],
    scoreboard_snapshot=board["snapshot_id"],
    actual_accuracy=None,
)
(args.output / "acceptance.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), "utf8"
)
(args.output / "sealed-export.json").write_text(
    json.dumps(value, ensure_ascii=False, indent=2), "utf8"
)
(args.output / "scoreboard.json").write_text(
    json.dumps(board, ensure_ascii=False, indent=2), "utf8"
)
print(json.dumps(result, ensure_ascii=True))
