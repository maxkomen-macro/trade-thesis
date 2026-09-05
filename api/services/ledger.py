"""Database-aware orchestration around the pure resolver: create/refresh/resolve ideas and serialize them."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import Idea, Instrument, PriceSnapshot, ResolutionEvent
from api.db.schemas import BarOut, EventOut, IdeaDetail, IdeaOut, InstrumentOut, JobSummary
from api.services import prices
from api.services.eodhd import EODHDClient, EODHDError
from api.services.resolver import Bar, Context, _bench_at, decide, in_window, progress_pct, spread_pct
from api.services.rules import benchmarks_in, describe, first_level

log = logging.getLogger("tt.ledger")

CHART_LOOKBACK_DAYS = 45
CHART_TAIL_DAYS = 5


def today_utc() -> date:
    return datetime.now(UTC).date()


# --- instruments ----------------------------------------------------------------------------------------------------


def get_or_create_instrument(
    db: Session,
    symbol: str,
    display_name: str | None = None,
    kind: str | None = None,
    client: EODHDClient | None = None,
) -> Instrument:
    """Instrument by EODHD symbol; when new and no name is given, the name comes from EODHD search (never invented)."""
    symbol = symbol.strip().upper()
    row = db.execute(select(Instrument).where(Instrument.symbol == symbol)).scalar_one_or_none()
    if row:
        return row
    if not display_name:
        client = client or EODHDClient()
        code, _, exchange = symbol.rpartition(".")
        hits = client.search(code, limit=10)
        match = next((h for h in hits if h.get("Code") == code and h.get("Exchange") == exchange), None)
        if match is None:
            raise EODHDError(f"Symbol {symbol} not found on EODHD search", status=404, path="/search")
        display_name = str(match.get("Name") or symbol)
        if kind is None:
            kind = "etf" if str(match.get("Type", "")).upper() == "ETF" else "stock"
    row = Instrument(symbol=symbol, display_name=display_name, kind=kind or "etf")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- resolution ---


def _entry_date(idea: Idea) -> date:
    return idea.entry_price_at.date() if idea.entry_price_at else idea.window_start


def _benchmark_symbols(idea: Idea) -> set[str]:
    syms = benchmarks_in(idea.success_rule_json)
    if idea.benchmark_symbol:
        syms.add(idea.benchmark_symbol)
    return syms


def _benchmark_bars(
    db: Session, idea: Idea, start: date, end: date, client: EODHDClient | None, refresh: bool
) -> list[Bar]:
    syms = _benchmark_symbols(idea)
    if not syms:
        return []
    sym = idea.benchmark_symbol or sorted(syms)[0]
    inst = get_or_create_instrument(db, sym, client=client)
    if refresh:
        prices.ensure_eod(db, inst, start, end, client)
    return prices.bars(db, inst.id, start, end)


def _benchmark_entry(bench: list[Bar], entry_date: date) -> float | None:
    """Benchmark close on the entry date, else the first close after it (same-day EOD may not exist yet)."""
    for b in bench:
        if b.as_of >= entry_date:
            return b.close
    return None


def _has_event(idea: Idea, event_type: str, occurred_on: date) -> bool:
    return any(e.event_type == event_type and e.occurred_on == occurred_on for e in idea.events)


def resolve_idea(
    db: Session, idea: Idea, client: EODHDClient | None = None, today: date | None = None, refresh: bool = True
) -> dict[str, Any]:
    """Refresh bars, run the decision, persist status/P&L/events. Returns a small summary. Raises EODHDError."""
    today = today or today_utc()
    if idea.status != "open" or idea.entry_price is None:
        return {"idea_id": idea.id, "status": idea.status, "changed": False}
    entry_date = _entry_date(idea)
    start = min(entry_date, idea.window_start) - timedelta(days=CHART_LOOKBACK_DAYS)
    end = min(today, idea.window_end + timedelta(days=CHART_TAIL_DAYS))
    if refresh:
        prices.ensure_eod(db, idea.instrument, start, end, client)
    bars = prices.bars(db, idea.instrument_id, start, end)
    bench = _benchmark_bars(db, idea, start, end, client, refresh)
    ctx = Context(
        direction=idea.direction,
        entry_price=idea.entry_price,
        entry_date=entry_date,
        window_start=idea.window_start,
        window_end=idea.window_end,
        benchmark_entry=_benchmark_entry(bench, entry_date),
    )
    d = decide(
        ctx,
        idea.success_rule_json,
        idea.invalidation_rule_json,
        idea.invalidation_is_note_only,
        idea.capital_assigned,
        bars,
        bench,
        today,
    )
    changed = False
    if d.on is not None:
        idea.hypothetical_pnl_pct, idea.hypothetical_pnl_abs = d.pnl_pct, d.pnl_abs
        idea.last_price, idea.last_price_as_of = d.price, d.on
    if d.spread_at_window_end_pct is not None:
        idea.spread_at_window_end_pct = d.spread_at_window_end_pct
    for ev in d.events:
        if ev["event_type"] == "progress" and _has_event(idea, "progress", ev["occurred_on"]):
            continue
        db.add(ResolutionEvent(idea_id=idea.id, benchmark_price=d.benchmark_price, **ev))
        changed = True
    if d.status != "open":
        idea.status = d.status
        idea.resolution_reason = d.reason
        idea.resolved_at = datetime.now(UTC)
        changed = True
    db.commit()
    return {"idea_id": idea.id, "status": idea.status, "reason": idea.resolution_reason, "changed": changed}


def backfill_window_end_spread(
    db: Session, idea: Idea, client: EODHDClient | None = None, today: date | None = None
) -> bool:
    """A relative idea that resolved before its window ended still needs the window-end spread once the window
    closes. Fills `spread_at_window_end_pct` and writes a `benchmark_update` event. Returns True when filled."""
    today = today or today_utc()
    if idea.spread_at_window_end_pct is not None or idea.entry_price is None or not _benchmark_symbols(idea):
        return False
    if today <= idea.window_end:
        return False
    entry_date = _entry_date(idea)
    start = min(entry_date, idea.window_start) - timedelta(days=CHART_LOOKBACK_DAYS)
    end = idea.window_end + timedelta(days=CHART_TAIL_DAYS)
    prices.ensure_eod(db, idea.instrument, start, end, client)
    bars = prices.bars(db, idea.instrument_id, start, end)
    bench = _benchmark_bars(db, idea, start, end, client, refresh=True)
    ctx = Context(
        idea.direction,
        idea.entry_price,
        entry_date,
        idea.window_start,
        idea.window_end,
        _benchmark_entry(bench, entry_date),
    )
    inside = in_window(bars, ctx)
    if not inside or ctx.benchmark_entry is None:
        return False
    fb = inside[-1]
    bench_close = _bench_at(bench, fb.as_of)
    value = spread_pct(idea.direction, idea.entry_price, fb.close, ctx.benchmark_entry, bench_close)
    if value is None:
        return False
    idea.spread_at_window_end_pct = value
    sym = idea.benchmark_symbol or sorted(_benchmark_symbols(idea))[0]
    db.add(
        ResolutionEvent(
            idea_id=idea.id,
            event_type="benchmark_update",
            price=fb.close,
            benchmark_price=bench_close,
            note=f"spread at window end {value:+.2f}% vs {sym}",
            occurred_on=fb.as_of,
        )
    )
    db.commit()
    return True


def close_manually(db: Session, idea: Idea, note: str | None, client: EODHDClient | None = None) -> Idea:
    today = today_utc()
    try:
        prices.ensure_eod(db, idea.instrument, today - timedelta(days=7), today, client)
    except EODHDError as exc:  # close with the last stored mark; the error is recorded in the event note
        note = f"{note or ''} (price refresh failed: {exc})".strip()
    last = prices.latest_close(db, idea.instrument_id)
    if last and idea.entry_price:
        from api.services.resolver import hypothetical_pnl, signed_return

        bench_last = bench_entry = None
        if idea.direction in ("outperform", "underperform"):
            bench = _benchmark_bars(db, idea, _entry_date(idea) - timedelta(days=7), today, client, refresh=False)
            bench_entry = _benchmark_entry(bench, _entry_date(idea))
            bench_last = bench[-1].close if bench else None
        r = signed_return(idea.direction, idea.entry_price, last[0], bench_entry, bench_last)
        idea.hypothetical_pnl_pct, idea.hypothetical_pnl_abs = hypothetical_pnl(idea.capital_assigned, r)
        idea.last_price, idea.last_price_as_of = last
    idea.status = "closed_manual"
    idea.resolution_reason = "manual"
    idea.resolved_at = datetime.now(UTC)
    db.add(
        ResolutionEvent(
            idea_id=idea.id,
            event_type="manual_close",
            price=last[0] if last else None,
            note=note,
            occurred_on=last[1] if last else today,
        )
    )
    db.commit()
    db.refresh(idea)
    return idea


def run_resolver(db: Session, client: EODHDClient | None = None, today: date | None = None) -> JobSummary:
    """The daily job: every open idea, in id order. Per-idea EODHD failures are reported, never hidden."""
    today = today or today_utc()
    client = client or EODHDClient()
    summary = JobSummary(job="resolve", as_of=today)
    ideas = db.execute(select(Idea).where(Idea.status == "open").order_by(Idea.id)).scalars().all()
    for idea in ideas:
        summary.ideas_checked += 1
        try:
            res = resolve_idea(db, idea, client, today)
        except EODHDError as exc:
            db.rollback()
            summary.errors.append({"idea_id": idea.id, "symbol": idea.instrument.symbol, **exc.to_dict()})
            continue
        if res.get("status") != "open":
            summary.resolved.append(res)
    # Window-end spreads for relative ideas that resolved early.
    pending = (
        db.execute(
            select(Idea).where(
                Idea.status != "open",
                Idea.benchmark_symbol.is_not(None),
                Idea.spread_at_window_end_pct.is_(None),
                Idea.window_end < today,
            )
        )
        .scalars()
        .all()
    )
    for idea in pending:
        try:
            if backfill_window_end_spread(db, idea, client, today):
                summary.resolved.append({"idea_id": idea.id, "backfilled": "spread_at_window_end_pct"})
        except EODHDError as exc:
            db.rollback()
            summary.errors.append({"idea_id": idea.id, "symbol": idea.instrument.symbol, **exc.to_dict()})
    return summary


def refresh_prices(db: Session, client: EODHDClient | None = None, today: date | None = None) -> JobSummary:
    """Manual refresh: bring every instrument with an open idea (and its benchmark) up to today, then re-mark."""
    today = today or today_utc()
    client = client or EODHDClient()
    summary = JobSummary(job="refresh-prices", as_of=today)
    ideas = db.execute(select(Idea).where(Idea.status == "open").order_by(Idea.id)).scalars().all()
    seen: set[str] = set()
    for idea in ideas:
        for sym in {idea.instrument.symbol, *_benchmark_symbols(idea)}:
            if sym in seen:
                continue
            seen.add(sym)
            inst = get_or_create_instrument(db, sym, client=client)
            try:
                summary.bars_added += prices.ensure_eod(
                    db, inst, today - timedelta(days=CHART_LOOKBACK_DAYS), today, client
                )
                summary.instruments_refreshed += 1
            except EODHDError as exc:
                db.rollback()
                summary.errors.append({"symbol": sym, **exc.to_dict()})
    for idea in ideas:
        summary.ideas_checked += 1
        try:
            res = resolve_idea(db, idea, client, today, refresh=False)
            if res.get("status") != "open":
                summary.resolved.append(res)
        except EODHDError as exc:
            summary.errors.append({"idea_id": idea.id, **exc.to_dict()})
    return summary


# --- serialization ---


def is_seed(idea: Idea) -> bool:
    return bool((idea.parsed_json or {}).get("seed"))


def direction_right_of(idea: Idea) -> bool | None:
    if idea.status == "right":
        return True
    if idea.status == "wrong":
        return False
    if idea.status in ("expired", "closed_manual"):
        if idea.resolution_reason and idea.resolution_reason.startswith("expired:"):
            return idea.resolution_reason.endswith("direction_right")
        return (idea.hypothetical_pnl_pct or 0) > 0 if idea.hypothetical_pnl_pct is not None else None
    return None


def to_idea_out(idea: Idea, hide_dollars: bool, today: date | None = None) -> IdeaOut:
    today = today or today_utc()
    entry = idea.entry_price or 0.0
    ctx = Context(
        direction=idea.direction,
        entry_price=entry,
        entry_date=_entry_date(idea),
        window_start=idea.window_start,
        window_end=idea.window_end,
    )
    prog, kind = progress_pct(ctx, idea.success_rule_json, idea.last_price, today) if entry else (0.0, "time")
    seed = is_seed(idea)
    return IdeaOut(
        id=idea.id,
        instrument=InstrumentOut.model_validate(idea.instrument),
        title=idea.title,
        thesis_text=idea.thesis_text,
        parsed_json=idea.parsed_json,
        direction=idea.direction,
        benchmark_symbol=idea.benchmark_symbol,
        success_rule_json=idea.success_rule_json,
        invalidation_rule_json=idea.invalidation_rule_json,
        invalidation_is_note_only=idea.invalidation_is_note_only,
        success_rule_text=describe(idea.success_rule_json, idea.direction),
        invalidation_rule_text=describe(idea.invalidation_rule_json, idea.direction)
        if idea.invalidation_rule_json
        else None,
        target_level=first_level(idea.success_rule_json),
        stop_level=first_level(idea.invalidation_rule_json),
        window_start=idea.window_start,
        window_end=idea.window_end,
        days_left=(idea.window_end - today).days if idea.status == "open" else None,
        catalyst_date=idea.catalyst_date,
        catalyst_note=idea.catalyst_note,
        conviction_pct=idea.conviction_pct,
        capital_assigned=None if hide_dollars else idea.capital_assigned,
        idea_type=idea.idea_type,
        entry_price=idea.entry_price,
        entry_price_at=idea.entry_price_at,
        radar_regime=idea.radar_regime,
        radar_probs_json=idea.radar_probs_json,
        tags=list(idea.tags or []),
        basket_symbols=list(idea.basket_symbols) if idea.basket_symbols else None,
        status=idea.status,
        resolved_at=idea.resolved_at,
        resolution_reason=idea.resolution_reason,
        direction_right=direction_right_of(idea),
        hypothetical_pnl_pct=idea.hypothetical_pnl_pct,
        hypothetical_pnl_abs=None if hide_dollars else idea.hypothetical_pnl_abs,
        spread_at_resolution_pct=(
            idea.hypothetical_pnl_pct
            if idea.direction in ("outperform", "underperform") and idea.status != "open"
            else None
        ),
        spread_at_window_end_pct=idea.spread_at_window_end_pct,
        last_price=idea.last_price,
        last_price_as_of=idea.last_price_as_of,
        progress_pct=round(prog, 1),
        progress_kind=kind,
        seed=seed,
        dollars_hidden=hide_dollars,
        created_at=idea.created_at,
        updated_at=idea.updated_at,
    )


def to_idea_detail(db: Session, idea: Idea, hide_dollars: bool, today: date | None = None) -> IdeaDetail:
    today = today or today_utc()
    base = to_idea_out(idea, hide_dollars, today)
    start = min(_entry_date(idea), idea.window_start) - timedelta(days=CHART_LOOKBACK_DAYS)
    end = min(today, idea.window_end + timedelta(days=CHART_TAIL_DAYS)) if idea.status != "open" else today
    bars = prices.bars(db, idea.instrument_id, start, end)
    bench = _benchmark_bars(db, idea, start, end, None, refresh=False)
    return IdeaDetail(
        **base.model_dump(),
        events=[EventOut.model_validate(e) for e in idea.events],
        bars=[BarOut(as_of=b.as_of, open=b.open, high=b.high, low=b.low, close=b.close) for b in bars],
        benchmark_bars=[BarOut(as_of=b.as_of, open=b.open, high=b.high, low=b.low, close=b.close) for b in bench],
    )


def latest_snapshot_rows(db: Session, instrument_id: int, n: int = 1) -> list[PriceSnapshot]:
    return list(
        db.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.instrument_id == instrument_id)
            .order_by(PriceSnapshot.as_of.desc())
            .limit(n)
        ).scalars()
    )
