"""Pricing module tests. Written before the pricing code and before any candidate generation (owner rule).

Oracles used here are independent of api/services/pricing.py:
  * textbook Black-Scholes values (Hull, Options, Futures and Other Derivatives, example 15.6: S=42, K=40, r=10%,
    sigma=20%, T=0.5 -> c=4.76, p=0.81; and the widely tabulated S=K=100, T=1, r=5%, sigma=20% -> c=10.4506,
    p=5.5735);
  * put-call parity, which holds for any inputs;
  * `statistics.NormalDist` as a second, independent normal CDF for hand-computed grid cells;
  * finite differences for the Greeks;
  * brute-force scans for breakevens and days-of-theta.
Nothing in this file touches the network or a database. All inputs are labeled fixtures.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import NormalDist

import pytest

from api.services.pricing import (
    Leg,
    breakevens,
    bs_greeks,
    bs_price,
    days_of_theta,
    grid_dates,
    grid_prices,
    implied_vol,
    payoff_at_expiry,
    probability_of_profit,
    scenario_grid,
    structure_ask,
    structure_bid,
    structure_mid,
    structure_value,
    year_fraction,
)

N = NormalDist().cdf  # independent CDF


def bs_reference(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """Independent Black-Scholes written out longhand with statistics.NormalDist (not the module under test)."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if right == "call":
        return S * math.exp(-q * T) * N(d1) - K * math.exp(-r * T) * N(d2)
    return K * math.exp(-r * T) * N(-d2) - S * math.exp(-q * T) * N(-d1)


# --- known values ---------------------------------------------------------------------------------------------------


def test_hull_example_15_6():
    # Hull: S=42, K=40, r=10%, sigma=20%, T=6 months -> c = 4.76, p = 0.81
    assert bs_price(42, 40, 0.5, 0.10, 0.20, "call") == pytest.approx(4.76, abs=0.005)
    assert bs_price(42, 40, 0.5, 0.10, 0.20, "put") == pytest.approx(0.81, abs=0.005)


def test_tabulated_atm_one_year():
    assert bs_price(100, 100, 1.0, 0.05, 0.20, "call") == pytest.approx(10.4506, abs=1e-4)
    assert bs_price(100, 100, 1.0, 0.05, 0.20, "put") == pytest.approx(5.5735, abs=1e-4)


def test_zero_rate_atm_call_equals_put():
    c = bs_price(100, 100, 1.0, 0.0, 0.20, "call")
    p = bs_price(100, 100, 1.0, 0.0, 0.20, "put")
    assert c == pytest.approx(p)
    assert c == pytest.approx(7.9656, abs=1e-4)  # 100 * (N(0.1) - N(-0.1))


def test_matches_independent_implementation_on_a_grid():
    for S in (50.0, 72.1, 100.0, 141.96):
        for K in (0.8 * S, S, 1.15 * S):
            for T in (7 / 365, 45 / 365, 1.0):
                for sigma in (0.12, 0.33, 0.8):
                    for right in ("call", "put"):
                        got = bs_price(S, K, T, 0.04, sigma, right, q=0.01)
                        ref = bs_reference(S, K, T, 0.04, sigma, right, q=0.01)
                        assert got == pytest.approx(ref, abs=1e-9), (S, K, T, sigma, right)


# --- put-call parity -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("q", [0.0, 0.02])
def test_put_call_parity(q):
    # c - p = S e^{-qT} - K e^{-rT}, for every S, K, T, r, sigma
    for S in (30.0, 68.5, 100.0, 250.0):
        for K in (0.7 * S, 0.95 * S, S, 1.05 * S, 1.4 * S):
            for T in (1 / 365, 21 / 365, 0.5, 2.0):
                for r in (0.0, 0.04, 0.09):
                    for sigma in (0.05, 0.3, 1.2):
                        c = bs_price(S, K, T, r, sigma, "call", q=q)
                        p = bs_price(S, K, T, r, sigma, "put", q=q)
                        assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-9)


def test_parity_also_holds_at_expiry_and_zero_vol():
    assert bs_price(105, 100, 0.0, 0.05, 0.2, "call") - bs_price(105, 100, 0.0, 0.05, 0.2, "put") == pytest.approx(5.0)
    c0 = bs_price(105, 100, 0.5, 0.05, 0.0, "call")
    p0 = bs_price(105, 100, 0.5, 0.05, 0.0, "put")
    assert c0 - p0 == pytest.approx(105 - 100 * math.exp(-0.025))


# --- limits and edge cases ------------------------------------------------------------------------------------------


def test_expiry_returns_intrinsic():
    assert bs_price(110, 100, 0.0, 0.05, 0.3, "call") == 10.0
    assert bs_price(90, 100, 0.0, 0.05, 0.3, "call") == 0.0
    assert bs_price(90, 100, -1 / 365, 0.05, 0.3, "put") == 10.0  # past expiry behaves like expiry


def test_zero_vol_is_discounted_forward_intrinsic():
    assert bs_price(110, 100, 1.0, 0.05, 0.0, "call") == pytest.approx(110 - 100 * math.exp(-0.05))
    assert bs_price(90, 100, 1.0, 0.05, 0.0, "call") == 0.0
    assert bs_price(90, 100, 1.0, 0.05, 0.0, "put") == pytest.approx(100 * math.exp(-0.05) - 90)


def test_no_arbitrage_bounds_and_monotonicity():
    S, r, T, sigma = 100.0, 0.03, 0.25, 0.4
    prev = None
    for K in range(60, 141, 5):
        c = bs_price(S, K, T, r, sigma, "call")
        p = bs_price(S, K, T, r, sigma, "put")
        assert max(S - K * math.exp(-r * T), 0) - 1e-12 <= c <= S
        assert max(K * math.exp(-r * T) - S, 0) - 1e-12 <= p <= K * math.exp(-r * T)
        if prev is not None:
            assert c <= prev  # call value falls as the strike rises
        prev = c
    # more time and more vol are worth more for an ATM option
    assert bs_price(100, 100, 0.5, r, sigma, "call") > bs_price(100, 100, 0.25, r, sigma, "call")
    assert bs_price(100, 100, 0.5, r, 0.6, "put") > bs_price(100, 100, 0.5, r, 0.4, "put")


def test_rejects_nonpositive_prices():
    with pytest.raises(ValueError):
        bs_price(0, 100, 1.0, 0.05, 0.2, "call")
    with pytest.raises(ValueError):
        bs_price(100, -1, 1.0, 0.05, 0.2, "put")
    with pytest.raises(ValueError):
        bs_price(100, 100, 1.0, 0.05, 0.2, "straddle")  # type: ignore[arg-type]


# --- greeks ------------------------------------------------------------------------------------------------------


def test_greeks_against_finite_differences():
    S, K, T, r, sigma = 100.0, 105.0, 0.4, 0.05, 0.3
    for right in ("call", "put"):
        g = bs_greeks(S, K, T, r, sigma, right)
        h = 1e-3
        delta_fd = (bs_price(S + h, K, T, r, sigma, right) - bs_price(S - h, K, T, r, sigma, right)) / (2 * h)
        gamma_fd = (
            bs_price(S + h, K, T, r, sigma, right)
            - 2 * bs_price(S, K, T, r, sigma, right)
            + bs_price(S - h, K, T, r, sigma, right)
        ) / (h * h)
        vega_fd = (bs_price(S, K, T, r, sigma + 1e-4, right) - bs_price(S, K, T, r, sigma - 1e-4, right)) / (2e-4) / 100
        theta_fd = bs_price(S, K, T - 1 / 365, r, sigma, right) - bs_price(S, K, T, r, sigma, right)
        rho_fd = (bs_price(S, K, T, r + 1e-4, sigma, right) - bs_price(S, K, T, r - 1e-4, sigma, right)) / (2e-4) / 100
        assert g.delta == pytest.approx(delta_fd, abs=1e-6)
        assert g.gamma == pytest.approx(gamma_fd, abs=1e-5)
        assert g.vega == pytest.approx(vega_fd, abs=1e-6)  # per 1 vol point
        assert g.theta == pytest.approx(theta_fd, abs=2e-4)  # per calendar day, one-day decay
        assert g.rho == pytest.approx(rho_fd, abs=1e-6)  # per 1 rate point


def test_greek_identities():
    S, K, T, r, sigma = 72.1, 70.0, 30 / 365, 0.04, 0.33
    c, p = bs_greeks(S, K, T, r, sigma, "call"), bs_greeks(S, K, T, r, sigma, "put")
    assert c.delta - p.delta == pytest.approx(1.0)
    assert 0 < c.delta < 1 and -1 < p.delta < 0
    assert c.gamma == pytest.approx(p.gamma) and c.gamma > 0
    assert c.vega == pytest.approx(p.vega) and c.vega > 0
    assert c.theta < 0  # a long, near-the-money call decays


def test_implied_vol_roundtrip_and_failure_modes():
    price = bs_price(100, 95, 0.3, 0.02, 0.37, "put")
    assert implied_vol(price, 100, 95, 0.3, 0.02, "put") == pytest.approx(0.37, abs=1e-6)
    assert implied_vol(0.0, 100, 120, 0.3, 0.02, "call") is None  # nothing reproduces a zero price
    assert implied_vol(4.0, 100, 95, 0.3, 0.02, "call") is None  # below intrinsic: no arbitrage-free vol


# --- structures ---------------------------------------------------------------------------------------------------


E = date(2026, 9, 5)
X = date(2027, 9, 5)  # 365 days later -> T = 1.0 exactly


def leg(right, strike, side=1, iv=0.20, expiry=X, bid=None, ask=None, mid=None):
    return Leg(right=right, strike=strike, expiry=expiry, side=side, qty=1, iv=iv, bid=bid, ask=ask, mid=mid)


def test_year_fraction_is_calendar_days_over_365():
    assert year_fraction(E, X) == pytest.approx(1.0)
    assert year_fraction(E, E + timedelta(days=21)) == pytest.approx(21 / 365)
    assert year_fraction(X, E) == 0.0  # never negative


def test_single_leg_value_matches_black_scholes():
    assert structure_value([leg("call", 100)], 100, E, 0.05) == pytest.approx(10.4506, abs=1e-4)
    assert structure_value([leg("put", 100)], 100, E, 0.05) == pytest.approx(5.5735, abs=1e-4)


def test_debit_call_spread_value_breakeven_and_payoff():
    legs = [leg("call", 100, +1), leg("call", 110, -1)]
    c100 = bs_reference(100, 100, 1.0, 0.05, 0.2, "call")
    c110 = bs_reference(100, 110, 1.0, 0.05, 0.2, "call")
    debit = structure_value(legs, 100, E, 0.05)
    assert debit == pytest.approx(c100 - c110, abs=1e-9)
    assert debit == pytest.approx(4.4106, abs=2e-3)  # hand: 10.4506 - 6.0401
    assert 0 < debit < 10 * math.exp(-0.05)  # a 10-wide spread is worth less than its discounted width
    assert breakevens(legs, debit) == [pytest.approx(100 + debit)]
    assert payoff_at_expiry(legs, 120) == 10.0
    assert payoff_at_expiry(legs, 105) == 5.0
    assert payoff_at_expiry(legs, 95) == 0.0
    # value at expiry equals the payoff
    assert structure_value(legs, 120, X, 0.05) == 10.0


def test_put_spread_breakeven_like_the_mockup():
    # bearish idea: long 70 put, short 66 put, priced 1.42 -> breakeven 68.58
    legs = [leg("put", 70, +1, iv=0.33), leg("put", 66, -1, iv=0.33)]
    assert breakevens(legs, 1.42) == [pytest.approx(68.58)]
    assert payoff_at_expiry(legs, 60) == 4.0
    assert payoff_at_expiry(legs, 68.5) == pytest.approx(1.5)
    assert payoff_at_expiry(legs, 72) == 0.0


def test_straddle_and_strangle_have_two_breakevens():
    straddle = [leg("call", 100, +1), leg("put", 100, +1)]
    assert breakevens(straddle, 8.0) == [pytest.approx(92.0), pytest.approx(108.0)]
    strangle = [leg("call", 105, +1), leg("put", 95, +1)]
    assert breakevens(strangle, 3.0) == [pytest.approx(92.0), pytest.approx(108.0)]
    assert breakevens([leg("call", 100)], 10.4506) == [pytest.approx(110.4506)]
    assert breakevens([leg("put", 100)], 5.5735) == [pytest.approx(94.4265)]


def test_structure_market_quotes_from_legs():
    long_leg = leg("put", 70, +1, bid=2.80, ask=2.90, mid=2.85)
    short_leg = leg("put", 66, -1, bid=1.40, ask=1.46, mid=1.43)
    assert structure_mid([long_leg, short_leg]) == pytest.approx(1.42)
    assert structure_bid([long_leg, short_leg]) == pytest.approx(
        2.80 - 1.46
    )  # sell the long at bid, buy back short at ask
    assert structure_ask([long_leg, short_leg]) == pytest.approx(2.90 - 1.40)
    assert structure_mid([leg("call", 100)]) is None  # no quotes -> no number, never a guess


# --- probability of profit --------------------------------------------------------------------------------------


def test_probability_of_profit_long_call_is_n_d2_at_breakeven():
    S, r, sigma = 100.0, 0.05, 0.2
    debit = bs_reference(S, 100, 1.0, r, sigma, "call")
    be = 100 + debit
    d2_be = (math.log(S / be) + (r - 0.5 * sigma * sigma) * 1.0) / (sigma * 1.0)
    expected = N(d2_be)
    assert expected == pytest.approx(0.364, abs=2e-3)  # hand-computed
    assert probability_of_profit([leg("call", 100)], debit, S, E, r, sigma) == pytest.approx(expected, abs=1e-9)


def test_probability_of_profit_put_and_straddle():
    S, r, sigma = 100.0, 0.0, 0.25
    debit = bs_reference(S, 100, 1.0, r, sigma, "put")
    be = 100 - debit
    d2_be = (math.log(S / be) - 0.5 * sigma * sigma) / sigma
    assert probability_of_profit([leg("put", 100)], debit, S, E, r, sigma) == pytest.approx(N(-d2_be), abs=1e-9)
    # straddle: profitable below the low breakeven or above the high one; both tails add up
    straddle = [leg("call", 100), leg("put", 100)]
    debit_s = 2 * debit
    lo, hi = 100 - debit_s, 100 + debit_s
    p_lo = N(-(math.log(S / lo) - 0.5 * sigma**2) / sigma)
    p_hi = N((math.log(S / hi) - 0.5 * sigma**2) / sigma)
    assert probability_of_profit(straddle, debit_s, S, E, r, sigma) == pytest.approx(p_lo + p_hi, abs=1e-9)
    # a cheaper structure is more likely to profit
    assert probability_of_profit([leg("put", 100)], debit * 0.5, S, E, r, sigma) > probability_of_profit(
        [leg("put", 100)], debit, S, E, r, sigma
    )


# --- scenario grid ---------------------------------------------------------------------------------------------------


def test_scenario_grid_against_hand_computed_cells():
    legs = [leg("call", 100)]
    r = 0.05
    debit = bs_reference(100, 100, 1.0, r, 0.2, "call")  # 10.4506
    mid_date = E + timedelta(days=219)  # 146 days left -> T = 0.4
    prices = [90.0, 95.0, 100.0, 105.0, 110.0]
    dates = [E, mid_date, X]
    grid = scenario_grid(legs, debit, prices, dates, r)
    assert len(grid) == 5 and all(len(row) == 3 for row in grid)
    # entry column, at spot: worth exactly the debit -> 0% return
    assert grid[2][0].value == pytest.approx(debit) and grid[2][0].ret_pct == pytest.approx(0.0)
    # entry column, S=110: hand value 17.663 (d1 = 0.82655, d2 = 0.62655)
    assert grid[4][0].value == pytest.approx(17.663, abs=1e-3)
    assert grid[4][0].value == pytest.approx(bs_reference(110, 100, 1.0, r, 0.2, "call"), abs=1e-9)
    # middle column uses T = 146/365
    assert grid[1][1].value == pytest.approx(bs_reference(95, 100, 146 / 365, r, 0.2, "call"), abs=1e-9)
    # expiry column is intrinsic, so the return at 110 is (10 - debit) / debit
    assert [c.value for c in (row[2] for row in grid)] == [0.0, 0.0, 0.0, 5.0, 10.0]
    assert grid[4][2].ret_pct == pytest.approx((10 - debit) / debit * 100)
    assert grid[0][2].ret_pct == pytest.approx(-100.0)
    # a call is worth more at higher prices and, out of the money, less as time passes
    for row_hi, row_lo in zip(grid[1:], grid[:-1], strict=True):
        assert all(h.value >= lo.value for h, lo in zip(row_hi, row_lo, strict=True))
    assert grid[0][0].value > grid[0][1].value > grid[0][2].value


def test_scenario_grid_holds_iv_constant_per_leg():
    # a put spread where each leg carries its own IV: the grid must use them, not an average
    legs = [leg("put", 70, +1, iv=0.40), leg("put", 66, -1, iv=0.30)]
    debit = structure_value(legs, 72.1, E, 0.04)
    grid = scenario_grid(legs, debit, [68.5], [E + timedelta(days=20)], 0.04)
    T = (X - (E + timedelta(days=20))).days / 365
    expected = bs_reference(68.5, 70, T, 0.04, 0.40, "put") - bs_reference(68.5, 66, T, 0.04, 0.30, "put")
    assert grid[0][0].value == pytest.approx(expected, abs=1e-9)
    assert grid[0][0].pnl == pytest.approx(expected - debit)


def test_grid_prices_run_from_stop_to_five_percent_past_target():
    # bearish USO idea from the mockup: stop 74, spot 72.1, target 68.5
    rows = grid_prices(stop=74.0, target=68.5, spot=72.1, direction="down")
    assert len(rows) == 9
    assert rows[0] == 74.0 and rows[-1] == pytest.approx(68.5 * 0.95)
    assert 68.5 in rows and 72.1 in rows  # target and spot are exact rows
    assert rows == sorted(rows, reverse=True)
    # bullish: ascending from stop to target + 5%
    up = grid_prices(stop=110.0, target=130.0, spot=120.0, direction="up")
    assert up[0] == 110.0 and up[-1] == pytest.approx(136.5) and up == sorted(up) and 130.0 in up and 120.0 in up
    # no stop: start one target-distance on the wrong side of spot
    no_stop = grid_prices(stop=None, target=68.5, spot=72.1, direction="down")
    assert no_stop[0] == pytest.approx(72.1 + (72.1 - 68.5))


def test_grid_dates_weekly_then_window_end_then_expiry():
    entry, window_end, expiry = date(2026, 9, 4), date(2026, 9, 25), date(2026, 10, 17)
    assert grid_dates(entry, window_end, expiry) == [
        date(2026, 9, 4),
        date(2026, 9, 11),
        date(2026, 9, 18),
        date(2026, 9, 25),
        date(2026, 10, 17),
    ]
    # window end that is not on the weekly grid still appears; expiry before the window end truncates the grid
    assert grid_dates(entry, date(2026, 9, 23), expiry)[-2:] == [date(2026, 9, 23), expiry]
    assert grid_dates(entry, window_end, date(2026, 9, 18)) == [date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 18)]


# --- days of theta you can afford ------------------------------------------------------------------------------------


def test_days_of_theta_matches_brute_force_closed_form():
    # ATM call, zero rate: value = S (2 N(sigma sqrt(T) / 2) - 1). Stop loss 50% -> first day the value halves.
    S, sigma = 100.0, 0.2
    expiry = E + timedelta(days=100)
    legs = [leg("call", 100, iv=sigma, expiry=expiry)]
    debit = structure_value(legs, S, E, 0.0)
    assert debit == pytest.approx(S * (2 * N(sigma * math.sqrt(100 / 365) / 2) - 1), abs=1e-9)

    def closed_form(days_left: int) -> float:
        return S * (2 * N(sigma * math.sqrt(days_left / 365) / 2) - 1) if days_left > 0 else 0.0

    brute = next(d for d in range(1, 101) if closed_form(100 - d) <= 0.5 * debit)
    assert brute == 76  # hand: value halves when T quarters, 25 days left
    assert days_of_theta(legs, debit, S, E, 0.0, stop_loss_pct=50.0) == brute


def test_days_of_theta_is_none_when_the_structure_keeps_its_value():
    # deep in the money put spread at spot: worth its full width at expiry, never loses 50%
    legs = [
        leg("put", 90, +1, iv=0.2, expiry=E + timedelta(days=30)),
        leg("put", 80, -1, iv=0.2, expiry=E + timedelta(days=30)),
    ]
    debit = structure_value(legs, 70.0, E, 0.0)
    assert days_of_theta(legs, debit, 70.0, E, 0.0, stop_loss_pct=50.0) is None
    # and an option already worthless at spot is stopped out on day one
    otm = [leg("call", 150, iv=0.2, expiry=E + timedelta(days=30))]
    assert days_of_theta(otm, 5.0, 100.0, E, 0.0, stop_loss_pct=50.0) == 1
