"""Parser tests: the model call is replaced by a fixture object; EODHD is mocked. No network."""

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
from api.services import parser
from api.services.eodhd import BASE_URL
from api.services.parser import ParsedThesisLLM, realized_vol_pct, resolve_symbol

WRITE = {"Authorization": "Bearer test-write-token"}
TODAY = date.today()


# FIXTURE: what the model would return for the USO thesis in the mockup.
def llm(**over) -> ParsedThesisLLM:
    base = dict(
        title="Placeholder",
        instrument_query="",
        instrument_symbol_guess="",
        instrument_kind="",
        direction="up",
        benchmark_query="",
        benchmark_symbol_guess="",
        success_type="none",
        success_comparator="none",
        success_level=0,
        success_pct=0,
        success_spread_pct=0,
        stop_comparator="none",
        stop_level=0,
        invalidation_is_note_only=False,
        window_start=TODAY.isoformat(),
        window_end="",
        catalyst_date="",
        catalyst_note="",
        conviction_pct=0,
        tags=[],
        questions=[],
    )
    base.update(over)
    return ParsedThesisLLM(**base)


LLM_USO = llm(
    title="USO breaks the August low as inventories build",
    instrument_query="USO",
    instrument_symbol_guess="USO",
    instrument_kind="commodity_etf",
    direction="down",
    success_type="level",
    success_comparator="close_at_or_below",
    success_level=68.5,
    stop_comparator="close_at_or_above",
    stop_level=74.0,
    window_end=(TODAY + timedelta(days=21)).isoformat(),
    tags=["energy", "macro", "technical"],
    questions=["catalyst_date: Is there a dated one \u2014 EIA weekly, OPEC meeting?"],
)
SEARCH_USO = [
    {"Code": "USO", "Exchange": "US", "Name": "United States Oil Fund LP", "Type": "ETF", "previousClose": 72.1}
]
QUOTE_USO = {
    "code": "USO.US",
    "timestamp": 1788552480,
    "close": 72.1,
    "open": 72.5,
    "high": 72.9,
    "low": 71.8,
    "volume": 100,
}


def eod(start: date, closes: list[float]):
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "close": c,
            "open": c,
            "high": c,
            "low": c,
            "adjusted_close": c,
            "volume": 1,
        }
        for i, c in enumerate(closes)
    ]


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


def test_realized_vol_is_annualized_std_of_log_returns():
    assert realized_vol_pct([100.0] * 25) == 0.0
    assert realized_vol_pct([100.0, 101.0]) is None  # too few bars
    v = realized_vol_pct([100 * (1.01 ** (i % 2)) for i in range(30)])
    assert v is not None and v > 0


@respx.mock
def test_resolve_symbol_accepts_exact_guess_and_asks_when_ambiguous():
    respx.get(f"{BASE_URL}/search/USO").mock(
        return_value=httpx.Response(
            200,
            json=SEARCH_USO + [{"Code": "USL", "Exchange": "US", "Name": "United States 12 Month Oil", "Type": "ETF"}],
        )
    )
    from api.services.eodhd import EODHDClient

    sym, cands = resolve_symbol(EODHDClient(token="t"), "USO", "USO")
    assert sym == "USO.US" and len(cands) == 2
    # mutual funds are not tradeable here and are dropped from candidates
    respx.get(f"{BASE_URL}/search/GMAHX").mock(
        return_value=httpx.Response(
            200, json=[{"Code": "GMAHX", "Exchange": "US", "Name": "Some Fund", "Type": "FUND"}]
        )
    )
    assert resolve_symbol(EODHDClient(token="t"), "GMAHX", None) == (None, [])
    respx.get(f"{BASE_URL}/search/oil%20fund").mock(
        return_value=httpx.Response(
            200,
            json=SEARCH_USO
            + [{"Code": "BNO", "Exchange": "US", "Name": "United States Brent Oil Fund", "Type": "ETF"}],
        )
    )
    sym2, cands2 = resolve_symbol(EODHDClient(token="t"), "oil fund", None)
    assert sym2 is None and {c["symbol"] for c in cands2} == {"USO.US", "BNO.US"}


def test_parse_endpoint_requires_token(client):
    assert (
        client.post("/api/ideas/parse", json={"thesis_text": "Oil looks heavy for the next three weeks."}).status_code
        == 401
    )


def test_parse_assembles_fields_questions_and_context(client, monkeypatch):
    monkeypatch.setattr(parser, "_call_model", lambda text, today: LLM_USO)
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/search/USO").mock(return_value=httpx.Response(200, json=SEARCH_USO))
        m.get(f"{BASE_URL}/real-time/USO.US").mock(return_value=httpx.Response(200, json=QUOTE_USO))
        m.get(f"{BASE_URL}/eod/USO.US").mock(
            return_value=httpx.Response(200, json=eod(TODAY - timedelta(days=40), [70 + (i % 3) for i in range(41)]))
        )
        r = client.post(
            "/api/ideas/parse",
            json={"thesis_text": "Oil looks heavy ... breaks 68.50 within three weeks; wrong above 74."},
            headers=WRITE,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["symbol"] == "USO.US" and d["instrument"]["display_name"] == "United States Oil Fund LP"
    assert d["direction"] == "down"
    assert d["success_rule_json"] == {
        "type": "level",
        "comparator": "close_at_or_below",
        "level": 68.5,
        "suggested": True,
    }
    assert d["success_rule_text"] == "closes at or below 68.5"
    assert (
        d["invalidation_rule_json"]["comparator"] == "close_at_or_above"
        and d["invalidation_rule_json"]["level"] == 74.0
    )
    assert d["window_end"] == (TODAY + timedelta(days=21)).isoformat()
    assert [q["field"] for q in d["questions"]] == ["catalyst_date"]  # nothing else is missing
    assert "\u2014" not in d["questions"][0]["question"]  # em dashes from the model are tidied
    ctx = d["context"]
    assert ctx["last_close"] == 72.1 and ctx["last_close_source"] == "realtime"
    assert ctx["realized_vol_20d_pct"] is not None and ctx["realized_vol_20d_pct"] > 0
    assert ctx["distance_to_target_pct"] == pytest.approx((68.5 / 72.1 - 1) * 100, rel=1e-3)
    assert d["regime"]["available"] is False
    assert d["parsed_json"]["model"] == "claude-sonnet-4-6" and d["tags"] == ["energy", "macro", "technical"]


def test_parse_never_fills_missing_levels_and_asks_instead(client, monkeypatch):
    vague = llm(
        title="Micron keeps running after earnings",
        instrument_query="Micron",
        instrument_symbol_guess="MU",
        tags=["semis"],
    )
    monkeypatch.setattr(parser, "_call_model", lambda text, today: vague)
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/search/MU").mock(
            return_value=httpx.Response(
                200, json=[{"Code": "MU", "Exchange": "US", "Name": "Micron Technology Inc", "Type": "Common Stock"}]
            )
        )
        m.get(f"{BASE_URL}/search/Micron").mock(
            return_value=httpx.Response(
                200, json=[{"Code": "MU", "Exchange": "US", "Name": "Micron Technology Inc", "Type": "Common Stock"}]
            )
        )
        m.get(f"{BASE_URL}/real-time/MU.US").mock(return_value=httpx.Response(500, text="down"))
        m.get(f"{BASE_URL}/eod/MU.US").mock(return_value=httpx.Response(500, text="down"))
        d = client.post(
            "/api/ideas/parse", json={"thesis_text": "Micron keeps running after earnings, I think."}, headers=WRITE
        ).json()
    assert d["symbol"] == "MU.US" and d["instrument"]["kind"] == "stock"
    assert d["success_rule_json"] is None and d["invalidation_rule_json"] is None and d["window_end"] is None
    assert {q["field"] for q in d["questions"]} == {"success_rule", "invalidation_rule", "window_end"}
    # EODHD failures are surfaced in the context, never replaced by numbers
    assert d["context"]["last_close"] is None and len(d["context"]["errors"]) == 2


def test_parse_relative_idea_resolves_benchmark(client, monkeypatch):
    rel = llm(
        title="Energy outperforms the market",
        instrument_query="energy sector",
        instrument_symbol_guess="XLE",
        direction="outperform",
        benchmark_query="S&P 500",
        benchmark_symbol_guess="SPY",
        success_type="relative",
        success_spread_pct=3.0,
        window_end=(TODAY + timedelta(days=60)).isoformat(),
        tags=["energy", "relative"],
    )
    monkeypatch.setattr(parser, "_call_model", lambda text, today: rel)
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/search/XLE").mock(
            return_value=httpx.Response(
                200, json=[{"Code": "XLE", "Exchange": "US", "Name": "Energy Select Sector SPDR", "Type": "ETF"}]
            )
        )
        m.get(f"{BASE_URL}/search/energy%20sector").mock(return_value=httpx.Response(200, json=[]))
        m.get(f"{BASE_URL}/search/SPY").mock(
            return_value=httpx.Response(
                200, json=[{"Code": "SPY", "Exchange": "US", "Name": "SPDR S&P 500", "Type": "ETF"}]
            )
        )
        m.get(f"{BASE_URL}/search/S&P%20500").mock(return_value=httpx.Response(200, json=[]))
        m.get(f"{BASE_URL}/real-time/XLE.US").mock(
            return_value=httpx.Response(200, json={"code": "XLE.US", "timestamp": 1788552480, "close": 64.06})
        )
        m.get(f"{BASE_URL}/eod/XLE.US").mock(return_value=httpx.Response(200, json=[]))
        d = client.post(
            "/api/ideas/parse", json={"thesis_text": "Energy outperforms the S&P by 3% over two months."}, headers=WRITE
        ).json()
    assert d["benchmark_symbol"] == "SPY.US"
    assert d["success_rule_json"] == {"type": "relative", "benchmark": "SPY.US", "spread_pct": 3.0, "suggested": True}
    assert d["success_rule_text"] == "outperforms SPY.US by 3%"
    assert d["context"]["realized_vol_20d_pct"] is None  # no bars -> no number, not a guess


def test_parse_reports_missing_anthropic_key_as_503(client, monkeypatch):
    monkeypatch.setattr(parser.settings, "anthropic_api_key", "")
    r = client.post("/api/ideas/parse", json={"thesis_text": "Something long enough to parse."}, headers=WRITE)
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
