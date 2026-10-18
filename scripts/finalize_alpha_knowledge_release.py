"""Seal fresh local verification evidence; never publishes methods or trades."""

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.quant.knowledge import ablation, release
from marketpulse.quant.knowledge.research_store import InvestmentStore
from marketpulse.quant.knowledge.rule_store import RuleStore
from marketpulse.quant.knowledge.store import KnowledgeStore
from marketpulse.quant.live.store import ResearchStore


def seal(data, evidence):
    private = Path.cwd().resolve() / ".phase3-quant-o"
    if any(not p.resolve().is_relative_to(private) for p in (data, evidence)):
        raise ValueError("private paths required")
    test = (evidence / "final-tests.log").read_text(encoding="utf8")
    match = re.search(r"(\d+) passed(?:, (\d+) skipped)?", test)
    if not match or re.search(r"\d+ failed|ERROR collecting", test):
        raise ValueError("fresh backend verification required")
    frontend = (evidence / "frontend-tests.log").read_text(encoding="utf8")
    passed = re.search(r"tests (\d+)", frontend)
    if not passed or not re.search(r"fail 0\b", frontend):
        raise ValueError("fresh frontend verification required")
    build = (evidence / "frontend-build.log").read_text(encoding="utf8")
    if "built in" not in build:
        raise ValueError("fresh build required")
    backup = json.loads((evidence / "backup-verification.json").read_text(encoding="utf8"))
    preservation = json.loads((evidence / "preservation.json").read_text(encoding="utf8"))
    source = json.loads((evidence / "source-health.json").read_text(encoding="utf8"))
    kb = json.loads((evidence / "knowledge-source-verification.json").read_text(encoding="utf8"))
    privacy = json.loads((evidence / "privacy-verification.json").read_text(encoding="utf8"))
    if (
        backup["status"] != "PASSED"
        or preservation["status"] != "PASSED"
        or kb["status"] != "PASSED"
        or privacy["status"] != "PASSED"
    ):
        raise ValueError("release gates not passed")
    engine = create_investigation_engine(
        "sqlite:///" + (data / "metadata.sqlite").resolve().as_posix()
    )
    sessions = create_session_factory(engine)
    blobs = LocalContentAddressedBlobStorage(data / "blobs")
    repo = InvestmentStore(ResearchStore(sessions, blobs, data / "live-research"))
    rules = RuleStore(KnowledgeStore(sessions, blobs))
    try:
        audit_id = json.loads(
            (evidence / "method-comparison/audit-pointer.json").read_text(encoding="utf8")
        )["audit_id"]
        state = rules.audit(audit_id)
        comparison = ablation.verify(repo.value, state)
        if state["status"] != "COMPLETED":
            raise ValueError("complete comparison required")
        artifacts = []
        for filename in (
            "final-tests.log",
            "frontend-tests.log",
            "frontend-build.log",
            "backup-verification.json",
            "preservation.json",
            "source-health.json",
            "knowledge-source-verification.json",
            "privacy-verification.json",
            "k6-tests.log",
            "k7-tests.log",
        ):
            raw = (evidence / filename).read_bytes()
            artifacts.append(
                dict(name=filename, sha256=hashlib.sha256(raw).hexdigest(), blob_ref=repo.raw(raw))
            )
        from marketpulse.quant.knowledge.observations import ObservationStore

        forward = ObservationStore(repo).summary()
        record = dict(
            version=release.VERSION,
            status="COMPLETED",
            created_at=datetime.now(UTC).isoformat(),
            user_acceptance="PENDING",
            scope="K1-K7_SOFTWARE_AND_EVIDENCE_RELEASE",
            tests=dict(
                backend=dict(passed=int(match[1]), skipped=int(match[2] or 0)),
                frontend=dict(passed=int(passed[1])),
                build=dict(status="PASSED"),
            ),
            forward_samples=forward["registered_samples"],
            matured_samples=sum(g["matured"] for g in forward["groups"]),
            source_health=source,
            ablation_audit_id=audit_id,
            ablation=comparison,
            artifacts=artifacts,
            root_refs=["blob://sha256/" + audit_id],
            gates=dict(
                software="PASSED",
                source_archives="PASSED",
                backup_restore="PASSED",
                history_preservation="PASSED",
                public_context_privacy="PASSED",
                fresh_market_refresh=source["status"],
                method_effect="UNVALIDATED",
                formal_5_2="NOT_ADMITTED",
            ),
            links=dict(
                investment="#/investment-research/RUN-IR-927e6de4705d4853bd0732a46b974e33",
                daily="#/decision/RUN-NS-9f77c5a29b3345e4a4f04612bb46791e",
                knowledge="#/knowledge",
            ),
            boundaries=[
                "前瞻样本尚未到期；不生成准确率或收益优势。",
                "比较为两证券单次公开材料重放，不进入前瞻样本或独立日期计数。",
                "财报及政策覆盖按每份封存输入说明，金融企业未套用非金融财报指标。",
                "新知识只形成待审核候选，不自动调参或发布。",
                "原5.2历史与独立全收益门禁保留；无自动交易、定时任务或外部独立专家审核。",
            ],
        )
        rid = rules.archive_audit(record)
        pointer = data / "knowledge-release.json"
        temp = pointer.with_suffix(".tmp")
        temp.write_text(json.dumps(dict(release_id=rid)), encoding="utf8")
        os.replace(temp, pointer)
        published = release.read(repo, rules)
        (evidence / "release.json").write_text(
            json.dumps(published, ensure_ascii=False, indent=2), encoding="utf8"
        )
        archive = release.export(repo, rules)
        (evidence / "knowledge-k7.zip").write_bytes(archive)
        print(json.dumps(release.verify_archive(evidence / "knowledge-k7.zip"), ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    seal(args.data, args.evidence)
