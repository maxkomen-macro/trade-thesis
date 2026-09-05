"""ideas.spread_at_window_end_pct for relative ideas

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ideas", sa.Column("spread_at_window_end_pct", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("ideas", "spread_at_window_end_pct")
