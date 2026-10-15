"""Early-window P2 acceptance, including append-only manual observation checks."""

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import httpx


def save(path, value):
    with path.open("x", encoding="utf8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entity")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if bool(args.entity) == bool(args.run_id):
        parser.error("choose either --entity (new model run) or --run-id (reuse saved run)")
    args.output.mkdir(exist_ok=False)
    with httpx.Client(base_url=args.url, timeout=90) as client:
        if args.entity:
            body = dict(
                request_key=str(uuid4()),
                entity=args.entity,
                research_mode="medium_term",
                horizon_months=3,
                question="研究未来三个月：利润与收入变化是否一致，原因与现金回收有哪些反证，什么条件会改变判断",
            )
            save(args.output / "request.json", body)
            response = client.post("/api/quant/decision/runs", json=body)
            response.raise_for_status()
            rid = response.json()["run_id"]
            print("created", rid, flush=True)
        else:
            rid = args.run_id
        deadline, last = time.monotonic() + 920, None
        while time.monotonic() < deadline:
            response = client.get("/api/quant/decision/runs/" + rid)
            response.raise_for_status()
            state = response.json()
            progress = (
                state["status"],
                state.get("model_calls", 0),
                state.get("process", [{}])[-1].get("detail"),
            )
            if progress != last:
                print(progress, flush=True)
                last = progress
            if state["status"] != "RUNNING":
                break
            time.sleep(2)
        save(args.output / "run.json", state)
        assert state["status"] == "COMPLETED", state.get("error")
        assert state["data"]["report"]["stage"] == "P2" and state["model_calls"] >= 4
        assert all(c.get("model") != "synthetic" for c in state["calls"] if c.get("role"))
        forecast = client.get(f"/api/quant/decision/runs/{rid}/forecast")
        forecast.raise_for_status()
        save(args.output / "forecast.json", forecast.json())
        export = client.get(f"/api/quant/decision/runs/{rid}/export")
        export.raise_for_status()
        assert export.json()["data"] == state["data"]
        save(args.output / "export.json", export.json())
        observed = client.post(f"/api/quant/decision/runs/{rid}/observe")
        observed.raise_for_status()
        assert all(
            o["maturity"] == "PENDING" and o["security_return"] is None
            for o in observed.json()["outcomes"]
        )
        repeated = client.post(f"/api/quant/decision/runs/{rid}/observe")
        repeated.raise_for_status()
        assert observed.json() == repeated.json()
        save(args.output / "outcomes.json", observed.json())
        for claim in state["data"]["report"]["claims"]:
            for eid in claim["support_ids"] + claim["counter_ids"]:
                client.get(f"/api/quant/decision/runs/{rid}/evidence/{eid}").raise_for_status()
        baseline = json.loads(
            Path(".phase3-quant-o/decision-p2-service-01/baseline.json").read_text("utf8")
        )
        with sqlite3.connect(
            "file:"
            + Path(".phase3-quant-o/alpha-history-real-02/metadata.sqlite").resolve().as_posix()
            + "?mode=ro",
            uri=True,
        ) as db:
            after = {
                r[0]: hashlib.sha256(r[1].encode()).hexdigest()
                for r in db.execute(
                    "select run_id,payload from inv_quant_research_run where status='COMPLETED'"
                )
            }
        assert all(after.get(k) == v for k, v in baseline.items()), (
            "preexisting completed records changed"
        )
        result = dict(
            run_id=rid,
            model_calls=state["model_calls"],
            tokens=state["tokens"],
            rounds=len(state["rounds"]),
            evidence_calls=state["evidence_calls"],
            outlook=state["data"]["report"]["opinion"]["outlook"],
            status=state["data"]["report"]["research_status"],
            old_completed_unchanged=len(baseline),
            prediction_validity="UNVERIFIED",
            user_acceptance="PENDING",
        )
        save(args.output / "acceptance.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
