"""ledger core: instruments, ideas, price_snapshots, resolution_events

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="etf"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_instruments_symbol", "instruments", ["symbol"], unique=True)

    op.create_table(
        "ideas",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("thesis_text", sa.Text, nullable=False),
        sa.Column("parsed_json", JSONB, nullable=True),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("benchmark_symbol", sa.String(32), nullable=True),
        sa.Column("success_rule_json", JSONB, nullable=False),
        sa.Column("invalidation_rule_json", JSONB, nullable=True),
        sa.Column("invalidation_is_note_only", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("window_start", sa.Date, nullable=False),
        sa.Column("window_end", sa.Date, nullable=False),
        sa.Column("catalyst_date", sa.Date, nullable=True),
        sa.Column("catalyst_note", sa.Text, nullable=True),
        sa.Column("conviction_pct", sa.Float, nullable=True),
        sa.Column("capital_assigned", sa.Float, nullable=False),
        sa.Column("idea_type", sa.String(8), nullable=False, server_default="paper"),
        sa.Column("entry_price", sa.Float, nullable=True),
        sa.Column("entry_price_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("radar_regime", sa.String(64), nullable=True),
        sa.Column("radar_probs_json", JSONB, nullable=True),
        sa.Column("tags", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("basket_symbols", ARRAY(sa.Text()), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reason", sa.String(64), nullable=True),
        sa.Column("hypothetical_pnl_pct", sa.Float, nullable=True),
        sa.Column("hypothetical_pnl_abs", sa.Float, nullable=True),
        sa.Column("last_price", sa.Float, nullable=True),
        sa.Column("last_price_as_of", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ideas_instrument_id", "ideas", ["instrument_id"])
    op.create_index("ix_ideas_status", "ideas", ["status"])
    op.create_index("ix_ideas_window_end", "ideas", ["window_end"])

    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer, sa.ForeignKey("instruments.id"), nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("open", sa.Float, nullable=True),
        sa.Column("high", sa.Float, nullable=True),
        sa.Column("low", sa.Float, nullable=True),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("adj_close", sa.Float, nullable=True),
        sa.Column("volume", sa.BigInteger, nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="eod"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("instrument_id", "as_of", "source", name="uq_price_snapshots_inst_asof_src"),
    )
    op.create_index("ix_price_snapshots_instrument_id", "price_snapshots", ["instrument_id"])

    op.create_table(
        "resolution_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("idea_id", sa.Integer, sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("benchmark_price", sa.Float, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("occurred_on", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_resolution_events_idea_id", "resolution_events", ["idea_id"])


def downgrade() -> None:
    op.drop_table("resolution_events")
    op.drop_table("price_snapshots")
    op.drop_table("ideas")
    op.drop_table("instruments")
