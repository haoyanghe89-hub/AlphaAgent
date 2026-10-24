"""Synthetic imported originals; no production archive, network or embeddings."""

import copy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from marketpulse.infrastructure.storage.models import BlobIntegrityError, BlobRef
from marketpulse.quant.knowledge import original_search as originals
from marketpulse.quant.knowledge.contracts import SourceDefinition


def source():
    return SourceDefinition(
        source_id="TEST-TASK-ORIGINAL",
        title="Synthetic archived article",
        authors=("TEST ONLY",),
        source_family="TEST-ORIGINALS",
        category="测试",
        tags=("测试",),
        url="https://www.berkshirehathaway.com/letters/1992.html",
        selector="main",
        anchors=("Anchor introduction", "Unrelated introductory material"),
        reading_note="合成检索测试，未提炼成发布规则。",
        publication_label="SYNTHETIC",
    )


def ingest(library, suffix=""):
    body = "Anchor introduction. Unrelated introductory material. " * 40
    body += " Costs and turnover reduce investors' net returns after fees and taxes. " * 8
    body += " Other observations on cash collection and operating cash flow. " * 8
    body += suffix
    return library.ingest_document(
        source(), ("<main>" + body + "</main>").encode(), datetime.now(UTC)
    )


@pytest.fixture
def runtime(rules):
    repo = SimpleNamespace(
        base=SimpleNamespace(sessions=rules.library.sessions, blobs=rules.library.blobs),
        blob=rules.blob,
        value=rules.value,
        raw_bytes=lambda ref: rules.library.blobs.get_bytes(BlobRef.from_uri(ref)),
    )
    return repo, rules.library


def state():
    return dict(
        created_at=datetime.now(UTC).isoformat(),
        config=dict(original_search_version=originals.VERSION),
        request=dict(question="PRIVATE QUESTION", profile=dict(capital="987654.32")),
    )


def context(topic="成本 净收益"):
    return dict(
        horizon="MEDIUM",
        evidence=[dict(evidence_id="STOCK-FACT")],
        methods=[],
        research_selection=[dict(hypothesis=topic, countercheck="", method_rule_ids=[])],
    )


def test_task_search_reads_beyond_fixed_anchors_and_unpublished_sources(runtime):
    repo, library = runtime
    doc = ingest(library)
    run, ctx, knowledge = state(), context(), dict(rules=[])
    ref = originals.collect(repo, run, ctx, knowledge)
    enriched = originals.attach(repo, run, ctx, knowledge, ref)
    result = enriched["original_research"]
    assert result["status"] == "AVAILABLE"
    assert result["documents_searched"] == 1
    assert "costs" in result["passages"][0]["quote"].lower()
    assert result["passages"][0]["start"] > doc["locators"][0]["end"]
    body = repo.raw_bytes(doc["text_ref"]).decode()
    for passage in result["passages"]:
        assert body[passage["start"] : passage["end"]] == passage["quote"]
        assert passage["version"] == doc["version"]
        assert len(passage["quote"]) <= originals.CHARS
    assert enriched["evidence"] == ctx["evidence"]  # Method prose is never a stock fact.
    assert run["original_search_ref"] == ref
    assert {doc["raw_ref"], doc["text_ref"], doc["manifest_ref"], ref} <= set(run["source_refs"])
    assert "PRIVATE QUESTION" not in str(repo.value(ref))
    assert "987654.32" not in str(repo.value(ref))


def test_different_tasks_retrieve_different_passages(runtime):
    repo, library = runtime
    ingest(library)
    knowledge = dict(rules=[])
    results = []
    for topic in ("costs fees taxes", "cash collection operating flow"):
        run, ctx = state(), context(topic)
        ref = originals.collect(repo, run, ctx, knowledge)
        results.append(originals.attach(repo, run, ctx, knowledge, ref)["original_research"])
    assert results[0]["passages"][0]["start"] != results[1]["passages"][0]["start"]


def test_new_imports_do_not_rewrite_task_snapshot_and_recovery_uses_original_ref(runtime):
    repo, library = runtime
    first = ingest(library)
    run, ctx, knowledge = state(), context(), dict(rules=[])
    ref = originals.collect(repo, run, ctx, knowledge)
    before = originals.attach(repo, run, ctx, knowledge, ref)
    later = ingest(library, " A later revised text about costs and fees.")
    assert later["version"] != first["version"]
    assert originals.collect(repo, run, ctx, knowledge) == ref
    assert originals.attach(repo, run, ctx, knowledge, ref) == before
    historical = state()
    historical["created_at"] = run["created_at"]
    historical_ref = originals.collect(repo, historical, ctx, knowledge)
    assert historical_ref == ref


def test_empty_search_and_old_tasks_are_explicit(runtime):
    repo, library = runtime
    ingest(library)
    run, ctx, knowledge = state(), context("zzzznosuchword"), dict(rules=[])
    ref = originals.collect(repo, run, ctx, knowledge)
    result = originals.attach(repo, run, ctx, knowledge, ref)["original_research"]
    assert result["status"] == "MISSING" and result["passages"] == []
    old = dict(config={})
    assert originals.collect(None, old, ctx, knowledge) is None
    assert originals.attach(None, old, ctx, knowledge, None) == ctx
    with pytest.raises(ValueError, match="缺少"):
        originals.attach(repo, run, ctx, knowledge, None)


def test_task_query_and_quoted_text_cannot_be_changed_on_replay(runtime):
    repo, library = runtime
    ingest(library)
    run, ctx, knowledge = state(), context(), dict(rules=[])
    ref = originals.collect(repo, run, ctx, knowledge)
    tampered = copy.deepcopy(repo.value(ref))
    tampered["result"]["passages"][0]["quote"] = "invented quotation"
    with pytest.raises(ValueError, match="不能重现"):
        originals.attach(repo, run, ctx, knowledge, repo.blob(tampered))
    with pytest.raises(ValueError, match="不能重现"):
        originals.attach(repo, run, context("cash collection"), knowledge, ref)


def test_future_corpus_is_excluded_and_raw_source_corruption_fails(runtime):
    repo, library = runtime
    run = state()
    doc = ingest(library)
    ctx, knowledge = context(), dict(rules=[])
    ref = originals.collect(repo, run, ctx, knowledge)
    result = originals.attach(repo, run, ctx, knowledge, ref)["original_research"]
    assert result["documents_searched"] == 0
    newer = state()
    new_ref = originals.collect(repo, newer, ctx, knowledge)
    library.blobs._path(BlobRef.from_uri(doc["raw_ref"])).write_bytes(b"corrupt source")
    with pytest.raises(BlobIntegrityError):
        originals.attach(repo, newer, ctx, knowledge, new_ref)


def test_context_is_bounded_diverse_and_independent_of_source_iteration_order(runtime):
    repo, library = runtime
    doc = ingest(library)
    body = repo.raw_bytes(doc["text_ref"]).decode()
    documents = []
    for index in range(6):
        changed = copy.deepcopy(doc)
        changed["document_id"] = "TEST-" + str(index)
        changed["source"]["source_family"] = "FAMILY-" + str(index // 3)
        documents.append((changed, body))
    task = dict(horizon="MEDIUM", query="成本 净收益 现金流")
    result = originals.search(documents, task)
    assert result == originals.search(list(reversed(documents)), task)
    assert len(result["passages"]) == originals.LIMIT
    assert len({p["source_family"] for p in result["passages"]}) == 2
    assert sum(len(p["quote"]) for p in result["passages"]) <= originals.LIMIT * originals.CHARS
    assert all(p["passage_id"].startswith("KO-") for p in result["passages"])
