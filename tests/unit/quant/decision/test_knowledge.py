"""Synthetic HTML tests knowledge provenance, not real-source coverage or returns."""

import copy
import hashlib
import json

import pytest

from marketpulse.quant.decision import knowledge


class Repo:
    def __init__(self):
        self.raws = {}

    def raw(self, raw):
        ref = "blob://sha256/" + hashlib.sha256(raw).hexdigest()
        self.raws[ref] = raw
        return ref

    def raw_bytes(self, ref):
        return self.raws[ref]

    def blob(self, value):
        return self.raw(json.dumps(value).encode())

    def value(self, ref):
        return json.loads(self.raw_bytes(ref))


class Client:
    def __init__(self, repo):
        self.repo = repo

    def fetch(self, key, url, **kwargs):
        note = next(n for n in knowledge.catalog() if n["source_url"] == url)
        raw = (
            "<html><script>hidden</script><p>Synthetic fixture only. "
            + " ".join(note["source_locator"]["anchors"])
            + "</p></html>"
        ).encode()
        ref = self.repo.raw(raw)
        return dict(
            key=key,
            url=url,
            source_family="METHOD_KNOWLEDGE",
            blob_ref=ref,
            sha256=ref.rsplit("/", 1)[-1],
            fetched_at="2026-10-13T03:00:00+00:00",
        ), raw.decode()


def test_public_excerpt_provenance_does_not_add_company_facts(monkeypatch):
    repo = Repo()
    subject = dict(security_id="688012.SH")
    data = dict(
        sources=[],
        bundle=dict(
            cutoff_at="2026-10-13T04:00:00+00:00",
            sources=[],
            items=[dict(subject_id="688012.SH", evidence_id="SYNTHETIC")],
        ),
    )
    snapshot = knowledge.collect(repo, Client(repo), subject)
    final = knowledge.attach(repo, data, snapshot)
    knowledge.verify(repo, final)
    assert final["bundle"]["items"] == data["bundle"]["items"]
    assert len(final["knowledge_notes"]) == 3
    assert all(n["coverage"] == "PUBLIC_EXCERPT_VERIFIED" for n in final["knowledge_notes"])
    assert all("整书" in n["read_scope"] for n in final["knowledge_notes"])
    assert "hidden" not in knowledge.text(b"<script>hidden</script><p>visible</p>")
    with pytest.raises(ValueError):
        knowledge.model_context(final, ["UNKNOWN"])
    for mutate in ("summary", "source_hash", "actual_locators"):
        changed = copy.deepcopy(final)
        changed["knowledge_notes"][0][mutate] = "changed"
        with pytest.raises(ValueError):
            knowledge.verify(repo, changed)
    changed = copy.deepcopy(final)
    changed["knowledge_snapshot"]["responses"][0]["note"]["summary"] = "invented definition"
    with pytest.raises(ValueError):
        knowledge.verify(repo, changed)
    changed = copy.deepcopy(snapshot)
    changed["responses"][0]["source"]["fetched_at"] = "2026-10-14T03:00:00+00:00"
    with pytest.raises(ValueError):
        knowledge.attach(repo, data, changed)
    monkeypatch.setattr(knowledge, "catalog", lambda: [])
    knowledge.verify(repo, final)  # Later catalogs cannot rewrite or disable this sealed version.
