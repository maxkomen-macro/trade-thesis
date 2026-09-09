"""Option positions (Phase 6): public read of positions and their daily marks; token-protected take / edit /
close / mark / delete. Feature-flagged on settings.options_enabled like the selector."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.auth import WriteAuth
from api.db.models import OptionPosition
from api.db.schemas import PositionClose, PositionOut, PositionTake, PositionUpdate
from api.db.session import get_db
from api.routers.ideas import _eodhd_http, _load, hide_dollars_for
from api.routers.settings import read_settings
from api.services import ledger, options, positions
from api.services.eodhd import EODHDClient, EODHDError

router = APIRouter(prefix="/api", tags=["positions"])


def _load_position(db: Session, position_id: int) -> OptionPosition:
    pos = positions.load_position(db, position_id)
    if pos is None:
        raise HTTPException(404, "Position not found")
    return pos


@router.get("/ideas/{idea_id}/positions", response_model=list[PositionOut])
def list_positions(idea_id: int, request: Request, db: Session = Depends(get_db)) -> list[PositionOut]:
    idea = _load(db, idea_id)
    hide = hide_dollars_for(request)
    return [positions.to_out(positions.load_position(db, p.id), hide) for p in idea.positions]  # type: ignore[arg-type]


@router.post("/ideas/{idea_id}/positions", response_model=PositionOut, status_code=201, dependencies=[WriteAuth])
def take_expression(idea_id: int, body: PositionTake, db: Session = Depends(get_db)) -> PositionOut:
    """Take this expression: quotes every leg of the chosen candidate afresh, opens the position at the structure
    mid (or the stated fill) with the given exit rules, and writes the first mark. Nothing is stored on an EODHD
    failure."""
    values = read_settings(db)
    if not values.get("options_enabled"):
        raise HTTPException(409, "Options selector is disabled (settings.options_enabled)")
    idea = _load(db, idea_id)
    if idea.status != "open":
        raise HTTPException(409, f"Idea is {idea.status}; only open ideas can be expressed")
    if idea.window_end < ledger.today_utc():
        raise HTTPException(422, f"the idea's window ended on {idea.window_end}")
    if positions.open_positions(db, idea.id):
        raise HTTPException(409, "This idea already has an open position; close it before taking another")
    analysis = options.latest_analysis(db, idea.id)
    if analysis is None:
        raise HTTPException(404, "No options analysis stored for this idea; run the selector first")
    cand = positions.candidate_from_analysis(analysis, body.candidate_name, body.rank)
    if cand is None:
        raise HTTPException(422, "Candidate not found in the latest analysis")
    try:
        pos = positions.open_position(db, idea, analysis, cand, body, values, EODHDClient())
    except EODHDError as exc:
        db.rollback()
        raise _eodhd_http(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return positions.to_out(_load_position(db, pos.id), hide_dollars=False)


@router.get("/positions/{position_id}", response_model=PositionOut)
def get_position(position_id: int, request: Request, db: Session = Depends(get_db)) -> PositionOut:
    return positions.to_out(_load_position(db, position_id), hide_dollars_for(request))


@router.patch("/positions/{position_id}", response_model=PositionOut, dependencies=[WriteAuth])
def update_position(position_id: int, body: PositionUpdate, db: Session = Depends(get_db)) -> PositionOut:
    """Edit the exit rules of an open position; the rules are re-applied to the stored marks at once."""
    pos = _load_position(db, position_id)
    if pos.status != "open":
        raise HTTPException(409, "Position is closed")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "No changes provided")
    for k, v in data.items():
        setattr(pos, k, v)
    from datetime import timedelta

    pos.time_stop_date = pos.expiry - timedelta(days=pos.time_stop_days_before_expiry)
    db.commit()
    positions.apply_exit_rules(db, _load_position(db, position_id))
    return positions.to_out(_load_position(db, position_id), hide_dollars=False)


@router.post("/positions/{position_id}/close", response_model=PositionOut, dependencies=[WriteAuth])
def close_position(position_id: int, body: PositionClose, db: Session = Depends(get_db)) -> PositionOut:
    pos = _load_position(db, position_id)
    if pos.status != "open":
        raise HTTPException(409, "Position is already closed")
    try:
        positions.close_manually(db, pos, body.exit_price, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return positions.to_out(_load_position(db, position_id), hide_dollars=False)


@router.post("/positions/{position_id}/mark", dependencies=[WriteAuth])
def mark_position(position_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Pull today's chain, write the mark, apply the exit rules. Returns the position and the job summary
    (errors included, never hidden)."""
    pos = _load_position(db, position_id)
    summary = positions.mark_positions(db, EODHDClient(), idea_id=pos.idea_id)
    return {"position": positions.to_out(_load_position(db, position_id), False).model_dump(), "summary": summary}


@router.delete("/positions/{position_id}", status_code=204, dependencies=[WriteAuth])
def delete_position(position_id: int, db: Session = Depends(get_db)) -> None:
    pos = _load_position(db, position_id)
    db.delete(pos)
    db.commit()
