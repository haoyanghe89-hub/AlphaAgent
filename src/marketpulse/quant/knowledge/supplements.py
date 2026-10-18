"""User ideas are untrusted candidates, never automatically published evidence."""

import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..contracts import digest
from ..domain import FrozenModel
from .models import KnowledgeSupplementDecisionRow, KnowledgeSupplementRow


class Submission(FrozenModel):
    request_key: UUID
    title: str = Field(min_length=2, max_length=120)
    text: str = Field(min_length=20, max_length=20000)
    kind: Literal["METHOD_IDEA", "MARKET_OPINION", "POLICY_MATERIAL"]
    source_url: HttpUrl | None = None
    author_claim: str | None = Field(default=None, max_length=100)
    published_at_claim: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    user_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validity(self):
        if self.source_url and self.source_url.scheme != "https":
            raise ValueError("出处链接须为HTTPS；提交不会自动访问该链接。")
        if self.kind == "MARKET_OPINION" and self.expires_at is None:
            raise ValueError("短期市场观点须填写有效期。")
        if (
            self.published_at_claim
            and self.expires_at
            and self.expires_at <= self.published_at_claim
        ):
            raise ValueError("有效期必须晚于声明发表时间。")
        return self


class Assessment(FrozenModel):
    verdict: Literal["NEEDS_SOURCE", "USEFUL", "REJECTED"]
    reason: str = Field(min_length=10, max_length=1500)
    counterexamples: tuple[str, ...] = Field(default=(), max_length=8)
    required_inputs: tuple[str, ...] = Field(default=(), max_length=10)
    verified_document_id: str | None = None
    verified_document_version: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def useful(self):
        if self.verdict == "USEFUL" and (not self.counterexamples or not self.required_inputs):
            raise ValueError("有用的观点也须记录反例和真实数据要求。")
        if bool(self.verified_document_id) != bool(self.verified_document_version):
            raise ValueError("出处核查必须绑定完整资料版本。")
        return self


class SupplementStore:
    def __init__(self, rules):
        self.rules, self.sessions = rules, rules.sessions
        self.blob, self.value = rules.blob, rules.value

    def submit(self, submission):
        request = Submission.model_validate(submission).model_dump(mode="json")
        sid = UUID(request["request_key"]).hex
        raw = request["text"]
        quarantine = bool(
            re.search(
                r"ignore (all |previous )?instructions|忽略.{0,6}(指令|规则)|"
                r"system\s*prompt|<\|system|�",
                raw,
                re.I,
            )
        )
        content_hash = digest(dict(text=raw.strip(), source_url=request["source_url"]))
        payload = dict(
            supplement_id=sid,
            request=request,
            created_at=datetime.now(UTC).isoformat(),
            content_hash=content_hash,
            status="QUARANTINED" if quarantine else "CANDIDATE",
            attribution="UNVERIFIED",
            trust="UNTRUSTED_SOURCE_CONTENT",
            retrieval_eligible=False,
        )
        try:
            with self.sessions.begin() as session:
                session.add(
                    KnowledgeSupplementRow(
                        supplement_id=sid,
                        created_at=payload["created_at"],
                        payload_ref=self.blob(payload),
                    )
                )
        except IntegrityError:
            old = self.get(sid)
            if old["request"] != request:
                raise ValueError("supplement request identity conflict") from None
            return old
        return self.get(sid)

    def get(self, sid):
        with self.sessions() as session:
            row = session.get(KnowledgeSupplementRow, sid)
            decisions = session.scalars(
                select(KnowledgeSupplementDecisionRow)
                .where(KnowledgeSupplementDecisionRow.supplement_id == sid)
                .order_by(KnowledgeSupplementDecisionRow.created_at)
            ).all()
        if row is None:
            raise KeyError(sid)
        payload = self.value(row.payload_ref)
        if payload["supplement_id"] != sid or payload["created_at"] != row.created_at:
            raise ValueError("supplement identity mismatch")
        request = Submission.model_validate(payload["request"]).model_dump(mode="json")
        if (
            UUID(request["request_key"]).hex != sid
            or digest(dict(text=request["text"].strip(), source_url=request["source_url"]))
            != payload["content_hash"]
        ):
            raise ValueError("supplement content mismatch")
        history = []
        for decision in decisions:
            item = self.value(decision.payload_ref)
            if digest(item) != decision.decision_id or item["supplement_id"] != sid:
                raise ValueError("supplement decision mismatch")
            history.append(item)
        return dict(payload, decisions=history)

    def list(self):
        with self.sessions() as session:
            ids = session.scalars(
                select(KnowledgeSupplementRow.supplement_id)
                .order_by(KnowledgeSupplementRow.created_at.desc())
                .limit(100)
            ).all()
        return [self.get(sid) for sid in ids]

    def _append(self, sid, decision):
        payload = dict(supplement_id=sid, created_at=datetime.now(UTC).isoformat(), **decision)
        key = digest(payload)
        refs = sorted(set(re.findall(r"blob://sha256/[0-9a-f]{64}", json.dumps(payload))))
        with self.sessions.begin() as session:
            session.add(
                KnowledgeSupplementDecisionRow(
                    decision_id=key,
                    supplement_id=sid,
                    created_at=payload["created_at"],
                    payload_ref=self.blob(payload),
                    blob_refs_json=json.dumps(refs),
                )
            )
        return self.get(sid)

    def assess(self, sid, assessment):
        item = self.get(sid)
        assessment = Assessment.model_validate(assessment)
        if item["status"] == "QUARANTINED" and assessment.verdict == "USEFUL":
            raise ValueError("quarantined content cannot be promoted")
        verified = None
        if assessment.verified_document_id:
            verified = self.rules.library.document(
                assessment.verified_document_id, assessment.verified_document_version
            )
            # Claimed author/URL is not established by merely attaching a different document.
            claimed = item["request"].get("source_url")
            if claimed and claimed.rstrip("/") != verified["source"]["url"].rstrip("/"):
                raise ValueError("verified source URL does not match candidate attribution")
            author = item["request"].get("author_claim")
            if author and author.casefold().strip() not in {
                a.casefold().strip() for a in verified["source"]["authors"]
            }:
                raise ValueError("claimed author does not match verified original attribution")
        return self._append(
            sid,
            dict(
                action="ASSESSED",
                assessment=assessment.model_dump(mode="json"),
                verified_source=verified,
            ),
        )

    def link_published_rule(self, sid, rule_id, version):
        item = self.get(sid)
        if item["status"] == "QUARANTINED" or item["request"]["kind"] == "MARKET_OPINION":
            raise ValueError("short-term or quarantined content cannot become a permanent method")
        assessments = [d for d in item["decisions"] if d["action"] == "ASSESSED"]
        latest = assessments[-1] if assessments else None
        if (
            not latest
            or latest["assessment"]["verdict"] != "USEFUL"
            or not latest.get("verified_source")
        ):
            raise ValueError("useful assessment and verified original required")
        rule = self.rules.rule(rule_id, version)
        if rule["status"] != "PUBLISHED":
            raise ValueError("only a reviewed published rule can be linked")
        source = latest["verified_source"]
        if not any(
            c["document_id"] == source["document_id"] and c["version"] == source["version"]
            for c in rule["rule"]["citations"]
        ):
            raise ValueError("published rule must cite the verified original")
        return self._append(
            sid,
            dict(
                action="INCORPORATED",
                rule_id=rule_id,
                version=version,
                rule_ref=self.rules.blob(rule),
            ),
        )
