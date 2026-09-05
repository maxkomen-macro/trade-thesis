"""Pydantic v2 response/request schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.rules import validate_invalidation, validate_rule


class Health(BaseModel):
    status: str
    version: str
    env: str
    db: str


class ServiceStatus(BaseModel):
    name: str
    ok: bool
    detail: str
    checked_at: str


class SystemStatus(BaseModel):
    db: ServiceStatus
    eodhd: ServiceStatus
    radar: ServiceStatus
    options_enabled: bool
    public_hide_dollars: bool
    settings: dict[str, Any]


class RegimePush(BaseModel):
    """Payload Radar's GitHub Action posts to POST /api/jobs/regime. Field names mirror Radar's `Regime` model
    (GET /api/regime/latest on Radar) so the action can forward that JSON unchanged, plus an optional `source`."""

    date: date
    label: str
    confidence: float | None = None
    growth_trend: float | None = None
    inflation_trend: float | None = None
    prob_goldilocks: float | None = None
    prob_overheating: float | None = None
    prob_stagflation: float | None = None
    prob_recession: float | None = None
    source: str = "radar"


class RegimeReadout(BaseModel):
    """Latest stored regime snapshot. `available` is false when nothing has been pushed yet."""

    available: bool
    as_of: date | None = None
    regime: str | None = None
    confidence: float | None = None
    growth_trend: float | None = None
    inflation_trend: float | None = None
    prob_goldilocks: float | None = None
    prob_overheating: float | None = None
    prob_stagflation: float | None = None
    prob_recession: float | None = None
    source: str | None = None
    stored_at: datetime | None = None
    age_days: int | None = None


# --- ledger ------------------------------------------------------------------------------------------------------

Direction = Literal["up", "down", "outperform", "underperform", "range"]
IdeaType = Literal["real", "paper"]
InstrumentKind = Literal["stock", "etf", "sector_proxy", "commodity_etf", "fx_etf", "index"]


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    symbol: str
    display_name: str
    kind: str
    created_at: datetime


class InstrumentCreate(BaseModel):
    symbol: str = Field(min_length=3, max_length=32, pattern=r"^[A-Z0-9.\-]+\.[A-Z]+$")
    display_name: str = Field(min_length=1, max_length=200)
    kind: InstrumentKind = "etf"


class SymbolHit(BaseModel):
    symbol: str
    name: str
    type: str
    exchange: str
    currency: str | None = None
    previous_close: float | None = None
    previous_close_date: str | None = None


class _IdeaFields(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    thesis_text: str = Field(min_length=1)
    parsed_json: dict[str, Any] | None = None
    direction: Direction
    benchmark_symbol: str | None = None
    success_rule_json: dict[str, Any]
    invalidation_rule_json: dict[str, Any] | None = None
    invalidation_is_note_only: bool = False
    window_start: date
    window_end: date
    catalyst_date: date | None = None
    catalyst_note: str | None = None
    conviction_pct: float | None = Field(default=None, ge=0, le=100)
    capital_assigned: float | None = Field(default=None, gt=0)
    idea_type: IdeaType = "paper"
    tags: list[str] = Field(default_factory=list)
    basket_symbols: list[str] | None = None
    entry_price: float | None = Field(default=None, gt=0)
    entry_price_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        self.success_rule_json = validate_rule(self.success_rule_json)
        if self.invalidation_rule_json is not None:
            self.invalidation_rule_json = validate_invalidation(self.invalidation_rule_json)
        if self.direction in ("outperform", "underperform") and not self.benchmark_symbol:
            raise ValueError("relative ideas need benchmark_symbol")
        self.tags = sorted({t.strip().lower() for t in self.tags if t.strip()})
        return self


class IdeaCreate(_IdeaFields):
    symbol: str = Field(min_length=3, max_length=32)
    display_name: str | None = None
    instrument_kind: InstrumentKind | None = None


class IdeaUpdate(BaseModel):
    """PATCH body: every field optional. Rules and windows are re-validated when present."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    thesis_text: str | None = None
    parsed_json: dict[str, Any] | None = None
    direction: Direction | None = None
    benchmark_symbol: str | None = None
    success_rule_json: dict[str, Any] | None = None
    invalidation_rule_json: dict[str, Any] | None = None
    clear_invalidation: bool = False
    invalidation_is_note_only: bool | None = None
    window_start: date | None = None
    window_end: date | None = None
    catalyst_date: date | None = None
    catalyst_note: str | None = None
    conviction_pct: float | None = Field(default=None, ge=0, le=100)
    capital_assigned: float | None = Field(default=None, gt=0)
    idea_type: IdeaType | None = None
    tags: list[str] | None = None
    basket_symbols: list[str] | None = None
    entry_price: float | None = Field(default=None, gt=0)
    entry_price_at: datetime | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.success_rule_json is not None:
            self.success_rule_json = validate_rule(self.success_rule_json)
        if self.invalidation_rule_json is not None:
            self.invalidation_rule_json = validate_invalidation(self.invalidation_rule_json)
        if self.tags is not None:
            self.tags = sorted({t.strip().lower() for t in self.tags if t.strip()})
        return self


class IdeaClose(BaseModel):
    note: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    price: float | None
    benchmark_price: float | None
    note: str | None
    occurred_on: date
    created_at: datetime


class BarOut(BaseModel):
    as_of: date
    open: float | None
    high: float | None
    low: float | None
    close: float


class IdeaOut(BaseModel):
    id: int
    instrument: InstrumentOut
    title: str
    thesis_text: str
    parsed_json: dict[str, Any] | None
    direction: str
    benchmark_symbol: str | None
    success_rule_json: dict[str, Any]
    invalidation_rule_json: dict[str, Any] | None
    invalidation_is_note_only: bool
    success_rule_text: str
    invalidation_rule_text: str | None
    target_level: float | None
    stop_level: float | None
    window_start: date
    window_end: date
    days_left: int | None
    catalyst_date: date | None
    catalyst_note: str | None
    conviction_pct: float | None
    capital_assigned: float | None  # null when dollars are hidden for this viewer
    idea_type: str
    entry_price: float | None
    entry_price_at: datetime | None
    radar_regime: str | None
    radar_probs_json: dict[str, Any] | None
    tags: list[str]
    basket_symbols: list[str] | None
    status: str
    resolved_at: datetime | None
    resolution_reason: str | None
    direction_right: bool | None
    hypothetical_pnl_pct: float | None
    hypothetical_pnl_abs: float | None  # null when dollars are hidden for this viewer
    spread_at_resolution_pct: float | None  # relative ideas: the spread that resolved it (path-dependent)
    spread_at_window_end_pct: float | None  # relative ideas: the spread at the window-end bar
    last_price: float | None
    last_price_as_of: date | None
    progress_pct: float
    progress_kind: str
    seed: bool
    dollars_hidden: bool
    created_at: datetime
    updated_at: datetime


class IdeaDetail(IdeaOut):
    events: list[EventOut]
    bars: list[BarOut]
    benchmark_bars: list[BarOut]


class RegimeBucket(BaseModel):
    regime: str
    ideas: int
    resolved: int
    direction_hit_rate: float | None
    target_hit_rate: float | None


class StatsOut(BaseModel):
    ideas_logged: int
    seed_count: int  # placeholder ideas excluded from every figure here
    open_count: int
    resolved_count: int
    direction_hit_rate: float | None
    target_hit_rate: float | None
    hypothetical_pnl_abs: float | None  # null when dollars are hidden
    hypothetical_pnl_pct_avg: float | None
    by_regime: list[RegimeBucket]
    resolving_soon: list[IdeaOut]
    dollars_hidden: bool


class JobSummary(BaseModel):
    job: str
    as_of: date
    ideas_checked: int = 0
    instruments_refreshed: int = 0
    bars_added: int = 0
    resolved: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ParseRequest(BaseModel):
    thesis_text: str = Field(min_length=10, max_length=8000)


class ParseResponse(BaseModel):
    """Everything the New Thesis page needs: proposed fields, open questions, and the deterministic context tile."""

    thesis_text: str
    title: str
    instrument: dict[str, Any] | None
    symbol: str | None
    symbol_candidates: list[dict[str, Any]]
    instrument_query: str
    direction: str
    benchmark_symbol: str | None
    benchmark_candidates: list[dict[str, Any]]
    success_rule_json: dict[str, Any] | None
    success_rule_text: str | None
    invalidation_rule_json: dict[str, Any] | None
    invalidation_rule_text: str | None
    invalidation_is_note_only: bool
    window_start: str
    window_end: str | None
    catalyst_date: str | None
    catalyst_note: str | None
    conviction_pct: float | None
    tags: list[str]
    questions: list[dict[str, str]]
    context: dict[str, Any] | None
    regime: dict[str, Any]
    parsed_json: dict[str, Any]
