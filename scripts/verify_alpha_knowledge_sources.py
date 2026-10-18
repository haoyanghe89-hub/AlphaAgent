"""Explicit, bounded K1 original capture and input-capability probe; no LLM calls."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.server import _upgrade_database
from marketpulse.quant.decision import tushare
from marketpulse.quant.decision.store import DecisionStore
from marketpulse.quant.decision.tools import SourceClient, resolve
from marketpulse.quant.knowledge.sources import sources
from marketpulse.quant.knowledge.store import KnowledgeStore
from marketpulse.quant.live.store import ResearchStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--probe-data", action="store_true")
    args = parser.parse_args()
    workspace = Path.cwd().resolve() / ".phase3-quant-o"
    for path in (args.data, args.evidence):
        if not path.resolve().is_relative_to(workspace):
            raise ValueError("private workspace data and evidence paths required")
    database = args.data / "metadata.sqlite"
    if not database.is_file():
        raise ValueError("existing database required; no new empty production DB")
    args.evidence.mkdir(exist_ok=True, parents=True)
    url = "sqlite:///" + database.resolve().as_posix()
    _upgrade_database(url)
    engine = create_investigation_engine(url)
    sessions = create_session_factory(engine)
    blobs = LocalContentAddressedBlobStorage(args.data / "blobs")
    library = KnowledgeStore(sessions, blobs)
    repo = DecisionStore(ResearchStore(sessions, blobs, args.data / "live-research"))
    call_path = args.evidence / "source-calls.json"
    state = (
        json.loads(call_path.read_text(encoding="utf8"))
        if call_path.is_file()
        else dict(request=dict(budget_profile=dict(max_seconds=900)), calls=[])
    )

    def save():
        call_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")

    client = SourceClient(repo, state, save, Event(), max_requests=80)
    results = []
    for source in sources():
        try:
            capture, raw = client.fetch(
                source.source_id, source.url, family=source.source_family, binary=True
            )
            doc = library.ingest_document(
                source, raw, datetime.fromisoformat(capture["fetched_at"])
            )
            results.append(
                dict(
                    source_id=source.source_id,
                    status="CAPTURED",
                    version=doc["version"],
                    raw_ref=doc["raw_ref"],
                    excerpt_count=len(doc["excerpts"]),
                )
            )
            print(source.source_id, "CAPTURED", len(raw), flush=True)
        except (ValueError, OSError) as error:
            results.append(
                dict(
                    source_id=source.source_id,
                    status="NOT_CAPTURED",
                    error_type=type(error).__name__,
                    reason=str(error)[:160],
                )
            )
            print(source.source_id, "NOT_CAPTURED", type(error).__name__, flush=True)
    report = dict(
        checked_at=datetime.now(UTC).isoformat(),
        sources=results,
        catalog=library.catalog(),
        model_calls=0,
    )
    (args.evidence / "source-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    if args.probe_data:
        probe_inputs(client, args.evidence)
    engine.dispose()
    print("CAPTURED", report["catalog"]["document_count"], "FTS", library.fts_available, flush=True)


def probe_inputs(client, evidence):
    now = datetime.now(UTC)
    matrix = dict(
        checked_at=now.isoformat(),
        securities=[],
        limitations=[
            "日线非复权；复权因子尚未探测，不能直接用于跨除权趋势。",
            "财报探测不是完整历史可得性、重述与正式回测准入证明。",
            "分钟资金仅有实际取得的样本，未承诺连续历史覆盖。",
        ],
    )
    for code in ("600519", "300750", "600036", "000333"):
        entry = dict(query=code, capabilities=[])
        try:
            subject = resolve(client, code)
            entry["subject"] = subject
            for api, params in [
                (
                    "daily",
                    dict(
                        ts_code=subject["security_id"],
                        start_date=(now - timedelta(days=220)).strftime("%Y%m%d"),
                        end_date=now.strftime("%Y%m%d"),
                    ),
                ),
                ("income", dict(ts_code=subject["security_id"], start_date="20250101")),
                ("cashflow", dict(ts_code=subject["security_id"], start_date="20250101")),
            ]:
                try:
                    source, rows = tushare.fetch(client, api, api, params)
                    if any(r.get("ts_code") != subject["security_id"] for r in rows):
                        raise ValueError("security identity mismatch")
                    entry["capabilities"].append(
                        dict(
                            api=api,
                            status="AVAILABLE_SAMPLE" if rows else "EMPTY",
                            count=len(rows),
                            fields=sorted(rows[0]) if rows else [],
                            source=source,
                            dates=sorted(
                                {str(r.get("trade_date") or r.get("end_date")) for r in rows}
                            ),
                        )
                    )
                except ValueError as error:
                    entry["capabilities"].append(
                        dict(api=api, status="UNAVAILABLE", error_type=type(error).__name__)
                    )
        except ValueError as error:
            entry["identity_error"] = type(error).__name__
        matrix["securities"].append(entry)
    (evidence / "input-capabilities.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf8"
    )


if __name__ == "__main__":
    main()
