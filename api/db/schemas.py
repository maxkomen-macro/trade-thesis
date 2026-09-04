"""Pydantic v2 response/request schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


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
