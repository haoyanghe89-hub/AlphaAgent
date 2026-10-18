"""Forward registrations and immutable observation events."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_15"
down_revision = "20261016_14"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inv_quant_investment_study",
        sa.Column("run_id", sa.String(50), primary_key=True),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("protocol_ref", sa.String(200), nullable=False),
    )
    op.create_table(
        "inv_quant_investment_observation",
        sa.Column("observation_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(50), nullable=False),
        sa.Column("created_at", sa.String(50), nullable=False),
        sa.Column("record_ref", sa.String(200), nullable=False),
        sa.Column("blob_refs_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_inv_quant_investment_observation_run_id", "inv_quant_investment_observation", ["run_id"]
    )
    if op.get_bind().dialect.name == "sqlite":
        for name in ("study", "observation"):
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER investment_{name}_no_{action.lower()} "
                    f"BEFORE {action} ON inv_quant_investment_{name} "
                    "BEGIN SELECT RAISE(ABORT, 'investment observation is immutable'); END"
                )


def downgrade():
    raise RuntimeError("Forward history is immutable; restore an explicit paired backup.")
