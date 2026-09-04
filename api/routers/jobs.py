"""Protected job endpoints. Callers send `Authorization: Bearer <CRON_SECRET>` (TT_WRITE_TOKEN also accepted).
Phase 1: /regime (Radar's daily push). Phase 2 adds /resolve (Vercel Cron) and /refresh-prices."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.auth import CronAuth
from api.db.schemas import RegimePush, RegimeReadout
from api.db.session import get_db
from api.services.radar import readout, store_regime

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=[CronAuth])


@router.post("/regime", response_model=RegimeReadout)
def push_regime(push: RegimePush, db: Session = Depends(get_db)) -> RegimeReadout:
    """Store today's regime as pushed by Radar's GitHub Action. Idempotent per (date, source)."""
    return readout(store_regime(db, push))
