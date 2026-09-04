"""ORM models. Phase 1 ships `settings` and `regime_snapshots`; Phase 2 adds instruments, ideas, price_snapshots,
resolution_events; Phases 5–6 add the option_* and chain_snapshots tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres (Neon); plain JSON on SQLite so the test suite can run on an in-memory database.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RegimeSnapshot(Base):
    """One row per (as_of, source). Radar pushes these via POST /api/jobs/regime; nothing calls Radar at runtime.
    `regime` is Radar's label (Goldilocks | Overheating | Stagflation | Recession Risk); `probs_json` holds the
    four probabilities (0–1) plus confidence/growth_trend/inflation_trend exactly as pushed."""

    __tablename__ = "regime_snapshots"
    __table_args__ = (UniqueConstraint("as_of", "source", name="uq_regime_snapshots_as_of_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    regime: Mapped[str] = mapped_column(String(64), nullable=False)
    probs_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="radar")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# Seed values for a fresh database. options_enabled is flipped in the DB (not here) once the EODHD options probe
# returns 200; it was set true on 2026-09-04 after the marketplace add-on went live (docs/eodhd-probe.md).
SETTINGS_SEED: dict[str, Any] = {
    "default_capital": 1000,
    "default_risk_pct": 2.0,
    "public_hide_dollars": True,
    "options_enabled": False,
    "default_take_profit_pct": 100,
    "default_stop_loss_pct": 50,
    "default_time_stop_days_before_expiry": 5,
}
