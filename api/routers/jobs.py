"""Protected job endpoints. Callers send `Authorization: Bearer <CRON_SECRET>` (TT_WRITE_TOKEN also accepted).
/regime: Radar's daily push. /resolve: the daily Vercel Cron (21:30 UTC). /refresh-prices: manual button."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import CronAuth
from api.db.schemas import JobSummary, RegimePush, RegimeReadout
from api.db.session import get_db
from api.services.ledger import refresh_prices, run_resolver
from api.services.radar import readout, store_regime

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[CronAuth])


@router.post("/regime", response_model=RegimeReadout)
def push_regime(push: RegimePush, db: Session = Depends(get_db)) -> RegimeReadout:
    """Store today's regime as pushed by Radar's GitHub Action. Idempotent per (date, source)."""
    return readout(store_regime(db, push))


@router.post("/resolve", response_model=JobSummary)
@router.get("/resolve", response_model=JobSummary)
def resolve_job(db: Session = Depends(get_db)) -> JobSummary:
    """Daily: refresh closes, check invalidation -> success -> expiry, write progress events.
    GET is accepted because Vercel Cron invokes the path with GET and `Authorization: Bearer <CRON_SECRET>`."""
    return run_resolver(db)


@router.post("/refresh-prices", response_model=JobSummary)
def refresh_prices_job(db: Session = Depends(get_db)) -> JobSummary:
    """Manual: pull the latest bars for every open idea's instrument (and benchmarks) and re-mark P&L."""
    return refresh_prices(db)
