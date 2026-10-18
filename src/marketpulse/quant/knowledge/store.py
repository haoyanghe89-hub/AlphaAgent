"""Append-only source versions on the existing DB and verified immutable blob store."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from marketpulse.infrastructure.storage.models import BlobRef

from ..contracts import canonical, digest
from .contracts import SourceDefinition
from .ingestion import EXTRACTOR, extract
from .models import KnowledgeDocumentRow
from .retrieval import searchable, tokens


class KnowledgeStore:
    def __init__(self, sessions, blobs):
        self.sessions, self.blobs = sessions, blobs
        with sessions() as session:
            self.fts_available = (
                bool(
                    session.scalar(
                        text(
                            "SELECT count(*) FROM sqlite_master "
                            "WHERE name='inv_quant_knowledge_search'"
                        )
                    )
                )
                if session.bind.dialect.name == "sqlite"
                else False
            )

    def _blob(self, value):
        return self.blobs.put_bytes(canonical(value).encode()).ref.uri

    def _value(self, ref):
        return json.loads(self.blobs.get_bytes(BlobRef.from_uri(ref)))

    def ingest_document(self, source: SourceDefinition, raw: bytes, fetched_at: datetime):
        now = datetime.now(UTC)
        if fetched_at.tzinfo is None or fetched_at > now + timedelta(minutes=2):
            raise ValueError("capture time must be aware and not in the future")
        normalized, locators = extract(raw, source)
        raw_ref = self.blobs.put_bytes(raw).ref.uri
        text_ref = self.blobs.put_bytes(normalized.encode()).ref.uri
        identity = dict(
            source=source.model_dump(mode="json"),
            raw_ref=raw_ref,
            text_ref=text_ref,
            locators=locators,
            extractor_version=EXTRACTOR,
        )
        version = digest(identity)
        with self.sessions() as session:
            old = session.get(KnowledgeDocumentRow, (source.source_id, version))
        if old:
            return self.document(source.source_id, version)
        manifest = dict(
            identity,
            document_id=source.source_id,
            version=version,
            fetched_at=fetched_at.astimezone(UTC).isoformat(),
            created_at=now.isoformat(),
            reading_scope="EXCERPT_CAPTURED",
            body_characters=len(normalized),
            rules_published=0,
            investment_validation="UNVALIDATED",
        )
        manifest_ref = self._blob(manifest)
        try:
            with self.sessions.begin() as session:
                session.add(
                    KnowledgeDocumentRow(
                        document_id=source.source_id,
                        version=version,
                        created_at=manifest["created_at"],
                        raw_ref=raw_ref,
                        text_ref=text_ref,
                        manifest_ref=manifest_ref,
                    )
                )
                session.flush()
                if self.fts_available:
                    self._index(session, manifest, normalized)
        except IntegrityError:
            # Another importer can publish the same exact immutable version.
            with self.sessions() as session:
                if session.get(KnowledgeDocumentRow, (source.source_id, version)) is None:
                    raise
        return self.document(source.source_id, version)

    def _index(self, session, manifest, normalized):
        session.execute(
            text("DELETE FROM inv_quant_knowledge_search WHERE document_id=:id"),
            {"id": manifest["document_id"]},
        )
        session.execute(
            text(
                "INSERT INTO inv_quant_knowledge_search(document_id,version,tokens) "
                "VALUES (:id,:version,:tokens)"
            ),
            dict(
                id=manifest["document_id"],
                version=manifest["version"],
                tokens=searchable(manifest, normalized),
            ),
        )

    def document(self, document_id, version):
        with self.sessions() as session:
            row = session.get(KnowledgeDocumentRow, (document_id, version))
        if row is None:
            raise KeyError("source version was not found")
        manifest = self._value(row.manifest_ref)
        if (
            manifest["document_id"] != document_id
            or manifest["version"] != version
            or manifest["raw_ref"] != row.raw_ref
            or manifest["text_ref"] != row.text_ref
            or manifest["created_at"] != row.created_at
            or manifest["extractor_version"] != EXTRACTOR
            or manifest["reading_scope"] != "EXCERPT_CAPTURED"
            or manifest["rules_published"] != 0
            or manifest["investment_validation"] != "UNVALIDATED"
        ):
            raise ValueError("source manifest differs from its registered version")
        identity = {
            key: manifest[key]
            for key in ("source", "raw_ref", "text_ref", "locators", "extractor_version")
        }
        if digest(identity) != version:
            raise ValueError("source identity differs from its version hash")
        source = SourceDefinition.model_validate(manifest["source"])
        raw = self.blobs.get_bytes(BlobRef.from_uri(row.raw_ref))
        normalized, locators = extract(raw, source)
        saved_text = self.blobs.get_bytes(BlobRef.from_uri(row.text_ref)).decode()
        if (
            saved_text != normalized
            or locators != manifest["locators"]
            or len(normalized) != manifest["body_characters"]
        ):
            raise ValueError("source text or character locator differs from original")
        return dict(
            manifest,
            manifest_ref=row.manifest_ref,
            title=source.title,
            excerpts=[
                dict(locator, text=normalized[locator["start"] : locator["end"]])
                for locator in locators
            ],
        )

    def _latest(self, cutoff=None):
        with self.sessions() as session:
            query = select(KnowledgeDocumentRow)
            if cutoff is not None:
                query = query.where(
                    KnowledgeDocumentRow.created_at <= cutoff.astimezone(UTC).isoformat()
                )
            rows = session.scalars(query.order_by(KnowledgeDocumentRow.created_at.desc())).all()
        seen, result = set(), []
        for row in rows:
            if row.document_id not in seen:
                seen.add(row.document_id)
                result.append(row)
            if len(result) == 200:
                break
        return result

    @staticmethod
    def _summary(doc):
        source = doc["source"]
        return dict(
            document_id=doc["document_id"],
            version=doc["version"],
            title=source["title"],
            authors=source["authors"],
            tags=source["tags"],
            category=source["category"],
            source_family=source["source_family"],
            url=source["url"],
            reading_note=source["reading_note"],
            publication_label=source["publication_label"],
            fetched_at=doc["fetched_at"],
            reading_scope=doc["reading_scope"],
            excerpt_count=len(doc["excerpts"]),
            body_characters=doc["body_characters"],
            media_type=source["media_type"],
        )

    def versions(self, document_id):
        with self.sessions() as session:
            rows = session.scalars(
                select(KnowledgeDocumentRow)
                .where(KnowledgeDocumentRow.document_id == document_id)
                .order_by(KnowledgeDocumentRow.created_at.desc())
            ).all()
        return [dict(version=r.version, created_at=r.created_at) for r in rows]

    def catalog(self):
        docs = [self._summary(self.document(r.document_id, r.version)) for r in self._latest()]
        return dict(
            document_count=len(docs),
            source_families=len({d["source_family"] for d in docs}),
            categories=sorted({d["category"] for d in docs}),
            rules_published=0,
            routes_executable=0,
            stage="K1_SOURCES",
            search_mode="CJK_BIGRAM_FTS5" if self.fts_available else "BOUNDED_KEYWORDS",
            documents=docs,
        )

    def search(self, query, *, category=None, cutoff=None, limit=30):
        if not 1 <= len(query.strip()) <= 120 or not 1 <= limit <= 50:
            raise ValueError("search query or limit is outside bounds")
        if cutoff is not None and cutoff.tzinfo is None:
            raise ValueError("search cutoff needs a timezone")
        terms = tokens(query)[:64]
        if not terms:
            return dict(
                items=[],
                search_mode="CJK_BIGRAM_FTS5" if self.fts_available else "BOUNDED_KEYWORDS",
            )
        matched = None
        use_fts = self.fts_available and cutoff is None
        if use_fts:
            expression = " AND ".join('"' + term + '"' for term in terms)
            with self.sessions() as session:
                matched = set(
                    session.execute(
                        text(
                            "SELECT document_id,version FROM inv_quant_knowledge_search "
                            "WHERE tokens MATCH :query"
                        ),
                        {"query": expression},
                    ).all()
                )
        items = []
        for row in self._latest(cutoff):
            if matched is not None and (row.document_id, row.version) not in matched:
                continue
            doc = self.document(row.document_id, row.version)
            if category and doc["source"]["category"] != category:
                continue
            if cutoff and datetime.fromisoformat(doc["fetched_at"]) > cutoff:
                continue
            if matched is None:
                normalized = self.blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
                if not set(terms) <= set(searchable(doc, normalized).split()):
                    continue
            items.append(self._summary(doc))
            if len(items) == limit:
                break
        return dict(items=items, search_mode="CJK_BIGRAM_FTS5" if use_fts else "BOUNDED_KEYWORDS")

    def rebuild_index(self):
        docs = [self.document(r.document_id, r.version) for r in self._latest()]
        if self.fts_available:
            with self.sessions.begin() as session:
                session.execute(text("DELETE FROM inv_quant_knowledge_search"))
                for doc in docs:
                    normalized = self.blobs.get_bytes(BlobRef.from_uri(doc["text_ref"])).decode()
                    self._index(session, doc, normalized)
        return len(docs)
