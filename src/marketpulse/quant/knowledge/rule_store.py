"""Append-only reference-rule lifecycle; write access exists only in local tooling."""

import json
import re
from datetime import UTC, datetime

from sqlalchemy import select, text

from ..contracts import digest
from .distillation import validate_rule
from .models import KnowledgeAuditRow, KnowledgePublicationRow, KnowledgeReviewRow, KnowledgeRuleRow
from .review import verify_reviews
from .rules import MethodRule


def now():
    return datetime.now(UTC).isoformat()


class RuleStore:
    def __init__(self, library):
        self.library = library
        self.sessions = library.sessions
        self.blob, self.value = library._blob, library._value

    def draft(self, rule):
        rule = MethodRule.model_validate(rule)
        validate_rule(rule, self.library)
        version = digest(rule.model_dump(mode="json"))
        with self.sessions() as session:
            row = session.get(KnowledgeRuleRow, (rule.rule_id, version))
        if row is None:
            payload = dict(
                rule=rule.model_dump(mode="json"),
                version=version,
                created_at=now(),
                curator="CODEX_SOURCE_READING",
                validation_status="UNVALIDATED",
            )
            with self.sessions.begin() as session:
                session.add(
                    KnowledgeRuleRow(
                        rule_id=rule.rule_id,
                        version=version,
                        created_at=payload["created_at"],
                        payload_ref=self.blob(payload),
                    )
                )
        return self.rule(rule.rule_id, version)

    def archive_audit(self, state):
        """Root every request/response, including failures, in shared GC/backup metadata."""
        audit_ref = self.blob(state)
        audit_id = digest(state)
        refs = sorted(set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(state))))
        for ref in refs:
            from marketpulse.infrastructure.storage.models import BlobRef

            self.library.blobs.get_bytes(BlobRef.from_uri(ref))
        with self.sessions.begin() as session:
            if session.get(KnowledgeAuditRow, audit_id) is None:
                session.add(
                    KnowledgeAuditRow(
                        audit_id=audit_id,
                        created_at=now(),
                        audit_ref=audit_ref,
                        blob_refs_json=json.dumps(refs),
                    )
                )
        return audit_id

    def audit(self, audit_id):
        with self.sessions() as session:
            row = session.get(KnowledgeAuditRow, audit_id)
        if row is None:
            raise KeyError("audit was not found")
        value = self.value(row.audit_ref)
        if digest(value) != audit_id:
            raise ValueError("AUDIT_HASH_MISMATCH")
        refs = sorted(set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(value))))
        if refs != json.loads(row.blob_refs_json):
            raise ValueError("AUDIT_ROOTS_MISMATCH")
        return value

    def _payload(self, rule_id, version, cache=None):
        with self.sessions() as session:
            row = session.get(KnowledgeRuleRow, (rule_id, version))
        if row is None:
            raise KeyError("rule version was not found")
        payload = self.value(row.payload_ref)
        rule = MethodRule.model_validate(payload["rule"])
        if (
            rule.rule_id != rule_id
            or digest(rule.model_dump(mode="json")) != version
            or payload["version"] != version
            or payload["created_at"] != row.created_at
        ):
            raise ValueError("RULE_IDENTITY_MISMATCH")
        families = validate_rule(rule, self.library, cache)
        return dict(payload, source_families=families)

    def record_review(self, rule_id, version, audit_id):
        rule = MethodRule.model_validate(self._payload(rule_id, version)["rule"])
        result = verify_reviews(rule, version, self.audit(audit_id), self.value)
        record = dict(rule_id=rule_id, version=version, audit_id=audit_id, **result)
        review_id = digest(record)
        with self.sessions.begin() as session:
            if session.get(KnowledgeReviewRow, review_id) is None:
                session.add(
                    KnowledgeReviewRow(
                        review_id=review_id,
                        rule_id=rule_id,
                        version=version,
                        created_at=now(),
                        review_ref=self.blob(record),
                    )
                )
        return dict(record, review_id=review_id)

    def review(self, review_id, cache=None):
        with self.sessions() as session:
            row = session.get(KnowledgeReviewRow, review_id)
        if row is None:
            raise KeyError("review was not found")
        record = self.value(row.review_ref)
        if (
            digest(record) != review_id
            or record["rule_id"] != row.rule_id
            or record["version"] != row.version
        ):
            raise ValueError("REVIEW_IDENTITY_MISMATCH")
        rule = MethodRule.model_validate(self._payload(row.rule_id, row.version, cache)["rule"])
        result = verify_reviews(rule, row.version, self.audit(record["audit_id"]), self.value)
        if any(result[k] != record[k] for k in result):
            raise ValueError("REVIEW_RESULT_MISMATCH")
        return dict(record, review_id=review_id)

    @staticmethod
    def _lock(session):
        if session.bind.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))

    def publish(self, rule_id, version, review_id):
        review = self.review(review_id)
        if (
            review["rule_id"] != rule_id
            or review["version"] != version
            or review["verdict"] != "APPROVE"
        ):
            raise ValueError("APPROVED_EXACT_VERSION_REQUIRED")
        with self.sessions.begin() as session:
            self._lock(session)
            events = session.scalars(
                select(KnowledgePublicationRow).where(
                    KnowledgePublicationRow.rule_id == rule_id,
                    KnowledgePublicationRow.version == version,
                )
            ).all()
            if any(e.action == "RETIRED" for e in events):
                raise ValueError("RETIRED_VERSION_CANNOT_REPUBLISH")
            if not events:
                event = dict(
                    rule_id=rule_id,
                    version=version,
                    action="PUBLISHED",
                    review_id=review_id,
                    created_at=now(),
                )
                session.add(
                    KnowledgePublicationRow(
                        event_id=digest(event),
                        rule_id=rule_id,
                        version=version,
                        action="PUBLISHED",
                        created_at=event["created_at"],
                        event_ref=self.blob(event),
                    )
                )
        return self.rule(rule_id, version)

    def retire(self, rule_id, version, reason):
        if not 10 <= len(reason.strip()) <= 500:
            raise ValueError("withdrawal requires a concrete reason")
        with self.sessions.begin() as session:
            self._lock(session)
            events = session.scalars(
                select(KnowledgePublicationRow).where(
                    KnowledgePublicationRow.rule_id == rule_id,
                    KnowledgePublicationRow.version == version,
                )
            ).all()
            if not any(e.action == "PUBLISHED" for e in events):
                raise ValueError("ONLY_PUBLISHED_RULE_CAN_RETIRE")
            if not any(e.action == "RETIRED" for e in events):
                event = dict(
                    rule_id=rule_id,
                    version=version,
                    action="RETIRED",
                    reason=reason,
                    created_at=now(),
                )
                session.add(
                    KnowledgePublicationRow(
                        event_id=digest(event),
                        rule_id=rule_id,
                        version=version,
                        action="RETIRED",
                        created_at=event["created_at"],
                        event_ref=self.blob(event),
                    )
                )
        return self.rule(rule_id, version)

    def rule(self, rule_id, version, cache=None):
        payload = self._payload(rule_id, version, cache)
        with self.sessions() as session:
            reviews = session.scalars(
                select(KnowledgeReviewRow)
                .where(KnowledgeReviewRow.rule_id == rule_id, KnowledgeReviewRow.version == version)
                .order_by(KnowledgeReviewRow.created_at)
            ).all()
            rows = session.scalars(
                select(KnowledgePublicationRow)
                .where(
                    KnowledgePublicationRow.rule_id == rule_id,
                    KnowledgePublicationRow.version == version,
                )
                .order_by(KnowledgePublicationRow.created_at)
            ).all()
        events = []
        for row in rows:
            event = self.value(row.event_ref)
            if (
                digest(event) != row.event_id
                or event["action"] != row.action
                or event["rule_id"] != rule_id
                or event["version"] != version
                or event["created_at"] != row.created_at
            ):
                raise ValueError("PUBLICATION_IDENTITY_MISMATCH")
            if row.action == "PUBLISHED":
                review = self.review(event["review_id"], cache)
                if (
                    review["verdict"] != "APPROVE"
                    or review["version"] != version
                    or review["rule_id"] != rule_id
                ):
                    raise ValueError("PUBLICATION_REVIEW_MISMATCH")
            events.append(event)
        status = (
            "RETIRED"
            if any(e["action"] == "RETIRED" for e in events)
            else "PUBLISHED"
            if events
            else "REVIEWED"
            if reviews
            else "DRAFT"
        )
        return dict(
            payload,
            rule_id=rule_id,
            status=status,
            events=events,
            review_ids=[r.review_id for r in reviews],
        )

    def current(self, cutoff=None):
        if cutoff is not None and cutoff.tzinfo is None:
            raise ValueError("cutoff needs timezone")
        with self.sessions() as session:
            query = select(KnowledgePublicationRow).where(
                KnowledgePublicationRow.action == "PUBLISHED"
            )
            if cutoff:
                query = query.where(
                    KnowledgePublicationRow.created_at <= cutoff.astimezone(UTC).isoformat()
                )
            rows = session.scalars(query.order_by(KnowledgePublicationRow.created_at.desc())).all()
        seen, result, cache = set(), [], {}
        if len(rows) > 2000:
            raise ValueError("reference catalog exceeds current bounded capacity")
        for row in rows:
            if row.rule_id in seen:
                continue
            seen.add(row.rule_id)
            item = self.rule(row.rule_id, row.version, cache)
            events = [
                e
                for e in item["events"]
                if not cutoff or datetime.fromisoformat(e["created_at"]) <= cutoff
            ]
            if any(e["action"] == "RETIRED" for e in events):
                continue
            result.append(
                dict(
                    item,
                    status="PUBLISHED",
                    events=events,
                    review_ids=[e["review_id"] for e in events if e["action"] == "PUBLISHED"],
                )
            )
        return result

    def catalog(self):
        rules = self.current()
        return dict(
            stage="K2_REFERENCE_RULES",
            rules_published=len(rules),
            routes_executable=0,
            validation_status="UNVALIDATED",
            source_families=len({f for r in rules for f in r["source_families"]}),
            items=rules,
        )
