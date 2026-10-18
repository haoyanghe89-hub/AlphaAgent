"""Local candidate intake, separate from published method rules."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_14"
down_revision = "20261016_13"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inv_quant_knowledge_supplement",
        sa.Column("supplement_id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("payload_ref", sa.String(200), nullable=False),
    )
    op.create_table(
        "inv_quant_knowledge_supplement_decision",
        sa.Column("decision_id", sa.String(64), primary_key=True),
        sa.Column("supplement_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("payload_ref", sa.String(200), nullable=False),
        sa.Column("blob_refs_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["supplement_id"], ["inv_quant_knowledge_supplement.supplement_id"]
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        for name in ("supplement", "supplement_decision"):
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER knowledge_{name}_no_{action.lower()} "
                    f"BEFORE {action} ON inv_quant_knowledge_{name} "
                    "BEGIN SELECT RAISE(ABORT, 'knowledge record is immutable'); END"
                )


def downgrade():
    raise RuntimeError("Candidate history is immutable; restore an explicit paired backup.")
