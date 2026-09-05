"""Resolver unit tests on labeled fixture price paths. No network, no database."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from api.services.resolver import Bar, Context, decide, evaluate, hypothetical_pnl, progress_pct, signed_return
from api.services.rules import describe, first_level, validate_invalidation, validate_rule

D0 = date(2026, 7, 1)


def path(closes, start=D0, lows=None, highs=None):
    """FIXTURE: one bar per calendar day starting at `start`."""
    out = []
    for i, c in enumerate(closes):
        lo = lows[i] if lows else c
        hi = highs[i] if highs else c
        out.append(Bar(as_of=start + timedelta(days=i), close=c, open=c, high=hi, low=lo))
    return out


def ctx(direction="up", entry=100.0, days=10, bench_entry=None):
    return Context(
        direction=direction,
        entry_price=entry,
        entry_date=D0,
        window_start=D0,
        window_end=D0 + timedelta(days=days),
        benchmark_entry=bench_entry,
    )


# --- math -----------------------------------------------------------------------------------------------------


def test_signed_return_inverts_for_down_ideas():
    assert signed_return("up", 100, 110) == pytest.approx(0.10)
    assert signed_return("down", 100, 110) == pytest.approx(-0.10)
    assert signed_return("down", 100, 90) == pytest.approx(0.10)


def test_relative_return_is_the_spread():
    # instrument +10%, benchmark +4% -> outperform +6%; underperform -6%
    assert signed_return("outperform", 100, 110, 50, 52) == pytest.approx(0.06)
    assert signed_return("underperform", 100, 110, 50, 52) == pytest.approx(-0.06)
    assert signed_return("outperform", 100, 110, None, 52) is None


def test_range_has_no_pnl_sign():
    assert signed_return("range", 100, 105) is None
    assert hypothetical_pnl(1000, None) == (None, None)
    assert hypothetical_pnl(1000, 0.05) == (5.0, 50.0)


# --- rules ----------------------------------------------------------------------------------------------------


def test_level_close_at_or_below_hits_on_first_qualifying_close():
    bars = path([100, 99, 97, 96.5, 95])
    hit = evaluate(
        {"type": "level", "comparator": "close_at_or_below", "level": 97}, ctx("down"), bars, [], D0 + timedelta(4)
    )
    assert hit.hit and hit.on == D0 + timedelta(2) and hit.price == 97


def test_level_touch_uses_low_and_high():
    bars = path([100, 100, 100], lows=[100, 94, 100], highs=[100, 100, 100])
    below = evaluate(
        {"type": "level", "comparator": "touch_at_or_below", "level": 95}, ctx("down"), bars, [], D0 + timedelta(2)
    )
    assert below.hit and below.on == D0 + timedelta(1) and below.price == 95
    above = evaluate(
        {"type": "level", "comparator": "touch_at_or_above", "level": 101}, ctx(), bars, [], D0 + timedelta(2)
    )
    assert not above.hit


def test_direction_only_resolves_once_window_closed():
    bars = path([100, 103, 105])
    c = ctx(days=10)
    assert not evaluate({"type": "direction"}, c, bars, [], D0 + timedelta(2)).hit
    # window over (today past window_end, no bar on the exact end date)
    hit = evaluate({"type": "direction"}, c, bars, [], D0 + timedelta(11))
    assert hit.hit and hit.price == 105
    # down idea with the same path is wrong
    assert not evaluate({"type": "direction"}, ctx("down", days=10), bars, [], D0 + timedelta(11)).hit


def test_pct_move_hits_at_any_close_inside_window():
    bars = path([100, 102, 104.5, 103])
    hit = evaluate({"type": "pct_move", "pct": 4.0}, ctx(), bars, [], D0 + timedelta(3))
    assert hit.hit and hit.on == D0 + timedelta(2)
    assert not evaluate({"type": "pct_move", "pct": 5.0}, ctx(), bars, [], D0 + timedelta(3)).hit


def test_relative_uses_benchmark_spread():
    bars = path([100, 104, 108])
    bench = path([50, 51, 52])  # +2%, +4%
    rule = {"type": "relative", "benchmark": "SPY.US", "spread_pct": 3.0}
    hit = evaluate(rule, ctx("outperform", bench_entry=50), bars, bench, D0 + timedelta(2))
    assert hit.hit and hit.on == D0 + timedelta(2)  # +8% vs +4% = 4% spread
    assert not evaluate(rule, ctx("outperform", bench_entry=None), bars, bench, D0 + timedelta(2)).hit


def test_all_of_and_any_of():
    bars = path([100, 103, 106])
    a = {"type": "level", "comparator": "close_at_or_above", "level": 102}
    b = {"type": "level", "comparator": "close_at_or_above", "level": 105}
    all_hit = evaluate({"type": "all_of", "rules": [a, b]}, ctx(), bars, [], D0 + timedelta(2))
    assert all_hit.hit and all_hit.on == D0 + timedelta(2)
    any_hit = evaluate({"type": "any_of", "rules": [a, b]}, ctx(), bars, [], D0 + timedelta(2))
    assert any_hit.hit and any_hit.on == D0 + timedelta(1)
    assert not evaluate({"type": "all_of", "rules": [a, {"type": "pct_move", "pct": 10}]}, ctx(), bars, [], D0).hit


def test_rules_outside_the_window_are_ignored():
    bars = path([100, 120], start=D0 - timedelta(days=5)) + path([100, 101], start=D0)
    hit = evaluate(
        {"type": "level", "comparator": "close_at_or_above", "level": 110}, ctx(), bars, [], D0 + timedelta(1)
    )
    assert not hit.hit


# --- decision order -----------------------------------------------------------------------------------------------


def test_stop_hit_closes_as_wrong_with_pnl_at_stop():
    bars = path([100, 96, 94, 110])
    d = decide(
        ctx(),
        {"type": "level", "comparator": "close_at_or_above", "level": 108},
        {"type": "level", "comparator": "close_at_or_below", "level": 95},
        False,
        1000,
        bars,
        [],
        D0 + timedelta(3),
    )
    assert d.status == "wrong" and d.reason == "stop_hit" and d.on == D0 + timedelta(2)
    assert d.pnl_pct == pytest.approx(-6.0) and d.pnl_abs == pytest.approx(-60.0)
    assert [e["event_type"] for e in d.events] == ["stop_hit"]


def test_note_only_invalidation_annotates_and_continues():
    bars = path([100, 94, 110])
    d = decide(
        ctx(),
        {"type": "level", "comparator": "close_at_or_above", "level": 108},
        {"type": "level", "comparator": "close_at_or_below", "level": 95},
        True,
        1000,
        bars,
        [],
        D0 + timedelta(2),
    )
    assert d.status == "right" and d.reason == "target_hit"
    assert [e["event_type"] for e in d.events] == ["stop_hit", "target_hit"]
    assert d.events[0]["note"].startswith("note only")


def test_earlier_of_stop_and_target_wins():
    bars = path([100, 110, 94])  # target first, then stop
    d = decide(
        ctx(),
        {"type": "level", "comparator": "close_at_or_above", "level": 108},
        {"type": "level", "comparator": "close_at_or_below", "level": 95},
        False,
        1000,
        bars,
        [],
        D0 + timedelta(2),
    )
    assert d.status == "right"


def test_expiry_records_direction_right_separately_from_target():
    bars = path([100, 102, 103])
    d = decide(
        ctx(days=2),
        {"type": "level", "comparator": "close_at_or_above", "level": 110},
        None,
        False,
        1000,
        bars,
        [],
        D0 + timedelta(2),
    )
    assert d.status == "expired" and d.reason == "expired:direction_right" and d.direction_right is True
    assert d.pnl_pct == pytest.approx(3.0)
    bars_down = path([100, 99, 98])
    d2 = decide(
        ctx(days=2),
        {"type": "level", "comparator": "close_at_or_above", "level": 110},
        None,
        False,
        1000,
        bars_down,
        [],
        D0 + timedelta(2),
    )
    assert d2.reason == "expired:direction_wrong"


def test_open_idea_writes_a_progress_event_with_pnl():
    bars = path([100, 101.5])
    d = decide(
        ctx("down"),
        {"type": "level", "comparator": "close_at_or_below", "level": 90},
        None,
        False,
        500,
        bars,
        [],
        D0 + timedelta(1),
    )
    assert d.status == "open" and d.events[0]["event_type"] == "progress"
    assert d.pnl_pct == pytest.approx(-1.5) and d.pnl_abs == pytest.approx(-7.5)
    assert d.events[0]["note"] == "pnl -1.50%"


def test_no_bars_means_no_decision():
    d = decide(ctx(), {"type": "direction"}, None, False, 1000, [], [], D0)
    assert d.status == "open" and d.events == []


# --- helpers ------------------------------------------------------------------------------------------------------


def test_progress_bar_semantics():
    c = ctx("down", entry=100, days=10)
    pct, kind = progress_pct(c, {"type": "level", "comparator": "close_at_or_below", "level": 90}, 95, D0)
    assert (pct, kind) == (50.0, "price")
    pct, kind = progress_pct(c, {"type": "direction"}, 95, D0 + timedelta(5))
    assert (pct, kind) == (50.0, "time")
    pct, kind = progress_pct(ctx(), {"type": "pct_move", "pct": 4.0}, 102, D0)
    assert pct == pytest.approx(50.0) and kind == "price"


def test_rule_validation_and_descriptions():
    r = validate_rule({"type": "level", "comparator": "close_at_or_below", "level": 68.5, "suggested": True})
    assert r["suggested"] is True and first_level(r) == 68.5
    with pytest.raises(ValidationError):
        validate_rule({"type": "level", "comparator": "sideways", "level": 1})
    with pytest.raises(ValidationError):
        validate_invalidation({"type": "direction"})
    assert describe(r, "down") == "closes at or below 68.5"
    assert (
        describe({"type": "relative", "benchmark": "XLE.US", "spread_pct": 3}, "outperform")
        == "outperforms XLE.US by 3%"
    )
    assert (
        describe({"type": "any_of", "rules": [r, {"type": "pct_move", "pct": 4}]}, "down")
        == "(closes at or below 68.5 or moves 4% down at any close)"
    )


# --- relative ideas: path-dependent hit plus the window-end spread -------------------------------------------------


def test_relative_decision_records_spread_at_window_end_only_when_closed():
    from api.services.resolver import spread_pct

    bars = path([100, 104, 108, 106])  # instrument
    bench = path([50, 50, 50, 52])  # benchmark flat, then +4% on the last day
    rule = {"type": "relative", "benchmark": "SPY.US", "spread_pct": 3.0}
    c = ctx("outperform", days=3, bench_entry=50)
    # window still open on day 2: hit on day 1 (+4%), no window-end spread yet
    d = decide(c, rule, None, False, 1000, bars[:3], bench[:3], D0 + timedelta(2))
    assert d.status == "right" and d.on == D0 + timedelta(1) and d.spread_at_window_end_pct is None
    # window closed: same hit, but the window-end spread is +6% - 4% = +2%
    d2 = decide(c, rule, None, False, 1000, bars, bench, D0 + timedelta(3))
    assert d2.status == "right" and d2.pnl_pct == pytest.approx(4.0)
    assert d2.spread_at_window_end_pct == pytest.approx(2.0)
    assert spread_pct("underperform", 100, 106, 50, 52) == pytest.approx(-2.0)
