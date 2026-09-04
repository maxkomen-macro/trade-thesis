"""Regime store. Macro Regime Radar's backend is localhost-only, so Trade Thesis never calls it at runtime.
Radar pushes its daily regime to POST /api/jobs/regime (see docs/regime-push.md); this module stores and reads
those rows. Radar's own route, for the pushing action, is GET /api/regime/latest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import RegimeSnapshot
from api.db.schemas import RegimePush, RegimeReadout

PROB_KEYS = ("prob_goldilocks", "prob_overheating", "prob_stagflation", "prob_recession")
EXTRA_KEYS = ("confidence", "growth_trend", "inflation_trend")


def store_regime(db: Session, push: RegimePush) -> RegimeSnapshot:
    """Upsert on (as_of, source). Re-pushing the same day replaces the row rather than duplicating it."""
    probs: dict[str, Any] = {k: getattr(push, k) for k in PROB_KEYS + EXTRA_KEYS}
    row = db.execute(
        select(RegimeSnapshot).where(RegimeSnapshot.as_of == push.date, RegimeSnapshot.source == push.source)
    ).scalar_one_or_none()
    if row is None:
        row = RegimeSnapshot(as_of=push.date, regime=push.label, probs_json=probs, source=push.source)
        db.add(row)
    else:
        row.regime = push.label
        row.probs_json = probs
    db.commit()
    db.refresh(row)
    return row


def latest_regime(db: Session | None) -> RegimeSnapshot | None:
    if db is None:
        return None
    return db.execute(
        select(RegimeSnapshot).order_by(RegimeSnapshot.as_of.desc(), RegimeSnapshot.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def readout(row: RegimeSnapshot | None) -> RegimeReadout:
    if row is None:
        return RegimeReadout(available=False)
    probs = row.probs_json or {}
    return RegimeReadout(
        available=True,
        as_of=row.as_of,
        regime=row.regime,
        source=row.source,
        stored_at=row.created_at,
        age_days=(datetime.now(UTC).date() - row.as_of).days,
        **{k: probs.get(k) for k in PROB_KEYS + EXTRA_KEYS},
    )


def regime_stamp(db: Session | None) -> tuple[str | None, dict[str, Any] | None]:
    """What an idea stores at creation: (radar_regime, radar_probs_json). Nulls when nothing has been pushed."""
    row = latest_regime(db)
    if row is None:
        return None, None
    return row.regime, {**(row.probs_json or {}), "as_of": row.as_of.isoformat(), "source": row.source}
