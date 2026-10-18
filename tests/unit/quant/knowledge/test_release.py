# ruff: noqa: F811
import asyncio
import copy
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketpulse.quant.knowledge import ablation as a
from marketpulse.quant.knowledge import release as r
from marketpulse.quant.knowledge.api import router

from .test_ablation import Port, frozen
from .test_research import runtime  # noqa: F401


def publish(runtime, rules, tmp_path):
    repo, manifest, state, settings = frozen(runtime)
    result = asyncio.run(a.execute(repo, rules, state, settings, lambda *_: None, port=Port()))
    audit_id = rules.archive_audit(result)
    record = dict(
        version=r.VERSION,
        status="COMPLETED",
        user_acceptance="PENDING",
        ablation_audit_id=audit_id,
        ablation=a.verify(repo.value, result),
    )
    release_id = rules.archive_audit(record)
    repo.base.legacy_root = tmp_path / "live-research"
    (tmp_path / "knowledge-release.json").write_text(
        json.dumps(dict(release_id=release_id)), encoding="utf8"
    )
    return repo, record, release_id


def test_release_export_is_self_contained_and_corruption_is_rejected(runtime, rules, tmp_path):
    repo, record, release_id = publish(runtime, rules, tmp_path)
    assert r.read(repo, rules)["release_id"] == release_id
    raw = r.export(repo, rules)
    path = tmp_path / "release.zip"
    path.write_bytes(raw)
    assert r.verify_archive(path)["comparison_arms"] == 6
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        entries = {item.filename: source.read(item) for item in source.infolist()}
    bad = json.loads(entries["comparison.json"])
    bad["report"]["profitability"] = "guaranteed"
    entries["comparison.json"] = json.dumps(bad).encode()
    with zipfile.ZipFile(path, "w") as dest:
        for name, content in entries.items():
            dest.writestr(name, content)
    with pytest.raises(ValueError, match="哈希"):
        r.verify_archive(path)
    modified = copy.deepcopy(record)
    modified["ablation"]["completed"] = 99
    rid = rules.archive_audit(modified)
    (tmp_path / "knowledge-release.json").write_text(
        json.dumps(dict(release_id=rid)), encoding="utf8"
    )
    with pytest.raises(ValueError):
        r.read(repo, rules)


def test_release_http_is_read_only_and_missing_pointer_has_explicit_state(runtime, rules, tmp_path):
    repo, _, _ = publish(runtime, rules, tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.research_store = repo.base
    app.state.knowledge_rules = rules
    with TestClient(app) as client:
        assert client.get("/api/quant/knowledge/release").json()["status"] == "COMPLETED"
        assert client.get("/api/quant/knowledge/release/comparison").json()["status"] == "COMPLETED"
        assert client.get("/api/quant/knowledge/release/export").status_code == 200
        assert client.post("/api/quant/knowledge/release").status_code == 405
        (tmp_path / "knowledge-release.json").unlink()
        assert client.get("/api/quant/knowledge/release").json()["status"] == "NOT_PUBLISHED"
        assert client.get("/api/quant/knowledge/release/export").status_code == 404
