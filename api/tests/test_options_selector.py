"""Options selector tests. The chain is a labeled FIXTURE in EODHD's JSON:API shape (quotes are Black-Scholes at a
flat 33% vol so every hand check below is reproducible); the rationale model call is replaced. No network."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db.models import Base, OptionAnalysis
from api.db.session import get_db, get_db_optional
from api.main import app
from api.services import options
from api.services.eodhd import BASE_URL, OPTIONS_CONTRACTS_PATH
from api.services.options import (
    RationaleLLM,
    SelectorInputs,
    evaluate_candidate,
    generate_candidates,
    normalize_chain,
    run_selector,
    select_expiries,
    target_level,
    text_uses_only_input_numbers,
)
from api.tests.test_ideas_api import eod_fixture, idea_body
from api.tests.test_pricing import bs_reference

WRITE = {"Authorization": "Bearer test-write-token"}

# FIXTURE parameters: a USO-like underlying at 72.10, bearish idea, target 68.50, stop 74, three-week window.
FIX_SPOT = 72.1
FIX_IV = 0.33
FIX_RATE = 0.04
T0 = date(2026, 9, 4)
WINDOW_END = date(2026, 9, 25)


def chain_fixture(today: date, window_end: date) -> dict:
    """FIXTURE: synthetic chain. Expiries: one week before the window end (no cushion), three weeks after, eight weeks
    after. Strikes 58..86 step 2, puts and calls. Quotes: Black-Scholes at 33% vol, 2% either side of mid, OI 500,
    except put 62 (OI 50 -> fails the OI filter), put 64 (12.5% either side -> fails the width filter) and put 78
    (no implied vol -> realized-vol fallback)."""
    expiries = [window_end - timedelta(days=7), window_end + timedelta(days=21), window_end + timedelta(days=56)]
    rows = []
    for exp in expiries:
        T = (exp - today).days / 365
        for K in range(58, 87, 2):
            for right in ("put", "call"):
                mid = max(round(bs_reference(FIX_SPOT, K, T, FIX_RATE, FIX_IV, right), 2), 0.05)
                wide = 0.125 if (right == "put" and K == 64) else 0.02
                bid, ask = round(mid * (1 - wide), 2), round(mid * (1 + wide), 2)
                contract = f"USO{exp:%y%m%d}{'P' if right == 'put' else 'C'}{int(K * 1000):08d}"
                rows.append(
                    {
                        "id": contract,
                        "type": "options-contracts",
                        "attributes": {
                            "contract": contract,
                            "underlying_symbol": "USO",
                            "exp_date": exp.isoformat(),
                            "type": right,
                            "strike": K,
                            "bid": bid,
                            "ask": ask,
                            "midpoint": round((bid + ask) / 2, 3),
                            "last": mid,
                            "volatility": None if (right == "put" and K == 78) else FIX_IV,
                            "delta": None,
                            "theta": None,
                            "open_interest": 50 if (right == "put" and K == 62) else 500,
                            "volume": 120,
                            "dte": (exp - today).days,
                            "bid_date": f"{(today + timedelta(days=1)).isoformat()}T03:59:59.000000Z",
                            "ask_date": f"{today.isoformat()} 19:59:59",
                            "tradetime": today.isoformat(),
                        },
                    }
                )
    return {"meta": {"offset": 0, "limit": 1000}, "data": rows}


def attrs(fix: dict, contract: str) -> dict:
    return next(r["attributes"] for r in fix["data"] if r["id"] == contract)


def inputs(**over) -> SelectorInputs:
    base = dict(
        symbol="USO.US",
        direction="down",
        spot=FIX_SPOT,
        today=T0,
        window_end=WINDOW_END,
        target=68.5,
        stop=74.0,
        capital=1000.0,
        risk_pct=2.0,
        account_size=2342.0,
        stop_loss_pct=50.0,
        rate_pct=4.0,
        realized_vol_20d_pct=30.0,
        conviction_pct=60.0,
        catalyst_date=None,
        inverse_symbol="SCO.US",
        inverse_leverage=2.0,
    )
    base.update(over)
    return SelectorInputs(**base)


@pytest.fixture
def rows():
    return normalize_chain(chain_fixture(T0, WINDOW_END)["data"])


@pytest.fixture
def no_model(monkeypatch):
    """The rationale model is unavailable: every string comes from the deterministic templates."""

    def boom(payload):
        raise RuntimeError("model disabled in tests")

    monkeypatch.setattr(options, "_call_rationale_model", boom)


# --- chain + expiries -----------------------------------------------------------------------------------------------


def test_normalize_chain_keeps_quotes_and_drops_broken_rows(rows):
    assert len(rows) == 3 * 15 * 2
    r = next(x for x in rows if x.contract == "USO261016P00070000")
    assert r.right == "put" and r.strike == 70 and r.expiry == date(2026, 10, 16) and r.oi == 500
    assert r.mid == pytest.approx((r.bid + r.ask) / 2, abs=1e-3)
    assert (
        normalize_chain([{"attributes": {"strike": 70}}, {"attributes": {"exp_date": "x", "strike": 1, "type": "put"}}])
        == []
    )
    as_of, trade = options.chain_timestamps(rows)
    assert as_of.isoformat().startswith("2026-09-05T03:59:59") and trade == T0


def test_select_expiries_takes_every_later_expiry_plus_the_one_before():
    exps = [date(2026, 9, 11), date(2026, 9, 18), date(2026, 10, 16), date(2026, 11, 20)]
    assert select_expiries(exps, WINDOW_END) == [
        (date(2026, 9, 18), -7),
        (date(2026, 10, 16), 21),
        (date(2026, 11, 20), 56),
    ]
    assert select_expiries(exps, date(2026, 9, 18)) == [
        (date(2026, 9, 11), -7),
        (date(2026, 9, 18), 0),
        (date(2026, 10, 16), 28),
        (date(2026, 11, 20), 63),
    ]
    assert select_expiries([date(2026, 9, 11)], WINDOW_END) == [(date(2026, 9, 11), -14)]


# --- candidate generation -------------------------------------------------------------------------------------------


def test_bearish_idea_builds_long_puts_and_put_debit_spreads_only(rows):
    cands = generate_candidates(inputs(), rows)
    assert {c.structure for c in cands} == {"long_put", "debit_spread"}
    assert all(lg.right == "put" for c in cands for lg in c.legs)
    singles = {c.legs[0].strike for c in cands if c.structure == "long_put"}
    assert singles == {66, 68, 70, 72, 74, 76, 78}  # 5% past target .. 10% in the money
    spreads = [(c.legs[0].strike, c.legs[1].strike) for c in cands if c.structure == "debit_spread"]
    assert all(lg > sh for lg, sh in spreads)
    assert {lg for lg, _ in spreads} == {68, 70, 72, 74, 76}  # long leg within 7% of spot
    assert {sh for _, sh in spreads} == {62, 64, 66, 68}  # short leg at or beyond target, within 12%
    assert sorted({(c.expiry, c.cushion_days) for c in cands}) == [
        (date(2026, 9, 18), -7),
        (date(2026, 10, 16), 21),
        (date(2026, 11, 20), 56),
    ]
    assert all(c.legs[0].side == 1 and c.legs[-1].side == (-1 if c.structure == "debit_spread" else 1) for c in cands)


def test_bullish_idea_mirrors_with_calls(rows):
    cands = generate_candidates(inputs(direction="up", target=76.0, stop=70.0), rows)
    assert {c.structure for c in cands} == {"long_call", "debit_spread"}
    assert all(lg.right == "call" for c in cands for lg in c.legs)
    spreads = [(c.legs[0].strike, c.legs[1].strike) for c in cands if c.structure == "debit_spread"]
    assert all(sh > lg for lg, sh in spreads) and {sh for _, sh in spreads} == {76, 78, 80, 82, 84}


def test_straddles_and_strangles_only_without_conviction_or_for_range_ideas(rows):
    with_conviction = generate_candidates(inputs(), rows)
    assert not any(c.structure in ("straddle", "strangle") for c in with_conviction)
    no_conviction = generate_candidates(inputs(conviction_pct=None), rows)
    kinds = {c.structure for c in no_conviction}
    assert {"straddle", "strangle", "long_put", "debit_spread"} <= kinds
    straddle = next(c for c in no_conviction if c.structure == "straddle")
    assert straddle.legs[0].strike == straddle.legs[1].strike and {lg.right for lg in straddle.legs} == {"call", "put"}
    strangle = next(c for c in no_conviction if c.structure == "strangle")
    assert strangle.legs[0].right == "call" and strangle.legs[1].right == "put"
    assert strangle.legs[1].strike < FIX_SPOT < strangle.legs[0].strike
    rng = generate_candidates(inputs(direction="range", target=None, stop=None), rows)
    assert {c.structure for c in rng} == {"straddle", "strangle"}


def test_directional_idea_without_a_target_builds_nothing(rows):
    assert generate_candidates(inputs(target=None), rows) == []


# --- metrics, hand-checked ------------------------------------------------------------------------------------------


def test_put_spread_metrics_match_hand_calculation(rows):
    fix = chain_fixture(T0, WINDOW_END)
    raw = next(
        c
        for c in generate_candidates(inputs(), rows)
        if c.structure == "debit_spread"
        and c.expiry == date(2026, 10, 16)
        and (c.legs[0].strike, c.legs[1].strike) == (70, 66)
    )
    c = evaluate_candidate(inputs(), raw)
    long_q, short_q = attrs(fix, "USO261016P00070000"), attrs(fix, "USO261016P00066000")
    debit = long_q["midpoint"] - short_q["midpoint"]
    assert c["name"] == "USO 16 Oct 70p / 66p" and c["kind"] == "Put debit spread" and c["tier_ok"] is True
    assert c["debit"] == pytest.approx(debit, abs=1e-6)
    assert c["structure_bid"] == pytest.approx(long_q["bid"] - short_q["ask"], abs=1e-6)
    assert c["structure_ask"] == pytest.approx(long_q["ask"] - short_q["bid"], abs=1e-6)
    assert c["spread_width_pct"] == pytest.approx((c["structure_ask"] - c["structure_bid"]) / debit * 100, abs=0.01)
    assert (
        c["open_interest"] == 500 and c["volume"] == 120 and c["passes_filters"] is True and c["filter_reasons"] == []
    )
    assert c["breakeven"] == pytest.approx(70 - debit, abs=1e-6)
    assert (
        c["target_to_breakeven"] == pytest.approx((70 - debit) - 68.5, abs=1e-6)
        and c["target_beyond_breakeven"] is True
    )
    assert c["move_spent_to_breakeven_pct"] == pytest.approx(
        abs((70 - debit) - FIX_SPOT) / (FIX_SPOT - 68.5) * 100, abs=0.1
    )
    # at target on the window end: 21 days of life left, priced at each leg's own (here identical) IV
    T_left = (date(2026, 10, 16) - WINDOW_END).days / 365
    v = bs_reference(68.5, 70, T_left, FIX_RATE, FIX_IV, "put") - bs_reference(
        68.5, 66, T_left, FIX_RATE, FIX_IV, "put"
    )
    assert c["eval_date"] == WINDOW_END.isoformat()
    assert c["value_at_target_window_end"] == pytest.approx(v, abs=1e-4)
    assert c["ret_at_target_window_end_pct"] == pytest.approx((v - debit) / debit * 100, abs=0.02)
    assert c["value_at_target_expiry"] == pytest.approx(1.5) and c["ret_at_target_expiry_pct"] == pytest.approx(
        (1.5 - debit) / debit * 100, abs=0.02
    )
    # model at entry reprices the fixture mid closely (the fixture IS Black-Scholes at 33%, rounded to cents)
    assert abs(c["model_vs_mid_pct"]) < 2.0
    assert 0 < c["pop_pct"] < 100 and c["theta_week_pct"] > 0 and isinstance(c["days_of_theta"], int)
    assert (
        c["iv"] == pytest.approx(FIX_IV)
        and c["iv_rv_ratio"] == pytest.approx(0.33 / 0.30, abs=1e-3)
        and c["iv_percentile_1y"] is None
    )
    # sizing: contracts from the idea's capital; the risk budget is account-level and only warns
    cost = debit * 100
    assert c["cost_per_contract"] == pytest.approx(cost, abs=1e-4)
    assert c["contracts"] == math.floor(1000 / cost) > 0 and c["affordable"] is True
    assert c["at_risk"] == pytest.approx(c["contracts"] * cost, abs=1e-4)
    assert c["account_size"] == 2342.0 and c["risk_budget"] == pytest.approx(46.84) and c["risk_pct"] == 2.0
    assert c["capital_exceeds_risk_budget"] is True  # 1000 > 46.84
    assert c["max_loss_per_contract"] == pytest.approx(cost, abs=1e-4) and c["max_gain_per_contract"] == pytest.approx(
        (4 - debit) * 100, abs=1e-4
    )
    assert c["score"] == pytest.approx(
        c["ret_at_target_window_end_pct"]
        - 2.0 * c["spread_width_pct"]
        - 1.0 * c["theta_week_pct"]
        - 50.0 * (c["iv_rv_ratio"] - 1.0),
        abs=0.01,
    )
    assert c["score_breakdown"]["cushion_penalty"] == 0.0


def test_no_cushion_expiry_is_valued_at_its_own_expiry_and_penalized(rows):
    raw = next(
        c
        for c in generate_candidates(inputs(), rows)
        if c.structure == "long_put" and c.expiry == date(2026, 9, 18) and c.legs[0].strike == 70
    )
    c = evaluate_candidate(inputs(), raw)
    assert c["cushion_days"] == -7 and "no cushion" in c["cushion"]
    assert c["eval_date"] == "2026-09-18"
    assert c["value_at_target_window_end"] == pytest.approx(1.5)  # intrinsic: the option has expired by the window end
    # 15 points flat, plus the return times the 7 of 21 window days the option leaves uncovered
    assert c["window_days"] == 21
    assert c["score_breakdown"]["cushion_penalty"] == pytest.approx(
        -(15 + c["ret_at_target_window_end_pct"] * 7 / 21), abs=0.01
    )


def test_risk_budget_is_account_level_and_does_not_change_contract_counts(rows):
    raw = next(
        c
        for c in generate_candidates(inputs(), rows)
        if c.structure == "long_put" and c.legs[0].strike == 70 and c.expiry == date(2026, 10, 16)
    )
    small = evaluate_candidate(inputs(), raw)  # 2% of 2,342 = 46.84 < 1,000 capital
    large = evaluate_candidate(inputs(account_size=100_000.0), raw)  # 2% of 100,000 = 2,000 > 1,000
    assert small["contracts"] == large["contracts"] == math.floor(1000 / small["cost_per_contract"]) > 0
    assert small["capital_exceeds_risk_budget"] is True and small["risk_budget"] == pytest.approx(46.84)
    assert large["capital_exceeds_risk_budget"] is False and large["risk_budget"] == 2000.0
    assert small["max_gain_per_contract"] == pytest.approx((70 - small["debit"]) * 100, abs=1e-4)


def test_hard_filters_flag_low_open_interest_and_wide_markets(rows):
    cands = [evaluate_candidate(inputs(), r) for r in generate_candidates(inputs(), rows)]
    with_62 = [c for c in cands if any(lg["strike"] == 62 for lg in c["legs"])]
    assert with_62 and all(
        not c["passes_filters"] and any("open interest 50" in r for r in c["filter_reasons"]) for c in with_62
    )
    # the width filter is per leg: any structure carrying the 25%-wide 64 put fails, however cheap that leg is
    with_64 = [c for c in cands if any(lg["strike"] == 64 for lg in c["legs"])]
    assert with_64 and all(
        not c["passes_filters"] and any("above 10% on a leg" in r for r in c["filter_reasons"]) for c in with_64
    )
    assert all(c["leg_width_pct_max"] > 10.0 for c in with_64)  # ~25%, give or take cent rounding on cheap legs
    # everything else is at most 4% wide per leg and passes, even a narrow vertical whose structure width tops 10%
    clean = [c for c in cands if not any(lg["strike"] in (62, 64, 78) for lg in c["legs"])]
    assert clean and all(c["passes_filters"] and c["filter_reasons"] == [] for c in clean)
    assert all(c["leg_width_pct_max"] <= 10.0 for c in clean)
    narrow = next(c for c in clean if c["name"] == "USO 16 Oct 68p / 66p")
    assert narrow["spread_width_pct"] > 10.0  # structure width, still penalized in the score
    assert narrow["score_breakdown"]["spread_penalty"] == pytest.approx(-2.0 * narrow["spread_width_pct"], abs=0.01)


def test_missing_chain_iv_falls_back_to_realized_and_is_flagged(rows):
    raw = next(
        c
        for c in generate_candidates(inputs(), rows)
        if c.structure == "long_put" and c.legs[0].strike == 78 and c.expiry == date(2026, 10, 16)
    )
    c = evaluate_candidate(inputs(), raw)
    assert c["iv_fallback"] is True and c["iv_source"] == "realized_20d" and c["legs"][0]["iv_source"] == "realized_20d"
    assert c["iv"] == pytest.approx(0.30) and c["passes_filters"] is True
    # without realized vol either, the candidate is unpriceable rather than guessed
    c2 = evaluate_candidate(inputs(realized_vol_20d_pct=None), raw)
    assert c2["passes_filters"] is False and any("no implied vol" in r for r in c2["filter_reasons"])
    assert "ret_at_target_window_end_pct" not in c2


# --- ranking, verdict, grids, shares --------------------------------------------------------------------------------


def test_run_selector_ranks_by_return_at_target_and_emits_trade(rows, no_model):
    body = run_selector(inputs(), rows)
    assert body["verdict"] == "trade" and body["verdict_reason"] == "ok" and body["rationale_source"] == "template"
    cands = body["candidates"]
    passing = [c for c in cands if c["rank"]]
    assert [c["rank"] for c in passing] == list(range(1, len(passing) + 1))
    assert all(a["score"] >= b["score"] for a, b in zip(passing, passing[1:], strict=False))
    assert all(c["passes_filters"] for c in passing) and all(not c["passes_filters"] for c in cands if not c["rank"])
    best = cands[0]
    assert best["ret_at_target_window_end_pct"] > 50 and best["target_beyond_breakeven"] is True
    assert body["verdict_text"].startswith("Best expression: " + best["name"])
    assert body["counts"]["generated"] == len(cands) and body["counts"]["passing"] == len(passing)
    assert body["iv_rv_ratio"] == pytest.approx(0.33 / 0.30, abs=1e-3) and body["iv_percentile_1y"] is None
    # grids and payoff curves for the top three only
    for c in cands[:3]:
        g = c["grid"]
        assert len(g["prices"]) == 9 and g["prices"][0] == 74.0 and g["prices"][-1] == pytest.approx(68.5 * 0.95)
        assert g["prices"][g["target_row"]] == 68.5 and g["prices"][g["spot_row"]] == FIX_SPOT
        assert g["dates"][0] == T0.isoformat() and g["dates"][-1] == c["expiry"]
        assert g["dates"][g["window_end_col"]] == c["eval_date"]
        assert len(g["ret_pct"]) == 9 and all(len(r) == len(g["dates"]) for r in g["ret_pct"])
        # expiry column at the target row is the return at expiry
        assert g["ret_pct"][g["target_row"]][-1] == pytest.approx(c["ret_at_target_expiry_pct"], abs=0.06)
        assert len(c["payoff_curve"]) == 41 and c["why"] and c["why_source"] == "template"
    assert all("grid" not in c for c in cands[3:])
    # shares comparison
    s = body["shares_comparison"]
    assert s["move_pct"] == pytest.approx((68.5 / 72.1 - 1) * 100, abs=0.01)
    assert s["shares"]["label"] == "Short USO shares" and s["shares"]["return_pct"] == pytest.approx(4.99, abs=0.01)
    assert s["shares"]["pnl_abs"] == pytest.approx(49.9, abs=0.1)
    assert s["inverse_etf"]["symbol"] == "SCO.US" and s["inverse_etf"]["return_pct"] == pytest.approx(9.99, abs=0.02)
    assert (
        s["best_option"]["name"] == best["name"]
        and s["best_option"]["return_pct"] == best["ret_at_target_window_end_pct"]
    )
    assert best["contracts"] > 0
    assert body["sizing"] == {
        "capital_assigned": 1000.0,
        "account_size": 2342.0,
        "risk_pct": 2.0,
        "risk_budget": 46.84,
        "capital_exceeds_risk_budget": True,
    }
    assert s["verdict"] == "option" and s["verdict_text"].startswith("Trade the ")
    # with too little capital for a single contract the vehicle verdict says so
    tiny = run_selector(inputs(capital=1.0), rows)  # one cent of capital: no contract of any kind
    assert tiny["verdict"] == "trade" and tiny["shares_comparison"]["verdict"] == "shares"
    assert tiny["shares_comparison"]["verdict_text"] == "Option unaffordable at this capital; shares used"


def test_small_target_means_no_trade_and_points_at_shares(rows, no_model):
    body = run_selector(inputs(target=71.5, stop=73.0), rows)
    assert body["verdict"] == "no_trade"
    assert body["verdict_reason"] in ("return_below_threshold", "target_inside_breakeven")
    assert body["verdict_text"].startswith("No trade:")
    assert body["shares_comparison"]["verdict"] == "shares"
    assert all((c.get("ret_at_target_window_end_pct") or 0) <= 50 for c in body["candidates"] if c["rank"])


def test_no_target_is_a_no_trade_with_the_reason_stated(rows, no_model):
    body = run_selector(inputs(target=None), rows)
    assert body["verdict"] == "no_trade" and body["verdict_reason"] == "no_target" and body["candidates"] == []
    assert "no price target" in body["verdict_text"]
    assert body["shares_comparison"]["shares"] is None and body["shares_comparison"]["verdict"] == "none"


def test_target_level_from_rules():
    idea = SimpleNamespace(
        success_rule_json={"type": "level", "comparator": "close_at_or_below", "level": 68.5},
        direction="down",
        entry_price=72.1,
    )
    assert target_level(idea) == 68.5
    idea = SimpleNamespace(success_rule_json={"type": "pct_move", "pct": 4.0}, direction="down", entry_price=100.0)
    assert target_level(idea) == 96.0
    idea = SimpleNamespace(success_rule_json={"type": "pct_move", "pct": 4.0}, direction="up", entry_price=100.0)
    assert target_level(idea) == 104.0
    assert (
        target_level(SimpleNamespace(success_rule_json={"type": "direction"}, direction="up", entry_price=100.0))
        is None
    )
    assert (
        target_level(
            SimpleNamespace(
                success_rule_json={"type": "relative", "benchmark": "SPY.US", "spread_pct": 3},
                direction="outperform",
                entry_price=100.0,
            )
        )
        is None
    )


# --- rationale guard ------------------------------------------------------------------------------------------------


def test_text_uses_only_input_numbers():
    payload = {"ret": 152.3, "expiry": "2026-10-16", "legs": [(70, "put", "long"), (66, "put", "short")], "note": "x"}
    assert text_uses_only_input_numbers("Returns 152% by Oct 16 with the 70/66 put spread.", payload)
    assert text_uses_only_input_numbers("Returns 152.3% in 2026.", payload)
    assert not text_uses_only_input_numbers("Returns 153% by Oct 16.", payload)  # 153 is not a rounding of 152.3
    assert not text_uses_only_input_numbers("Roughly 3 weeks of cushion.", payload)  # 3 is not in the input
    assert text_uses_only_input_numbers("No figures at all.", payload)


def test_model_rationale_is_kept_only_when_every_figure_is_in_the_input(rows, monkeypatch):
    seen = {}

    def fake(payload):
        seen["payload"] = payload
        best = payload["candidates"][0]
        return RationaleLLM(
            verdict_text=(
                f"Best expression: {best['name']}. It returns {best['ret_at_target_window_end_pct']:.0f}% at target."
            ),
            why=[
                f"Breakeven {best['breakeven']:.2f} is beyond the target.",
                "This one pays 999% if you squint.",
                "Fine.",
            ],
        )

    monkeypatch.setattr(options, "_call_rationale_model", fake)
    body = run_selector(inputs(), rows)
    assert body["rationale_source"] == "model" and body["verdict_text"].startswith("Best expression: ")
    top = body["candidates"][:3]
    assert top[0]["why_source"] == "model" and top[0]["why"].startswith("Breakeven")
    assert top[1]["why_source"] == "template" and "999" not in top[1]["why"]  # invented figure -> template
    assert top[2]["why_source"] == "model" and top[2]["why"] == "Fine."
    # the model only ever sees computed numbers, never the chain or prose
    assert set(seen["payload"]) >= {"instrument", "target", "candidates", "shares_comparison", "verdict"}
    assert "thesis_text" not in seen["payload"]


# --- endpoint -------------------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _db():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_db_optional] = _db
    try:
        yield TestClient(app), Local
    finally:
        app.dependency_overrides.clear()


# FIXTURE: USO delayed quote at 72.10 (timestamp 2026-09-04 19:28 UTC) and 70 days of closes drifting around 72.
QUOTE_USO = {
    "code": "USO.US",
    "timestamp": 1788552480,
    "open": 72.5,
    "high": 72.9,
    "low": 71.8,
    "close": 72.1,
    "volume": 100,
}


def uso_bars(today: date) -> list[dict]:
    return eod_fixture(today - timedelta(days=69), [72 + ((i * 7) % 5 - 2) * 0.3 for i in range(70)])


def open_idea(today: date, **over) -> dict:
    return idea_body(
        symbol="USO.US",
        display_name="United States Oil Fund LP",
        instrument_kind="commodity_etf",
        title="USO breaks the August low",
        thesis_text="Oil looks heavy; USO breaks 68.50 within three weeks; wrong above 74.",
        direction="down",
        success_rule_json={"type": "level", "comparator": "close_at_or_below", "level": 68.5},
        invalidation_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 74},
        window_start=today.isoformat(),
        window_end=(today + timedelta(days=21)).isoformat(),
        conviction_pct=60,
        tags=["energy"],
        **over,
    )


def test_endpoint_runs_stores_masks_and_guards(client, no_model):
    c, Local = client
    today = datetime.now(UTC).date()  # the API dates everything in UTC
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/real-time/USO.US").mock(return_value=httpx.Response(200, json=QUOTE_USO))
        m.get(f"{BASE_URL}/eod/USO.US").mock(return_value=httpx.Response(200, json=uso_bars(today)))
        chain = m.get(f"{BASE_URL}{OPTIONS_CONTRACTS_PATH}").mock(
            return_value=httpx.Response(200, json=chain_fixture(today, today + timedelta(days=21)))
        )
        created = c.post("/api/ideas", json=open_idea(today), headers=WRITE)
        assert created.status_code == 201, created.text
        iid = created.json()["id"]
        assert created.json()["status"] == "open" and created.json()["entry_price"] == 72.1

        assert c.get(f"/api/ideas/{iid}/options").status_code == 404  # nothing stored yet
        assert c.post(f"/api/ideas/{iid}/options").status_code == 401  # write-protected
        assert c.post(f"/api/ideas/{iid}/options", headers=WRITE).status_code == 409  # feature flag off
        assert chain.call_count == 0
        c.patch("/api/settings", json={"options_enabled": True}, headers=WRITE)
        bad = c.post(f"/api/ideas/{iid}/options", json={"inverse_symbol": "SCO.US"}, headers=WRITE)
        assert bad.status_code == 422  # leverage must be stated with the symbol

        r = c.post(f"/api/ideas/{iid}/options", json={"inverse_symbol": "sco.us", "inverse_leverage": 2}, headers=WRITE)
        assert r.status_code == 201, r.text
        a = r.json()
        assert chain.call_count == 1  # puts only: one page
        assert chain.calls.last.request.url.params["filter[type]"] == "put"
        assert chain.calls.last.request.url.params["filter[underlying_symbol]"] == "USO"
        assert chain.calls.last.request.url.params["filter[exp_date_from]"] == today.isoformat()
        assert float(chain.calls.last.request.url.params["filter[strike_from]"]) <= 68.5 * 0.98
        assert a["verdict"] in ("trade", "no_trade") and a["spot"] == 72.1 and a["spot_source"] == "realtime"
        assert a["rate_pct"] == 4.0 and a["realized_vol_20d"] is not None and a["dollars_hidden"] is False
        assert a["chain_trade_date"] == today.isoformat() and a["chain_as_of"] is not None
        assert a["params"]["chain"]["rows"] == 3 * 15 and a["params"]["rationale"]["source"] == "template"
        assert a["params"]["inputs"] == {
            "direction": "down",
            "target": 68.5,
            "stop": 74.0,
            "window_end": (today + timedelta(days=21)).isoformat(),
            "today": today.isoformat(),
            "capital_assigned": 1000.0,
            "default_risk_pct": 2.0,
            "account_size": 2342.0,
            "default_stop_loss_pct": 50.0,
            "risk_free_rate_pct": 4.0,
            "conviction_pct": 60.0,
            "inverse_symbol": "SCO.US",
            "inverse_leverage": 2.0,
        }
        best = a["candidates"][0]
        assert best["rank"] == 1 and best["capital_assigned"] == 1000.0 and best["contracts"] > 0
        assert (
            a["params"]["sizing"]["capital_exceeds_risk_budget"] is True
            and a["params"]["sizing"]["risk_budget"] == 46.84
        )
        assert a["shares_comparison"]["inverse_etf"]["symbol"] == "SCO.US"

        # public read: percentages stay, dollars go
        pub = c.get(f"/api/ideas/{iid}/options").json()
        assert pub["id"] == a["id"] and pub["dollars_hidden"] is True
        p0 = pub["candidates"][0]
        assert (
            p0["capital_assigned"] is None
            and p0["contracts"] is None
            and p0["at_risk"] is None
            and p0["risk_budget"] is None
        )
        assert (
            p0["ret_at_target_window_end_pct"] == best["ret_at_target_window_end_pct"] and p0["debit"] == best["debit"]
        )
        assert (
            pub["shares_comparison"]["shares"]["pnl_abs"] is None
            and pub["shares_comparison"]["shares"]["return_pct"] is not None
        )
        assert pub["params"]["inputs"]["capital_assigned"] is None and pub["params"]["inputs"]["account_size"] is None
        assert (
            pub["params"]["sizing"]["risk_budget"] is None
            and pub["params"]["sizing"]["capital_exceeds_risk_budget"] is True
        )
        assert p0["account_size"] is None
        writer = c.get(f"/api/ideas/{iid}/options", headers=WRITE).json()
        assert writer["dollars_hidden"] is False and writer["candidates"][0]["capital_assigned"] == 1000.0

        # a failing chain is a 502 and stores nothing
        chain.mock(return_value=httpx.Response(500, text="upstream down"))
        fail = c.post(f"/api/ideas/{iid}/options", headers=WRITE)
        assert fail.status_code == 502 and "HTTP 500" in fail.json()["detail"]["message"]
        with Local() as db:
            assert db.execute(select(func.count()).select_from(OptionAnalysis)).scalar_one() == 1
        # an empty chain is also an error, not an empty analysis
        chain.mock(return_value=httpx.Response(200, json={"meta": {}, "data": []}))
        assert c.post(f"/api/ideas/{iid}/options", headers=WRITE).status_code == 502
        # the latest run is what GET returns
        chain.mock(return_value=httpx.Response(200, json=chain_fixture(today, today + timedelta(days=21))))
        second = c.post(f"/api/ideas/{iid}/options", headers=WRITE).json()
        assert c.get(f"/api/ideas/{iid}/options").json()["id"] == second["id"] != a["id"]
        # deleting the idea removes its analyses
        assert c.delete(f"/api/ideas/{iid}", headers=WRITE).status_code == 204
        with Local() as db:
            assert db.execute(select(func.count()).select_from(OptionAnalysis)).scalar_one() == 0


def test_endpoint_rejects_an_ended_window(client, no_model):
    c, _ = client
    with respx.mock(assert_all_called=False) as m:
        from api.tests.test_ideas_api import MU_BARS, QUOTE_MU

        m.get(f"{BASE_URL}/real-time/MU.US").mock(return_value=httpx.Response(200, json=QUOTE_MU))
        m.get(f"{BASE_URL}/eod/MU.US").mock(return_value=httpx.Response(200, json=MU_BARS))
        iid = c.post("/api/ideas", json=idea_body(), headers=WRITE).json()["id"]  # window ended in Aug 2026
        c.patch("/api/settings", json={"options_enabled": True}, headers=WRITE)
        r = c.post(f"/api/ideas/{iid}/options", headers=WRITE)
    assert r.status_code == 422 and "window ended" in r.json()["detail"]
