"""Chain snapshots (Phase 6): the storage band, record dates, and the 1-year IV percentile.

Every selector run and every daily mark stores the fetched band into `chain_snapshots`, one row per
(instrument, record date, contract). Nothing here prices anything: rows are EODHD quotes copied verbatim, and the
percentile is a rank of stored numbers.

Record date: EODHD's `/contracts` endpoint is a snapshot "as of the last close" whose quote timestamps
(`bid_date` / `ask_date`) are UTC; the 23:59:59 ET close stamp lands on the next UTC calendar day, so the record
date is the ET date of the latest quote timestamp (verified live 2026-09-09: bid_date 2026-09-09T03:59:59Z on the
2026-09-08 close).

Retention: full band rows are kept CHAIN_RETENTION_DAYS; every band store also writes one `chain_daily_summary` row
(spot, at-the-money IV, 20-day realized vol, row count) and the daily cron rolls older record dates down to that row
and deletes the band rows.

IV percentile: the at-the-money IV of a record date is the mean call/put IV at the strike nearest the stored spot
on the expiry nearest 30 days out, stored in chain_daily_summary. The percentile of today's at-the-money IV against
the summary rows in the trailing year is reported once at least IV_PERCENTILE_MIN_DAYS days exist; until then the
selector keeps the IV-versus-realized stand-in and says so.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import ChainDailySummary, ChainSnapshot, PriceSnapshot
from api.services.eodhd import OPTIONS_CONTRACTS_PATH, EODHDClient, EODHDError

log = logging.getLogger("tt.chains")

MARKET_TZ = ZoneInfo("America/New_York")

# Storage band (docs/eodhd-probe.md): 120 days out, +-25% of spot, widened to cover the idea's levels.
BAND_DAYS = 120
BAND_PCT = 0.25
PAGE_LIMIT = 1000
FIELDS = (
    "contract,underlying_symbol,exp_date,type,strike,bid,ask,midpoint,last,volatility,delta,theta,"
    "open_interest,volume,dte,bid_date,ask_date,tradetime"
)

# IV percentile: needs this many distinct record dates in the trailing year before it replaces the stand-in.
IV_PERCENTILE_MIN_DAYS = 20
# Retention (owner decision 2026-09-09): full band rows for this many days, then one chain_daily_summary row per
# (instrument, record date) and the band rows are deleted. The summary row is written when the band is stored, so
# the percentile never depends on the band rows and reads the same before and after the rollup.
CHAIN_RETENTION_DAYS = 30
REALIZED_VOL_WINDOW = 20
ATM_TARGET_DTE = 30
ATM_MIN_DTE = 7


@dataclass(frozen=True)
class Quote:
    """One contract quote as EODHD returned it (normalized types, nothing filled in)."""

    contract: str
    expiry: date
    right: str
    strike: float
    bid: float | None
    ask: float | None
    mid: float | None
    last: float | None
    iv: float | None
    delta: float | None
    theta: float | None
    oi: int | None
    volume: int | None
    dte: int | None
    bid_date: str | None
    ask_date: str | None
    tradetime: str | None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _int(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def normalize(rows: list[dict[str, Any]]) -> list[Quote]:
    """JSON:API rows -> Quote. Rows without a strike, expiry or type are dropped; nothing is invented."""
    out: list[Quote] = []
    for r in rows:
        a = r.get("attributes", r) if isinstance(r, dict) else {}
        try:
            expiry = date.fromisoformat(str(a["exp_date"])[:10])
            strike = float(a["strike"])
            right = str(a["type"]).lower()
        except (KeyError, TypeError, ValueError):
            continue
        if right not in ("call", "put") or strike <= 0:
            continue
        mid = _num(a.get("midpoint"))
        bid, ask = _num(a.get("bid")), _num(a.get("ask"))
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        iv = _num(a.get("volatility"))
        out.append(
            Quote(
                contract=str(a.get("contract") or r.get("id") or f"{strike}{right}{expiry}"),
                expiry=expiry,
                right=right,
                strike=strike,
                bid=bid,
                ask=ask,
                mid=mid,
                last=_num(a.get("last")),
                iv=iv if iv and iv > 0 else None,
                delta=_num(a.get("delta")),
                theta=_num(a.get("theta")),
                oi=_int(a.get("open_interest")),
                volume=_int(a.get("volume")),
                dte=_int(a.get("dte")),
                bid_date=a.get("bid_date"),
                ask_date=a.get("ask_date"),
                tradetime=a.get("tradetime"),
            )
        )
    return out


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def quote_timestamps(rows: list[Quote]) -> tuple[datetime | None, date | None]:
    """(latest quote timestamp, latest trade date) across the rows."""
    stamps = [t for t in (parse_ts(r.bid_date) for r in rows) if t] + [
        t for t in (parse_ts(r.ask_date) for r in rows) if t
    ]
    trades = []
    for r in rows:
        try:
            trades.append(date.fromisoformat(str(r.tradetime)[:10]))
        except (TypeError, ValueError):
            continue
    return (max(stamps) if stamps else None), (max(trades) if trades else None)


def record_date(rows: list[Quote]) -> date | None:
    """The close the chain describes: ET date of the latest quote timestamp, else the latest trade date."""
    at, trade = quote_timestamps(rows)
    if at is not None:
        return at.astimezone(MARKET_TZ).date()
    return trade


def strike_band(spot: float, levels: list[float | None], pct: float = BAND_PCT) -> tuple[float, float]:
    lo, hi = spot * (1 - pct), spot * (1 + pct)
    for lvl in levels:
        if lvl:
            lo, hi = min(lo, lvl * 0.98), max(hi, lvl * 1.02)
    return lo, hi


def fetch_band(
    client: EODHDClient,
    bare_symbol: str,
    spot: float,
    today: date,
    rights: set[str],
    levels: list[float | None] | None = None,
    band_days: int = BAND_DAYS,
    band_pct: float = BAND_PCT,
) -> tuple[list[Quote], dict[str, Any]]:
    """Live chain inside the storage band, one request per right (paged only when a page comes back full).
    Raises EODHDError on any failure or an empty chain; nothing is synthesized."""
    lo, hi = strike_band(spot, levels or [], band_pct)
    calls = 0
    seen: dict[str, Quote] = {}
    for right in sorted(rights):
        offset = 0
        while True:
            params = {
                "filter[underlying_symbol]": bare_symbol,
                "filter[type]": right,
                "filter[exp_date_from]": today.isoformat(),
                "filter[exp_date_to]": (today + timedelta(days=band_days)).isoformat(),
                "filter[strike_from]": round(lo, 2),
                "filter[strike_to]": round(hi, 2),
                "fields[options-contracts]": FIELDS,
                "sort": "strike",
                "page[limit]": PAGE_LIMIT,
                "page[offset]": offset,
            }
            data = client._get(OPTIONS_CONTRACTS_PATH, params, fmt_json=False)
            calls += 1
            raw = data.get("data", []) if isinstance(data, dict) else []
            if not isinstance(raw, list):
                raise EODHDError("options chain returned an unexpected shape", status=200, path=OPTIONS_CONTRACTS_PATH)
            for row in normalize(raw):
                if row.right == right and lo - 1e-9 <= row.strike <= hi + 1e-9:
                    seen.setdefault(row.contract, row)
            if len(raw) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
            if offset > 10 * PAGE_LIMIT:  # safety valve
                break
    rows = sorted(seen.values(), key=lambda r: (r.expiry, r.right, r.strike))
    if not rows:
        raise EODHDError(
            f"no option contracts returned for {bare_symbol} (strikes {lo:.2f}-{hi:.2f}, next {band_days} days)",
            status=200,
            path=OPTIONS_CONTRACTS_PATH,
        )
    as_of, trade_date = quote_timestamps(rows)
    meta = {
        "requests": calls,
        "rows": len(rows),
        "rights": sorted(rights),
        "strike_band": [round(lo, 2), round(hi, 2)],
        "expiry_band_days": band_days,
        "expiries": sorted({r.expiry.isoformat() for r in rows}),
        "chain_as_of": as_of.isoformat() if as_of else None,
        "chain_trade_date": trade_date.isoformat() if trade_date else None,
        "record_date": (rd := record_date(rows)) and rd.isoformat(),
        "rows_without_iv": sum(1 for r in rows if r.iv is None),
    }
    return rows, meta


def fetch_contracts(client: EODHDClient, contracts: list[str]) -> tuple[list[Quote], int]:
    """Current quote for named contracts, one request each (`filter[contract]`, verified live 2026-09-09).
    Raises EODHDError when any request fails or a contract comes back empty (it has expired or never existed)."""
    out: list[Quote] = []
    calls = 0
    for ct in contracts:
        data = client._get(
            OPTIONS_CONTRACTS_PATH, {"filter[contract]": ct, "fields[options-contracts]": FIELDS}, fmt_json=False
        )
        calls += 1
        raw = data.get("data", []) if isinstance(data, dict) else []
        rows = [q for q in normalize(raw if isinstance(raw, list) else []) if q.contract == ct]
        if not rows:
            raise EODHDError(
                f"contract {ct} is not quoted (expired or unknown)", status=200, path=OPTIONS_CONTRACTS_PATH
            )
        out.append(rows[0])
    return out, calls


# --- storage --------------------------------------------------------------------------------------------------------


def store_rows(
    db: Session, instrument_id: int, rows: list[Quote], as_of: date, spot: float | None, source: str = "contracts"
) -> int:
    """Insert rows not already stored for (instrument, as_of, contract). Returns the number added."""
    if not rows:
        return 0
    have = set(
        db.execute(
            select(ChainSnapshot.contract).where(
                ChainSnapshot.instrument_id == instrument_id, ChainSnapshot.as_of == as_of
            )
        ).scalars()
    )
    n = 0
    for q in rows:
        if q.contract in have:
            continue
        have.add(q.contract)
        db.add(
            ChainSnapshot(
                instrument_id=instrument_id,
                as_of=as_of,
                contract=q.contract,
                expiry=q.expiry,
                right=q.right,
                strike=q.strike,
                bid=q.bid,
                ask=q.ask,
                mid=q.mid,
                last=q.last,
                iv=q.iv,
                delta=q.delta,
                theta=q.theta,
                oi=q.oi,
                volume=q.volume,
                spot=spot,
                quote_at=quote_timestamps([q])[0],
                source=source,
            )
        )
        n += 1
    if n:
        db.commit()
        log.info("stored %d chain rows for instrument %d as of %s", n, instrument_id, as_of)
    return n


def realized_vol_pct(closes: list[float], window: int = REALIZED_VOL_WINDOW) -> float | None:
    """Annualized std of daily log returns over the last `window` returns, in %. None with too few bars."""
    if len(closes) < window + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100.0, 2)


def _realized_vol_to(db: Session, instrument_id: int, as_of: date) -> float | None:
    closes = list(
        db.execute(
            select(PriceSnapshot.close)
            .where(
                PriceSnapshot.instrument_id == instrument_id,
                PriceSnapshot.source == "eod",
                PriceSnapshot.as_of <= as_of,
            )
            .order_by(PriceSnapshot.as_of.desc())
            .limit(REALIZED_VOL_WINDOW + 1)
        ).scalars()
    )
    closes.reverse()
    return realized_vol_pct(closes)


def upsert_summary(
    db: Session, instrument_id: int, as_of: date, rows: list[Any], spot: float | None, rolled_up: bool = False
) -> ChainDailySummary:
    """Write or refresh the (instrument, record date) summary from band rows: spot, at-the-money IV, 20-day realized
    vol to that date, row count."""
    row = db.execute(
        select(ChainDailySummary).where(
            ChainDailySummary.instrument_id == instrument_id, ChainDailySummary.as_of == as_of
        )
    ).scalar_one_or_none()
    if row is None:
        row = ChainDailySummary(instrument_id=instrument_id, as_of=as_of, row_count=0)
        db.add(row)
    row.spot = spot
    row.atm_iv = atm_iv(rows, spot, as_of)
    row.realized_vol_20d = _realized_vol_to(db, instrument_id, as_of)
    row.row_count = len(rows)
    if rolled_up:
        row.rolled_up_at = datetime.now(UTC)
    return row


def store_band(db: Session, instrument_id: int, rows: list[Quote], as_of: date, spot: float | None) -> int:
    """Store a full band (source `contracts`) and write its summary row. Returns the number of band rows added."""
    n = store_rows(db, instrument_id, rows, as_of, spot, source="contracts")
    band = [
        r
        for r in db.execute(
            select(ChainSnapshot).where(
                ChainSnapshot.instrument_id == instrument_id,
                ChainSnapshot.as_of == as_of,
                ChainSnapshot.source == "contracts",
            )
        ).scalars()
    ]
    spots = [r.spot for r in band if r.spot] or ([spot] if spot else [])
    upsert_summary(db, instrument_id, as_of, band, spots[0] if spots else None)
    db.commit()
    return n


def rollup_chain_snapshots(db: Session, today: date, retention_days: int = CHAIN_RETENTION_DAYS) -> dict[str, Any]:
    """Daily retention: every (instrument, record date) older than `retention_days` with band rows left gets its
    summary row (re)written from those rows and the rows deleted. Record dates that only hold per-contract rows
    (a position's legs quoted outside the band) are deleted without a summary."""
    cutoff = today - timedelta(days=retention_days)
    pairs = db.execute(
        select(ChainSnapshot.instrument_id, ChainSnapshot.as_of)
        .where(ChainSnapshot.as_of < cutoff)
        .group_by(ChainSnapshot.instrument_id, ChainSnapshot.as_of)
    ).all()
    out: dict[str, Any] = {"cutoff": cutoff.isoformat(), "dates_rolled": 0, "rows_deleted": 0, "summaries": 0}
    for inst_id, as_of in pairs:
        rows = list(
            db.execute(
                select(ChainSnapshot).where(ChainSnapshot.instrument_id == inst_id, ChainSnapshot.as_of == as_of)
            ).scalars()
        )
        band = [r for r in rows if r.source == "contracts"]
        if band:
            spots = [r.spot for r in band if r.spot]
            upsert_summary(db, inst_id, as_of, band, spots[0] if spots else None, rolled_up=True)
            out["summaries"] += 1
        for r in rows:
            db.delete(r)
        out["rows_deleted"] += len(rows)
        out["dates_rolled"] += 1
    db.commit()
    if out["dates_rolled"]:
        log.info("chain rollup: %s", out)
    return out


def stored_quotes(db: Session, instrument_id: int, as_of: date, contracts: list[str]) -> dict[str, ChainSnapshot]:
    rows = db.execute(
        select(ChainSnapshot).where(
            ChainSnapshot.instrument_id == instrument_id,
            ChainSnapshot.as_of == as_of,
            ChainSnapshot.contract.in_(contracts),
        )
    ).scalars()
    return {r.contract: r for r in rows}


def distinct_dates(db: Session, instrument_id: int) -> list[date]:
    return sorted(
        set(db.execute(select(ChainSnapshot.as_of).where(ChainSnapshot.instrument_id == instrument_id)).scalars())
    )


# --- at-the-money IV and its percentile -------------------------------------------------------------------------------


def atm_iv(rows: list[Any], spot: float | None, as_of: date) -> float | None:
    """Mean call/put IV at the strike nearest `spot` on the expiry nearest ATM_TARGET_DTE days out (at least
    ATM_MIN_DTE). `rows` need `.expiry`, `.strike`, `.right`, `.iv`. None without spot or without an IV there."""
    if not spot:
        return None
    with_iv = [r for r in rows if r.iv is not None and (r.expiry - as_of).days >= ATM_MIN_DTE]
    if not with_iv:
        return None
    expiry = min({r.expiry for r in with_iv}, key=lambda e: abs((e - as_of).days - ATM_TARGET_DTE))
    at_exp = [r for r in with_iv if r.expiry == expiry]
    strike = min({r.strike for r in at_exp}, key=lambda k: abs(k - spot))
    ivs = [r.iv for r in at_exp if r.strike == strike]
    return sum(ivs) / len(ivs) if ivs else None


def atm_iv_history(db: Session, instrument_id: int, start: date, end: date) -> dict[date, float]:
    """At-the-money IV per record date in [start, end], from chain_daily_summary (never from the band rows, so the
    series is the same before and after the retention rollup)."""
    rows = db.execute(
        select(ChainDailySummary).where(
            ChainDailySummary.instrument_id == instrument_id,
            ChainDailySummary.as_of >= start,
            ChainDailySummary.as_of <= end,
            ChainDailySummary.atm_iv.is_not(None),
        )
    ).scalars()
    return {r.as_of: r.atm_iv for r in rows}


def iv_percentile_1y(
    db: Session, instrument_id: int, today: date, current_atm_iv: float | None, min_days: int = IV_PERCENTILE_MIN_DAYS
) -> dict[str, Any]:
    """Percentile of `current_atm_iv` against the stored at-the-money IV on distinct record dates in the trailing
    year (today excluded). `percentile` stays None below `min_days` of history; `days` says how much exists."""
    hist = atm_iv_history(db, instrument_id, today - timedelta(days=365), today - timedelta(days=1))
    days = len(hist)
    out: dict[str, Any] = {
        "percentile": None,
        "days": days,
        "min_days": min_days,
        "atm_iv": round(current_atm_iv, 4) if current_atm_iv is not None else None,
        "note": "",
    }
    if current_atm_iv is None:
        out["note"] = "no at-the-money implied vol on today's chain"
        return out
    if days < min_days:
        out["note"] = (
            f"{days} day{'s' if days != 1 else ''} of stored chain history; the percentile needs {min_days}. "
            "IV versus 20-day realized stands in."
        )
        return out
    below = sum(1 for v in hist.values() if v < current_atm_iv)
    out["percentile"] = round(below / days * 100.0, 1)
    out["note"] = f"at-the-money IV rank over {days} record dates in the trailing year (chain_daily_summary)"
    return out
