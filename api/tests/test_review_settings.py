"""Settings update and review breakdowns on SQLite with EODHD mocked."""

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
from api.tests.test_ideas_api import MU_BARS, QUOTE_MU, idea_body

WRITE = {"Authorization": "Bearer test-write-token"}


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


def test_settings_read_and_protected_update(client):
    assert client.get("/api/settings").json()["default_capital"] == 1000
    assert client.patch("/api/settings", json={"default_capital": 2500}).status_code == 401
    assert client.patch("/api/settings", json={}, headers=WRITE).status_code == 422
    assert client.patch("/api/settings", json={"default_capital": -5}, headers=WRITE).status_code == 422
    out = client.patch(
        "/api/settings", json={"default_capital": 2500, "public_hide_dollars": False}, headers=WRITE
    ).json()
    assert out["default_capital"] == 2500 and out["public_hide_dollars"] is False
    assert client.get("/api/settings").json()["default_capital"] == 2500


def test_review_buckets_exclude_seeds(client):
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{BASE_URL}/real-time/MU.US").mock(return_value=httpx.Response(200, json=QUOTE_MU))
        m.get(f"{BASE_URL}/eod/MU.US").mock(return_value=httpx.Response(200, json=MU_BARS))
        client.post("/api/ideas", json=idea_body(), headers=WRITE)  # right
        client.post(
            "/api/ideas",
            json=idea_body(
                title="expired one",
                tags=["semis"],
                success_rule_json={"type": "level", "comparator": "close_at_or_above", "level": 999},
                invalidation_rule_json=None,
            ),
            headers=WRITE,
        )  # expired, direction right
        client.post("/api/ideas", json=idea_body(title="seed", parsed_json={"seed": True}), headers=WRITE)
    r = client.get("/api/review").json()
    assert r["ideas"] == 2 and r["seed_count"] == 1 and r["dollars_hidden"] is True
    outcome = {b["key"]: b for b in r["by_outcome"]}
    assert outcome["right"]["ideas"] == 1 and outcome["expired"]["ideas"] == 1 and "open" not in outcome
    assert outcome["right"]["total_pnl_abs"] is None  # hidden for the public
    tags = {b["key"]: b for b in r["by_tag"]}
    assert tags["semis"]["ideas"] == 2 and tags["earnings"]["ideas"] == 1
    assert r["by_regime"][0]["key"] == "Unknown" and r["by_regime"][0]["direction_hit_rate"] == 100.0
    assert r["by_idea_type"][0]["key"] == "paper" and r["by_idea_type"][0]["target_hit_rate"] == 50.0
    assert r["by_rule_type"][0]["key"] == "level" and r["by_rule_type"][0]["resolved"] == 2
    assert r["divergence"]["available"] is False
    writer = client.get("/api/review", headers=WRITE).json()
    assert writer["by_outcome"][0]["total_pnl_abs"] is not None
