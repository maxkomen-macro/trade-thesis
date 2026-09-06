"""Deterministic option pricing. Pure functions; no I/O, no LLM (CLAUDE.md rule 3).

Black-Scholes-Merton with a continuous dividend yield, Greeks, implied vol, multi-leg structures, breakevens,
market-implied probability of profit, the scenario grid, and days-of-theta. Tested in api/tests/test_pricing.py
against textbook values, put-call parity, an independent normal CDF, finite differences, and brute-force scans.

Conventions
  * prices are per share; the selector multiplies by 100 per contract
  * T is in years = calendar days / 365; T <= 0 means the option is at (or past) expiry and is worth intrinsic
  * sigma, r, q are decimals (0.33 = 33%)
  * theta is per calendar day (negative = decay); vega and rho are per one point (0.01) of vol / rate
  * every leg is priced with its own implied vol, held constant across the grid ("IV held" assumption in the UI)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

Right = Literal["call", "put"]

DAYS_PER_YEAR = 365.0
_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


# --- normal distribution ---------------------------------------------------------------------------------------


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT2PI


# --- Black-Scholes-Merton ----------------------------------------------------------------------------------------


def _check(S: float, K: float, right: str) -> None:
    if not (S > 0) or not (K > 0):
        raise ValueError(f"spot and strike must be positive (got S={S}, K={K})")
    if right not in ("call", "put"):
        raise ValueError(f"right must be 'call' or 'put' (got {right!r})")


def intrinsic(S: float, K: float, right: Right) -> float:
    return max(S - K, 0.0) if right == "call" else max(K - S, 0.0)


def d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> tuple[float, float]:
    vs = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vs
    return d1, d1 - vs


def bs_price(S: float, K: float, T: float, r: float, sigma: float, right: Right, q: float = 0.0) -> float:
    """Black-Scholes-Merton value per share. T <= 0 -> intrinsic; sigma <= 0 -> discounted forward intrinsic."""
    _check(S, K, right)
    if T <= 0:
        return intrinsic(S, K, right)
    fwd, kd = S * math.exp(-q * T), K * math.exp(-r * T)
    if sigma <= 0:
        return max(fwd - kd, 0.0) if right == "call" else max(kd - fwd, 0.0)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    if right == "call":
        return fwd * norm_cdf(d1) - kd * norm_cdf(d2)
    return kd * norm_cdf(-d2) - fwd * norm_cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1 vol point
    rho: float  # per 1 rate point


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, right: Right, q: float = 0.0) -> Greeks:
    _check(S, K, right)
    if T <= 0 or sigma <= 0:
        itm = intrinsic(S, K, right) > 0
        delta = (1.0 if itm else 0.0) if right == "call" else (-1.0 if itm else 0.0)
        return Greeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)
    d1, d2 = d1_d2(S, K, T, r, sigma, q)
    sq = math.sqrt(T)
    eq, er = math.exp(-q * T), math.exp(-r * T)
    pdf = norm_pdf(d1)
    gamma = eq * pdf / (S * sigma * sq)
    vega = S * eq * pdf * sq
    common = -S * eq * pdf * sigma / (2.0 * sq)
    if right == "call":
        delta = eq * norm_cdf(d1)
        theta = common - r * K * er * norm_cdf(d2) + q * S * eq * norm_cdf(d1)
        rho = K * T * er * norm_cdf(d2)
    else:
        delta = eq * (norm_cdf(d1) - 1.0)
        theta = common + r * K * er * norm_cdf(-d2) - q * S * eq * norm_cdf(-d1)
        rho = -K * T * er * norm_cdf(-d2)
    return Greeks(delta=delta, gamma=gamma, theta=theta / DAYS_PER_YEAR, vega=vega / 100.0, rho=rho / 100.0)


def implied_vol(
    price: float, S: float, K: float, T: float, r: float, right: Right, q: float = 0.0, hi: float = 10.0
) -> float | None:
    """Vol that reproduces `price`, by bisection. None when no arbitrage-free vol does (price at or below the
    zero-vol bound, non-positive, or above the vol=hi value)."""
    _check(S, K, right)
    if T <= 0 or price <= 0:
        return None
    lo_price = bs_price(S, K, T, r, 0.0, right, q)
    hi_price = bs_price(S, K, T, r, hi, right, q)
    if price <= lo_price + 1e-12 or price > hi_price:
        return None
    lo, up = 0.0, hi
    for _ in range(200):
        mid = 0.5 * (lo + up)
        if bs_price(S, K, T, r, mid, right, q) > price:
            up = mid
        else:
            lo = mid
        if up - lo < 1e-10:
            break
    return 0.5 * (lo + up)


# --- structures ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """One option leg. `side` +1 long / -1 short; `qty` is the ratio within the structure (1 for everything we build).
    Market fields (bid/ask/mid/oi/volume/delta) are copied from the stored chain row; `iv` is the model input,
    `iv_source` says where it came from ("chain" or "realized_20d" when the chain had no IV)."""

    right: Right
    strike: float
    expiry: date
    side: int = 1
    qty: int = 1
    iv: float | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    contract: str | None = None
    oi: int | None = None
    volume: int | None = None
    delta: float | None = None
    iv_source: str = "chain"


def year_fraction(as_of: date, expiry: date) -> float:
    return max((expiry - as_of).days, 0) / DAYS_PER_YEAR


def _common_expiry(legs: list[Leg]) -> date:
    if not legs:
        raise ValueError("structure has no legs")
    exp = {lg.expiry for lg in legs}
    if len(exp) != 1:
        raise ValueError("payoff and breakevens assume one expiry per structure")
    return legs[0].expiry


def leg_value(leg: Leg, S: float, as_of: date, r: float, q: float = 0.0) -> float:
    if leg.iv is None:
        raise ValueError(f"leg {leg.contract or leg.strike} has no implied vol; fill the fallback before pricing")
    return bs_price(S, leg.strike, year_fraction(as_of, leg.expiry), r, leg.iv, leg.right, q)


def structure_value(legs: list[Leg], S: float, as_of: date, r: float, q: float = 0.0) -> float:
    """Model value of the structure per share (a debit structure has a positive value)."""
    return sum(lg.side * lg.qty * leg_value(lg, S, as_of, r, q) for lg in legs)


def structure_greeks(legs: list[Leg], S: float, as_of: date, r: float, q: float = 0.0) -> Greeks:
    tot = [0.0, 0.0, 0.0, 0.0, 0.0]
    for lg in legs:
        if lg.iv is None:
            raise ValueError("leg without implied vol")
        g = bs_greeks(S, lg.strike, year_fraction(as_of, lg.expiry), r, lg.iv, lg.right, q)
        w = lg.side * lg.qty
        for i, v in enumerate((g.delta, g.gamma, g.theta, g.vega, g.rho)):
            tot[i] += w * v
    return Greeks(*tot)


def _sum_quotes(legs: list[Leg], long_field: str, short_field: str) -> float | None:
    total = 0.0
    for lg in legs:
        v = getattr(lg, long_field if lg.side > 0 else short_field)
        if v is None:
            return None
        total += lg.side * lg.qty * float(v)
    return total


def structure_mid(legs: list[Leg]) -> float | None:
    return _sum_quotes(legs, "mid", "mid")


def structure_bid(legs: list[Leg]) -> float | None:
    """What the structure fetches if sold now: long legs at the bid, short legs bought back at the ask."""
    return _sum_quotes(legs, "bid", "ask")


def structure_ask(legs: list[Leg]) -> float | None:
    """What the structure costs if bought now: long legs at the ask, short legs sold at the bid."""
    return _sum_quotes(legs, "ask", "bid")


def payoff_at_expiry(legs: list[Leg], S_T: float) -> float:
    _common_expiry(legs)
    return sum(lg.side * lg.qty * intrinsic(S_T, lg.strike, lg.right) for lg in legs)


def breakevens(legs: list[Leg], debit: float) -> list[float]:
    """Underlying prices at expiry where the structure's P&L (payoff - debit) is zero, ascending.
    The payoff is piecewise linear with kinks at the strikes, so each segment and the two rays are solved exactly."""
    _common_expiry(legs)
    strikes = sorted({lg.strike for lg in legs})

    def pnl(S: float) -> float:
        return payoff_at_expiry(legs, S) - debit

    roots: list[float] = []
    points = [0.0, *strikes]
    for a, b in zip(points[:-1], points[1:], strict=True):
        pa, pb = pnl(a), pnl(b)
        if pa == 0.0:
            roots.append(a)
        if pa * pb < 0:
            roots.append(a + (b - a) * (-pa) / (pb - pa))
    kmax = strikes[-1]
    p_kmax = pnl(kmax)
    if p_kmax == 0.0:
        roots.append(kmax)
    slope_right = sum(lg.side * lg.qty for lg in legs if lg.right == "call")
    if slope_right != 0 and p_kmax * slope_right < 0:
        roots.append(kmax - p_kmax / slope_right)
    out: list[float] = []
    for x in sorted(roots):
        if x > 0 and (not out or abs(x - out[-1]) > 1e-9):
            out.append(x)
    return out


def probability_of_profit(
    legs: list[Leg], debit: float, S: float, as_of: date, r: float, sigma: float, q: float = 0.0
) -> float:
    """Market-implied probability that the structure finishes above its cost: risk-neutral lognormal terminal
    distribution at the structure's expiry with vol `sigma`, integrated over the profitable price intervals."""
    _check(S, 1.0, "call")
    T = year_fraction(as_of, _common_expiry(legs))
    if T <= 0 or sigma <= 0:
        return 1.0 if payoff_at_expiry(legs, S) - debit > 0 else 0.0
    vs = sigma * math.sqrt(T)
    drift = (r - q - 0.5 * sigma * sigma) * T

    def cdf(x: float) -> float:  # P(S_T <= x)
        if x <= 0:
            return 0.0
        if math.isinf(x):
            return 1.0
        return norm_cdf((math.log(x / S) - drift) / vs)

    bes = breakevens(legs, debit)
    edges = [0.0, *bes, math.inf]
    total = 0.0
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        probe = (a + b) / 2 if not math.isinf(b) else (a * 2 if a > 0 else S * 2)
        if payoff_at_expiry(legs, probe) - debit > 0:
            total += cdf(b) - cdf(a)
    return min(1.0, max(0.0, total))


# --- scenario grid ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    value: float  # model value per share
    pnl: float  # value - debit, per share
    ret_pct: float | None  # pnl / debit in %, None when debit is not positive


def scenario_grid(
    legs: list[Leg], debit: float, prices: list[float], dates: list[date], r: float, q: float = 0.0
) -> list[list[Cell]]:
    """Rows = underlying prices, columns = valuation dates. Each leg keeps its own IV across the grid."""
    grid: list[list[Cell]] = []
    for S in prices:
        row: list[Cell] = []
        for d in dates:
            v = structure_value(legs, S, d, r, q)
            pnl = v - debit
            row.append(Cell(value=v, pnl=pnl, ret_pct=(pnl / debit * 100.0) if debit > 0 else None))
        grid.append(row)
    return grid


def grid_prices(
    stop: float | None, target: float, spot: float, direction: str, steps: int = 9, overshoot: float = 0.05
) -> list[float]:
    """Row prices: from the stop level to 5% past the target, `steps` values, with the target and the spot snapped
    in exactly. Without a stop the grid starts one target-distance on the wrong side of spot. Ordered stop -> past
    target."""
    bearish = target < spot if target != spot else direction in ("down", "underperform")
    end = target * (1.0 - overshoot) if bearish else target * (1.0 + overshoot)
    start = stop if stop is not None else spot + (spot - target)
    if steps < 3:
        raise ValueError("grid needs at least 3 rows")
    pts = [start + (end - start) * i / (steps - 1) for i in range(steps)]
    anchors: list[int] = []
    for anchor in (target, spot):
        if any(abs(p - anchor) < 1e-9 for p in pts):
            anchors.append(min(range(steps), key=lambda i: abs(pts[i] - anchor)))
            continue
        free = [i for i in range(steps) if i not in anchors]
        i = min(free, key=lambda i: abs(pts[i] - anchor))
        pts[i] = anchor
        anchors.append(i)
    pts = [round(p, 4) for p in pts]
    return sorted(set(pts), reverse=bearish)


def grid_dates(entry: date, window_end: date, expiry: date) -> list[date]:
    """Columns: entry, then weekly through the window end, then the window end, then expiry. An expiry before the
    window end truncates the list (nothing to value after the option has expired)."""
    out: list[date] = []
    d = entry
    while d < window_end:
        out.append(d)
        d = d.fromordinal(d.toordinal() + 7)
    out.append(window_end)
    out = [x for x in out if x < expiry] + [expiry]
    dedup: list[date] = []
    for x in out:
        if not dedup or x != dedup[-1]:
            dedup.append(x)
    return dedup


def days_of_theta(
    legs: list[Leg], debit: float, S: float, start: date, r: float, stop_loss_pct: float, q: float = 0.0
) -> int | None:
    """Days the underlying can sit at `S` before the structure's model value falls to debit x (1 - stop_loss_pct/100).
    None when that never happens before expiry (the structure keeps its value at this spot)."""
    threshold = debit * (1.0 - stop_loss_pct / 100.0)
    horizon = (min(lg.expiry for lg in legs) - start).days
    for d in range(1, max(horizon, 0) + 1):
        if structure_value(legs, S, start.fromordinal(start.toordinal() + d), r, q) <= threshold + 1e-12:
            return d
    return None
