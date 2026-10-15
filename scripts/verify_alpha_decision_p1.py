"""Manual real-source P1 acceptance; explicitly creates evidence tasks, no model calls."""

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


def save(path, value):
    with path.open("x", encoding="utf8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    summaries = []
    with httpx.Client(base_url=args.url, timeout=45) as client:
        health = client.get("/api/health")
        health.raise_for_status()
        caps = client.get("/api/quant/decision/capabilities").json()
        assert caps["stage"] == "P1" and caps["features"]["single_evidence"]
        assert not caps["features"]["next_session"] and not caps["experiment"]["enabled"]
        save(args.output / "capabilities.json", caps)
        for entity in ("中微公司", "北方华创"):
            request = dict(
                request_key=str(uuid4()),
                entity=entity,
                question="核查近期经营变化、产品需求与现金回收的依据和风险",
                horizon_months=3,
            )
            save(args.output / (entity + "-request.json"), request)
            response = client.post("/api/quant/decision/runs", json=request)
            response.raise_for_status()
            run_id = response.json()["run_id"]
            print(entity, run_id, "started", flush=True)
            deadline = time.monotonic() + 920
            while time.monotonic() < deadline:
                result = client.get("/api/quant/decision/runs/" + run_id)
                result.raise_for_status()
                state = result.json()
                if state["status"] != "RUNNING":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("acceptance wait exceeded task budget")
            save(args.output / (entity + "-run.json"), state)
            assert state["status"] == "COMPLETED", state.get("error")
            assert state["model_calls"] == 0
            data = state["data"]
            assert any(d["category"] == "periodic" for d in data["documents"])
            assert any(e["kind"] == "fact" for e in data["bundle"]["items"])
            assert any(e["kind"] == "flow" for e in data["bundle"]["items"])
            assert (
                client.post("/api/quant/decision/runs", json=request).json()["result_ref"]
                == state["result_ref"]
            )
            assert (
                client.post(
                    "/api/quant/decision/runs", json=dict(request, question="改条件应拒绝")
                ).status_code
                == 409
            )
            export = client.get("/api/quant/decision/runs/" + run_id + "/export")
            export.raise_for_status()
            assert export.json()["data"] == data
            for doc in data["documents"]:
                raw = client.get(
                    f"/api/quant/decision/runs/{run_id}/documents/{doc['document_id']}/raw"
                )
                raw.raise_for_status()
                assert hashlib.sha256(raw.content).hexdigest() == doc["content_hash"]
            for evidence in data["bundle"]["items"]:
                ref = client.get(
                    f"/api/quant/decision/runs/{run_id}/evidence/{evidence['evidence_id']}"
                )
                ref.raise_for_status()
                assert ref.json()["evidence"] == evidence
            summary = dict(
                entity=entity,
                run_id=run_id,
                documents=len(data["documents"]),
                evidence=len(data["bundle"]["items"]),
                gaps=len(data["report"]["gaps"]),
                source_calls=len(state["calls"]),
                model_calls=0,
                report_status=data["report"]["research_status"],
                cutoff=data["bundle"]["cutoff_at"],
            )
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=True), flush=True)
        for path in (
            "/api/quant/research/runs/RUN-LIVE-dde38e59cc1943e48a3b98937b7c979e",
            "/api/quant/research/runs/RUN-LIVE-dde38e59cc1943e48a3b98937b7c979e/export",
            "/api/quant/deliveries/RUN-COMP-ddfd157e25434b4997fbb5c3ff1fc18b",
        ):
            assert client.get(path).status_code == 200
    before = json.loads(args.baseline.read_text("utf8"))["completed_hashes"]
    with sqlite3.connect("file:" + args.database.resolve().as_posix() + "?mode=ro", uri=True) as db:
        after = {
            r[0]: hashlib.sha256(r[1].encode()).hexdigest()
            for r in db.execute(
                "select run_id,payload from inv_quant_research_run where status='COMPLETED'"
            )
        }
    assert all(after.get(k) == v for k, v in before.items()), "old completed payload changed"
    result = dict(
        stage="P1",
        verified_at=datetime.now(UTC).isoformat(),
        real_runs=summaries,
        old_completed_unchanged=len(before),
        old_routes_http=200,
        user_acceptance="PENDING",
        new_model_analysis="NOT_IMPLEMENTED_IN_P1",
        prediction_validity="UNVERIFIED",
    )
    save(args.output / "acceptance.json", result)
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
