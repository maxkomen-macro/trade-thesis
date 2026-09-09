"""Option positions (Phase 6): taking an expression, daily marks, exit rules, and dual P&L.

Pure part (no I/O): `structure_quote` values a structure from leg quotes, `decide_exit` walks the daily marks in
order and applies the exit rules. Tested in api/tests/test_positions.py, one test per exit reason.

Exit order per mark day (owner's spec): idea resolution -> stop loss -> take profit -> time stop -> expiry. Marks are
walked chronologically and the first day that trips any rule closes the position at that day's value. Manual
closes come from the API. Every value is a chain quote (long legs at mid, short legs at mid) stored in
option_snapshots with the leg quotes it was built from; after expiry the settlement is the payoff at the
underlying's close on the last trading day at or before the expiry (source `expiry_intrinsic`). Nothing is modeled.

P&L on premium: pnl_pct = (value / entry_debit - 1) x 100. Dollars: pnl_abs = contracts x 100 x (value - entry).
The idea's own P&L (underlying move x capital) is untouched; the two sit side by side on the detail page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.db.models import Idea, OptionAnalysis, OptionPosition, OptionSnapshot, ResolutionEvent
from api.db.schemas import OptionSnapshotOut, PositionOut, PositionSummary
from api.services import chains, prices
from api.services.eodhd import EODHDClient, EODHDError
from api.services.pricing import intrinsic

log = logging.getLogger("tt.positions")

TERMINAL_EVENTS = ("target_hit", "stop_hit", "expired", "manual_close")


# --- pure: structure values from leg quotes ---------------------------------------------------------------------------


def _side(leg: dict[str, Any]) -> int:
    return 1 if leg.get("side", "long") == "long" else -1


def _getter(q: Any):
    if isinstance(q, dict):
        return lambda k: q.get(k)
    return lambda k: getattr(q, k, None)


def structure_quote(legs: list[dict[str, Any]], quotes: dict[str, Any]) -> dict[str, Any] | None:
    """Value a structure from per-contract quotes (objects or dicts with bid/ask/mid/iv/delta/theta).
    Returns value (mid), bid (liquidation: long at bid, short bought back at ask), ask, iv (mean long-leg IV),
    delta/theta (signed sums when every leg has them) and the leg quotes used. None when a leg has no mid."""
    value = bid = ask = 0.0
    bid_ok = ask_ok = True
    long_ivs: list[float] = []
    deltas: list[float] = []
    thetas: list[float] = []
    used: list[dict[str, Any]] = []
    for leg in legs:
        q = quotes.get(leg["contract"])
        if q is None:
            return None
        g = _getter(q)
        mid, b, a = g("mid"), g("bid"), g("ask")
        if mid is None and b is not None and a is not None:
            mid = (b + a) / 2.0
        if mid is None:
            return None
        s, n = _side(leg), int(leg.get("qty", 1))
        value += s * n * mid
        if b is None or a is None:
            bid_ok = ask_ok = False
        else:
            bid += s * n * (b if s > 0 else a)
            ask += s * n * (a if s > 0 else b)
        if s > 0 and g("iv") is not None:
            long_ivs.append(float(g("iv")))
        if g("delta") is not None:
            deltas.append(s * n * float(g("delta")))
        if g("theta") is not None:
            thetas.append(s * n * float(g("theta")))
        quote_at = chains.quote_timestamps([q])[0] if not isinstance(q, dict) else None
        used.append(
            {
                "contract": leg["contract"],
                "side": leg.get("side", "long"),
                "qty": n,
                "bid": b,
                "ask": a,
                "mid": mid,
                "iv": g("iv"),
                "delta": g("delta"),
                "theta": g("theta"),
                "oi": g("oi"),
                "volume": g("volume"),
                # when this leg was last quoted; a band snapshot can carry intraday stamps on some contracts and
                # the previous close on others, so the mark date (the chain's record date) is not enough on its own
                "quote_at": quote_at.isoformat() if quote_at else None,
            }
        )
    return {
        "value": round(value, 4),
        "bid": round(bid, 4) if bid_ok else None,
        "ask": round(ask, 4) if ask_ok else None,
        "iv": round(sum(long_ivs) / len(long_ivs), 4) if long_ivs else None,
        "delta": round(sum(deltas), 4) if len(deltas) == len(legs) else None,
        "theta": round(sum(thetas), 4) if len(thetas) == len(legs) else None,
        "legs": used,
    }


def intrinsic_value(legs: list[dict[str, Any]], spot: float) -> float:
    """Payoff of the structure at expiry with the underlying at `spot`, per share."""
    return round(
        sum(_side(lg) * int(lg.get("qty", 1)) * intrinsic(spot, float(lg["strike"]), lg["right"]) for lg in legs), 4
    )


def pnl(entry_debit: float, value: float, contracts: int) -> tuple[float, float]:
    """(pnl_pct on premium, pnl_abs in dollars)."""
    return round((value / entry_debit - 1.0) * 100.0, 4), round(contracts * 100.0 * (value - entry_debit), 2)


# --- pure: exit decision ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    as_of: date
    value: float
    source: str = "chain_mid"


@dataclass(frozen=True)
class ExitRules:
    entry_debit: float
    contracts: int
    take_profit_pct: float
    stop_loss_pct: float
    time_stop_date: date
    expiry: date


@dataclass
class ExitDecision:
    status: str = "open"  # open | closed
    reason: str | None = None
    on: date | None = None
    value: float | None = None
    pnl_pct: float | None = None
    pnl_abs: float | None = None
    detail: str = ""
    checks: list[str] = field(default_factory=list)


def decide_exit(
    rules: ExitRules, marks: list[Mark], idea_resolved_on: date | None, idea_reason: str | None
) -> ExitDecision:
    """Walk the marks in date order; on each day test the rules in the spec's order and close at the first hit.
    `idea_resolved_on` is the bar date of the idea's terminal event (None while the idea is open). A settlement mark
    (source `expiry_intrinsic`) always closes as `expiry`: the contracts are gone, so no rule could have acted."""
    d = ExitDecision()
    stop_level = rules.entry_debit * (1.0 - rules.stop_loss_pct / 100.0)
    take_level = rules.entry_debit * (1.0 + rules.take_profit_pct / 100.0)
    for m in sorted(marks, key=lambda x: x.as_of):
        reason = detail = None
        if m.source == "expiry_intrinsic":
            # The contracts no longer exist: whatever the rules would have said, the settlement is the outcome.
            reason = "expiry"
            detail = f"expired {rules.expiry}; settled at intrinsic value {m.value:g}"
        elif idea_resolved_on is not None and m.as_of >= idea_resolved_on:
            reason = f"idea_resolved:{idea_reason or 'resolved'}"
            detail = f"idea resolved ({idea_reason or 'resolved'}) on {idea_resolved_on}; position closed at the mark"
        elif m.value <= stop_level + 1e-12:
            reason = "stop_loss"
            detail = (
                f"value {m.value:g} at or below the stop level {stop_level:.4g} ({rules.stop_loss_pct:g}% of premium)"
            )
        elif m.value >= take_level - 1e-12:
            reason = "take_profit"
            detail = (
                f"value {m.value:g} at or above the take-profit level {take_level:.4g} (+{rules.take_profit_pct:g}%)"
            )
        elif m.as_of >= rules.time_stop_date:
            reason = "time_stop"
            days_before = (rules.expiry - rules.time_stop_date).days
            detail = f"time stop {rules.time_stop_date} reached ({days_before} days before expiry)"
        elif m.as_of >= rules.expiry:
            reason = "expiry"
            detail = f"expiry {rules.expiry} reached; closed at the last chain mark {m.value:g}"
        if reason:
            d.status, d.reason, d.on, d.value, d.detail = "closed", reason, m.as_of, m.value, detail
            d.pnl_pct, d.pnl_abs = pnl(rules.entry_debit, m.value, rules.contracts)
            return d
        d.checks.append(f"{m.as_of}: open at {m.value:g}")
    return d


# --- DB helpers -------------------------------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def idea_terminal_date(idea: Idea) -> tuple[date | None, str | None]:
    """Bar date of the idea's terminal event once it is resolved (target/stop/expiry/manual close)."""
    if idea.status == "open":
        return None, None
    terminal = [e for e in idea.events if e.event_type in TERMINAL_EVENTS]
    if idea.invalidation_is_note_only:
        terminal = [e for e in terminal if not (e.event_type == "stop_hit" and (e.note or "").startswith("note only"))]
    if terminal:
        last = max(terminal, key=lambda e: e.occurred_on)
        return last.occurred_on, idea.resolution_reason
    return (idea.resolved_at.date() if idea.resolved_at else None), idea.resolution_reason


def load_position(db: Session, position_id: int) -> OptionPosition | None:
    return db.execute(
        select(OptionPosition)
        .options(
            selectinload(OptionPosition.snapshots),
            selectinload(OptionPosition.idea).selectinload(Idea.instrument),
            selectinload(OptionPosition.idea).selectinload(Idea.events),
        )
        .where(OptionPosition.id == position_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def open_positions(db: Session, idea_id: int | None = None) -> list[OptionPosition]:
    stmt = (
        select(OptionPosition)
        .options(
            selectinload(OptionPosition.snapshots),
            selectinload(OptionPosition.idea).selectinload(Idea.instrument),
            selectinload(OptionPosition.idea).selectinload(Idea.events),
        )
        .where(OptionPosition.status == "open")
        .order_by(OptionPosition.id)
    )
    if idea_id is not None:
        stmt = stmt.where(OptionPosition.idea_id == idea_id)
    return list(db.execute(stmt).scalars().all())


def _has_snapshot(pos: OptionPosition, as_of: date) -> bool:
    return any(s.as_of == as_of for s in pos.snapshots)


def _underlying_close(db: Session, instrument_id: int, as_of: date) -> float | None:
    bars = prices.bars(db, instrument_id, as_of, as_of)
    if bars:
        return bars[0].close
    rt = prices.bars(db, instrument_id, as_of, as_of, source="realtime")
    return rt[0].close if rt else None


def _add_snapshot(
    db: Session, pos: OptionPosition, as_of: date, q: dict[str, Any], spot: float | None, source: str
) -> OptionSnapshot:
    pct, abs_ = pnl(pos.entry_debit, q["value"], pos.contracts)
    snap = OptionSnapshot(
        position_id=pos.id,
        as_of=as_of,
        value=q["value"],
        bid=q.get("bid"),
        ask=q.get("ask"),
        pnl_pct=pct,
        pnl_abs=abs_,
        spot=spot,
        iv=q.get("iv"),
        delta=q.get("delta"),
        theta=q.get("theta"),
        legs_json=q.get("legs", []),
        source=source,
    )
    db.add(snap)
    pos.snapshots.append(snap)
    pos.last_value, pos.last_value_as_of = q["value"], as_of
    if pos.status == "open":
        pos.pnl_pct, pos.pnl_abs = pct, abs_
    return snap


def _close(db: Session, pos: OptionPosition, d: ExitDecision, source: str) -> None:
    pos.status = "closed"
    pos.exit_reason = d.reason
    pos.exit_value = d.value
    pos.exit_as_of = d.on
    pos.exit_source = source
    pos.closed_at = _now()
    pos.pnl_pct, pos.pnl_abs = d.pnl_pct, d.pnl_abs
    db.add(
        ResolutionEvent(
            idea_id=pos.idea_id,
            position_id=pos.id,
            event_type="position_closed",
            price=d.value,
            note=f"{pos.name} closed: {d.reason} at {d.value:g} ({d.pnl_pct:+.1f}% on premium). {d.detail}",
            occurred_on=d.on or _now().date(),
        )
    )


def apply_exit_rules(db: Session, pos: OptionPosition) -> ExitDecision:
    """Re-run the exit decision over every stored mark and persist a close when one trips. Deterministic; safe to
    call repeatedly."""
    idea = pos.idea
    resolved_on, reason = idea_terminal_date(idea)
    rules = ExitRules(
        entry_debit=pos.entry_debit,
        contracts=pos.contracts,
        take_profit_pct=pos.take_profit_pct,
        stop_loss_pct=pos.stop_loss_pct,
        time_stop_date=pos.time_stop_date,
        expiry=pos.expiry,
    )
    marks = [Mark(s.as_of, s.value, s.source) for s in pos.snapshots]
    d = decide_exit(rules, marks, resolved_on, reason)
    if d.status == "closed" and pos.status == "open":
        src = next((s.source for s in pos.snapshots if s.as_of == d.on), "chain_mid")
        _close(db, pos, d, src)
        db.commit()
    return d


# --- daily marks ------------------------------------------------------------------------------------------------------


def mark_positions(
    db: Session, client: EODHDClient | None = None, today: date | None = None, idea_id: int | None = None
) -> dict[str, Any]:
    """The daily position job. Per instrument with an open position: one band request per right (stored into
    chain_snapshots), per-contract requests for legs the band missed, one mark per position for the chain's record
    date, then the exit rules. Expired structures settle at intrinsic from the underlying's EOD close. EODHD failures
    are reported per position; nothing is invented."""
    client = client or EODHDClient()
    today = today or _now().date()
    summary: dict[str, Any] = {"positions_checked": 0, "marked": [], "closed": [], "errors": [], "chain_requests": 0}
    positions = open_positions(db, idea_id)
    by_inst: dict[int, list[OptionPosition]] = {}
    for p in positions:
        by_inst.setdefault(p.idea.instrument_id, []).append(p)
    for inst_id, group in by_inst.items():
        inst = group[0].idea.instrument
        live: list[OptionPosition] = [p for p in group if p.expiry >= today]
        expired: list[OptionPosition] = [p for p in group if p.expiry < today]
        rec: date | None = None
        quotes: dict[str, Any] = {}
        if live:
            try:
                spot, _at, _src = prices.stamp_entry(db, inst, client)
                levels = [float(lg["strike"]) for p in live for lg in p.legs_json]
                rows, meta = chains.fetch_band(
                    client, inst.symbol.rsplit(".", 1)[0], spot, today, {"call", "put"}, levels
                )
                summary["chain_requests"] += meta["requests"]
                rec = chains.record_date(rows) or today
                prices.ensure_eod(db, inst, rec - timedelta(days=7), today, client)
                rec_spot = _underlying_close(db, inst_id, rec) or (spot if rec == today else None)
                chains.store_band(db, inst_id, rows, rec, rec_spot)
                quotes = {r.contract: r for r in rows}
                missing = sorted({lg["contract"] for p in live for lg in p.legs_json} - set(quotes))
                if missing:
                    extra, n = chains.fetch_contracts(client, missing)
                    summary["chain_requests"] += n
                    chains.store_rows(db, inst_id, extra, rec, rec_spot, source="contract")
                    quotes.update({r.contract: r for r in extra})
            except EODHDError as exc:
                db.rollback()
                for p in live:
                    summary["errors"].append({"position_id": p.id, "idea_id": p.idea_id, **exc.to_dict()})
                live = []
        for p in live:
            summary["positions_checked"] += 1
            try:
                if rec is not None and not _has_snapshot(p, rec):
                    q = structure_quote(p.legs_json, quotes)
                    if q is None:
                        summary["errors"].append(
                            {"position_id": p.id, "idea_id": p.idea_id, "error": "a leg has no usable mid quote"}
                        )
                    else:
                        _add_snapshot(db, p, rec, q, _underlying_close(db, inst_id, rec), "chain_mid")
                        db.commit()
                        summary["marked"].append({"position_id": p.id, "as_of": rec.isoformat(), "value": q["value"]})
                d = apply_exit_rules(db, p)
                if d.status == "closed":
                    summary["closed"].append(
                        {
                            "position_id": p.id,
                            "idea_id": p.idea_id,
                            "reason": d.reason,
                            "on": d.on.isoformat() if d.on else None,
                        }
                    )
            except EODHDError as exc:
                db.rollback()
                summary["errors"].append({"position_id": p.id, "idea_id": p.idea_id, **exc.to_dict()})
        for p in expired:
            summary["positions_checked"] += 1
            try:
                settle_expired(db, p, client)
                d = apply_exit_rules(db, p)
                if d.status == "closed":
                    summary["closed"].append(
                        {
                            "position_id": p.id,
                            "idea_id": p.idea_id,
                            "reason": d.reason,
                            "on": d.on.isoformat() if d.on else None,
                        }
                    )
            except EODHDError as exc:
                db.rollback()
                summary["errors"].append({"position_id": p.id, "idea_id": p.idea_id, **exc.to_dict()})
    return summary


def settle_expired(db: Session, pos: OptionPosition, client: EODHDClient | None) -> OptionSnapshot | None:
    """After expiry the contracts vanish from the chain: settle at the payoff with the underlying's EOD close on the
    last trading day at or before the expiry. Raises EODHDError when that bar cannot be fetched."""
    inst = pos.idea.instrument
    prices.ensure_eod(db, inst, pos.expiry - timedelta(days=7), pos.expiry, client)
    bars = prices.bars(db, inst.id, pos.expiry - timedelta(days=7), pos.expiry)
    if not bars:
        raise EODHDError(f"no EOD close for {inst.symbol} on or before the {pos.expiry} expiry", path="/eod")
    bar = bars[-1]
    if _has_snapshot(pos, bar.as_of) and any(s.source == "expiry_intrinsic" for s in pos.snapshots):
        return None
    if _has_snapshot(pos, bar.as_of):  # a chain mark exists for that day; the settlement replaces it
        snap = next(s for s in pos.snapshots if s.as_of == bar.as_of)
        db.delete(snap)
        pos.snapshots.remove(snap)
        db.flush()
    value = intrinsic_value(pos.legs_json, bar.close)
    q = {
        "value": value,
        "bid": value,
        "ask": value,
        "legs": [{"contract": lg["contract"], "intrinsic_at": bar.close} for lg in pos.legs_json],
    }
    snap = _add_snapshot(db, pos, bar.as_of, q, bar.close, "expiry_intrinsic")
    db.commit()
    return snap


# --- take / close -----------------------------------------------------------------------------------------------------


def candidate_from_analysis(analysis: OptionAnalysis, name: str | None, rank: int | None) -> dict[str, Any] | None:
    for c in analysis.candidates_json or []:
        if (name and c.get("name") == name) or (rank and c.get("rank") == rank):
            return c
    return None


def open_position(
    db: Session,
    idea: Idea,
    analysis: OptionAnalysis,
    candidate: dict[str, Any],
    body: Any,
    values: dict[str, Any],
    client: EODHDClient | None = None,
    today: date | None = None,
) -> OptionPosition:
    """Take an expression: fresh quotes for every leg (one request each), entry at the structure mid or the stated
    fill, contracts from the idea's capital unless given, exit rules from the body (prefilled from Settings by the
    UI). Raises EODHDError (nothing stored) when a leg cannot be quoted, ValueError for an unusable candidate."""
    client = client or EODHDClient()
    today = today or _now().date()
    legs = [
        {
            "contract": lg["contract"],
            "expiry": lg["expiry"],
            "strike": lg["strike"],
            "right": lg["right"],
            "side": lg["side"],
            "qty": lg.get("qty", 1),
        }
        for lg in candidate.get("legs", [])
    ]
    if not legs or candidate.get("debit") is None:
        raise ValueError("candidate has no priceable legs")
    expiry = date.fromisoformat(candidate["expiry"])
    if expiry < today:
        raise ValueError(f"the candidate expired on {expiry}")
    inst = idea.instrument
    quotes, n_calls = chains.fetch_contracts(client, [lg["contract"] for lg in legs])
    rec = chains.record_date(quotes) or today
    spot, spot_at, _src = prices.stamp_entry(db, inst, client)
    q = structure_quote(legs, {r.contract: r for r in quotes})
    if q is None or q["value"] <= 0:
        raise ValueError("no usable mid quote for every leg; nothing stored")
    chains.store_rows(db, inst.id, quotes, rec, spot if rec == spot_at.date() else None, source="contract")
    for lg, r in zip(legs, quotes, strict=True):
        at = chains.quote_timestamps([r])[0]
        lg.update(
            {
                "entry_bid": r.bid,
                "entry_ask": r.ask,
                "entry_mid": r.mid,
                "entry_iv": r.iv,
                "entry_oi": r.oi,
                "entry_quote_at": at.isoformat() if at else None,
            }
        )
    fill = getattr(body, "fill_price", None)
    entry_debit = float(fill) if fill else q["value"]
    cost = entry_debit * 100.0
    contracts = getattr(body, "contracts", None) or int(float(idea.capital_assigned) // cost)
    if contracts <= 0:
        raise ValueError(
            f"the idea's capital {idea.capital_assigned:g} does not cover one contract at {cost:.2f}; "
            "state a contract count"
        )
    tp = getattr(body, "take_profit_pct", None) or float(values.get("default_take_profit_pct", 100))
    sl = getattr(body, "stop_loss_pct", None) or float(values.get("default_stop_loss_pct", 50))
    ts_days = getattr(body, "time_stop_days_before_expiry", None)
    if ts_days is None:
        ts_days = int(values.get("default_time_stop_days_before_expiry", 5))
    pos = OptionPosition(
        idea_id=idea.id,
        analysis_id=analysis.id,
        name=candidate["name"],
        structure=candidate["structure"],
        kind=candidate["kind"],
        legs_json=legs,
        expiry=expiry,
        contracts=contracts,
        entry_debit=round(entry_debit, 4),
        entry_cost=round(contracts * cost, 2),
        entry_as_of=chains.quote_timestamps(quotes)[0] or spot_at,
        entry_source="fill" if fill else "chain_mid",
        entry_spot=spot,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        time_stop_days_before_expiry=ts_days,
        time_stop_date=expiry - timedelta(days=ts_days),
        note=getattr(body, "note", None),
    )
    db.add(pos)
    db.flush()
    _add_snapshot(db, pos, rec, q, spot if rec == spot_at.date() else _underlying_close(db, inst.id, rec), "chain_mid")
    db.add(
        ResolutionEvent(
            idea_id=idea.id,
            position_id=pos.id,
            event_type="position_opened",
            price=q["value"],
            note=(
                f"{pos.name} opened: {contracts} contract{'s' if contracts != 1 else ''} at {entry_debit:.2f} "
                f"({pos.entry_source}), stop {sl:g}%, take profit {tp:g}%, time stop {pos.time_stop_date}"
            ),
            occurred_on=rec,
        )
    )
    db.commit()
    db.refresh(pos)
    log.info("opened position %d on idea %d (%s, %d chain requests)", pos.id, idea.id, pos.name, n_calls + 0)
    return pos


def close_manually(db: Session, pos: OptionPosition, exit_price: float | None, note: str | None) -> OptionPosition:
    """Close at the stated fill, else at the latest stored mark. 422 upstream when neither exists."""
    if exit_price is not None:
        value, on, src = float(exit_price), _now().date(), "fill"
    elif pos.snapshots:
        last = max(pos.snapshots, key=lambda s: s.as_of)
        value, on, src = last.value, last.as_of, last.source
    else:
        raise ValueError("no stored mark to close at; state an exit price")
    pct, abs_ = pnl(pos.entry_debit, value, pos.contracts)
    d = ExitDecision("closed", "manual", on, value, pct, abs_, note or "closed manually")
    _close(db, pos, d, src)
    if note:
        pos.note = f"{pos.note + ' ' if pos.note else ''}{note}".strip()
    db.commit()
    db.refresh(pos)
    return pos


# --- serialization ----------------------------------------------------------------------------------------------------


def to_summary(pos: OptionPosition, hide_dollars: bool) -> PositionSummary:
    return PositionSummary(
        id=pos.id,
        name=pos.name,
        kind=pos.kind,
        status=pos.status,
        exit_reason=pos.exit_reason,
        expiry=pos.expiry,
        contracts=None if hide_dollars else pos.contracts,
        pnl_pct=pos.pnl_pct,
        pnl_abs=None if hide_dollars else pos.pnl_abs,
        last_value=pos.last_value,
        last_value_as_of=pos.last_value_as_of,
    )


def to_out(pos: OptionPosition, hide_dollars: bool) -> PositionOut:
    return PositionOut(
        id=pos.id,
        idea_id=pos.idea_id,
        analysis_id=pos.analysis_id,
        name=pos.name,
        structure=pos.structure,
        kind=pos.kind,
        legs=pos.legs_json,
        expiry=pos.expiry,
        contracts=None if hide_dollars else pos.contracts,
        entry_debit=pos.entry_debit,
        entry_cost=None if hide_dollars else pos.entry_cost,
        entry_as_of=pos.entry_as_of,
        entry_source=pos.entry_source,
        entry_spot=pos.entry_spot,
        take_profit_pct=pos.take_profit_pct,
        stop_loss_pct=pos.stop_loss_pct,
        time_stop_days_before_expiry=pos.time_stop_days_before_expiry,
        time_stop_date=pos.time_stop_date,
        status=pos.status,
        exit_reason=pos.exit_reason,
        exit_value=pos.exit_value,
        exit_as_of=pos.exit_as_of,
        exit_source=pos.exit_source,
        closed_at=pos.closed_at,
        pnl_pct=pos.pnl_pct,
        pnl_abs=None if hide_dollars else pos.pnl_abs,
        last_value=pos.last_value,
        last_value_as_of=pos.last_value_as_of,
        note=pos.note,
        snapshots=[
            OptionSnapshotOut(
                as_of=s.as_of,
                value=s.value,
                bid=s.bid,
                ask=s.ask,
                pnl_pct=s.pnl_pct,
                pnl_abs=None if hide_dollars else s.pnl_abs,
                spot=s.spot,
                iv=s.iv,
                delta=s.delta,
                theta=s.theta,
                source=s.source,
                legs=s.legs_json,
                created_at=s.created_at,
            )
            for s in sorted(pos.snapshots, key=lambda s: s.as_of)
        ],
        dollars_hidden=hide_dollars,
        created_at=pos.created_at,
        updated_at=pos.updated_at,
    )
