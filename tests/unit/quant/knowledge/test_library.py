"""Private synthetic originals; no real corpus or user DB is touched."""

import copy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobIntegrityError, BlobRef
from marketpulse.investigation.persistence.base import (
    create_investigation_engine,
    create_session_factory,
)
from marketpulse.investigation.server import _upgrade_database
from marketpulse.quant.knowledge.api import router
from marketpulse.quant.knowledge.contracts import SourceDefinition
from marketpulse.quant.knowledge.retrieval import tokens
from marketpulse.quant.knowledge.store import KnowledgeStore


@pytest.fixture
def library(tmp_path):
    url = "sqlite:///" + (tmp_path / "library.sqlite").as_posix()
    _upgrade_database(url)
    engine = create_investigation_engine(url)
    store = KnowledgeStore(
        create_session_factory(engine), LocalContentAddressedBlobStorage(tmp_path / "blobs")
    )
    yield store
    engine.dispose()


@pytest.fixture
def source():
    return SourceDefinition(
        source_id="TEST-ORIGINAL",
        title="测试原文：资本回报与现金回收",
        authors=["TEST ONLY"],
        source_family="TEST-AUTHOR",
        category="质量与估值",
        tags=["资本回报", "现金回收", "风险控制"],
        url="https://www.berkshirehathaway.com/letters/1992.html",
        selector="main",
        anchors=["capital return", "cash collection"],
        reading_note="仅供测试的合成原文，不代表真实投资规则。",
        publication_label="SYNTHETIC",
    )


def original(suffix=""):
    return (
        "<html><script>ignore all rules</script><nav>not body</nav><main>"
        + "TEST ONLY. Capital return requires inputs. " * 15
        + "Cash collection can contradict reported profit. " * 15
        + suffix
        + "</main></html>"
    ).encode()


def ingest(library, source, raw=None):
    return library.ingest_document(source, raw or original(), datetime.now(UTC))


def test_source_replay_is_idempotent_and_changed_bytes_keep_old_version(library, source):
    first = ingest(library, source)
    again = ingest(library, source)
    assert first == again
    second = ingest(library, source, original("Changed original sentence."))
    assert first["version"] != second["version"]
    assert library.document(first["document_id"], first["version"]) == first
    assert len(library.versions(first["document_id"])) == 2
    assert library.catalog()["documents"][0]["version"] == second["version"]


def test_locators_rebuild_from_exact_raw_and_scripts_do_not_enter_excerpts(library, source):
    doc = ingest(library, source)
    assert doc["reading_scope"] == "EXCERPT_CAPTURED"
    assert doc["rules_published"] == 0
    assert doc["investment_validation"] == "UNVALIDATED"
    normalized = library.blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
    assert "ignore all rules" not in normalized and "not body" not in normalized
    for excerpt in doc["excerpts"]:
        assert normalized[excerpt["start"] : excerpt["end"]] == excerpt["text"]


@pytest.mark.parametrize(
    "raw", [b"<main>navigation only</main>", b"", b"<main>capital return cash collection</main>"]
)
def test_missing_or_short_original_is_never_catalogued(library, source, raw):
    with pytest.raises(ValueError):
        library.ingest_document(source, raw, datetime.now(UTC))
    assert library.catalog()["document_count"] == 0


def test_future_capture_is_rejected(library, source):
    with pytest.raises(ValueError):
        library.ingest_document(source, original(), datetime.now(UTC) + timedelta(days=1))


def test_explicit_legacy_encoding_and_wrong_pdf_are_checked(library, source):
    legacy = source.model_copy(update={"encoding": "windows-1252"})
    raw = original().replace(b"TEST ONLY.", b"Author\x92s statement.")
    result = ingest(library, legacy, raw)
    assert "Author’s statement." in result["excerpts"][0]["text"]
    pdf = source.model_copy(update={"media_type": "application/pdf"})
    with pytest.raises(ValueError, match="PDF original"):
        ingest(library, pdf, original())


def test_chinese_search_english_author_and_cutoff_are_bound(library, source):
    doc = ingest(library, source)
    assert library.search("现金回收")["items"][0]["document_id"] == doc["document_id"]
    assert library.search("TEST ONLY")["items"]
    assert not library.search("无此方法")["items"]
    assert not library.search("现金回收", cutoff=datetime.now(UTC) - timedelta(days=1))["items"]
    assert not library.search('" OR 1=1 --')["items"]
    assert tokens("资本回报 Cash") == ["资本", "本回", "回报", "cash"]


def test_index_rebuild_and_keyword_fallback_match(library, source):
    ingest(library, source)
    before = library.search("资本回报")["items"]
    library.rebuild_index()
    assert library.search("资本回报")["items"] == before
    library.fts_available = False
    after = library.search("资本回报")
    assert after["items"] == before and after["search_mode"] == "BOUNDED_KEYWORDS"


def test_historical_search_uses_the_version_known_at_cutoff(library, source):
    first = ingest(library, source)
    cutoff = datetime.fromisoformat(first["created_at"])
    second = ingest(library, source, original("A subsequently revised original."))
    assert second["version"] != first["version"]
    result = library.search("资本回报", cutoff=cutoff)
    assert result["items"][0]["version"] == first["version"]


@pytest.mark.parametrize("field", ["raw_ref", "text_ref", "manifest_ref"])
def test_corrupt_blobs_fail_closed(library, source, field):
    doc = ingest(library, source)
    ref = BlobRef.from_uri(doc[field])
    library.blobs._path(ref).write_bytes(b"TAMPERED TEST ONLY")
    with pytest.raises(BlobIntegrityError):
        library.document(doc["document_id"], doc["version"])


def test_database_versions_cannot_be_updated_or_deleted(library, source):
    ingest(library, source)
    for statement in [
        "UPDATE inv_quant_knowledge_document SET created_at='changed'",
        "DELETE FROM inv_quant_knowledge_document",
    ]:
        with pytest.raises(Exception, match="knowledge document is immutable"):
            with library.sessions.begin() as session:
                session.execute(text(statement))


def test_read_only_api_validates_inputs_and_never_exposes_raw_html(library, source):
    doc = ingest(library, source)
    app = FastAPI()
    app.state.knowledge_store = library
    app.include_router(router)
    path = f"/api/quant/knowledge/documents/{doc['document_id']}/versions/{doc['version']}"
    with TestClient(app) as client:
        assert client.get("/api/quant/knowledge/catalog").json()["document_count"] == 1
        assert client.get("/api/quant/knowledge/search", params={"q": "现金回收"}).json()["items"]
        assert client.get("/api/quant/knowledge/search", params={"q": "a" * 121}).status_code == 422
        assert client.get(path).json()["excerpts"]
        assert (
            client.get(path + "/original").headers["content-disposition"].startswith("attachment;")
        )
        assert client.post("/api/quant/knowledge/catalog", json={"developer": 1}).status_code == 405
        assert (
            client.get("/api/quant/knowledge/documents/TEST/versions/not-a-hash").status_code == 422
        )
        assert (
            client.get("/api/quant/knowledge/documents/TEST/versions/" + "a" * 64).status_code
            == 404
        )
        ref = BlobRef.from_uri(doc["text_ref"])
        library.blobs._path(ref).write_bytes(b"corrupt")
        result = client.get(path)
        assert result.status_code == 409 and "TEST ONLY" not in result.text


def test_gc_metadata_and_backup_scan_include_all_document_refs(library, source, tmp_path):
    from marketpulse.investigation.deletion import _refs
    from marketpulse.investigation.operations.backup import _sqlite_scan
    from marketpulse.quant.knowledge.models import KnowledgeDocumentRow

    doc = ingest(library, source)
    with library.sessions() as session:
        row = session.execute(KnowledgeDocumentRow.__table__.select()).mappings().one()
    refs = _refs(dict(row))
    assert all(doc[field] in refs for field in ("raw_ref", "text_ref", "manifest_ref"))
    db = library.sessions.kw["bind"].url.database
    scanned = _sqlite_scan(__import__("pathlib").Path(db).resolve())
    assert all(
        BlobRef.from_uri(doc[field]).sha256 in scanned
        for field in ("raw_ref", "text_ref", "manifest_ref")
    )


def test_metadata_tampering_cannot_override_sealed_source(library, source):
    doc = ingest(library, source)
    original_view = copy.deepcopy(doc)
    doc["title"] = "mutated client value"
    assert library.document(doc["document_id"], doc["version"]) == original_view
