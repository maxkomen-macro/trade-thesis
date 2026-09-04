"""regime_snapshots: daily regime pushed by Radar

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regime_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("regime", sa.String(64), nullable=False),
        sa.Column("probs_json", JSONB, nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="radar"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("as_of", "source", name="uq_regime_snapshots_as_of_source"),
    )
    op.create_index("ix_regime_snapshots_as_of", "regime_snapshots", ["as_of"])


def downgrade() -> None:
    op.drop_index("ix_regime_snapshots_as_of", table_name="regime_snapshots")
    op.drop_table("regime_snapshots")
