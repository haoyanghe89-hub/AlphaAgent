"""Register the medium-term ledger before legacy archive/GC enumerates metadata."""

import sqlalchemy as sa
from alembic import op

revision = "20261012_11"
down_revision = "20261010_10"
branch_labels = None
depends_on = None


def upgrade():
    # P2 development databases may already contain these append-only tables.
    # Adopt them in place; never replace their forecasts or observation history.
    metadata = sa.MetaData()
    sa.Table(
        "inv_quant_decision_forecast",
        metadata,
        sa.Column("forecast_id", sa.String(100), primary_key=True),
        sa.Column("run_id", sa.String(100), nullable=False, unique=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("record_ref", sa.String(200), nullable=False),
        sa.Column("report_ref", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    sa.Table(
        "inv_quant_decision_observation",
        metadata,
        sa.Column("observation_id", sa.String(100), primary_key=True),
        sa.Column("forecast_id", sa.String(100), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("record_ref", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint("forecast_id", "input_hash"),
    )
    metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("inv_quant_decision_observation")
    op.drop_table("inv_quant_decision_forecast")
