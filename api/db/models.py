"""ORM models. Phase 1: `settings`, `regime_snapshots`. Phase 2: `instruments`, `ideas`, `price_snapshots`,
`resolution_events`. Phases 5–6 add the option_* and chain_snapshots tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on Postgres (Neon); plain JSON on SQLite so the test suite can run on an in-memory database.
JSONType = JSON().with_variant(JSONB(), "postgresql")
# text[] on Postgres, JSON list on SQLite.
TagsType = JSON().with_variant(ARRAY(Text()), "postgresql")

DIRECTIONS = ("up", "down", "outperform", "underperform", "range")
IDEA_TYPES = ("real", "paper")
STATUSES = ("open", "right", "wrong", "expired", "closed_manual")
INSTRUMENT_KINDS = ("stock", "etf", "sector_proxy", "commodity_etf", "fx_etf", "index")
EVENT_TYPES = ("progress", "target_hit", "stop_hit", "expired", "manual_close", "benchmark_update")


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


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)  # EODHD format: USO.US
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="etf")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    ideas: Mapped[list[Idea]] = relationship(back_populates="instrument")


class Idea(Base):
    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    thesis_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    success_rule_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    invalidation_rule_json: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    invalidation_is_note_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    catalyst_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    catalyst_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    conviction_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    capital_assigned: Mapped[float] = mapped_column(Float, nullable=False)
    idea_type: Mapped[str] = mapped_column(String(8), nullable=False, default="paper")
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    radar_regime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    radar_probs_json: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    tags: Mapped[list[str]] = mapped_column(TagsType, nullable=False, default=list)
    basket_symbols: Mapped[list[str] | None] = mapped_column(TagsType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hypothetical_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    hypothetical_pnl_abs: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Relative ideas: (instrument return - benchmark return) at the window-end bar, signed by direction, in %.
    # Resolution stays path-dependent; this is stored so the detail page can show both numbers.
    spread_at_window_end_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Latest mark used for the P&L above; both trace to a price_snapshots row.
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    instrument: Mapped[Instrument] = relationship(back_populates="ideas")
    events: Mapped[list[ResolutionEvent]] = relationship(
        back_populates="idea", cascade="all, delete-orphan", order_by="ResolutionEvent.occurred_on"
    )


class PriceSnapshot(Base):
    """One bar per (instrument, as_of, source). `source`: eod | intraday | realtime. Every price shown in the UI
    traces back to one of these rows via `fetched_at`."""

    __tablename__ = "price_snapshots"
    __table_args__ = (UniqueConstraint("instrument_id", "as_of", "source", name="uq_price_snapshots_inst_asof_src"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False, index=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="eod")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ResolutionEvent(Base):
    __tablename__ = "resolution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    idea: Mapped[Idea] = relationship(back_populates="events")


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
