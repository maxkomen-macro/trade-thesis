"""Ideas API on in-memory SQLite with EODHD mocked (labeled fixtures). No network."""

from datetime import date, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db.models import Base
from api.db.session import get_db, get_db_optional
from api.main import app
from api.services.eodhd import BASE_URL

WRITE = {"Authorization": "Bearer test-write-token"}
CRON = {"Authorization": "Bearer test-cron-secret"}

# FIXTURE: a delayed quote as EODHD returns it; timestamp = 2026-07-15 19:30 UTC.
QUOTE_MU = {
    "code": "MU.US",
    "timestamp": 1784143800,
    "open": 118.0,
    "high": 121.0,
    "low": 117.5,
    "close": 120.0,
    "volume": 1000,
}


def eod_fixture(start: date, closes: list[float]) -> list[dict]:
    """FIXTURE: daily bars, one per calendar day (weekends included for simplicity)."""
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "open": c,
            "high": c + 1,
            "low": c - 1,
            "close": c,
            "adjusted_close": c,
            "volume": 1000 + i,
        }
        for i, c in enumerate(closes)
    ]


# MU path from Jul 10: flat, then rallies through 130 on Jul 20, fades after.
# MU path from Jul 10: flat, rallies through 130 on Jul 20, fades to 125 and stays there through tomorrow.
MU_BARS = eod_fixture(
    date(2026, 7, 10),
    [118, 118, 119, 119, 119, 120, 122, 125, 127, 129, 131, 133, 130, 128, 126]
    + [125] * ((date.today() - date(2026, 7, 24)).days + 2),
)


def idea_body(**over):
    body = {
        "symbol": "MU.US",
        "display_name": "Micron Technology",
        "instrument_kind": "stock",
        "title": "MU post-earnings continuation",
        "thesis_text": "HBM pricing power carries the stock through the gap.",
        "direction": "up",
        "success_rule_json": {"type": "level", "comparator": "close_at_or_above", "level": 130},
        "invalidation_rule_json": {"type": "level", "comparator": "close_at_or_below", "level": 110},
        "window_start": "2026-07-15",
        "window_end": "2026-08-05",
        "capital_assigned": 1000,
        "idea_type": "paper",
        "tags": ["Semis", "earnings", "semis "],
    }
    body.update(over)
    return body


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
def eodhd():
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/real-time/MU.US").mock(return_value=httpx.Response(200, json=QUOTE_MU))
        m.get(f"{BASE_URL}/eod/MU.US").mock(return_value=httpx.Response(200, json=MU_BARS))
        yield m


def test_create_requires_token(client, eodhd):
    assert client.post("/api/ideas", json=idea_body()).status_code == 401


def test_create_stamps_entry_from_eodhd_and_resolves(client, eodhd):
    r = client.post("/api/ideas", json=idea_body(), headers=WRITE)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["entry_price"] == 120.0 and body["entry_price_at"].startswith("2026-07-15")
    assert body["instrument"]["symbol"] == "MU.US"
    assert body["tags"] == ["earnings", "semis"]
    # window is in the past relative to today and 131 >= 130 closed on Jul 20 -> right
    assert body["status"] == "right" and body["resolution_reason"] == "target_hit"
    assert [e["event_type"] for e in body["events"]] == ["target_hit"]
    assert body["events"][0]["occurred_on"] == "2026-07-20"
    assert body["hypothetical_pnl_pct"] == pytest.approx((131 / 120 - 1) * 100, rel=1e-4)
    assert body["hypothetical_pnl_abs"] == pytest.approx(1000 * (131 / 120 - 1), rel=1e-4)
    assert body["target_level"] == 130 and body["stop_level"] == 110
    assert body["success_rule_text"] == "closes at or above 130"
    assert len(body["bars"]) > 10 and body["radar_regime"] is None


def test_dollars_hidden_for_public_but_not_for_writer(client, eodhd):
    client.post("/api/ideas", json=idea_body(), headers=WRITE)
    public = client.get("/api/ideas").json()[0]
    assert public["dollars_hidden"] is True
    assert public["capital_assigned"] is None and public["hypothetical_pnl_abs"] is None
    assert public["hypothetical_pnl_pct"] is not None  # percentages stay visible
    writer = client.get("/api/ideas", headers=WRITE).json()[0]
    assert writer["dollars_hidden"] is False and writer["capital_assigned"] == 1000
    stats = client.get("/api/stats").json()
    assert stats["dollars_hidden"] is True and stats["hypothetical_pnl_abs"] is None
    assert stats["ideas_logged"] == 1 and stats["target_hit_rate"] == 100.0 and stats["direction_hit_rate"] == 100.0
    assert stats["by_regime"][0]["regime"] == "Unknown"


def test_list_filters(client, eodhd):
    client.post("/api/ideas", json=idea_body(), headers=WRITE)
    assert len(client.get("/api/ideas?status=right").json()) == 1
    assert len(client.get("/api/ideas?status=open").json()) == 0
    assert len(client.get("/api/ideas?tag=semis").json()) == 1
    assert len(client.get("/api/ideas?tag=energy").json()) == 0
    assert len(client.get("/api/ideas?q=micron").json()) == 1


def test_stop_hit_path_resolves_wrong(client, eodhd):
    # A bearish MU idea stopped out by the rally: first close >= 130 is Jul 20 (131).
    body = idea_body(
        direction="down",
        success_rule_json={"type": "level", "comparator": "close_at_or_below", "level": 110},
        invalidation_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 130},
    )
    r = client.post("/api/ideas", json=body, headers=WRITE).json()
    assert r["status"] == "wrong" and r["resolution_reason"] == "stop_hit" and r["direction_right"] is False
    assert r["events"][0]["occurred_on"] == "2026-07-20" and r["events"][0]["price"] == 131
    assert r["hypothetical_pnl_pct"] == pytest.approx(-(131 / 120 - 1) * 100, rel=1e-4)


def test_expired_idea_records_direction_flag(client, eodhd):
    body = idea_body(
        success_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 200},
        invalidation_rule_json=None,
    )
    r = client.post("/api/ideas", json=body, headers=WRITE).json()
    assert (
        r["status"] == "expired"
        and r["resolution_reason"] == "expired:direction_right"
        and r["direction_right"] is True
    )


def test_open_idea_patch_close_delete(client, eodhd):
    today = date.today()
    body = idea_body(
        window_start=today.isoformat(),
        window_end=(today + timedelta(days=30)).isoformat(),
        success_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 999},
        invalidation_rule_json=None,
        entry_price=120.0,
    )
    created = client.post("/api/ideas", json=body, headers=WRITE).json()
    assert created["status"] == "open" and created["days_left"] == 30 and created["progress_kind"] == "price"
    iid = created["id"]

    patched = client.patch(f"/api/ideas/{iid}", json={"title": "Renamed", "tags": ["x"]}, headers=WRITE).json()
    assert patched["title"] == "Renamed" and patched["tags"] == ["x"]
    bad = client.patch(f"/api/ideas/{iid}", json={"window_end": today.isoformat()}, headers=WRITE)
    assert bad.status_code == 422

    closed = client.post(f"/api/ideas/{iid}/close", json={"note": "took profit early"}, headers=WRITE).json()
    assert closed["status"] == "closed_manual" and closed["events"][-1]["event_type"] == "manual_close"
    assert client.post(f"/api/ideas/{iid}/close", json={}, headers=WRITE).status_code == 409

    assert client.delete(f"/api/ideas/{iid}", headers=WRITE).status_code == 204
    assert client.get(f"/api/ideas/{iid}").status_code == 404


def test_eodhd_failure_surfaces_as_502_not_a_guess(client):
    with respx.mock() as m:
        m.get(f"{BASE_URL}/real-time/MU.US").mock(return_value=httpx.Response(500, text="upstream down"))
        r = client.post("/api/ideas", json=idea_body(), headers=WRITE)
    assert r.status_code == 502
    assert "HTTP 500" in r.json()["detail"]["message"]


def test_jobs_resolve_and_refresh(client, eodhd):
    today = date.today()
    client.post(
        "/api/ideas",
        json=idea_body(
            window_start=today.isoformat(),
            window_end=(today + timedelta(days=30)).isoformat(),
            success_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 999},
            invalidation_rule_json=None,
            entry_price=120.0,
        ),
        headers=WRITE,
    )
    assert client.post("/api/jobs/resolve").status_code == 401
    assert client.get("/api/jobs/resolve").status_code == 401
    assert client.get("/api/jobs/resolve", headers=CRON).json()["job"] == "resolve"  # Vercel Cron uses GET
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert s["job"] == "resolve" and s["ideas_checked"] == 1 and s["errors"] == []
    s2 = client.post("/api/jobs/refresh-prices", headers=CRON).json()
    assert s2["job"] == "refresh-prices" and s2["instruments_refreshed"] == 1


def test_symbol_search_is_write_protected_and_maps_fields(client):
    with respx.mock() as m:
        m.get(f"{BASE_URL}/search/united oil").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "Code": "USO",
                        "Exchange": "US",
                        "Name": "United States Oil Fund LP",
                        "Type": "ETF",
                        "Currency": "USD",
                        "previousClose": 141.99,
                        "previousCloseDate": "2026-09-04",
                    }
                ],
            )
        )
        assert client.get("/api/instruments/search?q=united oil").status_code == 401
        hits = client.get("/api/instruments/search?q=united oil", headers=WRITE).json()
    assert hits[0]["symbol"] == "USO.US" and hits[0]["previous_close"] == 141.99


def test_relative_idea_needs_benchmark(client, eodhd):
    r = client.post("/api/ideas", json=idea_body(direction="outperform"), headers=WRITE)
    assert r.status_code == 422


# FIXTURE: SPY flat at 500 until Jul 20, then 520 (+4%) for the rest of the window.
SPY_BARS = eod_fixture(date(2026, 7, 10), [500] * 11 + [520] * ((date.today() - date(2026, 7, 20)).days + 2))
SPY_SEARCH = [{"Code": "SPY", "Exchange": "US", "Name": "SPDR S&P 500 ETF Trust", "Type": "ETF"}]


def test_relative_idea_stores_both_spreads_and_backfills(client, eodhd):
    eodhd.get(f"{BASE_URL}/eod/SPY.US").mock(return_value=httpx.Response(200, json=SPY_BARS))
    eodhd.get(f"{BASE_URL}/search/SPY").mock(return_value=httpx.Response(200, json=SPY_SEARCH))
    body = idea_body(
        direction="outperform",
        benchmark_symbol="SPY.US",
        success_rule_json={"type": "relative", "benchmark": "SPY.US", "spread_pct": 3.0},
        invalidation_rule_json=None,
    )
    r = client.post("/api/ideas", json=body, headers=WRITE)
    assert r.status_code == 201, r.text
    d = r.json()
    # Hit on Jul 17: MU 125 vs entry 120 = +4.17%, SPY flat -> spread +4.17% >= 3%
    assert d["status"] == "right" and d["events"][0]["occurred_on"] == "2026-07-17"
    assert d["spread_at_resolution_pct"] == pytest.approx((125 / 120 - 1) * 100, rel=1e-4)
    # Window end Aug 5: MU 125 (+4.17%) vs SPY 520 (+4%) -> +0.17%
    assert d["spread_at_window_end_pct"] == pytest.approx((125 / 120 - 1) * 100 - 4.0, rel=1e-3)
    assert len(d["benchmark_bars"]) > 10

    # Simulate an idea that resolved before its window closed: null the column, then the daily job backfills it.
    from sqlalchemy import update

    from api.db.models import Idea

    db = next(client.app.dependency_overrides[get_db]())
    db.execute(update(Idea).where(Idea.id == d["id"]).values(spread_at_window_end_pct=None))
    db.commit()
    s = client.post("/api/jobs/resolve", headers=CRON).json()
    assert any(x.get("backfilled") == "spread_at_window_end_pct" for x in s["resolved"]), s
    after = client.get(f"/api/ideas/{d['id']}").json()
    assert after["spread_at_window_end_pct"] == pytest.approx((125 / 120 - 1) * 100 - 4.0, rel=1e-3)
    assert after["events"][-1]["event_type"] == "benchmark_update"
    assert "spread at window end" in after["events"][-1]["note"]


def test_seeded_idea_does_not_move_the_stats(client, eodhd):
    before = client.get("/api/stats").json()
    r = client.post("/api/ideas", json=idea_body(parsed_json={"seed": True, "seed_note": "placeholder"}), headers=WRITE)
    assert r.status_code == 201 and r.json()["seed"] is True and r.json()["status"] == "right"
    after = client.get("/api/stats").json()
    assert after["ideas_logged"] == before["ideas_logged"] == 0
    assert after["resolved_count"] == 0 and after["target_hit_rate"] is None and after["direction_hit_rate"] is None
    assert after["hypothetical_pnl_pct_avg"] is None and after["by_regime"] == [] and after["resolving_soon"] == []
    assert after["seed_count"] == 1
    # still listed in the table, flagged
    rows = client.get("/api/ideas").json()
    assert len(rows) == 1 and rows[0]["seed"] is True
    # a real idea moves them
    client.post("/api/ideas", json=idea_body(title="real one"), headers=WRITE)
    real = client.get("/api/stats").json()
    assert real["ideas_logged"] == 1 and real["resolved_count"] == 1 and real["target_hit_rate"] == 100.0
