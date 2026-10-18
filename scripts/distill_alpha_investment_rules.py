"""Local, bounded source-reading candidates + two real model review roles.

No HTTP publication endpoint, external expert claim, scheduled job or trading.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.server import _upgrade_database
from marketpulse.quant.decision.next_session.agents import DailyRoles
from marketpulse.quant.knowledge.budget import release_unconnected_reservation
from marketpulse.quant.knowledge.review import PROMPT_VERSION, PROMPTS, covered_versions
from marketpulse.quant.knowledge.rule_store import RuleStore
from marketpulse.quant.knowledge.rules import ReviewBatch
from marketpulse.quant.knowledge.seeds import candidates
from marketpulse.quant.knowledge.store import KnowledgeStore


async def run(args):
    workspace = Path.cwd().resolve() / ".phase3-quant-o"
    if any(not p.resolve().is_relative_to(workspace) for p in (args.data, args.evidence)):
        raise ValueError("private workspace paths required")
    database = args.data / "metadata.sqlite"
    if not database.is_file():
        raise ValueError("existing source database required")
    args.evidence.mkdir(parents=True, exist_ok=True)
    url = "sqlite:///" + database.resolve().as_posix()
    _upgrade_database(url)
    engine = create_investigation_engine(url)
    library = KnowledgeStore(
        create_session_factory(engine), LocalContentAddressedBlobStorage(args.data / "blobs")
    )
    store = RuleStore(library)
    drafts = [store.draft(rule) for rule in candidates(library)]
    if len(drafts) < 24 or len({d["rule_id"] for d in drafts}) != len(drafts):
        raise ValueError("distinct source-grounded candidates required")
    path = args.evidence / "model-calls.json"
    state = (
        json.loads(path.read_text(encoding="utf8"))
        if path.is_file()
        else dict(
            config=dict(
                prompt_version=PROMPT_VERSION,
                budget=dict(max_model_calls=24, max_tokens=60000, max_seconds=900),
            ),
            model_calls=0,
            charged_tokens=0,
            tokens=0,
            calls=[],
            elapsed_seconds=0,
            extraction=dict(
                curator="CODEX_SOURCE_READING",
                whole_book_read=False,
                note="原文定位后由Codex提出候选；两次真实模型职责审核，非外部专家验证。",
            ),
        )
    )
    if state["config"]["prompt_version"] != PROMPT_VERSION:
        raise ValueError("cannot mix review protocol versions")
    prior_elapsed = state["elapsed_seconds"]
    started = time.monotonic()
    if args.settle_unconnected_log:
        proof_path = args.settle_unconnected_log.resolve()
        if not proof_path.is_relative_to(args.evidence.resolve()):
            raise ValueError("proof log must belong to this review batch")
        proof = dict(client_max_retries=0, failure_log=proof_path.read_text(encoding="utf8"))
        release_unconnected_reservation(state, 0, proof, library._blob(proof))

    def save():
        state["elapsed_seconds"] = prior_elapsed + time.monotonic() - started
        store.archive_audit(state)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")

    def check():
        if prior_elapsed + time.monotonic() - started > 900:
            raise ValueError("offline review time budget exhausted")

    repo = SimpleNamespace(blob=library._blob, raw=lambda raw: library.blobs.put_bytes(raw).ref.uri)
    try:
        async with DailyRoles(
            repo,
            state,
            Settings.from_env(),
            save,
            check,
            prompts=PROMPTS,
            schema_version="investment-rule-review-v1",
            output_tokens=2300,
            reserve_limit=40000,
            transport_attempts=1,
        ) as roles:
            for offset in range(0, len(drafts), 6):
                batch = drafts[offset : offset + 6]
                for role in PROMPTS:
                    covered = covered_versions(state, role, library._value)
                    pending = [d for d in batch if (d["rule_id"], d["version"]) not in covered]
                    if not pending:
                        continue
                    context = dict(
                        rules=[dict(version=d["version"], rule=d["rule"]) for d in pending]
                    )
                    roles.output_tokens = min(2300, 700 * len(pending))
                    print(f"REVIEW batch={offset // 6 + 1} role={role}", flush=True)
                    await roles.call(role, ReviewBatch, context)
        save()
        audit_id = store.archive_audit(state)
        results = []
        for draft in drafts:
            review = store.record_review(draft["rule_id"], draft["version"], audit_id)
            if args.publish_reviewed and review["verdict"] == "APPROVE":
                store.publish(draft["rule_id"], draft["version"], review["review_id"])
            results.append(review)
        report = dict(
            audit_id=audit_id,
            model_calls=state["model_calls"],
            reported_tokens=state["tokens"],
            charged_tokens=state["charged_tokens"],
            unknown_usage_calls=sum(
                c["status"] == "FAILED" and bool(c.get("raw_model_response_ref"))
                for c in state["calls"]
            ),
            elapsed_seconds=state["elapsed_seconds"],
            reviews=results,
            published=store.catalog()["rules_published"],
            validation_status="UNVALIDATED",
        )
        (args.evidence / "rule-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(
            json.dumps({k: v for k, v in report.items() if k != "reviews"}, ensure_ascii=False),
            flush=True,
        )
        if any(r["verdict"] != "APPROVE" for r in results):
            raise ValueError(
                "some candidates require source-reading revision; "
                "rejected versions remain immutable"
            )
    finally:
        save()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--publish-reviewed", action="store_true")
    parser.add_argument("--settle-unconnected-log", type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
