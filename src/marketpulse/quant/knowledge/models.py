"""Register original references with the shared archive, backup and GC metadata."""

from sqlalchemy import ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from marketpulse.investigation.persistence.base import Base


class KnowledgeDocumentRow(Base):
    __tablename__ = "inv_quant_knowledge_document"
    document_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    text_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    manifest_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class KnowledgeRuleRow(Base):
    __tablename__ = "inv_quant_knowledge_rule"
    rule_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class KnowledgeAuditRow(Base):
    __tablename__ = "inv_quant_knowledge_audit"
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    audit_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    # Shared GC and paired backup scan DB columns, not transitive JSON blobs.
    blob_refs_json: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeReviewRow(Base):
    __tablename__ = "inv_quant_knowledge_review"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "version"],
            ["inv_quant_knowledge_rule.rule_id", "inv_quant_knowledge_rule.version"],
        ),
    )
    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    review_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class KnowledgePublicationRow(Base):
    __tablename__ = "inv_quant_knowledge_publication"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "version"],
            ["inv_quant_knowledge_rule.rule_id", "inv_quant_knowledge_rule.version"],
        ),
        UniqueConstraint("rule_id", "version", "action"),
    )
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    event_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class KnowledgeSupplementRow(Base):
    __tablename__ = "inv_quant_knowledge_supplement"
    supplement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class KnowledgeSupplementDecisionRow(Base):
    __tablename__ = "inv_quant_knowledge_supplement_decision"
    __table_args__ = (
        ForeignKeyConstraint(["supplement_id"], ["inv_quant_knowledge_supplement.supplement_id"]),
    )
    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    supplement_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    blob_refs_json: Mapped[str] = mapped_column(Text, nullable=False)


class InvestmentStudyRow(Base):
    __tablename__ = "inv_quant_investment_study"
    run_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    protocol_ref: Mapped[str] = mapped_column(String(200), nullable=False)


class InvestmentObservationRow(Base):
    __tablename__ = "inv_quant_investment_observation"
    observation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    record_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    blob_refs_json: Mapped[str] = mapped_column(Text, nullable=False)
