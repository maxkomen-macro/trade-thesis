"""Deterministic resolution engine. Pure functions over price bars; no I/O, no LLM.

Per-run order for each open idea (see run_resolver in jobs):
    refresh bars -> check invalidation -> check success -> check window expiry -> write a progress event
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from api.services.rules import first_level

NEGATIVE_DIRECTIONS = ("down", "underperform")


@dataclass(frozen=True)
class Bar:
    as_of: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None


@dataclass(frozen=True)
class Context:
    direction: str
    entry_price: float
    entry_date: date
    window_start: date
    window_end: date
    benchmark_entry: float | None = None


@dataclass(frozen=True)
class Hit:
    hit: bool
    on: date | None = None
    price: float | None = None
    detail: str = ""


@dataclass
class Decision:
    status: str  # open | right | wrong | expired
    reason: str | None = None
    on: date | None = None
    price: float | None = None
    benchmark_price: float | None = None
    pnl_pct: float | None = None
    pnl_abs: float | None = None
    direction_right: bool | None = None
    spread_at_window_end_pct: float | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


# --- math ------------------------------------------------------------------------------------------------------


def sign(direction: str) -> int:
    return -1 if direction in NEGATIVE_DIRECTIONS else 1


def signed_return(
    direction: str,
    entry: float,
    last: float,
    benchmark_entry: float | None = None,
    benchmark_last: float | None = None,
) -> float | None:
    """Decimal return of the thesis (0.05 = +5%). Relative ideas use the return spread. `range` has no sign: None."""
    if direction == "range":
        return None
    if entry <= 0:
        return None
    ret = last / entry - 1.0
    if direction in ("outperform", "underperform"):
        if not benchmark_entry or benchmark_last is None:
            return None
        ret -= benchmark_last / benchmark_entry - 1.0
    return sign(direction) * ret


def spread_pct(
    direction: str, entry: float, last: float, benchmark_entry: float | None, benchmark_last: float | None
) -> float | None:
    """(instrument return - benchmark return) in %, signed by direction. None without benchmark data."""
    if not entry or not benchmark_entry or benchmark_last is None:
        return None
    raw = (last / entry - 1.0) - (benchmark_last / benchmark_entry - 1.0)
    return round(sign(direction) * raw * 100.0, 4)


def hypothetical_pnl(capital: float, signed_ret: float | None) -> tuple[float | None, float | None]:
    """(pnl_pct, pnl_abs) against the assigned capital. This is a paper-portfolio figure."""
    if signed_ret is None:
        return None, None
    return round(signed_ret * 100.0, 4), round(capital * signed_ret, 2)


def in_window(bars: list[Bar], ctx: Context) -> list[Bar]:
    start = max(ctx.entry_date, ctx.window_start)
    return [b for b in bars if start <= b.as_of <= ctx.window_end]


def window_closed(bars: list[Bar], ctx: Context, today: date) -> bool:
    """True once we hold the window-end bar, or the window end has passed (weekend/holiday end date)."""
    if bars and bars[-1].as_of >= ctx.window_end:
        return True
    return today > ctx.window_end


def final_bar(bars: list[Bar], ctx: Context) -> Bar | None:
    inside = in_window(bars, ctx)
    return inside[-1] if inside else None


def _bench_at(bench: list[Bar], as_of: date) -> float | None:
    """Benchmark close on `as_of`, else the last close before it."""
    last = None
    for b in bench:
        if b.as_of > as_of:
            break
        last = b.close
    return last


# --- rule evaluation ---------------------------------------------------------------------------------------------


def evaluate(rule: dict[str, Any], ctx: Context, bars: list[Bar], bench: list[Bar], today: date) -> Hit:
    t = rule.get("type")
    inside = in_window(bars, ctx)
    if t == "level":
        return _eval_level(rule, inside)
    if t == "direction":
        return _eval_direction(ctx, bars, today)
    if t == "pct_move":
        return _eval_pct_move(rule, ctx, inside)
    if t == "relative":
        return _eval_relative(rule, ctx, inside, bench)
    if t in ("all_of", "any_of"):
        hits = [evaluate(r, ctx, bars, bench, today) for r in rule.get("rules", [])]
        if t == "all_of":
            if all(h.hit for h in hits) and hits:
                latest = max(hits, key=lambda h: h.on or date.min)
                return Hit(True, latest.on, latest.price, "all conditions met")
            return Hit(False, detail="not all conditions met")
        firsts = [h for h in hits if h.hit]
        if firsts:
            first = min(firsts, key=lambda h: h.on or date.max)
            return Hit(True, first.on, first.price, first.detail)
        return Hit(False, detail="no condition met")
    return Hit(False, detail=f"unknown rule type {t!r}")


def _eval_level(rule: dict[str, Any], inside: list[Bar]) -> Hit:
    level = float(rule["level"])
    comp = rule["comparator"]
    for b in inside:
        if comp == "close_at_or_below" and b.close <= level:
            return Hit(True, b.as_of, b.close, f"close {b.close:g} <= {level:g}")
        if comp == "close_at_or_above" and b.close >= level:
            return Hit(True, b.as_of, b.close, f"close {b.close:g} >= {level:g}")
        if comp == "touch_at_or_below" and (b.low if b.low is not None else b.close) <= level:
            return Hit(True, b.as_of, level, f"low {b.low if b.low is not None else b.close:g} <= {level:g}")
        if comp == "touch_at_or_above" and (b.high if b.high is not None else b.close) >= level:
            return Hit(True, b.as_of, level, f"high {b.high if b.high is not None else b.close:g} >= {level:g}")
    return Hit(False, detail=f"level {level:g} not reached")


def _eval_direction(ctx: Context, bars: list[Bar], today: date) -> Hit:
    if not window_closed(bars, ctx, today):
        return Hit(False, detail="window still open")
    fb = final_bar(bars, ctx)
    if fb is None:
        return Hit(False, detail="no bars inside the window")
    r = signed_return(ctx.direction, ctx.entry_price, fb.close)
    if r is not None and r > 0:
        return Hit(True, fb.as_of, fb.close, f"window-end close {fb.close:g} vs entry {ctx.entry_price:g}")
    return Hit(
        False, fb.as_of, fb.close, f"window-end close {fb.close:g} on the wrong side of entry {ctx.entry_price:g}"
    )


def _eval_pct_move(rule: dict[str, Any], ctx: Context, inside: list[Bar]) -> Hit:
    target = float(rule["pct"]) / 100.0
    for b in inside:
        r = signed_return(ctx.direction, ctx.entry_price, b.close)
        if r is not None and r >= target:
            return Hit(True, b.as_of, b.close, f"moved {r * 100:+.2f}% (target {rule['pct']:g}%)")
    return Hit(False, detail=f"{rule['pct']:g}% move not reached")


def _eval_relative(rule: dict[str, Any], ctx: Context, inside: list[Bar], bench: list[Bar]) -> Hit:
    spread = float(rule["spread_pct"]) / 100.0
    if not ctx.benchmark_entry:
        return Hit(False, detail="benchmark entry price missing")
    for b in inside:
        bl = _bench_at(bench, b.as_of)
        if bl is None:
            continue
        r = signed_return(ctx.direction, ctx.entry_price, b.close, ctx.benchmark_entry, bl)
        if r is not None and r >= spread:
            return Hit(
                True,
                b.as_of,
                b.close,
                f"spread {r * 100:+.2f}% vs {rule['benchmark']} (target {rule['spread_pct']:g}%)",
            )
    return Hit(False, detail=f"spread of {rule['spread_pct']:g}% vs {rule['benchmark']} not reached")


# --- decision ----------------------------------------------------------------------------------------------------


def decide(
    ctx: Context,
    success_rule: dict[str, Any],
    invalidation_rule: dict[str, Any] | None,
    invalidation_is_note_only: bool,
    capital: float,
    bars: list[Bar],
    bench: list[Bar],
    today: date,
) -> Decision:
    """Apply the resolution order and return the new status plus the events to record."""
    d = Decision(status="open")
    inside = in_window(bars, ctx)
    if not inside:
        return d

    stop = evaluate(invalidation_rule, ctx, bars, bench, today) if invalidation_rule else Hit(False)
    win = evaluate(success_rule, ctx, bars, bench, today)

    # Spread at the window-end bar, known only once the window is closed; independent of how the idea resolves.
    if ctx.benchmark_entry and window_closed(bars, ctx, today):
        fb = inside[-1]
        d.spread_at_window_end_pct = spread_pct(
            ctx.direction, ctx.entry_price, fb.close, ctx.benchmark_entry, _bench_at(bench, fb.as_of)
        )

    def mark(as_of: date, price: float) -> None:
        bl = _bench_at(bench, as_of) if ctx.benchmark_entry else None
        r = signed_return(ctx.direction, ctx.entry_price, price, ctx.benchmark_entry, bl)
        d.pnl_pct, d.pnl_abs = hypothetical_pnl(capital, r)
        d.on, d.price, d.benchmark_price = as_of, price, bl
        d.direction_right = (r is not None and r > 0) if r is not None else None

    # Invalidation first; if both hit, the earlier date wins and a tie goes to the stop (conservative).
    if stop.hit and (not win.hit or (stop.on or date.min) <= (win.on or date.max)):
        if invalidation_is_note_only:
            d.events.append(
                {
                    "event_type": "stop_hit",
                    "occurred_on": stop.on,
                    "price": stop.price,
                    "note": f"note only: {stop.detail}",
                }
            )
        else:
            d.status, d.reason = "wrong", "stop_hit"
            mark(stop.on, stop.price)  # type: ignore[arg-type]
            d.events.append(
                {"event_type": "stop_hit", "occurred_on": stop.on, "price": stop.price, "note": stop.detail}
            )
            return d

    if win.hit:
        d.status, d.reason = "right", "target_hit"
        mark(win.on, win.price)  # type: ignore[arg-type]
        d.events.append({"event_type": "target_hit", "occurred_on": win.on, "price": win.price, "note": win.detail})
        return d

    last = inside[-1]
    mark(last.as_of, last.close)
    if window_closed(bars, ctx, today):
        d.status = "expired"
        d.reason = "expired:direction_right" if d.direction_right else "expired:direction_wrong"
        d.events.append(
            {
                "event_type": "expired",
                "occurred_on": last.as_of,
                "price": last.close,
                "note": f"window ended; {win.detail}; direction {'right' if d.direction_right else 'wrong'}",
            }
        )
        return d

    d.events.append(
        {
            "event_type": "progress",
            "occurred_on": last.as_of,
            "price": last.close,
            "note": f"pnl {d.pnl_pct:+.2f}%" if d.pnl_pct is not None else "pnl n/a",
        }
    )
    return d


def progress_pct(ctx: Context, success_rule: dict[str, Any], last: float | None, today: date) -> tuple[float, str]:
    """Ledger progress bar: price progress toward a level/pct target when one exists, else time elapsed."""
    level = first_level(success_rule)
    if last is not None and level is not None and level != ctx.entry_price:
        frac = (last - ctx.entry_price) / (level - ctx.entry_price)
        return max(0.0, min(100.0, frac * 100.0)), "price"
    if last is not None and success_rule.get("type") == "pct_move":
        r = signed_return(ctx.direction, ctx.entry_price, last)
        if r is not None:
            return max(0.0, min(100.0, r / (float(success_rule["pct"]) / 100.0) * 100.0)), "price"
    total = max(1, (ctx.window_end - ctx.window_start).days)
    elapsed = (min(today, ctx.window_end) - ctx.window_start).days
    return max(0.0, min(100.0, elapsed / total * 100.0)), "time"
