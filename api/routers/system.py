"""Health, connection status, and the regime readout used by the top bar."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import is_writer
from api.config import APP_VERSION, settings
from api.db.models import SETTINGS_SEED, Setting
from api.db.schemas import Health, RegimeReadout, ServiceStatus, SystemStatus
from api.db.session import get_db_optional, get_engine, get_sessionmaker, ping_db
from api.services.eodhd import EODHDClient, EODHDError
from api.services.radar import latest_regime, readout

router = APIRouter(prefix="/api", tags=["system"])

REGIME_STALE_AFTER_DAYS = 4  # Radar pushes on trading days; a long weekend is 3 days


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_settings() -> tuple[dict[str, Any], str]:
    """DB settings with seed fallback. Returns (values, source) so the UI can label unsaved defaults."""
    values = dict(SETTINGS_SEED)
    if get_engine() is None:
        return values, "seed (no database)"
    try:
        with get_sessionmaker()() as db:
            rows = db.execute(select(Setting)).scalars().all()
        for row in rows:
            values[row.key] = row.value_json
        return values, "db"
    except Exception as exc:  # reported, not hidden
        return values, f"seed (db error: {type(exc).__name__})"


@router.get("/health", response_model=Health)
def health() -> Health:
    ok, detail = ping_db()
    return Health(status="ok", version=APP_VERSION, env=settings.app_env, db="ok" if ok else detail)


@router.get("/status", response_model=SystemStatus)
def status(request: Request, db: Session | None = Depends(get_db_optional)) -> SystemStatus:
    db_ok, db_detail = ping_db()

    try:
        u = EODHDClient().user()
        plan = u.get("subscriptionType", "?")
        used, limit = u.get("apiRequests", "?"), u.get("dailyRateLimit", "?")
        eod_ok, eod_detail = True, f"{plan} plan, {used}/{limit} calls today"
    except EODHDError as exc:
        eod_ok, eod_detail = False, str(exc)

    regime = readout(latest_regime(db)) if db_ok else RegimeReadout(available=False)
    if not db_ok:
        regime_ok, regime_detail = False, "no database"
    elif not regime.available:
        regime_ok, regime_detail = False, "no regime pushed yet (Radar → POST /api/jobs/regime)"
    else:
        regime_ok = (regime.age_days or 0) <= REGIME_STALE_AFTER_DAYS
        regime_detail = f"{regime.regime} as of {regime.as_of} ({regime.age_days}d old, source {regime.source})"

    values, source = load_settings()
    if bool(values.get("public_hide_dollars", settings.public_hide_dollars)) and not is_writer(request):
        values = {**values, "account_size": None}  # dollar-valued setting, owner-only
    return SystemStatus(
        db=ServiceStatus(name="Neon Postgres", ok=db_ok, detail=db_detail, checked_at=_now()),
        eodhd=ServiceStatus(name="EODHD", ok=eod_ok, detail=eod_detail, checked_at=_now()),
        radar=ServiceStatus(name="Radar regime feed", ok=regime_ok, detail=regime_detail, checked_at=_now()),
        options_enabled=bool(values.get("options_enabled", False)),
        public_hide_dollars=bool(values.get("public_hide_dollars", settings.public_hide_dollars)),
        settings={**values, "_source": source},
    )


@router.get("/regime", response_model=RegimeReadout)
def regime(db: Session | None = Depends(get_db_optional)) -> RegimeReadout:
    """Latest regime pushed by Radar. Never calls Radar; returns available=false when nothing is stored."""
    return readout(latest_regime(db))
