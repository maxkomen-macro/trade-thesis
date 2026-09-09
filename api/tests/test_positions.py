"""Phase 6: option positions. Pure exit-engine tests (one per exit reason, in the spec's order) on labeled FIXTURE
marks, then the take / mark / close / settle / divergence flow on SQLite with EODHD mocked. No network."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db.models import Base, ChainDailySummary, ChainSnapshot, Idea, OptionPosition
from api.db.session import get_db, get_db_optional
from api.main import app
from api.services import chains, options
from api.services.eodhd import BASE_URL, OPTIONS_CONTRACTS_PATH
from api.services.positions import ExitRules, Mark, decide_exit, intrinsic_value, structure_quote
from api.tests.test_ideas_api import MU_BARS, QUOTE_MU, idea_body
from api.tests.test_pricing import bs_reference

WRITE = {"Authorization": "Bearer test-write-token"}
CRON = {"Authorization": "Bearer test-cron-secret"}
D0 = date(2026, 7, 1)


# --- pure: exit engine ---------------------------------------------------------------------------------------------


def rules(**over) -> ExitRules:
    base = dict(
        entry_debit=2.0,
        contracts=3,
        take_profit_pct=100.0,
        stop_loss_pct=50.0,
        time_stop_date=D0 + timedelta(days=10),
        expiry=D0 + timedelta(days=15),
    )
    base.update(over)
    return ExitRules(**base)


def marks(values, start=D0, source="chain_mid"):
    """FIXTURE: one mark per calendar day."""
    return [Mark(start + timedelta(days=i), v, source) for i, v in enumerate(values)]


def test_open_while_no_rule_trips():
    d = decide_exit(rules(), marks([2.0, 2.4, 1.6, 3.0]), None, None)
    assert d.status == "open" and d.reason is None and len(d.checks) == 4


def test_exit_idea_resolved_first_even_when_stop_would_trip():
    # the idea's terminal bar is day 2; the mark that day (0.9) would also be a stop loss, but resolution wins
    d = decide_exit(rules(), marks([2.0, 1.8, 0.9]), D0 + timedelta(days=2), "target_hit")
    assert d.status == "closed" and d.reason == "idea_resolved:target_hit" and d.on == D0 + timedelta(days=2)
    assert d.value == 0.9 and d.pnl_pct == pytest.approx(-55.0) and d.pnl_abs == pytest.approx(3 * 100 * (0.9 - 2.0))
    # resolved on a day without a mark yet: stays open until a mark on or after that date arrives
    d2 = decide_exit(rules(), marks([2.0, 1.8]), D0 + timedelta(days=5), "target_hit")
    assert d2.status == "open"


def test_exit_stop_loss():
    d = decide_exit(rules(), marks([2.0, 1.5, 1.0, 1.4]), None, None)
    assert d.reason == "stop_loss" and d.on == D0 + timedelta(days=2) and d.value == 1.0
    assert d.pnl_pct == pytest.approx(-50.0) and d.pnl_abs == pytest.approx(-300.0)
    assert "50% of premium" in d.detail


def test_exit_take_profit():
    d = decide_exit(rules(), marks([2.0, 3.0, 4.2, 5.0]), None, None)
    assert d.reason == "take_profit" and d.on == D0 + timedelta(days=2) and d.value == 4.2
    assert d.pnl_pct == pytest.approx(110.0) and d.pnl_abs == pytest.approx(660.0)


def test_exit_time_stop():
    vals = [2.0, 2.1, 2.2, 2.1, 2.0, 2.2, 2.3, 2.1, 2.0, 2.2, 2.4, 2.5]  # day 10 is the time stop date
    d = decide_exit(rules(), marks(vals), None, None)
    assert d.reason == "time_stop" and d.on == D0 + timedelta(days=10) and d.value == 2.4
    assert d.pnl_pct == pytest.approx(20.0) and "5 days before expiry" in d.detail


def test_exit_expiry_from_settlement_mark_and_from_a_chain_mark_on_expiry_day():
    r = rules(time_stop_date=D0 + timedelta(days=16))  # no time stop before expiry
    settled = marks([2.0, 2.1], start=D0) + [Mark(D0 + timedelta(days=15), 0.0, "expiry_intrinsic")]
    d = decide_exit(r, settled, None, None)
    assert d.reason == "expiry" and d.value == 0.0 and d.pnl_pct == pytest.approx(-100.0)
    # a settlement mark is the outcome whatever the rules say (contracts are gone): not a stop loss
    d2 = decide_exit(rules(), [Mark(D0 + timedelta(days=15), 0.0, "expiry_intrinsic")], None, None)
    assert d2.reason == "expiry"
    # chain still quoting on the expiry date -> the ordinary order applies and expiry is the last check
    d3 = decide_exit(r, [Mark(D0 + timedelta(days=15), 2.5, "chain_mid")], None, None)
    assert d3.reason == "expiry" and d3.value == 2.5


def test_exit_order_within_a_day_and_earliest_day_wins():
    # day 10 is the time stop date and the value is also below the stop: stop loss is checked first
    vals = [2.0] * 10 + [0.8]
    d = decide_exit(rules(), marks(vals), None, None)
    assert d.reason == "stop_loss" and d.on == D0 + timedelta(days=10)
    # take profit on the time-stop day: take profit is checked before the time stop
    d2 = decide_exit(rules(), marks([2.0] * 10 + [4.5]), None, None)
    assert d2.reason == "take_profit"
    # a stop on day 1 beats a take-profit on day 3 (chronological)
    d3 = decide_exit(rules(), marks([2.0, 0.9, 3.0, 5.0]), None, None)
    assert d3.reason == "stop_loss" and d3.on == D0 + timedelta(days=1)
    # marks arrive unsorted: still chronological
    d4 = decide_exit(rules(), list(reversed(marks([2.0, 0.9, 3.0, 5.0]))), None, None)
    assert d4.reason == "stop_loss" and d4.on == D0 + timedelta(days=1)


def test_structure_quote_and_intrinsic():
    legs = [
        {"contract": "L", "side": "long", "qty": 1, "strike": 120, "right": "put"},
        {"contract": "S", "side": "short", "qty": 1, "strike": 110, "right": "put"},
    ]
    quotes = {
        "L": {"bid": 5.0, "ask": 5.4, "mid": 5.2, "iv": 0.33, "delta": -0.45, "theta": -0.05},
        "S": {"bid": 2.0, "ask": 2.2, "mid": 2.1, "iv": 0.36, "delta": -0.2, "theta": -0.03},
    }
    q = structure_quote(legs, quotes)
    assert (
        q["value"] == pytest.approx(3.1)
        and q["bid"] == pytest.approx(5.0 - 2.2)
        and q["ask"] == pytest.approx(5.4 - 2.0)
    )
    assert q["iv"] == pytest.approx(0.33) and q["delta"] == pytest.approx(-0.25) and q["theta"] == pytest.approx(-0.02)
    assert [u["contract"] for u in q["legs"]] == ["L", "S"]
    assert structure_quote(legs, {"L": quotes["L"]}) is None  # a missing leg makes the structure unpriceable
    assert intrinsic_value(legs, 100.0) == pytest.approx(10.0)  # capped at the width
    assert intrinsic_value(legs, 115.0) == pytest.approx(5.0)
    assert intrinsic_value(legs, 125.0) == 0.0


# --- chain record dates and the IV percentile ----------------------------------------------------------------------


def test_record_date_is_the_eastern_date_of_the_quote_stamp():
    rows = chains.normalize(
        [
            {
                "id": "MU260918P00120000",
                "attributes": {
                    "contract": "MU260918P00120000",
                    "exp_date": "2026-09-18",
                    "type": "put",
                    "strike": 120,
                    "bid": 1,
                    "ask": 1.2,
                    "midpoint": 1.1,
                    "bid_date": "2026-09-09T03:59:59.000000Z",  # 23:59:59 ET on Sep 8, verified live 2026-09-09
                    "ask_date": "2026-09-08 19:59:59",
                    "tradetime": "2026-09-08",
                },
            }
        ]
    )
    assert chains.record_date(rows) == date(2026, 9, 8)
    assert chains.quote_timestamps(rows)[1] == date(2026, 9, 8)


# --- API flow on SQLite -------------------------------------------------------------------------------------------

TODAY = datetime.now(UTC).date()
WINDOW_END = TODAY + timedelta(days=21)
EXP_A = WINDOW_END + timedelta(days=21)  # cushion expiry
EXP_B = WINDOW_END - timedelta(days=7)
FIX_IV = 0.33
FIX_RATE = 0.04
SPOT = 120.0  # QUOTE_MU close


def chain_rows(quote_date: date, factor: float = 1.0, spot: float = SPOT, iv: float = FIX_IV) -> list[dict]:
    """FIXTURE: MU puts and calls, strikes 100..140 step 5, two expiries. Mids are Black-Scholes at `iv` scaled by
    `factor` (so a later 'day' can be quoted lower or higher deterministically); bid/ask 2% either side."""
    rows = []
    for exp in (EXP_B, EXP_A):
        T = max((exp - quote_date).days, 1) / 365
        for K in range(100, 141, 5):
            for right in ("put", "call"):
                mid = max(round(bs_reference(spot, K, T, FIX_RATE, iv, right) * factor, 2), 0.05)
                bid, ask = round(mid * 0.98, 2), round(mid * 1.02, 2)
                ct = f"MU{exp:%y%m%d}{'P' if right == 'put' else 'C'}{int(K * 1000):08d}"
                rows.append(
                    {
                        "id": ct,
                        "type": "options-contracts",
                        "attributes": {
                            "contract": ct,
                            "underlying_symbol": "MU",
                            "exp_date": exp.isoformat(),
                            "type": right,
                            "strike": K,
                            "bid": bid,
                            "ask": ask,
                            "midpoint": round((bid + ask) / 2, 3),
                            "last": mid,
                            "volatility": iv,
                            "delta": -0.4 if right == "put" else 0.6,
                            "theta": -0.03,
                            "open_interest": 500,
                            "volume": 100,
                            "dte": (exp - quote_date).days,
                            "bid_date": f"{(quote_date + timedelta(days=1)).isoformat()}T03:59:59.000000Z",
                            "ask_date": f"{quote_date.isoformat()} 19:59:59",
                            "tradetime": quote_date.isoformat(),
                        },
                    }
                )
    return rows


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
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def db(client):
    return next(client.app.dependency_overrides[get_db]())


@pytest.fixture
def chain_state():
    """Mutable chain 'clock': tests advance the quote date and scale the quotes between job runs."""
    return {"date": TODAY, "factor": 1.0, "calls": []}


@pytest.fixture
def eodhd(chain_state, monkeypatch):
    monkeypatch.setattr(options, "_call_rationale_model", lambda payload: (_ for _ in ()).throw(RuntimeError("off")))

    def chain(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        rows = chain_rows(chain_state["date"], chain_state["factor"])
        chain_state["calls"].append(params)
        if "filter[contract]" in params:
            rows = [r for r in rows if r["id"] == params["filter[contract]"]]
        elif "filter[type]" in params:
            rows = [r for r in rows if r["attributes"]["type"] == params["filter[type]"]]
        return httpx.Response(200, json={"meta": {"offset": 0, "limit": 1000}, "data": rows})

    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/real-time/MU.US").mock(return_value=httpx.Response(200, json=QUOTE_MU))
        m.get(f"{BASE_URL}/eod/MU.US").mock(return_value=httpx.Response(200, json=MU_BARS))
        m.get(f"{BASE_URL}{OPTIONS_CONTRACTS_PATH}").mock(side_effect=chain)
        yield m


def open_idea(client, **over) -> dict:
    body = idea_body(
        direction="down",
        success_rule_json={"type": "level", "comparator": "close_at_or_below", "level": 105},
        invalidation_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 135},
        window_start=TODAY.isoformat(),
        window_end=WINDOW_END.isoformat(),
        entry_price=SPOT,
        conviction_pct=60,
        **over,
    )
    r = client.post("/api/ideas", json=body, headers=WRITE)
    assert r.status_code == 201, r.text
    return r.json()


def enable_options(client):
    assert client.patch("/api/settings", json={"options_enabled": True}, headers=WRITE).status_code == 200


def run_selector(client, idea_id: int) -> dict:
    r = client.post(f"/api/ideas/{idea_id}/options", json={}, headers=WRITE)
    assert r.status_code == 201, r.text
    return r.json()


def take(client, idea_id: int, **body) -> httpx.Response:
    return client.post(f"/api/ideas/{idea_id}/positions", json={"rank": 1, **body}, headers=WRITE)


def test_take_expression_quotes_legs_afresh_and_stores_the_entry_mark(client, eodhd, chain_state, db):
    idea = open_idea(client)
    assert take(client, idea["id"]).status_code == 409  # options disabled
    enable_options(client)
    assert take(client, idea["id"]).status_code == 404  # no analysis yet
    a = run_selector(client, idea["id"])
    assert a["verdict"] == "trade" and a["iv_percentile_1y"] is None
    # the selector run stored the band (puts only: a bearish idea with conviction) under the chain's record date
    stored = db.execute(select(ChainSnapshot)).scalars().all()
    assert len(stored) == len(chain_rows(TODAY)) // 2 and {s.as_of for s in stored} == {TODAY}
    assert all(s.right == "put" for s in stored)
    assert a["params"]["chain"]["stored_rows"] == len(stored) and a["params"]["iv_percentile"]["days"] == 0
    best = a["candidates"][0]
    assert client.post(f"/api/ideas/{idea['id']}/positions", json={"rank": 1}).status_code == 401

    calls_before = len(chain_state["calls"])
    r = take(client, idea["id"], take_profit_pct=80, stop_loss_pct=40, time_stop_days_before_expiry=3, note="test")
    assert r.status_code == 201, r.text
    p = r.json()
    # one per-contract request per leg, entry at the structure mid of those fresh quotes
    leg_calls = [c for c in chain_state["calls"][calls_before:] if "filter[contract]" in c]
    assert [c["filter[contract]"] for c in leg_calls] == [lg["contract"] for lg in best["legs"]]
    assert (
        p["name"] == best["name"]
        and p["entry_source"] == "chain_mid"
        and p["entry_debit"] == pytest.approx(best["debit"], abs=1e-4)
    )
    cost = p["entry_debit"] * 100
    assert p["contracts"] == math.floor(1000 / cost) and p["entry_cost"] == pytest.approx(
        p["contracts"] * cost, abs=0.01
    )
    assert p["take_profit_pct"] == 80 and p["stop_loss_pct"] == 40 and p["time_stop_days_before_expiry"] == 3
    assert p["time_stop_date"] == (date.fromisoformat(p["expiry"]) - timedelta(days=3)).isoformat()
    assert p["status"] == "open" and p["pnl_pct"] == 0.0 and len(p["snapshots"]) == 1
    snap = p["snapshots"][0]
    assert snap["as_of"] == TODAY.isoformat() and snap["source"] == "chain_mid" and snap["value"] == p["entry_debit"]
    assert [lg["contract"] for lg in snap["legs"]] == [lg["contract"] for lg in p["legs"]]
    assert p["legs"][0]["entry_mid"] is not None and p["note"] == "test"
    # the idea carries the position summary, the divergence facts and a timeline event; public dollars are hidden
    d = client.get(f"/api/ideas/{idea['id']}", headers=WRITE).json()
    assert d["position"]["id"] == p["id"] and d["position"]["contracts"] == p["contracts"]
    assert (
        d["divergence"]["option_pnl_pct"] == 0.0
        and d["divergence"]["cell"] is None
        and "open" in d["divergence"]["text"]
    )
    assert d["events"][-1]["event_type"] == "position_opened"
    pub = client.get(f"/api/ideas/{idea['id']}").json()
    assert pub["position"]["contracts"] is None and pub["position"]["pnl_abs"] is None
    pub_pos = client.get(f"/api/positions/{p['id']}").json()
    assert pub_pos["dollars_hidden"] is True and pub_pos["contracts"] is None and pub_pos["entry_cost"] is None
    assert pub_pos["snapshots"][0]["pnl_abs"] is None and pub_pos["pnl_pct"] == 0.0
    # a second open position on the same idea is refused
    assert take(client, idea["id"]).status_code == 409
    assert len(client.get(f"/api/ideas/{idea['id']}/positions").json()) == 1


def test_take_with_fill_price_and_contract_count(client, eodhd):
    idea = open_idea(client)
    enable_options(client)
    a = run_selector(client, idea["id"])
    # unknown candidate
    assert (
        client.post(f"/api/ideas/{idea['id']}/positions", json={"candidate_name": "nope"}, headers=WRITE).status_code
        == 422
    )
    r = take(client, idea["id"], fill_price=1.23, contracts=2)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["entry_debit"] == 1.23 and p["entry_source"] == "fill" and p["contracts"] == 2
    assert p["entry_cost"] == pytest.approx(246.0)
    # the mark is the chain mid, so P&L against the stated fill is not zero
    assert p["snapshots"][0]["value"] == pytest.approx(a["candidates"][0]["debit"], abs=1e-4)
    assert p["pnl_pct"] == pytest.approx((p["snapshots"][0]["value"] / 1.23 - 1) * 100, abs=0.01)


def test_take_fails_closed_when_a_leg_cannot_be_quoted(client, eodhd, chain_state, db):
    idea = open_idea(client)
    enable_options(client)
    run_selector(client, idea["id"])

    def broken(request: httpx.Request) -> httpx.Response:
        if "filter[contract]" in dict(request.url.params):
            return httpx.Response(500, text="upstream down")
        return httpx.Response(200, json={"meta": {}, "data": chain_rows(TODAY)})

    eodhd.get(f"{BASE_URL}{OPTIONS_CONTRACTS_PATH}").mock(side_effect=broken)
    r = take(client, idea["id"])
    assert r.status_code == 502 and "HTTP 500" in r.json()["detail"]["message"]
    assert db.execute(select(OptionPosition)).scalars().all() == []


def test_daily_job_marks_from_the_chain_and_stops_out(client, eodhd, chain_state, db):
    idea = open_idea(client)
    enable_options(client)
    run_selector(client, idea["id"])
    p = take(client, idea["id"]).json()
    # same chain date again: no duplicate mark, position open
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert (
        s["positions"]["positions_checked"] == 1 and s["positions"]["marked"] == [] and s["positions"]["closed"] == []
    )
    assert s["positions"]["errors"] == [] and s["positions"]["chain_requests"] == 2  # puts and calls, one page each
    # next day: quotes 20% lower -> a mark, still open (stop is 50%)
    chain_state["date"], chain_state["factor"] = TODAY + timedelta(days=1), 0.8
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert (
        len(s["positions"]["marked"]) == 1
        and s["positions"]["marked"][0]["as_of"] == (TODAY + timedelta(days=1)).isoformat()
    )
    pos = client.get(f"/api/positions/{p['id']}", headers=WRITE).json()
    assert pos["status"] == "open" and len(pos["snapshots"]) == 2
    assert pos["last_value_as_of"] == (TODAY + timedelta(days=1)).isoformat()
    assert -50 < pos["pnl_pct"] < -15  # quotes scaled by 0.8 plus a day of the fixture's Black-Scholes decay
    assert pos["pnl_abs"] == pytest.approx(pos["contracts"] * 100 * (pos["last_value"] - pos["entry_debit"]), abs=0.01)
    # day 3: quotes at 40% -> stop loss
    chain_state["date"], chain_state["factor"] = TODAY + timedelta(days=2), 0.4
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert s["positions"]["closed"] == [
        {
            "position_id": p["id"],
            "idea_id": idea["id"],
            "reason": "stop_loss",
            "on": (TODAY + timedelta(days=2)).isoformat(),
        }
    ]
    pos = client.get(f"/api/positions/{p['id']}", headers=WRITE).json()
    assert pos["status"] == "closed" and pos["exit_reason"] == "stop_loss" and pos["exit_source"] == "chain_mid"
    assert (
        pos["exit_as_of"] == (TODAY + timedelta(days=2)).isoformat()
        and pos["exit_value"] == pos["snapshots"][-1]["value"]
    )
    assert pos["pnl_pct"] < -50
    d = client.get(f"/api/ideas/{idea['id']}", headers=WRITE).json()
    assert d["events"][-1]["event_type"] == "position_closed" and "stop_loss" in d["events"][-1]["note"]
    assert d["status"] == "open" and d["position"]["status"] == "closed"  # the idea itself keeps running
    # chain history accumulated one record date per job day
    assert len(chains.distinct_dates(db, d["instrument"]["id"])) == 3
    # the closed position no longer costs chain requests
    before = len(chain_state["calls"])
    client.post("/api/jobs/resolve", headers=CRON)
    assert len(chain_state["calls"]) == before


def test_take_profit_and_time_stop_via_patch(client, eodhd, chain_state):
    idea = open_idea(client)
    enable_options(client)
    run_selector(client, idea["id"])
    p = take(client, idea["id"], take_profit_pct=30).json()
    chain_state["date"], chain_state["factor"] = TODAY + timedelta(days=1), 1.7  # +70% minus a day of decay
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert s["positions"]["closed"][0]["reason"] == "take_profit"
    pos = client.get(f"/api/positions/{p['id']}").json()
    assert pos["exit_reason"] == "take_profit" and pos["pnl_pct"] > 30
    assert client.patch(f"/api/positions/{p['id']}", json={"stop_loss_pct": 30}, headers=WRITE).status_code == 409

    # a second idea: tighten the time stop so the stored mark already sits past it
    idea2 = open_idea(client, title="second")
    run_selector(client, idea2["id"])
    chain_state["factor"] = 1.0
    p2 = take(client, idea2["id"]).json()
    days = (date.fromisoformat(p2["expiry"]) - (TODAY + timedelta(days=1))).days
    r = client.patch(f"/api/positions/{p2['id']}", json={"time_stop_days_before_expiry": days}, headers=WRITE)
    assert r.status_code == 200 and r.json()["time_stop_date"] == (TODAY + timedelta(days=1)).isoformat()
    assert r.json()["status"] == "closed" and r.json()["exit_reason"] == "time_stop"  # the day-2 mark trips it
    assert r.json()["exit_as_of"] == (TODAY + timedelta(days=1)).isoformat()


def test_idea_resolution_closes_the_position_at_the_next_mark(client, eodhd, chain_state):
    idea = open_idea(client)
    enable_options(client)
    run_selector(client, idea["id"])
    p = take(client, idea["id"]).json()
    closed = client.post(f"/api/ideas/{idea['id']}/close", json={"note": "done"}, headers=WRITE).json()
    assert closed["status"] == "closed_manual"
    terminal = closed["events"][-1]["occurred_on"]  # manual_close on the latest stored close
    chain_state["date"] = max(TODAY, date.fromisoformat(terminal)) + timedelta(days=1)
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert s["positions"]["closed"][0]["reason"] == "idea_resolved:manual"
    pos = client.get(f"/api/positions/{p['id']}", headers=WRITE).json()
    assert pos["status"] == "closed" and pos["exit_reason"] == "idea_resolved:manual"
    d = client.get(f"/api/ideas/{idea['id']}", headers=WRITE).json()
    assert d["divergence"]["resolved"] is True and d["divergence"]["cell"] is not None


def test_expired_position_settles_at_intrinsic_from_the_eod_close(client, eodhd, chain_state, db):
    idea = open_idea(client)
    enable_options(client)
    run_selector(client, idea["id"])
    p = take(client, idea["id"]).json()
    # move the position back in time: opened ten days ago, expired three days ago, never marked since
    row = db.get(OptionPosition, p["id"])
    row.expiry = TODAY - timedelta(days=3)
    row.time_stop_date = row.expiry - timedelta(days=row.time_stop_days_before_expiry)
    row.snapshots[0].as_of = TODAY - timedelta(days=10)  # before the time stop (expiry - 5)
    db.commit()
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert s["positions"]["closed"][0]["reason"] == "expiry"
    pos = client.get(f"/api/positions/{p['id']}", headers=WRITE).json()
    last = pos["snapshots"][-1]
    assert last["source"] == "expiry_intrinsic" and pos["exit_source"] == "expiry_intrinsic"
    assert date.fromisoformat(last["as_of"]) <= TODAY - timedelta(days=3)
    # MU closed at 125 (fixture) on every recent day: a put spread struck at/below 125 is worth its intrinsic
    assert last["spot"] == 125.0 and last["value"] == pytest.approx(intrinsic_value(pos["legs"], 125.0))
    assert pos["exit_value"] == last["value"] and pos["exit_reason"] == "expiry"
    assert last["legs"][0]["intrinsic_at"] == 125.0


def test_manual_close_and_delete(client, eodhd):
    idea = open_idea(client)
    enable_options(client)
    run_selector(client, idea["id"])
    p = take(client, idea["id"]).json()
    assert client.post(f"/api/positions/{p['id']}/close", json={"exit_price": 0.5}).status_code == 401
    r = client.post(f"/api/positions/{p['id']}/close", json={"exit_price": 0.5, "note": "sold"}, headers=WRITE)
    assert r.status_code == 200, r.text
    pos = r.json()
    assert pos["status"] == "closed" and pos["exit_reason"] == "manual" and pos["exit_source"] == "fill"
    assert pos["exit_value"] == 0.5 and pos["pnl_pct"] == pytest.approx((0.5 / pos["entry_debit"] - 1) * 100, abs=0.01)
    assert pos["note"] == "sold"
    assert client.post(f"/api/positions/{p['id']}/close", json={}, headers=WRITE).status_code == 409
    before = client.get(f"/api/ideas/{idea['id']}").json()["events"]
    assert [e["event_type"] for e in before[-2:]] == ["position_opened", "position_closed"]
    assert client.delete(f"/api/positions/{p['id']}", headers=WRITE).status_code == 204
    assert client.get(f"/api/positions/{p['id']}").status_code == 404
    after = client.get(f"/api/ideas/{idea['id']}").json()
    assert after["position"] is None
    # the position's timeline events went with it; the idea's own events stay
    assert not any(e["event_type"].startswith("position_") for e in after["events"])
    assert len(after["events"]) == len(before) - 2


def test_review_divergence_matrix_and_stats(client, eodhd, chain_state, db):
    enable_options(client)
    # idea A: thesis resolved right (manual close counts by P&L sign), option lost -> right call, wrong contract
    a = open_idea(client, title="A")
    run_selector(client, a["id"])
    pa = take(client, a["id"]).json()
    chain_state["date"], chain_state["factor"] = TODAY + timedelta(days=1), 0.3
    client.post("/api/jobs/resolve", headers=CRON)  # stop loss on A
    ia = db.get(Idea, a["id"])
    ia.status, ia.resolution_reason, ia.hypothetical_pnl_pct = "right", "target_hit", 4.0
    db.commit()
    # idea B: thesis wrong, option won
    chain_state["factor"] = 1.0
    b = open_idea(client, title="B")
    run_selector(client, b["id"])
    pb = take(client, b["id"], take_profit_pct=30).json()
    chain_state["date"], chain_state["factor"] = TODAY + timedelta(days=2), 1.6
    client.post("/api/jobs/resolve", headers=CRON)  # take profit on B
    ib = db.get(Idea, b["id"])
    ib.status, ib.resolution_reason, ib.hypothetical_pnl_pct = "wrong", "stop_hit", -3.0
    db.commit()
    # idea C: open with an open position; seed idea D excluded
    chain_state["factor"] = 1.0
    c = open_idea(client, title="C")
    run_selector(client, c["id"])
    take(client, c["id"])
    r = client.get("/api/review", headers=WRITE).json()
    dv = r["divergence"]
    assert dv["available"] is True and dv["resolved_positions"] == 2 and dv["open_positions"] == 1
    assert dv["option_beat_thesis"] == 1  # B's option beat B's thesis; A's did not
    assert (
        dv["thesis_right_option_lost"]["count"] == 1
        and dv["thesis_right_option_lost"]["positions"][0]["name"] == pa["name"]
    )
    assert (
        dv["thesis_right_option_lost"]["avg_option_pnl_pct"] < -50
        and dv["thesis_right_option_lost"]["avg_thesis_pnl_pct"] == 4.0
    )
    assert (
        dv["thesis_wrong_option_won"]["count"] == 1
        and dv["thesis_wrong_option_won"]["positions"][0]["name"] == pb["name"]
    )
    assert dv["thesis_right_option_won"]["count"] == 0 and dv["thesis_wrong_option_lost"]["count"] == 0
    assert "the option beat the thesis in 1" in dv["note"] and "stop_loss" in dv["note"]
    stats = client.get("/api/stats").json()
    assert stats["positions_open"] == 1 and stats["positions_closed"] == 2 and stats["option_beat_thesis"] == 1
    # divergence text on the ideas
    da = client.get(f"/api/ideas/{a['id']}").json()["divergence"]
    assert da["cell"] == "thesis_right_option_lost" and da["text"].startswith("Right call, wrong contract")
    db_ = client.get(f"/api/ideas/{b['id']}").json()["divergence"]
    assert db_["cell"] == "thesis_wrong_option_won" and db_["option_beat_thesis"] is True


def test_iv_percentile_replaces_the_stand_in_once_history_exists(client, eodhd, chain_state, db):
    idea = open_idea(client)
    enable_options(client)
    inst_id = idea["instrument"]["id"]
    # FIXTURE history: 25 record dates of stored band with rising at-the-money IV, spot stored with each row
    for i in range(1, 26):
        d = TODAY - timedelta(days=i)
        rows = chains.normalize(chain_rows(d, 1.0, iv=0.20 + 0.01 * i))
        chains.store_band(db, inst_id, rows, d, SPOT)
    assert len(chains.distinct_dates(db, inst_id)) == 25
    summaries = db.execute(select(ChainDailySummary).order_by(ChainDailySummary.as_of)).scalars().all()
    assert len(summaries) == 25 and summaries[-1].as_of == TODAY - timedelta(days=1)
    assert summaries[-1].atm_iv == pytest.approx(0.21) and summaries[-1].spot == SPOT and summaries[-1].row_count == 36
    assert summaries[-1].realized_vol_20d == 0.0  # the fixture's closes are flat
    pct = chains.iv_percentile_1y(db, inst_id, TODAY, 0.33)
    # history IVs run 0.21 .. 0.45; 0.33 sits above 12 of the 25 -> 48th percentile
    assert pct["percentile"] == pytest.approx(48.0) and pct["days"] == 25
    a = run_selector(client, idea["id"])
    assert a["iv_percentile_1y"] == pytest.approx(48.0)
    best = a["candidates"][0]
    assert best["iv_percentile_1y"] == pytest.approx(48.0)
    assert best["iv_rv_ratio"] is None  # the fixture's flat closes give zero realized vol: no stand-in either
    assert best["score_breakdown"]["iv_penalty"] == 0.0  # below the median: no IV penalty
    assert "48" in a["verdict_text"] or "percentile" in a["params"]["iv_percentile"]["note"]
    # too little history: percentile stays null with the reason stated
    few = chains.iv_percentile_1y(db, inst_id, TODAY - timedelta(days=20), 0.3)
    assert few["percentile"] is None and few["days"] == 5 and "needs 20" in few["note"]


def test_rollup_keeps_thirty_days_and_leaves_the_percentile_unchanged(client, eodhd, db):
    idea = open_idea(client)
    enable_options(client)
    inst_id = idea["instrument"]["id"]
    # FIXTURE: 60 record dates of band (IV rising with age), plus one per-contract row on an old date
    for i in range(1, 61):
        d = TODAY - timedelta(days=i)
        chains.store_band(db, inst_id, chains.normalize(chain_rows(d, 1.0, iv=0.20 + 0.005 * i)), d, SPOT)
    old = TODAY - timedelta(days=70)  # a date with a leg quote but no band: rolled without a summary
    chains.store_rows(db, inst_id, chains.normalize(chain_rows(old))[:1], old, None, source="contract")
    before_rows = db.execute(select(func.count()).select_from(ChainSnapshot)).scalar()
    assert before_rows == 60 * 36 + 1
    before = chains.iv_percentile_1y(db, inst_id, TODAY, 0.33)
    assert before["days"] == 60 and before["percentile"] is not None

    out = chains.rollup_chain_snapshots(db, TODAY)
    # dates strictly older than 30 days are gone (31..60 = 30 dates), the last 30 keep their band rows
    assert out["dates_rolled"] == 31 and out["summaries"] == 30 and out["rows_deleted"] == 30 * 36 + 1
    kept = chains.distinct_dates(db, inst_id)
    assert len(kept) == 30 and min(kept) == TODAY - timedelta(days=30)
    assert db.execute(select(func.count()).select_from(ChainSnapshot)).scalar() == 30 * 36
    # summaries cover every date, rolled ones are stamped, and the percentile is identical
    rows = {r.as_of: r for r in db.execute(select(ChainDailySummary)).scalars()}
    assert len(rows) == 60
    assert (
        rows[TODAY - timedelta(days=45)].rolled_up_at is not None and rows[TODAY - timedelta(days=45)].row_count == 36
    )
    assert rows[TODAY - timedelta(days=5)].rolled_up_at is None
    after = chains.iv_percentile_1y(db, inst_id, TODAY, 0.33)
    assert after == before
    # a second run is a no-op
    assert chains.rollup_chain_snapshots(db, TODAY)["dates_rolled"] == 0
    # the cron carries the rollup summary after the marks
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert (
        s["positions"]["rollup"]["dates_rolled"] == 0
        and s["positions"]["rollup"]["cutoff"] == (TODAY - timedelta(days=30)).isoformat()
    )
