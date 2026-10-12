"""Shared live research registry; immutable data remains content addressed."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_10"
down_revision = "20261004_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inv_quant_research_run",
        sa.Column("run_id", sa.String(100), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table("inv_quant_research_run")
