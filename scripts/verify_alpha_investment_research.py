"""Actual four-industry model run, private export and old-history assertions."""

import argparse
import hashlib
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    stage = args.evidence.resolve()
    if not stage.is_relative_to(Path.cwd().resolve() / ".phase3-quant-o"):
        raise ValueError("private workspace evidence required")
    path = stage / "real-request.json"
    if not path.exists():
        path.write_text(
            json.dumps(
                dict(
                    schema_version="investment-research-v1",
                    request_key=str(uuid4()),
                    entities=["000333", "600036", "601088", "600009"],
                    horizon="SWING",
                    horizon_sessions=20,
                    profile=dict(
                        capital="500000",
                        available_cash="500000",
                        input_origin="SYNTHETIC_ACCEPTANCE",
                    ),
                )
            ),
            encoding="utf8",
        )
    definition = json.loads(path.read_text(encoding="utf8"))
    root = "http://127.0.0.1:8839/api/quant/investment-research/runs"
    response = requests.post(root, json=definition, timeout=60)
    response.raise_for_status()
    state = response.json()
    url = root + "/" + state["run_id"]
    if args.resume and state["status"] == "INTERRUPTED":
        response = requests.post(url + "/resume", timeout=60)
        response.raise_for_status()
        state = response.json()
    deadline = time.monotonic() + 910
    last = None
    while state["status"] == "RUNNING" and time.monotonic() < deadline:
        signature = (state["status"], state.get("stage"), state.get("model_calls", 0))
        if signature != last:
            print(
                json.dumps(
                    dict(
                        run_id=state["run_id"],
                        status=state["status"],
                        stage=state.get("stage"),
                        model_calls=state.get("model_calls", 0),
                    ),
                    ensure_ascii=False,
                ),
                flush=True,
            )
            last = signature
        time.sleep(2)
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        state = response.json()
    (stage / "real-run.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8"
    )
    assert state["status"] == "COMPLETED", state.get("error")
    data = state["data"]
    assert (
        len(data["research_opinions"]) == 4
        and len({c["industry_code"] for c in data["candidates"]}) == 4
    )
    assert all(
        o["direction"] in ("FAVORABLE", "UNFAVORABLE") and o["opposing"]
        for o in data["research_opinions"]
    )
    assert (
        state["model_calls"] <= 24
        and state["charged_tokens"] <= 60000
        and state["elapsed_seconds"] <= 900
    )
    audit = requests.get(url + "/audit", timeout=60)
    audit.raise_for_status()
    audit = audit.json()
    assert {c["role"] for c in audit["model_calls"]} == {"Planner", "Analyst", "Verifier", "Writer"}
    assert not audit["profile_sent_to_model"] and not audit["holdings_sent_to_model"]
    assert all(
        "profile" not in c["request"]["context"] and "portfolio" not in c["request"]["context"]
        for c in audit["model_calls"]
    )
    (stage / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf8"
    )
    again = requests.post(root, json=definition, timeout=60)
    again.raise_for_status()
    assert again.json()["result_ref"] == state["result_ref"]
    exported = requests.get(url + "/export", timeout=60)
    exported.raise_for_status()
    (stage / "verification.zip").write_bytes(exported.content)
    count = 0
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert json.loads(archive.read("record.json"))["result_ref"] == state["result_ref"]
        for name in archive.namelist():
            if name.startswith("blobs/"):
                assert hashlib.sha256(archive.read(name)).hexdigest() == name[6:-4]
                count += 1
    baseline = json.loads((stage / "baseline.json").read_text(encoding="utf8"))
    with sqlite3.connect(baseline["database"]) as c:
        for run_id, expected in baseline["runs"].items():
            raw = c.execute(
                "select payload from inv_quant_research_run where run_id=?", (run_id,)
            ).fetchone()[0]
            assert hashlib.sha256(raw.encode()).hexdigest() == expected, run_id
        for table, expected in baseline["knowledge_tables"].items():
            assert [
                list(r) for r in c.execute('select * from "' + table + '" order by rowid')
            ] == expected, table
    result = dict(
        run_id=state["run_id"],
        model_calls=state["model_calls"],
        charged_tokens=state["charged_tokens"],
        exported_verified_blobs=count,
        old_runs_unchanged=len(baseline["runs"]),
        knowledge_unchanged=True,
    )
    (stage / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
