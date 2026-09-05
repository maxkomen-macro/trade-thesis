"""Price snapshots: fetch EOD bars from EODHD, cache in price_snapshots, never re-fetch a stored date."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import Instrument, PriceSnapshot
from api.services.eodhd import EODHDClient, EODHDError
from api.services.resolver import Bar

log = logging.getLogger("tt.prices")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def stored_dates(db: Session, instrument_id: int, start: date, end: date, source: str = "eod") -> set[date]:
    rows = db.execute(
        select(PriceSnapshot.as_of).where(
            PriceSnapshot.instrument_id == instrument_id,
            PriceSnapshot.source == source,
            PriceSnapshot.as_of >= start,
            PriceSnapshot.as_of <= end,
        )
    ).scalars()
    return set(rows)


def _store_eod_rows(
    db: Session, instrument_id: int, rows: list[dict[str, Any]], have: set[date], lo: date, hi: date
) -> int:
    """Store rows inside [lo, hi] whose date is not already present. Rows outside the requested range are
    ignored so an over-generous upstream response can never violate the (instrument, as_of, source) constraint."""
    n = 0
    for r in rows:
        try:
            as_of = date.fromisoformat(str(r["date"]))
        except (KeyError, ValueError):
            continue
        close = _num(r.get("close"))
        if close is None or as_of in have or not (lo <= as_of <= hi):
            continue
        db.add(
            PriceSnapshot(
                instrument_id=instrument_id,
                as_of=as_of,
                open=_num(r.get("open")),
                high=_num(r.get("high")),
                low=_num(r.get("low")),
                close=close,
                adj_close=_num(r.get("adjusted_close")),
                volume=int(r["volume"]) if _num(r.get("volume")) is not None else None,
                source="eod",
            )
        )
        have.add(as_of)
        n += 1
    return n


def ensure_eod(db: Session, instrument: Instrument, start: date, end: date, client: EODHDClient | None = None) -> int:
    """Fill gaps at either edge of [start, end] from EODHD. Returns the number of new rows. Raises EODHDError."""
    client = client or EODHDClient()
    have = stored_dates(db, instrument.id, start, end)
    fetch: list[tuple[date, date]] = []
    if not have:
        fetch.append((start, end))
    else:
        lo, hi = min(have), max(have)
        if start < lo:
            fetch.append((start, lo - timedelta(days=1)))
        if end > hi:
            fetch.append((hi + timedelta(days=1), end))
    added = 0
    for a, b in fetch:
        if a > b:
            continue
        rows = client.eod(instrument.symbol, a, b)
        added += _store_eod_rows(db, instrument.id, rows, have, a, b)
    if added:
        db.commit()
        log.info("stored %d eod bars for %s (%s..%s)", added, instrument.symbol, start, end)
    return added


def bars(db: Session, instrument_id: int, start: date, end: date, source: str = "eod") -> list[Bar]:
    rows = db.execute(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.instrument_id == instrument_id,
            PriceSnapshot.source == source,
            PriceSnapshot.as_of >= start,
            PriceSnapshot.as_of <= end,
        )
        .order_by(PriceSnapshot.as_of)
    ).scalars()
    return [Bar(as_of=r.as_of, close=r.close, open=r.open, high=r.high, low=r.low) for r in rows]


def latest_close(db: Session, instrument_id: int) -> tuple[float, date] | None:
    row = db.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.instrument_id == instrument_id, PriceSnapshot.source == "eod")
        .order_by(PriceSnapshot.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (row.close, row.as_of) if row else None


def close_on_or_after(db: Session, instrument: Instrument, day: date, client: EODHDClient | None = None) -> Bar | None:
    """The EOD close on `day`, or the next trading day's close (weekends/holidays). Used by the seed script."""
    ensure_eod(db, instrument, day, day + timedelta(days=7), client)
    found = bars(db, instrument.id, day, day + timedelta(days=7))
    return found[0] if found else None


def stamp_entry(db: Session, instrument: Instrument, client: EODHDClient | None = None) -> tuple[float, datetime, str]:
    """Entry price for a new idea: EODHD delayed quote, stored as a `realtime` snapshot so it is traceable.
    Falls back to the latest stored EOD close if the quote has no numeric close. Raises EODHDError otherwise."""
    client = client or EODHDClient()
    quotes = client.real_time([instrument.symbol])
    q = quotes[0] if quotes else {}
    close = _num(q.get("close"))
    ts = _num(q.get("timestamp"))
    if close is not None and ts is not None:
        at = datetime.fromtimestamp(ts, tz=UTC)
        as_of = at.date()
        if as_of not in stored_dates(db, instrument.id, as_of, as_of, source="realtime"):
            db.add(
                PriceSnapshot(
                    instrument_id=instrument.id,
                    as_of=as_of,
                    open=_num(q.get("open")),
                    high=_num(q.get("high")),
                    low=_num(q.get("low")),
                    close=close,
                    volume=int(q["volume"]) if _num(q.get("volume")) is not None else None,
                    source="realtime",
                )
            )
            db.commit()
        return close, at, "realtime"
    last = latest_close(db, instrument.id)
    if last:
        return last[0], datetime.combine(last[1], datetime.min.time(), tzinfo=UTC), "eod"
    raise EODHDError(f"No usable price for {instrument.symbol}: real-time returned {q!r}", path="/real-time")
