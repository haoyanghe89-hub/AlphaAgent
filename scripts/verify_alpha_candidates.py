"""Real P5 acceptance, immutable history and actual shared-budget role records."""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf8")
parser = argparse.ArgumentParser()
parser.add_argument("--run-id", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(exist_ok=False)


def save(name, value):
    (args.output / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


with httpx.Client(base_url="http://127.0.0.1:8839", timeout=90) as client:
    previous = None
    for _ in range(450):
        r = client.get("/api/quant/decision/runs/" + args.run_id)
        r.raise_for_status()
        state = r.json()
        progress = (state["status"], state["model_calls"], state.get("process", [{}])[-1].get("detail"))
        if progress != previous:
            print(progress, flush=True)
            previous = progress
        if state["status"] != "RUNNING":
            break
        time.sleep(2)
    save("run", state)
    assert state["status"] == "COMPLETED", state.get("error")
    data = state["data"]
    assert data["report"]["stage"] == "P5"
    assert data["coverage"]["universe_total"] == len(data["members"])
    assert data["coverage"]["available"] + data["coverage"]["missing"] + data["coverage"]["excluded"] == len(data["members"])
    assert [p["security_id"] for p in data["report"]["candidates"]] == data["selected"]
    assert 0 < len(data["selected"]) <= 3  # This acceptance requires an actual non-empty real sample.
    assert all(r["business_verified"] for r in data["members"] if r["security_id"] in data["selected"])
    assert all(p["outlook"] in {"FAVORABLE", "UNFAVORABLE"} for p in data["report"]["candidates"])
    assert all(r["risk_status"] == "UNKNOWN" and r["leader_type"] != "BUSINESS" for r in data["members"])
    assert state["model_calls"] >= 4 and state["tokens"] <= 60000 and state["elapsed_seconds"] <= 900
    source_calls = [c for c in state["calls"] if not c.get("role")]
    assert len(source_calls) <= 52
    assert [c["role"] for c in data["role_records"]] == ["Planner", "Analyst", "Verifier", "Writer"]
    for opinion in data["report"]["candidates"]:
        for eid in opinion["evidence_ids"] + opinion["counter_ids"]:
            response = client.get(f"/api/quant/decision/runs/{args.run_id}/evidence/{eid}")
            response.raise_for_status()
            assert response.json()["evidence"]["subject_id"] == opinion["security_id"]
    response = client.get(f"/api/quant/decision/runs/{args.run_id}/export")
    response.raise_for_status()
    assert response.json() == state
    save("export", response.json())
baseline = json.loads(Path(".phase3-quant-o/decision-p5-real-01/baseline.json").read_text(encoding="utf8"))
db_path = Path(".phase3-quant-o/alpha-history-real-02/metadata.sqlite").resolve()
with sqlite3.connect("file:" + db_path.as_posix() + "?mode=ro", uri=True) as db:
    now = {rid: hashlib.sha256(value.encode()).hexdigest() for rid, value in db.execute("select run_id,payload from inv_quant_research_run where status='COMPLETED'")}
assert all(now.get(k) == v for k, v in baseline.items())
result = dict(run_id=args.run_id, universe=len(data["members"]), selected=data["selected"],
              source_calls=len(source_calls), model_calls=state["model_calls"], tokens=state["tokens"],
              unchanged_completed=len(baseline), user_acceptance="PENDING", prediction_validity="UNVERIFIED")
save("acceptance", result)
print(json.dumps(result, ensure_ascii=False), flush=True)
