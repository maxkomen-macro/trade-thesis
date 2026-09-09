"""option_positions, option_snapshots, chain_snapshots (Phase 6 position tracking)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per (instrument, record date, contract) inside the storage band (120 days, +-25% of spot, widened to
    # the idea's target and stop). Written by every selector run and by the daily job for instruments with an open
    # position. Feeds daily marks and the 1-year IV percentile.
    op.create_table(
        "chain_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("contract", sa.String(48), nullable=False),
        sa.Column("expiry", sa.Date, nullable=False),
        sa.Column("right", sa.String(4), nullable=False),
        sa.Column("strike", sa.Float, nullable=False),
        sa.Column("bid", sa.Float, nullable=True),
        sa.Column("ask", sa.Float, nullable=True),
        sa.Column("mid", sa.Float, nullable=True),
        sa.Column("last", sa.Float, nullable=True),
        sa.Column("iv", sa.Float, nullable=True),
        sa.Column("delta", sa.Float, nullable=True),
        sa.Column("theta", sa.Float, nullable=True),
        sa.Column("oi", sa.Integer, nullable=True),
        sa.Column("volume", sa.Integer, nullable=True),
        sa.Column("spot", sa.Float, nullable=True),
        sa.Column("quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="contracts"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("instrument_id", "as_of", "contract", name="uq_chain_snapshots_inst_asof_contract"),
    )
    op.create_index("ix_chain_snapshots_inst_asof", "chain_snapshots", ["instrument_id", "as_of"])

    op.create_table(
        "option_positions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("idea_id", sa.Integer, sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "analysis_id", sa.Integer, sa.ForeignKey("option_analyses.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("structure", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("legs_json", JSONB, nullable=False),
        sa.Column("expiry", sa.Date, nullable=False),
        sa.Column("contracts", sa.Integer, nullable=False),
        sa.Column("entry_debit", sa.Float, nullable=False),
        sa.Column("entry_cost", sa.Float, nullable=False),
        sa.Column("entry_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_source", sa.String(16), nullable=False, server_default="chain_mid"),
        sa.Column("entry_spot", sa.Float, nullable=True),
        sa.Column("take_profit_pct", sa.Float, nullable=False),
        sa.Column("stop_loss_pct", sa.Float, nullable=False),
        sa.Column("time_stop_days_before_expiry", sa.Integer, nullable=False),
        sa.Column("time_stop_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("exit_reason", sa.String(64), nullable=True),
        sa.Column("exit_value", sa.Float, nullable=True),
        sa.Column("exit_as_of", sa.Date, nullable=True),
        sa.Column("exit_source", sa.String(24), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pnl_pct", sa.Float, nullable=True),
        sa.Column("pnl_abs", sa.Float, nullable=True),
        sa.Column("last_value", sa.Float, nullable=True),
        sa.Column("last_value_as_of", sa.Date, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_option_positions_idea_id", "option_positions", ["idea_id"])
    op.create_index("ix_option_positions_status", "option_positions", ["status"])

    op.create_table(
        "option_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "position_id", sa.Integer, sa.ForeignKey("option_positions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("bid", sa.Float, nullable=True),
        sa.Column("ask", sa.Float, nullable=True),
        sa.Column("pnl_pct", sa.Float, nullable=False),
        sa.Column("pnl_abs", sa.Float, nullable=False),
        sa.Column("spot", sa.Float, nullable=True),
        sa.Column("iv", sa.Float, nullable=True),
        sa.Column("delta", sa.Float, nullable=True),
        sa.Column("theta", sa.Float, nullable=True),
        sa.Column("legs_json", JSONB, nullable=False),
        sa.Column("source", sa.String(24), nullable=False, server_default="chain_mid"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("position_id", "as_of", name="uq_option_snapshots_position_asof"),
    )
    op.create_index("ix_option_snapshots_position_id", "option_snapshots", ["position_id"])


def downgrade() -> None:
    op.drop_index("ix_option_snapshots_position_id", table_name="option_snapshots")
    op.drop_table("option_snapshots")
    op.drop_index("ix_option_positions_status", table_name="option_positions")
    op.drop_index("ix_option_positions_idea_id", table_name="option_positions")
    op.drop_table("option_positions")
    op.drop_index("ix_chain_snapshots_inst_asof", table_name="chain_snapshots")
    op.drop_table("chain_snapshots")
