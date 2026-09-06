"""option_analyses (Phase 5 selector runs) + risk_free_rate_pct setting

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "option_analyses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("idea_id", sa.Integer, sa.ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chain_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chain_trade_date", sa.Date, nullable=True),
        sa.Column("spot", sa.Float, nullable=False),
        sa.Column("spot_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot_source", sa.String(16), nullable=False, server_default="realtime"),
        sa.Column("iv_percentile_1y", sa.Float, nullable=True),
        sa.Column("iv_rv_ratio", sa.Float, nullable=True),
        sa.Column("realized_vol_20d", sa.Float, nullable=True),
        sa.Column("rate_pct", sa.Float, nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("verdict_text", sa.Text, nullable=False),
        sa.Column("candidates_json", JSONB, nullable=False),
        sa.Column("shares_comparison_json", JSONB, nullable=False),
        sa.Column("params_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_option_analyses_idea_id", "option_analyses", ["idea_id"])
    # Model assumption (percent), not market data; the owner can change it in Settings.
    op.execute(
        "INSERT INTO settings (key, value_json) VALUES ('risk_free_rate_pct', '4.0'::jsonb) "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE key = 'risk_free_rate_pct'")
    op.drop_index("ix_option_analyses_idea_id", table_name="option_analyses")
    op.drop_table("option_analyses")
