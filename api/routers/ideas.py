"""Ideas: public read, token-protected write.
Dollar figures are masked for non-writers when public_hide_dollars is on."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.auth import WriteAuth, is_writer
from api.db.models import Idea, Instrument
from api.db.schemas import (
    IdeaClose,
    IdeaCreate,
    IdeaDetail,
    IdeaOut,
    IdeaUpdate,
    ParseRequest,
    ParseResponse,
    RegimeBucket,
    StatsOut,
)
from api.db.session import get_db
from api.routers.system import load_settings
from api.services import ledger, parser, prices
from api.services.eodhd import EODHDClient, EODHDError
from api.services.radar import regime_stamp

router = APIRouter(prefix="/api", tags=["ideas"])

RESOLVING_SOON_DAYS = 14


def hide_dollars_for(request: Request) -> bool:
    values, _ = load_settings()
    return bool(values.get("public_hide_dollars", True)) and not is_writer(request)


def _load(db: Session, idea_id: int) -> Idea:
    # populate_existing: the resolver adds events via the session, so a re-load must refresh the collection.
    idea = db.execute(
        select(Idea)
        .options(selectinload(Idea.instrument), selectinload(Idea.events), selectinload(Idea.positions))
        .where(Idea.id == idea_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if idea is None:
        raise HTTPException(404, "Idea not found")
    return idea


def _eodhd_http(exc: EODHDError) -> HTTPException:
    return HTTPException(502, detail={"message": str(exc), **exc.to_dict()})


@router.get("/ideas", response_model=list[IdeaOut])
def list_ideas(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    idea_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, le=500),
) -> list[IdeaOut]:
    stmt = (
        select(Idea)
        .options(selectinload(Idea.instrument), selectinload(Idea.positions))
        .order_by(Idea.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Idea.status == status)
    if idea_type:
        stmt = stmt.where(Idea.idea_type == idea_type)
    rows = db.execute(stmt).scalars().all()
    hide = hide_dollars_for(request)
    out = []
    for idea in rows:
        if tag and tag.lower() not in [t.lower() for t in (idea.tags or [])]:
            continue
        if q:
            hay = f"{idea.title} {idea.thesis_text} {idea.instrument.symbol} {idea.instrument.display_name}".lower()
            if q.lower() not in hay:
                continue
        out.append(ledger.to_idea_out(idea, hide))
    return out


@router.get("/stats", response_model=StatsOut)
def stats(request: Request, db: Session = Depends(get_db)) -> StatsOut:
    hide = hide_dollars_for(request)
    all_ideas = (
        db.execute(select(Idea).options(selectinload(Idea.instrument), selectinload(Idea.positions))).scalars().all()
    )
    # Placeholder (seed) ideas never move the numbers; they only appear in the table with a tag.
    ideas = [i for i in all_ideas if not ledger.is_seed(i)]
    today = ledger.today_utc()
    positions = [p for i in ideas for p in (i.positions or [])]
    closed = [(i, p) for i in ideas for p in (i.positions or []) if p.status == "closed"]
    beat = sum(
        1
        for i, p in closed
        if p.pnl_pct is not None and i.hypothetical_pnl_pct is not None and p.pnl_pct > i.hypothetical_pnl_pct
    )
    resolved = [i for i in ideas if i.status in ("right", "wrong", "expired")]
    dir_flags = [ledger.direction_right_of(i) for i in resolved]
    dir_known = [f for f in dir_flags if f is not None]
    pnl_abs = [i.hypothetical_pnl_abs for i in ideas if i.hypothetical_pnl_abs is not None]
    pnl_pct = [i.hypothetical_pnl_pct for i in ideas if i.hypothetical_pnl_pct is not None]

    buckets: dict[str, list[Idea]] = {}
    for i in ideas:
        buckets.setdefault(i.radar_regime or "Unknown", []).append(i)
    by_regime = []
    for name, group in sorted(buckets.items()):
        res = [i for i in group if i.status in ("right", "wrong", "expired")]
        flags = [f for f in (ledger.direction_right_of(i) for i in res) if f is not None]
        by_regime.append(
            RegimeBucket(
                regime=name,
                ideas=len(group),
                resolved=len(res),
                direction_hit_rate=round(sum(flags) / len(flags) * 100, 1) if flags else None,
                target_hit_rate=round(sum(1 for i in res if i.status == "right") / len(res) * 100, 1) if res else None,
            )
        )
    soon = sorted(
        (i for i in ideas if i.status == "open" and i.window_end <= today + timedelta(days=RESOLVING_SOON_DAYS)),
        key=lambda i: i.window_end,
    )
    return StatsOut(
        ideas_logged=len(ideas),
        seed_count=len(all_ideas) - len(ideas),
        open_count=sum(1 for i in ideas if i.status == "open"),
        resolved_count=len(resolved),
        direction_hit_rate=round(sum(dir_known) / len(dir_known) * 100, 1) if dir_known else None,
        target_hit_rate=round(sum(1 for i in resolved if i.status == "right") / len(resolved) * 100, 1)
        if resolved
        else None,
        hypothetical_pnl_abs=None if hide else (round(sum(pnl_abs), 2) if pnl_abs else None),
        hypothetical_pnl_pct_avg=round(sum(pnl_pct) / len(pnl_pct), 2) if pnl_pct else None,
        by_regime=by_regime,
        resolving_soon=[ledger.to_idea_out(i, hide, today) for i in soon[:10]],
        dollars_hidden=hide,
        positions_open=sum(1 for p in positions if p.status == "open"),
        positions_closed=len(closed),
        option_beat_thesis=beat,
    )


@router.post("/ideas/parse", response_model=ParseResponse, dependencies=[WriteAuth])
def parse_idea(body: ParseRequest, db: Session = Depends(get_db)) -> ParseResponse:
    """Prose -> thesis schema via Anthropic, symbols verified on EODHD, context numbers from stored snapshots."""
    try:
        return ParseResponse(**parser.parse_thesis(db, body.thesis_text))
    except RuntimeError as exc:  # missing key / empty structured output
        raise HTTPException(503, str(exc)) from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(502, f"Anthropic API unreachable: {exc}") from exc


@router.get("/ideas/{idea_id}", response_model=IdeaDetail)
def get_idea(idea_id: int, request: Request, db: Session = Depends(get_db)) -> IdeaDetail:
    return ledger.to_idea_detail(db, _load(db, idea_id), hide_dollars_for(request))


@router.post("/ideas", response_model=IdeaDetail, status_code=201, dependencies=[WriteAuth])
def create_idea(body: IdeaCreate, db: Session = Depends(get_db)) -> IdeaDetail:
    client = EODHDClient()
    values, _ = load_settings()
    try:
        inst = ledger.get_or_create_instrument(db, body.symbol, body.display_name, body.instrument_kind, client)
        if body.entry_price is not None:
            entry, entry_at = body.entry_price, body.entry_price_at or datetime.now(UTC)
        else:
            entry, entry_at, _src = prices.stamp_entry(db, inst, client)
    except EODHDError as exc:
        raise _eodhd_http(exc) from exc
    regime, probs = regime_stamp(db)
    idea = Idea(
        instrument_id=inst.id,
        title=body.title,
        thesis_text=body.thesis_text,
        parsed_json=body.parsed_json,
        direction=body.direction,
        benchmark_symbol=body.benchmark_symbol,
        success_rule_json=body.success_rule_json,
        invalidation_rule_json=body.invalidation_rule_json,
        invalidation_is_note_only=body.invalidation_is_note_only,
        window_start=body.window_start,
        window_end=body.window_end,
        catalyst_date=body.catalyst_date,
        catalyst_note=body.catalyst_note,
        conviction_pct=body.conviction_pct,
        capital_assigned=body.capital_assigned or float(values.get("default_capital", 1000)),
        idea_type=body.idea_type,
        entry_price=entry,
        entry_price_at=entry_at,
        radar_regime=regime,
        radar_probs_json=probs,
        tags=body.tags,
        basket_symbols=body.basket_symbols,
    )
    db.add(idea)
    db.commit()
    idea = _load(db, idea.id)
    try:
        ledger.resolve_idea(db, idea, client)
    except EODHDError as exc:  # the idea exists; price history can be refreshed later
        db.rollback()
        idea.parsed_json = {**(idea.parsed_json or {}), "price_refresh_error": exc.to_dict()}
        db.commit()
    return ledger.to_idea_detail(db, _load(db, idea.id), hide_dollars=False)


@router.patch("/ideas/{idea_id}", response_model=IdeaDetail, dependencies=[WriteAuth])
def update_idea(idea_id: int, body: IdeaUpdate, db: Session = Depends(get_db)) -> IdeaDetail:
    idea = _load(db, idea_id)
    data = body.model_dump(exclude_unset=True, exclude={"clear_invalidation"})
    for k, v in data.items():
        setattr(idea, k, v)
    if body.clear_invalidation:
        idea.invalidation_rule_json = None
    if idea.window_end <= idea.window_start:
        raise HTTPException(422, "window_end must be after window_start")
    if idea.direction in ("outperform", "underperform") and not idea.benchmark_symbol:
        raise HTTPException(422, "relative ideas need benchmark_symbol")
    db.commit()
    if idea.status == "open":
        try:
            ledger.resolve_idea(db, _load(db, idea_id), EODHDClient())
        except EODHDError as exc:
            db.rollback()
            raise _eodhd_http(exc) from exc
    return ledger.to_idea_detail(db, _load(db, idea_id), hide_dollars=False)


@router.post("/ideas/{idea_id}/close", response_model=IdeaDetail, dependencies=[WriteAuth])
def close_idea(idea_id: int, body: IdeaClose, db: Session = Depends(get_db)) -> IdeaDetail:
    idea = _load(db, idea_id)
    if idea.status != "open":
        raise HTTPException(409, f"Idea is already {idea.status}")
    ledger.close_manually(db, idea, body.note, EODHDClient())
    return ledger.to_idea_detail(db, _load(db, idea_id), hide_dollars=False)


@router.post("/ideas/{idea_id}/resolve", response_model=IdeaDetail, dependencies=[WriteAuth])
def resolve_now(idea_id: int, db: Session = Depends(get_db)) -> IdeaDetail:
    idea = _load(db, idea_id)
    client = EODHDClient()
    try:
        ledger.resolve_idea(db, idea, client)
    except EODHDError as exc:
        db.rollback()
        raise _eodhd_http(exc) from exc
    # Phase 6: mark this idea's open position from today's chain and apply its exit rules. Chain failures are
    # returned by POST /api/positions/{id}/mark; here they only reach the log.
    from api.services import positions

    summary = positions.mark_positions(db, client, idea_id=idea_id)
    for err in summary.get("errors", []):
        logging.getLogger("tt.positions").warning("mark failed on resolve: %s", err)
    return ledger.to_idea_detail(db, _load(db, idea_id), hide_dollars=False)


@router.delete("/ideas/{idea_id}", status_code=204, dependencies=[WriteAuth])
def delete_idea(idea_id: int, db: Session = Depends(get_db)) -> None:
    idea = _load(db, idea_id)
    db.delete(idea)
    db.commit()


@router.get("/instruments/{instrument_id}/bars")
def instrument_bars(
    instrument_id: int,
    db: Session = Depends(get_db),
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    inst = db.get(Instrument, instrument_id)
    if inst is None:
        raise HTTPException(404, "Instrument not found")
    end = end or ledger.today_utc()
    start = start or end - timedelta(days=180)
    return [b.__dict__ for b in prices.bars(db, inst.id, start, end)]
