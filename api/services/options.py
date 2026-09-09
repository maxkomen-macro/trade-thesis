"""Options expression selector (Phase 5).

Pipeline for POST /api/ideas/{id}/options:
    spot (EODHD delayed quote, stored as a realtime price snapshot)
    -> 20-day realized vol from stored EOD bars
    -> chain from EODHD UnicornBay (/mp/unicornbay/options/contracts), one page per needed right, strike band
       +-25% of spot widened to cover the target and stop, expiries within 120 days
    -> candidates: long call / long put, vertical debit spreads, and (range ideas or no stated conviction) long
       straddles / strangles, for every expiry that ends after window_end plus the one just before it ("no cushion")
    -> deterministic metrics per candidate with Black-Scholes at each leg's chain IV (api/services/pricing.py)
    -> hard filters (OI >= 100 on every leg, widest leg's bid/ask width <= 10%), score, verdict, top three with
       scenario grids
    -> shares / inverse-ETF comparison
    -> rationale strings: the model rewrites the computed numbers into two or three sentences and may not add a
       figure that is not in its input (checked here; a template string replaces any output that fails)
    -> one option_analyses row

No number here is invented: prices, quotes, IV, OI and volume come from the stored chain rows carried in
candidates_json; the only model assumption is the risk-free rate (settings.risk_free_rate_pct), stated in the UI.
The IV percentile over one year comes from stored chain history (chain_snapshots, api/services/chains.py); until
IV_PERCENTILE_MIN_DAYS record dates exist `iv_percentile_1y` is null and the IV-versus-realized ratio stands in.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.config import settings
from api.db.models import Idea, OptionAnalysis
from api.db.schemas import OptionAnalysisOut
from api.services import chains, prices
from api.services.chains import realized_vol_pct
from api.services.eodhd import EODHDClient, EODHDError
from api.services.pricing import (
    Leg,
    breakevens,
    days_of_theta,
    grid_dates,
    grid_prices,
    payoff_at_expiry,
    probability_of_profit,
    scenario_grid,
    structure_ask,
    structure_bid,
    structure_greeks,
    structure_mid,
    structure_value,
)
from api.services.rules import first_level

log = logging.getLogger("tt.options")

RATIONALE_MODEL = "claude-sonnet-4-6"

# Chain request band (matches the storage band in the spec).
CHAIN_BAND_DAYS = chains.BAND_DAYS
CHAIN_BAND_PCT = chains.BAND_PCT

# Candidate strike bands (fractions of spot / target).
SINGLE_ITM_PCT = 0.10  # long options from 10% in the money ...
SINGLE_BEYOND_TARGET_PCT = 0.05  # ... to 5% beyond the target
SPREAD_LONG_BAND_PCT = 0.07  # long leg of a vertical: within 7% of spot
SPREAD_SHORT_BEYOND_PCT = 0.12  # short leg: at or beyond the target, up to 12% past it
MAX_LONG_STRIKES = 5
MAX_SHORT_STRIKES = 5
MAX_STRADDLE_STRIKES = 3

# Hard filters and scoring. Weights are points of return-on-premium (%) taken off the ranking metric.
FILTERS = {"min_open_interest": 100, "max_spread_width_pct": 10.0}
MIN_RETURN_AT_TARGET_PCT = 50.0
SCORE_WEIGHTS = {
    "spread_width_pct": 2.0,  # per % of bid/ask width
    "theta_week_pct": 1.0,  # per % of premium decaying per week with the underlying at spot
    "iv_percentile_over_50": 0.5,  # per percentile point above the median, once chain history exists
    "iv_rv_ratio_over_1": 50.0,  # per 1.0 of IV/realized above parity (used until the percentile exists)
    "no_cushion": 15.0,  # flat penalty for an expiry before the window end ...
    "uncovered_window": 1.0,  # ... plus the return times the fraction of the window the option does not cover
}

STRUCTURE_KIND = {
    "long_call": "Long call",
    "long_put": "Long put",
    "debit_spread": "debit spread",
    "straddle": "Long straddle",
    "strangle": "Long strangle",
}
# Fidelity option Level 2 (owner): buy calls/puts, covered calls, cash-secured puts, long straddles/strangles,
# spreads up to 4 legs. Everything the selector builds is on this list; short premium is not.
TIER2_ALLOWED = set(STRUCTURE_KIND)
TIER_NOTE = "Fidelity Level 2: long options, long straddles/strangles, and spreads up to four legs"

BEARISH = ("down", "underperform")
BULLISH = ("up", "outperform")


# --- inputs and chain rows ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectorInputs:
    symbol: str  # EODHD symbol, USO.US
    direction: str
    spot: float
    today: date
    window_end: date
    target: float | None
    stop: float | None
    capital: float
    risk_pct: float
    account_size: float
    stop_loss_pct: float
    rate_pct: float
    realized_vol_20d_pct: float | None
    conviction_pct: float | None = None
    catalyst_date: date | None = None
    inverse_symbol: str | None = None
    inverse_leverage: float | None = None
    # 1-year at-the-money IV percentile from chain_snapshots (None until IV_PERCENTILE_MIN_DAYS of history).
    iv_percentile_1y: float | None = None
    iv_percentile_note: str = "needs stored chain history; IV versus 20-day realized shown instead"

    @property
    def bare(self) -> str:
        return self.symbol.rsplit(".", 1)[0]

    @property
    def rate(self) -> float:
        return self.rate_pct / 100.0

    @property
    def bearish(self) -> bool:
        return self.direction in BEARISH

    @property
    def wants_straddles(self) -> bool:
        return self.direction == "range" or self.conviction_pct is None

    @property
    def rights(self) -> set[str]:
        if self.direction == "range":
            return {"call", "put"}
        base = {"put"} if self.bearish else {"call"}
        return base | ({"call", "put"} if self.wants_straddles else set())


# Chain rows, normalization, timestamps and the band request live in api/services/chains.py (Phase 6 shares them
# with the daily marks). The names below are kept for the selector's callers and tests.
ChainRow = chains.Quote
normalize_chain = chains.normalize
chain_timestamps = chains.quote_timestamps
_parse_ts = chains.parse_ts


def fetch_chain(
    client: EODHDClient,
    inp: SelectorInputs,
    band_days: int = CHAIN_BAND_DAYS,
    band_pct: float = CHAIN_BAND_PCT,
) -> tuple[list[ChainRow], dict[str, Any]]:
    """Live chain within the storage band, one request per needed right. Raises EODHDError on any failure or an
    empty chain; nothing is synthesized."""
    return chains.fetch_band(
        client, inp.bare, inp.spot, inp.today, inp.rights, [inp.target, inp.stop], band_days, band_pct
    )


# --- candidate generation -------------------------------------------------------------------------------------------


def select_expiries(expiries: list[date], window_end: date) -> list[tuple[date, int]]:
    """Every expiry on or after the window end, plus the one immediately before it. (expiry, cushion_days)."""
    exps = sorted(set(expiries))
    before = [e for e in exps if e < window_end]
    after = [e for e in exps if e >= window_end]
    chosen = ([before[-1]] if before else []) + after
    return [(e, (e - window_end).days) for e in chosen]


def _leg(row: ChainRow, side: int) -> Leg:
    return Leg(
        right=row.right,  # type: ignore[arg-type]
        strike=row.strike,
        expiry=row.expiry,
        side=side,
        qty=1,
        iv=row.iv,
        bid=row.bid,
        ask=row.ask,
        mid=row.mid,
        contract=row.contract,
        oi=row.oi,
        volume=row.volume,
        delta=row.delta,
        iv_source="chain" if row.iv is not None else "missing",
    )


def _nearest(rows: list[ChainRow], to: float, n: int) -> list[ChainRow]:
    return sorted(rows, key=lambda r: abs(r.strike - to))[:n]


@dataclass
class RawCandidate:
    structure: str
    legs: list[Leg]
    expiry: date
    cushion_days: int
    notes: list[str] = field(default_factory=list)


def generate_candidates(inp: SelectorInputs, rows: list[ChainRow]) -> list[RawCandidate]:
    """Enumerate structures per the spec. Directional ideas need a target level; without one only straddles and
    strangles (when allowed) are built."""
    by_exp: dict[date, dict[str, list[ChainRow]]] = {}
    for r in rows:
        by_exp.setdefault(r.expiry, {}).setdefault(r.right, []).append(r)
    out: list[RawCandidate] = []
    spot, target = inp.spot, inp.target
    directional = inp.direction in BEARISH + BULLISH
    for expiry, cushion in select_expiries(list(by_exp), inp.window_end):
        calls = sorted(by_exp[expiry].get("call", []), key=lambda r: r.strike)
        puts = sorted(by_exp[expiry].get("put", []), key=lambda r: r.strike)
        if directional and target is not None:
            if inp.bearish:
                lo, hi = min(spot, target) * (1 - SINGLE_BEYOND_TARGET_PCT), spot * (1 + SINGLE_ITM_PCT)
                singles = [r for r in puts if lo <= r.strike <= hi]
                longs = _nearest(
                    [r for r in puts if abs(r.strike / spot - 1) <= SPREAD_LONG_BAND_PCT], spot, MAX_LONG_STRIKES
                )
                shorts = _nearest(
                    [r for r in puts if target * (1 - SPREAD_SHORT_BEYOND_PCT) <= r.strike <= target],
                    target,
                    MAX_SHORT_STRIKES,
                )
                single_kind = "long_put"
            else:
                lo, hi = spot * (1 - SINGLE_ITM_PCT), max(spot, target) * (1 + SINGLE_BEYOND_TARGET_PCT)
                singles = [r for r in calls if lo <= r.strike <= hi]
                longs = _nearest(
                    [r for r in calls if abs(r.strike / spot - 1) <= SPREAD_LONG_BAND_PCT], spot, MAX_LONG_STRIKES
                )
                shorts = _nearest(
                    [r for r in calls if target <= r.strike <= target * (1 + SPREAD_SHORT_BEYOND_PCT)],
                    target,
                    MAX_SHORT_STRIKES,
                )
                single_kind = "long_call"
            for r in singles:
                out.append(RawCandidate(single_kind, [_leg(r, +1)], expiry, cushion))
            for lg in longs:
                for sh in shorts:
                    if (inp.bearish and sh.strike < lg.strike) or (not inp.bearish and sh.strike > lg.strike):
                        out.append(RawCandidate("debit_spread", [_leg(lg, +1), _leg(sh, -1)], expiry, cushion))
        if inp.wants_straddles and calls and puts:
            put_by_k = {r.strike: r for r in puts}
            for c in _nearest([c for c in calls if c.strike in put_by_k], spot, MAX_STRADDLE_STRIKES):
                out.append(RawCandidate("straddle", [_leg(c, +1), _leg(put_by_k[c.strike], +1)], expiry, cushion))
            otm_calls = sorted([c for c in calls if spot < c.strike <= spot * 1.10], key=lambda r: r.strike)
            otm_puts = sorted([p for p in puts if spot * 0.90 <= p.strike < spot], key=lambda r: -r.strike)
            for c, p in list(zip(otm_calls, otm_puts, strict=False))[:MAX_STRADDLE_STRIKES]:
                out.append(RawCandidate("strangle", [_leg(c, +1), _leg(p, +1)], expiry, cushion))
    return out


# --- metrics --------------------------------------------------------------------------------------------------------


def _fmt_strike(k: float) -> str:
    return f"{k:g}"


def candidate_name(inp: SelectorInputs, raw: RawCandidate) -> str:
    exp = raw.expiry.strftime("%-d %b")
    if raw.structure in ("long_call", "long_put"):
        lg = raw.legs[0]
        return f"{inp.bare} {exp} {_fmt_strike(lg.strike)}{lg.right[0]}"
    if raw.structure == "debit_spread":
        lg, sh = raw.legs
        return f"{inp.bare} {exp} {_fmt_strike(lg.strike)}{lg.right[0]} / {_fmt_strike(sh.strike)}{sh.right[0]}"
    if raw.structure == "straddle":
        return f"{inp.bare} {exp} {_fmt_strike(raw.legs[0].strike)} straddle"
    c, p = raw.legs
    return f"{inp.bare} {exp} {_fmt_strike(p.strike)}p / {_fmt_strike(c.strike)}c strangle"


def candidate_kind(raw: RawCandidate) -> str:
    if raw.structure == "debit_spread":
        return f"{'Put' if raw.legs[0].right == 'put' else 'Call'} debit spread"
    return STRUCTURE_KIND[raw.structure]


def _days(n: int) -> str:
    return "1 day" if abs(n) == 1 else f"{abs(n)} days"


def _cushion_text(cushion_days: int, window_end: date) -> str:
    if cushion_days < 0:
        return f"expires {_days(cushion_days)} before the window end (no cushion)"
    if cushion_days == 0:
        return "expires at the window end (no cushion)"
    return f"{_days(cushion_days)} past the window end"


def _round(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(v, nd)


def evaluate_candidate(inp: SelectorInputs, raw: RawCandidate) -> dict[str, Any]:
    """Every metric the spec lists, computed deterministically. Missing chain IV falls back to the 20-day realized
    vol and is flagged; missing quotes make the candidate unpriceable rather than guessed."""
    r = inp.rate
    rv = inp.realized_vol_20d_pct / 100.0 if inp.realized_vol_20d_pct else None
    legs: list[Leg] = []
    iv_fallback = False
    for lg in raw.legs:
        if lg.iv is None:
            if rv:
                legs.append(Leg(**{**lg.__dict__, "iv": rv, "iv_source": "realized_20d"}))
                iv_fallback = True
            else:
                legs.append(lg)
        else:
            legs.append(lg)
    out: dict[str, Any] = {
        "rank": None,
        "structure": raw.structure,
        "name": candidate_name(inp, raw),
        "kind": candidate_kind(raw),
        "expiry": raw.expiry.isoformat(),
        "cushion_days": raw.cushion_days,
        "cushion": _cushion_text(raw.cushion_days, inp.window_end),
        "window_days": max((inp.window_end - inp.today).days, 1),
        "tier_ok": raw.structure in TIER2_ALLOWED,
        "tier_note": TIER_NOTE,
        "legs": [
            {
                "contract": lg.contract,
                "expiry": lg.expiry.isoformat(),
                "strike": lg.strike,
                "right": lg.right,
                "side": "long" if lg.side > 0 else "short",
                "qty": lg.qty,
                "bid": lg.bid,
                "ask": lg.ask,
                "mid": lg.mid,
                "iv": lg.iv,
                "iv_source": lg.iv_source,
                "oi": lg.oi,
                "volume": lg.volume,
                "delta": lg.delta,
            }
            for lg in legs
        ],
        "iv_fallback": iv_fallback,
        "passes_filters": True,
        "filter_reasons": [],
    }
    reasons: list[str] = out["filter_reasons"]
    if not out["tier_ok"]:
        reasons.append("not allowed at Fidelity Level 2")
    debit, sbid, sask = structure_mid(legs), structure_bid(legs), structure_ask(legs)
    out.update({"debit": _round(debit), "structure_bid": _round(sbid), "structure_ask": _round(sask)})
    if debit is None or debit <= 0:
        reasons.append(
            "no usable mid quote for every leg" if debit is None else "structure has no positive cost at mid"
        )
        out["passes_filters"] = False
        return out
    # Structure width = what the round trip costs (score penalty, shown on the card). The hard liquidity filter is
    # per leg: netting two tight legs into a small debit inflates the structure's percentage without any leg being
    # illiquid, so the widest leg decides.
    width_pct = ((sask - sbid) / debit * 100.0) if (sask is not None and sbid is not None) else None
    leg_widths = [
        ((lg.ask - lg.bid) / lg.mid * 100.0) if (lg.ask is not None and lg.bid is not None and lg.mid) else None
        for lg in legs
    ]
    leg_width_max = None if any(w is None for w in leg_widths) else max(leg_widths)  # type: ignore[type-var]
    out["spread_width_pct"] = _round(width_pct, 2)
    out["leg_width_pct_max"] = _round(leg_width_max, 2)
    out["leg_widths_pct"] = [_round(w, 2) for w in leg_widths]
    oi = min((lg.oi if lg.oi is not None else 0) for lg in legs)
    vol = min((lg.volume if lg.volume is not None else 0) for lg in legs)
    out.update({"open_interest": oi, "volume": vol})
    if oi < FILTERS["min_open_interest"]:
        reasons.append(f"open interest {oi} below {FILTERS['min_open_interest']}")
    if leg_width_max is None or leg_width_max > FILTERS["max_spread_width_pct"]:
        reasons.append(
            "bid/ask width unknown"
            if leg_width_max is None
            else f"bid/ask width {leg_width_max:.1f}% above {FILTERS['max_spread_width_pct']:g}% on a leg"
        )
    if any(lg.iv is None for lg in legs):
        reasons.append("no implied vol on the chain and no realized vol to fall back to")
        out["passes_filters"] = False
        return out

    # Model values. eval_date = window end, or the expiry when it comes first (the option is gone by then).
    eval_date = min(inp.window_end, raw.expiry)
    out["eval_date"] = eval_date.isoformat()
    model_entry = structure_value(legs, inp.spot, inp.today, r)
    value_spot_eval = structure_value(legs, inp.spot, eval_date, r)
    out["model_value_at_entry"] = _round(model_entry)
    out["model_vs_mid_pct"] = _round((model_entry / debit - 1.0) * 100.0, 2)
    out["value_at_spot_window_end"] = _round(value_spot_eval)
    out["ret_at_spot_window_end_pct"] = _round((value_spot_eval - debit) / debit * 100.0, 2)
    long_ivs = [lg.iv for lg in legs if lg.side > 0 and lg.iv is not None]
    iv = sum(long_ivs) / len(long_ivs) if long_ivs else None
    out["iv"] = _round(iv)
    out["iv_source"] = "realized_20d" if iv_fallback else "chain"
    out["iv_rv_ratio"] = _round(iv / rv, 3) if (iv and rv) else None
    out["iv_percentile_1y"] = inp.iv_percentile_1y
    out["iv_percentile_note"] = inp.iv_percentile_note

    bes = breakevens(legs, debit)
    out["breakevens"] = [_round(b) for b in bes]
    be: float | None = None
    if bes:
        if inp.bearish:
            below = [b for b in bes if b < inp.spot]
            be = max(below) if below else min(bes)
        else:
            above = [b for b in bes if b > inp.spot]
            be = min(above) if above else max(bes)
    out["breakeven"] = _round(be)
    if inp.target is not None:
        dist = (be - inp.target) if inp.bearish else (inp.target - be) if be is not None else None
        out["target_to_breakeven"] = _round(dist)
        out["target_to_breakeven_pct"] = _round(dist / inp.spot * 100.0, 2) if dist is not None else None
        out["target_beyond_breakeven"] = bool(dist is not None and dist > 0)
        # fraction of the move to target spent reaching breakeven (the mockup's "62% of your move")
        move = abs(inp.target - inp.spot)
        out["move_spent_to_breakeven_pct"] = (
            _round(abs(be - inp.spot) / move * 100.0, 1) if (be is not None and move) else None
        )
        v_t_eval = structure_value(legs, inp.target, eval_date, r)
        v_t_exp = payoff_at_expiry(legs, inp.target)
        out["value_at_target_window_end"] = _round(v_t_eval)
        out["ret_at_target_window_end_pct"] = _round((v_t_eval - debit) / debit * 100.0, 2)
        out["value_at_target_expiry"] = _round(v_t_exp)
        out["ret_at_target_expiry_pct"] = _round((v_t_exp - debit) / debit * 100.0, 2)
    else:
        out.update(
            {
                "target_to_breakeven": None,
                "target_to_breakeven_pct": None,
                "target_beyond_breakeven": None,
                "move_spent_to_breakeven_pct": None,
                "value_at_target_window_end": None,
                "ret_at_target_window_end_pct": None,
                "value_at_target_expiry": None,
                "ret_at_target_expiry_pct": None,
            }
        )
    pop_iv = iv if iv else rv
    out["pop_pct"] = (
        _round(probability_of_profit(legs, debit, inp.spot, inp.today, r, pop_iv) * 100.0, 1) if pop_iv else None
    )
    out["pop_iv"] = _round(pop_iv)
    g = structure_greeks(legs, inp.spot, inp.today, r)
    out["theta_per_day"] = _round(g.theta)
    out["delta"] = _round(g.delta)
    days = max((eval_date - inp.today).days, 1)
    out["theta_week_pct"] = _round((model_entry - value_spot_eval) / debit * 100.0 * 7.0 / days, 2)
    out["days_of_theta"] = days_of_theta(legs, debit, inp.spot, inp.today, r, inp.stop_loss_pct)

    # Sizing (owner decision 2026-09-05): contracts come from the idea's capital; the risk budget is account-level
    # (account_size x default_risk_pct) and only warns when the idea's capital exceeds it.
    cost = debit * 100.0
    risk_budget = inp.account_size * inp.risk_pct / 100.0
    n = math.floor(inp.capital / cost) if cost > 0 else 0
    width = abs(legs[0].strike - legs[1].strike) if raw.structure == "debit_spread" else None
    if raw.structure == "long_put":
        max_gain = (legs[0].strike - debit) * 100.0
    elif raw.structure == "debit_spread":
        max_gain = (width - debit) * 100.0  # type: ignore[operator]
    else:
        max_gain = None  # calls, straddles and strangles are uncapped
    out.update(
        {
            "cost_per_contract": _round(cost, 2),
            "capital_assigned": inp.capital,
            "account_size": inp.account_size,
            "risk_pct": inp.risk_pct,
            "risk_budget": _round(risk_budget, 2),
            "capital_exceeds_risk_budget": inp.capital > risk_budget,
            "contracts": n,
            "at_risk": _round(n * cost, 2),
            "max_loss_per_contract": _round(cost, 2),
            "max_gain_per_contract": _round(max_gain, 2),
            "affordable": n > 0,
        }
    )
    out["passes_filters"] = not reasons
    out.update(score_candidate(out))
    return out


def score_candidate(c: dict[str, Any]) -> dict[str, Any]:
    """Ranking metric = return on premium at target on the window end, minus the penalties in SCORE_WEIGHTS."""
    ret = c.get("ret_at_target_window_end_pct")
    if ret is None:
        return {"score": None, "score_breakdown": {"reason": "no target level to evaluate"}}
    parts: dict[str, float] = {"ret_at_target_window_end_pct": ret}
    parts["spread_penalty"] = -SCORE_WEIGHTS["spread_width_pct"] * (c.get("spread_width_pct") or 0.0)
    parts["theta_penalty"] = -SCORE_WEIGHTS["theta_week_pct"] * max(c.get("theta_week_pct") or 0.0, 0.0)
    if c.get("iv_percentile_1y") is not None:
        parts["iv_penalty"] = -SCORE_WEIGHTS["iv_percentile_over_50"] * max(c["iv_percentile_1y"] - 50.0, 0.0)
    elif c.get("iv_rv_ratio") is not None:
        parts["iv_penalty"] = -SCORE_WEIGHTS["iv_rv_ratio_over_1"] * max(c["iv_rv_ratio"] - 1.0, 0.0)
    else:
        parts["iv_penalty"] = 0.0
    cushion_days = c.get("cushion_days") or 0
    if cushion_days < 0:
        uncovered = min(1.0, -cushion_days / max(c.get("window_days") or 1, 1))
        parts["cushion_penalty"] = -(
            SCORE_WEIGHTS["no_cushion"] + SCORE_WEIGHTS["uncovered_window"] * max(ret, 0.0) * uncovered
        )
    else:
        parts["cushion_penalty"] = 0.0
    score = sum(parts.values())
    return {"score": round(score, 3), "score_breakdown": {k: round(v, 3) for k, v in parts.items()}}


# --- ranking, grids, verdict ----------------------------------------------------------------------------------------


def rank_candidates(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Passing candidates first, by score; the rest follow (unranked) so the stored list shows why they fell out."""
    passing = sorted(
        (c for c in cands if c["passes_filters"] and c.get("score") is not None), key=lambda c: -c["score"]
    )
    for i, c in enumerate(passing, 1):
        c["rank"] = i
    rest = [c for c in cands if not (c["passes_filters"] and c.get("score") is not None)]
    rest.sort(key=lambda c: -(c.get("ret_at_target_window_end_pct") or -1e9))
    return passing + rest


def attach_grid(inp: SelectorInputs, c: dict[str, Any]) -> None:
    """Scenario grid (rows: stop -> 5% past target, 9 steps; columns: entry, weekly, window end, expiry) and the
    payoff-at-expiry curve for one candidate. IV per leg is held constant (stated in the UI)."""
    legs = [
        Leg(
            right=lg["right"],
            strike=lg["strike"],
            expiry=date.fromisoformat(lg["expiry"]),
            side=1 if lg["side"] == "long" else -1,
            qty=lg["qty"],
            iv=lg["iv"],
        )
        for lg in c["legs"]
    ]
    debit = c["debit"]
    expiry = date.fromisoformat(c["expiry"])
    if inp.target is None:
        c["grid"] = None
    else:
        rows = grid_prices(inp.stop, inp.target, inp.spot, inp.direction)
        cols = grid_dates(inp.today, inp.window_end, expiry)
        cells = scenario_grid(legs, debit, rows, cols, inp.rate)
        eval_date = min(inp.window_end, expiry)
        c["grid"] = {
            "prices": rows,
            "dates": [d.isoformat() for d in cols],
            "ret_pct": [[_round(cell.ret_pct, 1) for cell in row] for row in cells],
            "values": [[_round(cell.value, 3) for cell in row] for row in cells],
            "target_row": rows.index(inp.target) if inp.target in rows else None,
            "spot_row": rows.index(inp.spot) if inp.spot in rows else None,
            "window_end_col": cols.index(eval_date) if eval_date in cols else len(cols) - 1,
            "iv_held": c.get("iv"),
            "assumption": "Black-Scholes at each leg's chain IV, held constant; rate from Settings",
        }
    lo = min(
        [lg.strike for lg in legs]
        + [inp.spot]
        + ([inp.target] if inp.target else [])
        + ([inp.stop] if inp.stop else [])
    )
    hi = max(
        [lg.strike for lg in legs]
        + [inp.spot]
        + ([inp.target] if inp.target else [])
        + ([inp.stop] if inp.stop else [])
    )
    lo, hi = lo * 0.94, hi * 1.06
    pts = 41
    c["payoff_curve"] = [
        [round(p, 4), round(payoff_at_expiry(legs, p) - debit, 4)]
        for p in (lo + (hi - lo) * i / (pts - 1) for i in range(pts))
    ]


def decide_verdict(inp: SelectorInputs, ranked: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    """(verdict, reason_code, facts). no_trade when nothing passes, when the best return at target on the window end
    is at or below 50%, or when the target sits inside breakeven for every passing candidate."""
    passing = [c for c in ranked if c.get("rank")]
    if inp.target is None:
        return "no_trade", "no_target", {"why": "the idea has no price target to size an expression against"}
    if not passing:
        return "no_trade", "no_candidate_passes_filters", {"why": "no contract passes the liquidity filters"}
    best = passing[0]
    if not any(c.get("target_beyond_breakeven") for c in passing):
        return "no_trade", "target_inside_breakeven", {"why": "the target sits inside breakeven for every candidate"}
    if (best.get("ret_at_target_window_end_pct") or 0.0) <= MIN_RETURN_AT_TARGET_PCT:
        return (
            "no_trade",
            "return_below_threshold",
            {"why": f"the best return at target by the window end is {best['ret_at_target_window_end_pct']:.0f}%"},
        )
    return "trade", "ok", {}


def shares_comparison(inp: SelectorInputs, best: dict[str, Any] | None, verdict: str) -> dict[str, Any]:
    """Return on capital_assigned for shares (and the named inverse ETF) making the same move, next to the best
    option's return on premium. The inverse-ETF figure is leverage x the underlying move, before daily rebalancing."""
    out: dict[str, Any] = {
        "target": inp.target,
        "spot": inp.spot,
        "move_pct": None,
        "shares": None,
        "inverse_etf": None,
        "best_option": None,
        "verdict": "none",
        "verdict_text": "No trade",
    }
    if inp.target is None:
        return out
    move = (inp.target / inp.spot - 1.0) * 100.0
    out["move_pct"] = round(move, 2)
    sign = -1.0 if inp.bearish else 1.0
    if inp.direction == "range":
        sign = 0.0
    shares_ret = sign * move
    out["shares"] = {
        "label": f"{'Short' if inp.bearish else 'Long'} {inp.bare} shares",
        "return_pct": round(shares_ret, 2),
        "pnl_abs": round(inp.capital * shares_ret / 100.0, 2),
        "capital": inp.capital,
    }
    if inp.inverse_symbol and inp.inverse_leverage:
        inv_ret = inp.inverse_leverage * shares_ret
        out["inverse_etf"] = {
            "symbol": inp.inverse_symbol,
            "leverage": inp.inverse_leverage,
            "label": f"{inp.inverse_symbol.rsplit('.', 1)[0]} ({inp.inverse_leverage:g}x inverse)",
            "return_pct": round(inv_ret, 2),
            "pnl_abs": round(inp.capital * inv_ret / 100.0, 2),
            "capital": inp.capital,
            "note": f"{inp.inverse_leverage:g}x the underlying move, before daily-rebalance drag",
        }
    if best is not None and best.get("ret_at_target_window_end_pct") is not None:
        out["best_option"] = {
            "name": best["name"],
            "kind": best["kind"],
            "return_pct": best["ret_at_target_window_end_pct"],
            "return_at_expiry_pct": best.get("ret_at_target_expiry_pct"),
            "at_risk": best.get("at_risk"),
            "contracts": best.get("contracts"),
            "pnl_abs": round((best.get("at_risk") or 0.0) * best["ret_at_target_window_end_pct"] / 100.0, 2),
        }
    if verdict == "trade" and best is not None and not best.get("contracts"):
        out["verdict"] = "shares"
        out["verdict_text"] = "Option unaffordable at this capital; shares used"
    elif verdict == "trade" and best is not None:
        out["verdict"] = "option"
        out["verdict_text"] = f"Trade the {best['kind'].lower()}"
    else:
        out["verdict"] = "shares"
        out["verdict_text"] = "Shares are the cleaner vehicle" if inp.direction != "range" else "No trade"
    return out


# --- rationale: the model rewrites our numbers; it may not add any --------------------------------------------------


class RationaleLLM(BaseModel):
    verdict_text: str = Field(description="Two or three plain sentences for the verdict banner")
    why: list[str] = Field(description="One or two sentences per candidate, same order as the input candidates")


RATIONALE_SYSTEM = """You write the short plain-English rationale for an options expression selector.
You receive computed numbers only. Rules:
- Use only figures that appear in the input. You may round a figure to fewer decimals (153.81 -> 154%) but never
  introduce a number, date, level, percentage or count that is not in the input, and never compute anything.
- Write percentages as whole numbers with a percent sign (154%, 56% of the move). Write implied vol with the
  `iv_pct` field (41% implied vol), never the decimal. Describe the time cushion with the `cushion` text, never
  as a negative number. Prices keep two decimals (138.03).
- verdict_text: two or three sentences. Say what the best expression is (or why there is no trade), how much of the
  move is spent reaching breakeven, whether implied vol is rich versus 20-day realized, what the time cushion is,
  and what the option gives up. Plain sentences, sentence case, no bullet points, no em dashes, no parentheses.
- why: for each candidate in order, one or two sentences on why it ranks where it does. Same rules.
"""


def _call_rationale_model(payload: dict[str, Any]) -> RationaleLLM:
    """Single structured-output request. Separate so tests can replace it."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=45.0, max_retries=1)
    import json

    response = client.messages.parse(
        model=RATIONALE_MODEL,
        max_tokens=1200,
        system=RATIONALE_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        output_format=RationaleLLM,
    )
    parsed = response.parsed_output
    if parsed is None:
        raise RuntimeError(f"rationale model returned no structured output (stop_reason={response.stop_reason})")
    return parsed


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers_in(obj: Any, acc: set[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, int | float):
        acc.add(float(obj))
        return
    if isinstance(obj, str):
        for tok in _NUM_RE.findall(obj):
            acc.add(float(tok))
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, acc)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _numbers_in(v, acc)


def text_uses_only_input_numbers(text: str, payload: dict[str, Any]) -> bool:
    """Every numeric token in `text` must be a rounding of a number in `payload` (dates contribute their parts)."""
    allowed: set[float] = set()
    _numbers_in(payload, allowed)
    for tok in _NUM_RE.findall(text):
        v = float(tok)
        decimals = len(tok.split(".")[1]) if "." in tok else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(v - a) <= tol or abs(abs(v) - abs(a)) <= tol for a in allowed):
            return False
    return True


def _template_verdict(inp: SelectorInputs, verdict: str, reason: str, facts: dict[str, Any], best: dict | None) -> str:
    if verdict == "trade" and best:
        bits = [
            f"Best expression: {best['name']}, a {best['kind'].lower()} returning "
            f"{best['ret_at_target_window_end_pct']:.0f}% on premium if {inp.bare} reaches {inp.target:g} "
            f"by {inp.window_end.strftime('%b %-d')}."
        ]
        if best.get("move_spent_to_breakeven_pct") is not None:
            bits.append(
                f"Breakeven {best['breakeven']:.2f} spends {best['move_spent_to_breakeven_pct']:.0f}% of the move."
            )
        if best.get("iv_percentile_1y") is not None:
            bits.append(f"Implied vol sits at the {best['iv_percentile_1y']:.0f}th percentile of its stored year.")
        elif best.get("iv_rv_ratio") is not None:
            bits.append(
                f"Implied vol is {best['iv_rv_ratio']:.2f}x the 20-day realized vol."
                if best["iv_rv_ratio"] >= 1.1
                else "Implied vol is in line with the 20-day realized vol."
            )
        bits.append(f"The {best['expiry']} expiry {best['cushion']}.")
        return " ".join(bits)
    why = facts.get("why", reason)
    tail = ""
    if best and best.get("ret_at_target_window_end_pct") is not None:
        tail = (
            f" The best candidate, {best['name']}, returns {best['ret_at_target_window_end_pct']:.0f}% at target "
            f"by the window end against a {MIN_RETURN_AT_TARGET_PCT:.0f}% bar."
        )
    return f"No trade: {why}.{tail}"


def _template_why(c: dict[str, Any]) -> str:
    parts = []
    if c.get("ret_at_target_window_end_pct") is not None:
        parts.append(f"Returns {c['ret_at_target_window_end_pct']:.0f}% at target by {c['eval_date']}")
    if c.get("breakeven") is not None:
        parts.append(f"breakeven {c['breakeven']:.2f}")
    if c.get("pop_pct") is not None:
        parts.append(f"market-implied P(profit) {c['pop_pct']:.0f}%")
    if c.get("days_of_theta") is not None:
        parts.append(f"{c['days_of_theta']} days of theta at spot")
    if c.get("spread_width_pct") is not None:
        parts.append(f"bid/ask {c['spread_width_pct']:.1f}% wide")
    parts.append(c["cushion"])
    return "; ".join(parts) + "."


def rationale_payload(inp: SelectorInputs, verdict: str, reason: str, top: list[dict[str, Any]], shares: dict) -> dict:
    keys = (
        "name",
        "kind",
        "expiry",
        "cushion_days",
        "debit",
        "breakeven",
        "target_to_breakeven",
        "move_spent_to_breakeven_pct",
        "ret_at_target_window_end_pct",
        "ret_at_target_expiry_pct",
        "ret_at_spot_window_end_pct",
        "pop_pct",
        "theta_week_pct",
        "days_of_theta",
        "spread_width_pct",
        "open_interest",
        "iv",
        "iv_rv_ratio",
        "iv_percentile_1y",
        "contracts",
        "at_risk",
        "max_gain_per_contract",
        "cost_per_contract",
    )
    return {
        "instrument": inp.bare,
        "direction": inp.direction,
        "spot": inp.spot,
        "target": inp.target,
        "stop": inp.stop,
        "window_end": inp.window_end.isoformat(),
        "window_end_text": inp.window_end.strftime("%b %-d"),
        "window_days": max((inp.window_end - inp.today).days, 1),
        "capital_assigned": inp.capital,
        "realized_vol_20d_pct": inp.realized_vol_20d_pct,
        "catalyst_date": inp.catalyst_date.isoformat() if inp.catalyst_date else None,
        "verdict": verdict,
        "verdict_reason": reason,
        "return_threshold_pct": MIN_RETURN_AT_TARGET_PCT,
        "candidates": [
            {k: c.get(k) for k in keys}
            | {
                "cushion": c.get("cushion"),
                "expiry_text": date.fromisoformat(c["expiry"]).strftime("%b %-d"),
                "iv_pct": _round(c["iv"] * 100.0, 2) if c.get("iv") else None,
                "legs": [(lg["strike"], lg["right"], lg["side"]) for lg in c["legs"]],
            }
            for c in top
        ],
        "shares_comparison": {
            k: shares.get(k) for k in ("move_pct", "shares", "inverse_etf", "best_option", "verdict_text")
        },
    }


def write_rationale(
    inp: SelectorInputs, verdict: str, reason: str, facts: dict, top: list[dict], shares: dict
) -> tuple[str, str]:
    """(verdict_text, source). Tries the model; any failure, or any figure not in the input, falls back to templates."""
    best = top[0] if top else None
    template = _template_verdict(inp, verdict, reason, facts, best)
    for c in top:
        c["why"], c["why_source"] = _template_why(c), "template"
    payload = rationale_payload(inp, verdict, reason, top, shares)
    try:
        llm = _call_rationale_model(payload)
    except (RuntimeError, anthropic.APIError, ValueError) as exc:
        log.warning("rationale model unavailable, using templates: %s", exc)
        return template, "template"
    source = "template"
    verdict_text = template
    if llm.verdict_text.strip() and text_uses_only_input_numbers(llm.verdict_text, payload):
        verdict_text, source = llm.verdict_text.strip(), "model"
    else:
        log.warning("rationale verdict_text rejected (figure not in input)")
    for c, why in zip(top, llm.why, strict=False):
        if why.strip() and text_uses_only_input_numbers(why, payload):
            c["why"], c["why_source"] = why.strip(), "model"
        else:
            log.warning("rationale why rejected for %s", c["name"])
    return verdict_text, source


# --- orchestration --------------------------------------------------------------------------------------------------


def target_level(idea: Idea) -> float | None:
    """The price the selector sizes against: a level rule's level, or entry x (1 +- pct) for a pct_move rule.
    direction / relative / range rules have no price target."""
    lvl = first_level(idea.success_rule_json)
    if lvl is not None:
        return lvl
    rule = idea.success_rule_json or {}
    if rule.get("type") == "pct_move" and idea.entry_price:
        sign = -1.0 if idea.direction in BEARISH else 1.0
        return round(idea.entry_price * (1.0 + sign * float(rule["pct"]) / 100.0), 4)
    return None


def run_selector(inp: SelectorInputs, rows: list[ChainRow]) -> dict[str, Any]:
    """Pure part of the pipeline: candidates -> metrics -> ranking -> verdict -> grids -> shares comparison ->
    rationale. Returns the analysis body (no database)."""
    raw = generate_candidates(inp, rows)
    cands = [evaluate_candidate(inp, rc) for rc in raw]
    ranked = rank_candidates(cands)
    top = [c for c in ranked if c.get("rank")][:3]
    for c in top:
        attach_grid(inp, c)
    verdict, reason, facts = decide_verdict(inp, ranked)
    shares = shares_comparison(inp, top[0] if top else None, verdict)
    verdict_text, source = write_rationale(inp, verdict, reason, facts, top, shares)
    ivs = [c["iv"] for c in top if c.get("iv")]
    rv = inp.realized_vol_20d_pct
    risk_budget = inp.account_size * inp.risk_pct / 100.0
    return {
        "verdict": verdict,
        "verdict_reason": reason,
        "verdict_text": verdict_text,
        "rationale_source": source,
        "candidates": ranked,
        "shares_comparison": shares,
        "sizing": {
            "capital_assigned": inp.capital,
            "account_size": inp.account_size,
            "risk_pct": inp.risk_pct,
            "risk_budget": round(risk_budget, 2),
            "capital_exceeds_risk_budget": inp.capital > risk_budget,
        },
        "counts": {
            "generated": len(cands),
            "passing": sum(1 for c in cands if c["passes_filters"]),
            "by_structure": {s: sum(1 for c in cands if c["structure"] == s) for s in STRUCTURE_KIND},
        },
        "iv_rv_ratio": round((sum(ivs) / len(ivs)) / (rv / 100.0), 3) if (ivs and rv) else None,
        "iv_percentile_1y": inp.iv_percentile_1y,
        "iv_percentile_note": inp.iv_percentile_note,
    }


def build_inputs(
    idea: Idea,
    spot: float,
    values: dict[str, Any],
    rv_pct: float | None,
    today: date,
    req,
    iv_percentile: dict[str, Any] | None = None,
) -> SelectorInputs:
    stop = first_level(idea.invalidation_rule_json)
    pct = iv_percentile or {}
    return SelectorInputs(
        symbol=idea.instrument.symbol,
        direction=idea.direction,
        spot=spot,
        today=today,
        window_end=idea.window_end,
        target=target_level(idea),
        stop=stop,
        capital=float(idea.capital_assigned),
        risk_pct=float(values.get("default_risk_pct", 2.0)),
        account_size=float(values.get("account_size", 2342)),
        stop_loss_pct=float(values.get("default_stop_loss_pct", 50)),
        rate_pct=float(values.get("risk_free_rate_pct", 4.0)),
        realized_vol_20d_pct=rv_pct,
        conviction_pct=idea.conviction_pct,
        catalyst_date=idea.catalyst_date,
        inverse_symbol=getattr(req, "inverse_symbol", None),
        inverse_leverage=getattr(req, "inverse_leverage", None),
        iv_percentile_1y=pct.get("percentile"),
        iv_percentile_note=pct.get("note") or SelectorInputs.iv_percentile_note,
    )


def analyze_idea(
    db: Session,
    idea: Idea,
    values: dict[str, Any],
    client: EODHDClient | None = None,
    req: Any = None,
    today: date | None = None,
) -> OptionAnalysis:
    """Run the selector for an idea and store one option_analyses row. Raises EODHDError (nothing stored) when the
    spot or the chain cannot be fetched, ValueError when the idea cannot be analysed."""
    client = client or EODHDClient()
    today = today or datetime.now(UTC).date()
    if idea.window_end < today:
        raise ValueError(f"the idea's window ended on {idea.window_end}; nothing to express")
    inst = idea.instrument
    spot, spot_at, spot_src = prices.stamp_entry(db, inst, client)
    rv_pct: float | None = None
    rv_error: dict[str, Any] | None = None
    try:
        prices.ensure_eod(db, inst, today - timedelta(days=60), today, client)
        bars = prices.bars(db, inst.id, today - timedelta(days=60), today)
        rv_pct = realized_vol_pct([b.close for b in bars])
    except EODHDError as exc:  # the selector can run without it; IV fallback and IV/RV then stay null
        rv_error = exc.to_dict()
    inp = build_inputs(idea, spot, values, rv_pct, today, req)
    rows, chain_meta = fetch_chain(client, inp)
    # Store the band (Phase 6): it feeds daily marks and the IV percentile. Then rank today's ATM IV against the
    # stored history and re-build the inputs with the percentile (used in the score once enough days exist).
    rec = chains.record_date(rows) or today
    chain_meta["stored_rows"] = chains.store_band(db, inst.id, rows, rec, spot)
    pct = chains.iv_percentile_1y(db, inst.id, rec, chains.atm_iv(rows, spot, rec))
    inp = build_inputs(idea, spot, values, rv_pct, today, req, pct)
    body = run_selector(inp, rows)
    row = OptionAnalysis(
        idea_id=idea.id,
        chain_as_of=_parse_ts(chain_meta.get("chain_as_of")),
        chain_trade_date=date.fromisoformat(chain_meta["chain_trade_date"])
        if chain_meta.get("chain_trade_date")
        else None,
        spot=spot,
        spot_as_of=spot_at,
        spot_source=spot_src,
        iv_percentile_1y=body["iv_percentile_1y"],
        iv_rv_ratio=body["iv_rv_ratio"],
        realized_vol_20d=rv_pct,
        rate_pct=inp.rate_pct,
        verdict=body["verdict"],
        verdict_text=body["verdict_text"],
        candidates_json=body["candidates"],
        shares_comparison_json=body["shares_comparison"],
        params_json={
            "inputs": {
                "direction": inp.direction,
                "target": inp.target,
                "stop": inp.stop,
                "window_end": inp.window_end.isoformat(),
                "today": inp.today.isoformat(),
                "capital_assigned": inp.capital,
                "default_risk_pct": inp.risk_pct,
                "account_size": inp.account_size,
                "default_stop_loss_pct": inp.stop_loss_pct,
                "risk_free_rate_pct": inp.rate_pct,
                "conviction_pct": inp.conviction_pct,
                "inverse_symbol": inp.inverse_symbol,
                "inverse_leverage": inp.inverse_leverage,
            },
            "chain": chain_meta,
            "iv_percentile": pct,
            "sizing": body["sizing"],
            "counts": body["counts"],
            "verdict_reason": body["verdict_reason"],
            "rationale": {"model": RATIONALE_MODEL, "source": body["rationale_source"]},
            "filters": FILTERS,
            "score_weights": SCORE_WEIGHTS,
            "min_return_at_target_pct": MIN_RETURN_AT_TARGET_PCT,
            "errors": [e for e in ({"what": "realized_vol", **rv_error} if rv_error else None,) if e],
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def latest_analysis(db: Session, idea_id: int) -> OptionAnalysis | None:
    return db.execute(
        select(OptionAnalysis)
        .where(OptionAnalysis.idea_id == idea_id)
        .order_by(OptionAnalysis.created_at.desc(), OptionAnalysis.id.desc())
        .limit(1)
    ).scalar_one_or_none()


DOLLAR_KEYS = ("capital_assigned", "account_size", "risk_budget", "contracts", "at_risk")


def _mask_candidate(c: dict[str, Any]) -> dict[str, Any]:
    m = dict(c)
    for k in DOLLAR_KEYS:
        if k in m:
            m[k] = None
    return m


def to_analysis_out(row: OptionAnalysis, hide_dollars: bool) -> OptionAnalysisOut:
    cands = row.candidates_json or []
    shares = dict(row.shares_comparison_json or {})
    params = dict(row.params_json or {})
    if hide_dollars:
        cands = [_mask_candidate(c) for c in cands]
        for key in ("shares", "inverse_etf", "best_option"):
            if shares.get(key):
                shares[key] = {**shares[key], "pnl_abs": None, "capital": None, "at_risk": None, "contracts": None}
        if "inputs" in params:
            params["inputs"] = {**params["inputs"], "capital_assigned": None, "account_size": None}
        if "sizing" in params:
            params["sizing"] = {**params["sizing"], "capital_assigned": None, "account_size": None, "risk_budget": None}
    return OptionAnalysisOut(
        id=row.id,
        idea_id=row.idea_id,
        created_at=row.created_at,
        chain_as_of=row.chain_as_of,
        chain_trade_date=row.chain_trade_date,
        spot=row.spot,
        spot_as_of=row.spot_as_of,
        spot_source=row.spot_source,
        iv_percentile_1y=row.iv_percentile_1y,
        iv_rv_ratio=row.iv_rv_ratio,
        realized_vol_20d=row.realized_vol_20d,
        rate_pct=row.rate_pct,
        verdict=row.verdict,
        verdict_text=row.verdict_text,
        candidates=cands,
        shares_comparison=shares,
        params=params,
        dollars_hidden=hide_dollars,
    )
