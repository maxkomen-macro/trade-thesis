"""Settings: public read of the non-secret defaults, token-protected update."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import WriteAuth
from api.db.models import SETTINGS_SEED, Setting
from api.db.session import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    default_capital: float | None = Field(default=None, gt=0, le=10_000_000)
    default_risk_pct: float | None = Field(default=None, gt=0, le=100)
    public_hide_dollars: bool | None = None
    options_enabled: bool | None = None
    default_take_profit_pct: float | None = Field(default=None, gt=0, le=10_000)
    default_stop_loss_pct: float | None = Field(default=None, gt=0, le=100)
    default_time_stop_days_before_expiry: int | None = Field(default=None, ge=0, le=365)


def read_settings(db: Session) -> dict[str, Any]:
    values = dict(SETTINGS_SEED)
    for row in db.execute(select(Setting)).scalars():
        values[row.key] = row.value_json
    return values


@router.get("")
def get_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return read_settings(db)


@router.patch("", dependencies=[WriteAuth])
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(422, "No settings provided")
    for key, value in changes.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value_json=value))
        else:
            row.value_json = value
    db.commit()
    return read_settings(db)
