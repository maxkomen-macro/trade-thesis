"""account_size setting for the account-level risk budget

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Owner decision 2026-09-05: default_risk_pct applies to the account, not the idea. Seeded from the owner's
    # stated account size; editable in Settings, hidden from public viewers.
    op.execute(
        "INSERT INTO settings (key, value_json) VALUES ('account_size', '2342'::jsonb) ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE key = 'account_size'")
