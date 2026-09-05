"""Instruments and EODHD symbol search. Search is write-protected because it spends the EODHD quota."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import WriteAuth
from api.db.models import Instrument
from api.db.schemas import InstrumentCreate, InstrumentOut, SymbolHit
from api.db.session import get_db
from api.services.eodhd import EODHDClient, EODHDError, eodhd_symbol

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


@router.get("", response_model=list[InstrumentOut])
def list_instruments(db: Session = Depends(get_db)) -> list[InstrumentOut]:
    rows = db.execute(select(Instrument).order_by(Instrument.symbol)).scalars().all()
    return [InstrumentOut.model_validate(r) for r in rows]


@router.post("", response_model=InstrumentOut, status_code=201, dependencies=[WriteAuth])
def create_instrument(body: InstrumentCreate, db: Session = Depends(get_db)) -> InstrumentOut:
    existing = db.execute(select(Instrument).where(Instrument.symbol == body.symbol)).scalar_one_or_none()
    if existing:
        return InstrumentOut.model_validate(existing)
    row = Instrument(symbol=body.symbol, display_name=body.display_name, kind=body.kind)
    db.add(row)
    db.commit()
    db.refresh(row)
    return InstrumentOut.model_validate(row)


@router.get("/search", response_model=list[SymbolHit], dependencies=[WriteAuth])
def search_symbols(q: str = Query(min_length=1), limit: int = Query(default=8, le=20)) -> list[SymbolHit]:
    try:
        hits = EODHDClient().search(q, limit=limit)
    except EODHDError as exc:
        raise HTTPException(502, detail={"message": str(exc), **exc.to_dict()}) from exc
    out = []
    for h in hits:
        try:
            out.append(
                SymbolHit(
                    symbol=eodhd_symbol(str(h["Code"]), str(h["Exchange"])),
                    name=str(h.get("Name", "")),
                    type=str(h.get("Type", "")),
                    exchange=str(h.get("Exchange", "")),
                    currency=h.get("Currency"),
                    previous_close=float(h["previousClose"]) if h.get("previousClose") not in (None, "NA") else None,
                    previous_close_date=h.get("previousCloseDate"),
                )
            )
        except (KeyError, ValueError):
            continue
    return out
