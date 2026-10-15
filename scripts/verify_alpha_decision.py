"""Read-only P0 service acceptance. Does not call a model or create predictions."""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8839")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    checks = {}
    with httpx.Client(base_url=args.url, timeout=30) as client:
        for path in (
            "/api/health",
            "/api/quant/decision/capabilities",
            "/api/quant/decision/methods",
            "/api/quant/decision/evaluation-cases",
            "/api/quant/decision/schema",
        ):
            response = client.get(path)
            response.raise_for_status()
            checks[path] = response.status_code
        data = client.get("/api/quant/decision/capabilities").json()
        assert data["stage"] == "P0" and not any(data["features"].values())
        assert not data["experiment"]["enabled"]
        assert len(data["methods"]) == 8 and data["evaluation"]["total"] == 48
        assert data["evaluation"]["executed"] == 0
        checks["audit_snapshot_at"] = data["audit"]["audited_at"]
        checks["real_source_probe_count"] = len(data["audit"]["sources"])
        checks["model_configured_no_model_call_in_p0"] = data["model_configured"]
        old_paths = [
            "/api/quant/research/runs/RUN-LIVE-dde38e59cc1943e48a3b98937b7c979e",
            "/api/quant/research/runs/RUN-LIVE-dde38e59cc1943e48a3b98937b7c979e/export",
            "/api/quant/deliveries/RUN-COMP-ddfd157e25434b4997fbb5c3ff1fc18b",
            "/api/quant/delivery",
        ]
        for path in old_paths:
            response = client.get(path)
            response.raise_for_status()
            checks[path] = response.status_code
    if args.baseline:
        if not args.database:
            raise ValueError("database required with baseline")
        before = json.loads(args.baseline.read_text("utf8"))["completed_hashes"]
        with sqlite3.connect(
            "file:" + args.database.resolve().as_posix() + "?mode=ro", uri=True
        ) as db:
            after = {
                row[0]: hashlib.sha256(row[1].encode()).hexdigest()
                for row in db.execute(
                    "select run_id,payload from inv_quant_research_run where status='COMPLETED'"
                )
            }
        assert before == after, "completed research payload changed"
        checks["completed_records_unchanged"] = len(after)
    output = dict(
        stage="P0",
        verified_at=datetime.now(UTC).isoformat(),
        checks=checks,
        browser="separate Playwright acceptance record",
        user_acceptance="PENDING",
        prediction_validity="UNVERIFIED",
    )
    with args.output.open("x", encoding="utf8") as f:
        f.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    main()
