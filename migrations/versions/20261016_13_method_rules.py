"""Immutable method versions, model audits, reviews and publication events."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_13"
down_revision = "20261016_12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inv_quant_knowledge_rule",
        sa.Column("rule_id", sa.String(80), primary_key=True),
        sa.Column("version", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("payload_ref", sa.String(200), nullable=False),
    )
    op.create_table(
        "inv_quant_knowledge_audit",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("audit_ref", sa.String(200), nullable=False),
        sa.Column("blob_refs_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "inv_quant_knowledge_review",
        sa.Column("review_id", sa.String(64), primary_key=True),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("review_ref", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id", "version"],
            ["inv_quant_knowledge_rule.rule_id", "inv_quant_knowledge_rule.version"],
        ),
    )
    op.create_table(
        "inv_quant_knowledge_publication",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("rule_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("event_ref", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id", "version"],
            ["inv_quant_knowledge_rule.rule_id", "inv_quant_knowledge_rule.version"],
        ),
        sa.UniqueConstraint("rule_id", "version", "action"),
    )
    if op.get_bind().dialect.name == "sqlite":
        for name in ("rule", "audit", "review", "publication"):
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER knowledge_{name}_no_{action.lower()} "
                    f"BEFORE {action} ON inv_quant_knowledge_{name} "
                    "BEGIN SELECT RAISE(ABORT, 'knowledge record is immutable'); END"
                )


def downgrade():
    raise RuntimeError("Method history is immutable; restore an explicit paired backup.")
