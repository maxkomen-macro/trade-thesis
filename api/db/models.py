"""ORM models. Phase 1: `settings`, `regime_snapshots`. Phase 2: `instruments`, `ideas`, `price_snapshots`,
`resolution_events`. Phase 5: `option_analyses`. Phase 6: `option_positions`, `option_snapshots`,
`chain_snapshots`."""

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
    Index,
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
EVENT_TYPES = (
    "progress",
    "target_hit",
    "stop_hit",
    "expired",
    "manual_close",
    "benchmark_update",
    "position_opened",
    "position_closed",
)
POSITION_STATUSES = ("open", "closed")
# Exit reasons in the order the position resolver applies them (api/services/positions.py).
EXIT_REASONS = ("idea_resolved", "stop_loss", "take_profit", "time_stop", "expiry", "manual")


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
    analyses: Mapped[list[OptionAnalysis]] = relationship(
        back_populates="idea", cascade="all, delete-orphan", order_by="OptionAnalysis.created_at"
    )
    positions: Mapped[list[OptionPosition]] = relationship(
        back_populates="idea", cascade="all, delete-orphan", order_by="OptionPosition.created_at"
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
    # Set on position_opened / position_closed so the events go with the position when it is deleted.
    position_id: Mapped[int | None] = mapped_column(
        ForeignKey("option_positions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    idea: Mapped[Idea] = relationship(back_populates="events")


class OptionAnalysis(Base):
    """One run of the options expression selector for an idea (Phase 5). Every number the Expression page shows comes
    from this row: `candidates_json` carries each candidate's legs with the chain quotes they were built from
    (bid/ask/mid/IV/OI/volume and their timestamps), the metrics, the scenario grid for the top three, and the
    rationale strings; `spot` / `spot_as_of` trace to a price_snapshots row; `chain_as_of` is the chain quote time."""

    __tablename__ = "option_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False, index=True)
    chain_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chain_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    spot: Mapped[float] = mapped_column(Float, nullable=False)
    spot_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spot_source: Mapped[str] = mapped_column(String(16), nullable=False, default="realtime")
    iv_percentile_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_rv_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_vol_20d: Mapped[float | None] = mapped_column(Float, nullable=True)  # percent, annualized
    rate_pct: Mapped[float] = mapped_column(Float, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)  # trade | no_trade
    verdict_text: Mapped[str] = mapped_column(Text, nullable=False)
    candidates_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    shares_comparison_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    params_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    idea: Mapped[Idea] = relationship(back_populates="analyses")


class ChainSnapshot(Base):
    """One option contract's quote on one record date (Phase 6). Rows come from EODHD's UnicornBay `/contracts`
    endpoint inside the storage band (120 days, +-25% of spot, widened to the idea's target and stop); the record
    date is the ET date of the quote timestamps (`bid_date` / `ask_date`), which lag the UTC clock by one calendar
    day after the close. `spot` is the underlying quote captured with the chain, used for the at-the-money IV series
    behind the 1-year IV percentile. Daily marks read leg quotes from here, never from a model."""

    __tablename__ = "chain_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of", "contract", name="uq_chain_snapshots_inst_asof_contract"),
        Index("ix_chain_snapshots_inst_asof", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    contract: Mapped[str] = mapped_column(String(48), nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    right: Mapped[str] = mapped_column(String(4), nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="contracts")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ChainDailySummary(Base):
    """One row per (instrument, record date), written whenever a band is stored and kept indefinitely. Band rows in
    chain_snapshots older than CHAIN_RETENTION_DAYS are rolled down to this row (spot, at-the-money IV, 20-day
    realized vol, row count) and deleted by the daily cron. The 1-year IV percentile reads `atm_iv` from here."""

    __tablename__ = "chain_daily_summary"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of", name="uq_chain_daily_summary_inst_asof"),
        Index("ix_chain_daily_summary_inst_asof", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    atm_iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_vol_20d: Mapped[float | None] = mapped_column(Float, nullable=True)  # percent, annualized
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rolled_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OptionPosition(Base):
    """An expression taken on an idea (Phase 6): the structure's legs as quoted at entry (`legs_json` keeps each
    leg's contract, side, strike, right, expiry and the entry bid/ask/mid), the contract count, the entry debit per
    share (structure mid from a fresh chain quote, or the owner's stated fill), and the exit rules. Marked daily
    from chain_snapshots; closed by the position resolver in the order idea resolution -> stop loss -> take profit
    -> time stop -> expiry, or manually. `pnl_pct` is the return on premium; `pnl_abs` = contracts x 100 x
    (value - entry_debit)."""

    __tablename__ = "option_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False, index=True)
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("option_analyses.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    structure: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    legs_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    contracts: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_debit: Mapped[float] = mapped_column(Float, nullable=False)  # per share
    entry_cost: Mapped[float] = mapped_column(Float, nullable=False)  # dollars: debit x 100 x contracts
    entry_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_source: Mapped[str] = mapped_column(String(16), nullable=False, default="chain_mid")  # chain_mid | fill
    entry_spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_pct: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss_pct: Mapped[float] = mapped_column(Float, nullable=False)
    time_stop_days_before_expiry: Mapped[int] = mapped_column(Integer, nullable=False)
    time_stop_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # per share
    exit_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_abs: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_value_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    idea: Mapped[Idea] = relationship(back_populates="positions")
    snapshots: Mapped[list[OptionSnapshot]] = relationship(
        back_populates="position", cascade="all, delete-orphan", order_by="OptionSnapshot.as_of"
    )
    events: Mapped[list[ResolutionEvent]] = relationship(cascade="all, delete-orphan")


class OptionSnapshot(Base):
    """One daily mark of a position (Phase 6): the structure's value per share from the chain (long legs at mid,
    short legs at mid), the liquidation bid/ask, P&L on premium and in dollars, the underlying close, and the leg
    quotes the mark was built from (`legs_json`). `source` is `chain_mid`, or `expiry_intrinsic` when the contracts
    have expired and the settlement is the payoff at the underlying's close on the expiry date."""

    __tablename__ = "option_snapshots"
    __table_args__ = (UniqueConstraint("position_id", "as_of", name="uq_option_snapshots_position_asof"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("option_positions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_abs: Mapped[float] = mapped_column(Float, nullable=False)
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    legs_json: Mapped[Any] = mapped_column(JSONType, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="chain_mid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    position: Mapped[OptionPosition] = relationship(back_populates="snapshots")


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
    # Model assumption for Black-Scholes, in percent (not market data). 4.0 reprices UnicornBay's own theoretical
    # values within 1% on 2026-09-05 (docs/options-selector.md); set it to the current 3-month bill yield.
    "risk_free_rate_pct": 4.0,
    # Account size in dollars for the risk budget (account_size x default_risk_pct); owner-set, hidden from the public.
    "account_size": 2342,
}
