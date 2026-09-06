"""Options expression selector endpoints (Phase 5). Public read of the latest stored run; token-protected run.
Feature-flagged on settings.options_enabled (flipped only after the EODHD options probe returned live chain data)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.auth import WriteAuth
from api.db.schemas import OptionAnalysisOut, OptionsRunRequest
from api.db.session import get_db
from api.routers.ideas import _eodhd_http, _load, hide_dollars_for
from api.routers.settings import read_settings
from api.services import options
from api.services.eodhd import EODHDClient, EODHDError

router = APIRouter(prefix="/api", tags=["options"])


@router.get("/ideas/{idea_id}/options", response_model=OptionAnalysisOut)
def latest_options_analysis(idea_id: int, request: Request, db: Session = Depends(get_db)) -> OptionAnalysisOut:
    """The most recent stored selector run for the idea. 404 until one has been run."""
    _load(db, idea_id)
    row = options.latest_analysis(db, idea_id)
    if row is None:
        raise HTTPException(404, "No options analysis stored for this idea yet")
    return options.to_analysis_out(row, hide_dollars_for(request))


@router.post("/ideas/{idea_id}/options", response_model=OptionAnalysisOut, status_code=201, dependencies=[WriteAuth])
def run_options_analysis(
    idea_id: int, body: OptionsRunRequest | None = None, db: Session = Depends(get_db)
) -> OptionAnalysisOut:
    """Pull spot and the live chain from EODHD, score every candidate, store one option_analyses row.
    Any EODHD failure is a 502 and nothing is stored."""
    values = read_settings(db)
    if not values.get("options_enabled"):
        raise HTTPException(409, "Options selector is disabled (settings.options_enabled)")
    idea = _load(db, idea_id)
    try:
        row = options.analyze_idea(db, idea, values, EODHDClient(), body or OptionsRunRequest())
    except EODHDError as exc:
        db.rollback()
        raise _eodhd_http(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return options.to_analysis_out(row, hide_dollars=False)
