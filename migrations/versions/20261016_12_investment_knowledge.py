"""Append-only investment originals and a rebuildable bilingual search projection."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_12"
down_revision = "20261012_11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inv_quant_knowledge_document",
        sa.Column("document_id", sa.String(80), primary_key=True),
        sa.Column("version", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("raw_ref", sa.String(200), nullable=False),
        sa.Column("text_ref", sa.String(200), nullable=False),
        sa.Column("manifest_ref", sa.String(200), nullable=False),
    )
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER knowledge_document_no_{action.lower()} "
                f"BEFORE {action} ON inv_quant_knowledge_document "
                "BEGIN SELECT RAISE(ABORT, 'knowledge document is immutable'); END"
            )
        connection = op.get_bind()
        if connection.exec_driver_sql("SELECT sqlite_compileoption_used('ENABLE_FTS5')").scalar():
            op.execute(
                "CREATE VIRTUAL TABLE inv_quant_knowledge_search USING "
                "fts5(document_id UNINDEXED, version UNINDEXED, tokens)"
            )


def downgrade():
    raise RuntimeError("Knowledge originals are append-only; restore an explicit paired backup.")
