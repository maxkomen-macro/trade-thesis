"""baseline: settings table + seeds

Revision ID: 0001
Revises:
Create Date: 2026-09-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Frozen copy of api.db.models.SETTINGS_SEED at the time of this migration.
SEED = {
    "default_capital": 1000,
    "default_risk_pct": 2.0,
    "public_hide_dollars": True,
    "options_enabled": False,  # EODHD options probe returned 403 on 2026-09-04 (docs/eodhd-probe.md)
    "default_take_profit_pct": 100,
    "default_stop_loss_pct": 50,
    "default_time_stop_days_before_expiry": 5,
}


def upgrade() -> None:
    settings = op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value_json", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(settings, [{"key": k, "value_json": v} for k, v in SEED.items()])


def downgrade() -> None:
    op.drop_table("settings")
