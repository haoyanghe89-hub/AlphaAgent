"""Real read-only data acceptance. Never print credentials or use synthetic observations."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.quant.decision import market_scan, screening, tushare
from marketpulse.quant.decision.contracts import ResearchRequest
from marketpulse.quant.decision.store import DecisionStore, fingerprint
from marketpulse.quant.live.store import ResearchStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("market", "candidates"), default="market")
    args = parser.parse_args()
    root, output = args.data.resolve(), args.output.resolve()
    workspace = Path.cwd().resolve() / ".phase3-quant-o"
    if not root.is_relative_to(workspace) or not output.is_relative_to(workspace):
        raise ValueError("private workspace data and evidence directories required")
    if not tushare.load_token():
        raise ValueError("TUSHARE_TOKEN is not configured locally")
    output.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "metadata.sqlite") as db:
        all_rows = db.execute("select run_id,status,payload from inv_quant_research_run").fetchall()
        if any(status == "RUNNING" for _, status, _ in all_rows):
            raise ValueError("active research exists; do not bypass the running task")
        baseline = {
            rid: hashlib.sha256(payload.encode()).hexdigest()
            for rid, status, payload in all_rows
            if status == "COMPLETED"
        }
        before_path = output / "baseline.json"
        if not before_path.exists():
            before_path.write_text(json.dumps(baseline), encoding="utf8")
            with sqlite3.connect(output / "before.sqlite") as target:
                db.backup(target)
    engine = create_engine(
        "sqlite:///" + (root / "metadata.sqlite").as_posix(),
        connect_args=dict(check_same_thread=False),
    )
    repo = DecisionStore(
        ResearchStore(
            sessionmaker(engine),
            LocalContentAddressedBlobStorage(root / "blobs"),
            root / "live-research",
        )
    )
    request = ResearchRequest(
        request_key=uuid4(),
        scope="market" if args.mode == "market" else "industry",
        entity="设备与制造观察范围" if args.mode == "market" else "集成电路制造",
        research_mode="market_scan" if args.mode == "market" else "candidates",
        question="用真实申万分类、行情、同期间报表和公司原文形成有反证的研究结论。",
    )
    state, _ = repo.create(request.model_dump(mode="json"), fingerprint())
    (output / (args.mode + "-created.json")).write_text(
        json.dumps(dict(run_id=state["run_id"])), encoding="utf8"
    )
    runner = market_scan.run if args.mode == "market" else screening.run
    asyncio.run(
        runner(repo, state["run_id"], threading.Event(), Settings.from_env(require_api_key=False))
    )
    result = repo.get(state["run_id"])
    (output / (args.mode + "-export.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    (output / (state["run_id"] + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
    )
    data = result.get("data", {})
    coverage = {k: v for k, v in data.get("coverage", {}).items() if k != "unconnected"}
    print(
        json.dumps(
            dict(
                run_id=state["run_id"],
                status=result["status"],
                error=result.get("error"),
                model_calls=result.get("model_calls", 0),
                source_calls=sum(not c.get("role") for c in result["calls"]),
                coverage=coverage,
                selected=data.get("selected"),
                summary=data.get("report", {}).get("summary"),
            ),
            ensure_ascii=False,
        )
    )
    engine.dispose()


if __name__ == "__main__":
    main()
