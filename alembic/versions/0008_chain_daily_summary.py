"""chain_daily_summary (30-day retention rollup of chain_snapshots) + resolution_events.position_id

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per (instrument, record date): written when a band is stored, kept forever. Band rows older than
    # CHAIN_RETENTION_DAYS are rolled down to it and deleted by the daily cron. The 1-year IV percentile reads here.
    op.create_table(
        "chain_daily_summary",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("spot", sa.Float, nullable=True),
        sa.Column("atm_iv", sa.Float, nullable=True),
        sa.Column("realized_vol_20d", sa.Float, nullable=True),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("rolled_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("instrument_id", "as_of", name="uq_chain_daily_summary_inst_asof"),
    )
    op.create_index("ix_chain_daily_summary_inst_asof", "chain_daily_summary", ["instrument_id", "as_of"])
    # Timeline events written by a position go with it when the position is deleted.
    op.add_column(
        "resolution_events",
        sa.Column(
            "position_id",
            sa.Integer,
            sa.ForeignKey("option_positions.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_resolution_events_position_id", "resolution_events", ["position_id"])


def downgrade() -> None:
    op.drop_index("ix_resolution_events_position_id", table_name="resolution_events")
    op.drop_column("resolution_events", "position_id")
    op.drop_index("ix_chain_daily_summary_inst_asof", table_name="chain_daily_summary")
    op.drop_table("chain_daily_summary")
