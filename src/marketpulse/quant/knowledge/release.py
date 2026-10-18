"""Verified local release pointers and a self-contained public-evidence export."""

import io
import json
import re
import zipfile

from ..contracts import digest
from . import ablation

VERSION = "investment-knowledge-release-v1"


def read(repo, rules):
    path = repo.base.legacy_root.parent / "knowledge-release.json"
    if not path.is_file():
        return dict(status="NOT_PUBLISHED")
    pointer = json.loads(path.read_text(encoding="utf8"))
    record = rules.audit(pointer["release_id"])
    if (
        record["version"] != VERSION
        or record["status"] != "COMPLETED"
        or record["user_acceptance"] != "PENDING"
    ):
        raise ValueError("交付记录版本或状态不一致")
    state = rules.audit(record["ablation_audit_id"])
    summary = ablation.verify(repo.value, state)
    if state["status"] != "COMPLETED" or summary != record["ablation"]:
        raise ValueError("交付记录与真实方法对照不一致")
    return dict(record, release_id=pointer["release_id"])


def export(repo, rules):
    record = read(repo, rules)
    if record.get("status") != "COMPLETED":
        raise KeyError("release unavailable")
    original = {k: v for k, v in record.items() if k != "release_id"}
    state = rules.audit(record["ablation_audit_id"])
    roots = set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps([record, state])))
    roots |= {
        "blob://sha256/" + record["release_id"],
        "blob://sha256/" + record["ablation_audit_id"],
    }
    blobs = {}
    total = 0
    while roots:
        ref = roots.pop()
        if ref in blobs:
            continue
        raw = repo.raw_bytes(ref)
        blobs[ref] = raw
        total += len(raw)
        if len(blobs) > 500 or total > 80_000_000:
            raise ValueError("交付包超出有界大小")
        try:
            roots.update(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(json.loads(raw))))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("release.json", json.dumps(record, ensure_ascii=False, indent=2))
        archive.writestr("comparison.json", json.dumps(state, ensure_ascii=False, indent=2))
        for ref, raw in sorted(blobs.items()):
            archive.writestr("blobs/" + ref.rsplit("/", 1)[1] + ".bin", raw)
        archive.writestr(
            "README.txt",
            "本地公开资料验收包。按SHA256逐字核验全部blobs；通过scripts/verify_alpha_knowledge_release.py离线复核。方法对照不是策略收益或历史预测优势；前瞻样本尚未到期。",
        )
    if digest(original) != record["release_id"]:
        raise ValueError("交付哈希不一致")
    return stream.getvalue()


def verify_archive(path):
    import hashlib

    with zipfile.ZipFile(path) as archive:
        if (
            len(archive.infolist()) > 503
            or sum(i.file_size for i in archive.infolist()) > 85_000_000
        ):
            raise ValueError("交付包大小异常")
        blobs = {}
        for item in archive.infolist():
            if item.filename in {"release.json", "comparison.json", "README.txt"}:
                continue
            if not re.fullmatch(r"blobs/[0-9a-f]{64}\.bin", item.filename):
                raise ValueError("不支持的交付包路径")
            raw = archive.read(item)
            sha = item.filename[6:-4]
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError("交付包原文哈希失败")
            if sha in blobs:
                raise ValueError("交付包重复内容路径")
            blobs[sha] = raw
        record = json.loads(archive.read("release.json"))
        state = json.loads(archive.read("comparison.json"))

    def value(ref):
        return json.loads(blobs[ref.rsplit("/", 1)[1]])

    if (
        digest({k: v for k, v in record.items() if k != "release_id"}) != record["release_id"]
        or digest(state) != record["ablation_audit_id"]
    ):
        raise ValueError("交付报告或对照记录哈希失败")
    if (
        value("blob://sha256/" + record["release_id"])
        != {k: v for k, v in record.items() if k != "release_id"}
        or value("blob://sha256/" + record["ablation_audit_id"]) != state
    ):
        raise ValueError("交付报告不等于归档")
    if ablation.verify(value, state) != record["ablation"]:
        raise ValueError("交付对照结论不一致")
    return dict(
        release_id=record["release_id"],
        verified_blobs=len(blobs),
        comparison_arms=record["ablation"]["completed"],
        future_accuracy=None,
        profitability=None,
    )
