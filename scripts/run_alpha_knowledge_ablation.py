"""Explicit local CLI for a bounded, frozen public-input method comparison."""

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from marketpulse.config import Settings
from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.quant.knowledge import ablation as a
from marketpulse.quant.knowledge.research_store import InvestmentStore
from marketpulse.quant.knowledge.rule_store import RuleStore
from marketpulse.quant.knowledge.store import KnowledgeStore
from marketpulse.quant.live.store import ResearchStore


async def main(args):
    private = Path.cwd().resolve() / ".phase3-quant-o"
    if not args.data.resolve().is_relative_to(
        private
    ) or not args.evidence.resolve().is_relative_to(private):
        raise ValueError("private workspace paths required")
    if not (args.data / "metadata.sqlite").is_file():
        raise ValueError("existing database required")
    args.evidence.mkdir(exist_ok=True, parents=True)
    engine = create_investigation_engine(
        "sqlite:///" + (args.data / "metadata.sqlite").resolve().as_posix()
    )
    sessions = create_session_factory(engine)
    blobs = LocalContentAddressedBlobStorage(args.data / "blobs")
    repo = InvestmentStore(ResearchStore(sessions, blobs, args.data / "live-research"))
    rules = RuleStore(KnowledgeStore(sessions, blobs))
    settings = Settings.from_env(require_api_key=True)
    path = args.evidence / "ablation-state.json"
    try:
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf8"))
        else:
            source = repo.get(args.source_run)
            manifest = a.freeze(repo, source, settings.model)
            manifest_ref = repo.blob(manifest)
            root_refs = set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(manifest)))
            for ref in list(root_refs):
                try:
                    root_refs.update(
                        re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(repo.value(ref)))
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            state = dict(
                version=a.VERSION,
                run_id="K7-" + manifest_ref.rsplit("/", 1)[1][:32],
                created_at=datetime.now(UTC).isoformat(),
                status="FROZEN",
                manifest_ref=manifest_ref,
                root_refs=sorted(root_refs),
                config=dict(prompt_version=a.PROMPT_VERSION, budget=a.BUDGET),
                model_calls=0,
                charged_tokens=0,
                tokens=0,
                calls=[],
                results={},
                elapsed_seconds=0,
            )
            audit = rules.archive_audit(state)
            (args.evidence / "frozen-manifest.json").write_text(
                json.dumps(dict(manifest, audit_id=audit), ensure_ascii=False, indent=2),
                encoding="utf8",
            )

        def persist(state, audit_id):
            # Complete snapshots are CAS immutable; this local pointer supports recovery.
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
            os.replace(temp, path)
            (args.evidence / "audit-pointer.json").write_text(
                json.dumps(dict(audit_id=audit_id)), encoding="utf8"
            )

        persist(state, rules.archive_audit(state))
        if args.prepare_only:
            print(
                json.dumps(
                    dict(
                        status=state["status"],
                        run_id=state["run_id"],
                        model_calls=state["model_calls"],
                    )
                )
            )
            return
        result = await a.execute(repo, rules, state, settings, persist)
        if result["status"] == "COMPLETED":
            report = a.verify(repo.value, result)
            (args.evidence / "ablation-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
            )
        print(
            json.dumps(
                {
                    k: result.get(k)
                    for k in (
                        "run_id",
                        "status",
                        "model_calls",
                        "charged_tokens",
                        "elapsed_seconds",
                        "error",
                    )
                },
                ensure_ascii=False,
            )
        )
        if result["status"] != "COMPLETED":
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--source-run", default="RUN-IR-927e6de4705d4853bd0732a46b974e33")
    p.add_argument("--prepare-only", action="store_true")
    asyncio.run(main(p.parse_args()))
