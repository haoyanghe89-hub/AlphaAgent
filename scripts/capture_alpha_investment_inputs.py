"""Read and seal actual K3 inputs; no model calls, personal profiles or trading."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.quant.decision.store import DecisionStore
from marketpulse.quant.decision.tools import SourceClient
from marketpulse.quant.knowledge.inputs import capture_inputs
from marketpulse.quant.live.store import ResearchStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--entities", nargs="+", default=["000333", "600036", "601088", "600009"])
    args = parser.parse_args()
    workspace = Path.cwd().resolve() / ".phase3-quant-o"
    if any(not p.resolve().is_relative_to(workspace) for p in (args.data, args.evidence)):
        raise ValueError("private workspace paths required")
    engine = create_investigation_engine(
        "sqlite:///" + (args.data / "metadata.sqlite").resolve().as_posix()
    )
    blobs = LocalContentAddressedBlobStorage(args.data / "blobs")
    repo = DecisionStore(
        ResearchStore(create_session_factory(engine), blobs, args.data / "live-research")
    )
    call_path = args.evidence / "input-calls.json"
    state = (
        json.loads(call_path.read_text(encoding="utf8"))
        if call_path.is_file()
        else dict(request=dict(budget_profile=dict(max_seconds=900)), calls=[])
    )
    args.evidence.mkdir(parents=True, exist_ok=True)

    def save():
        (args.evidence / "input-calls.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8"
        )

    client = SourceClient(repo, state, save, Event(), max_requests=80)
    try:
        packet = capture_inputs(client, args.entities, datetime.now(UTC))
        packet_ref = repo.blob(packet)
        (args.evidence / "inputs.json").write_text(
            json.dumps(dict(packet=packet, packet_ref=packet_ref), ensure_ascii=False, indent=2),
            encoding="utf8",
        )
        summary = dict(
            market_date=packet["market_date"],
            calls=len(state["calls"]),
            stocks=[
                dict(
                    security_id=r["ts_code"],
                    name=r["name"],
                    industry=r.get("industry"),
                    daily=len(r["daily"]),
                    adjustments=len(r["adjustments"]),
                    sw=[
                        (m.get("l1_code"), m.get("l1_name"))
                        for m in r["industry_memberships"]
                        if m.get("is_new") == "Y"
                    ],
                    financials={k: len(v) for k, v in r["financials"].items()},
                )
                for r in packet["stocks"]
            ],
        )
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        from marketpulse.quant.decision.next_session.calendar import plain, rule_text

        for item in packet["trading_rule_sources"]:
            if item["kind"] not in ("RULES", "DEFERRED"):
                continue
            t = plain(rule_text(repo.raw_bytes(item["source"]["blob_ref"])))
            for anchor in ("100股", "200股", "2026年7月6日", "3.3.8", "3.3.10"):
                at = t.find(anchor)
                if at >= 0:
                    print(
                        item["exchange"],
                        item["kind"],
                        anchor,
                        t[max(0, at - 120) : at + 260],
                        flush=True,
                    )
    finally:
        save()
        engine.dispose()


if __name__ == "__main__":
    main()
