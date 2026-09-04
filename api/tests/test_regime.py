"""Regime push + readout on an in-memory SQLite database (JSONType falls back to JSON off Postgres)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.db.models import Base
from api.db.session import get_db, get_db_optional
from api.main import app
from api.services.radar import regime_stamp

CRON = {"Authorization": "Bearer test-cron-secret"}
WRITE = {"Authorization": "Bearer test-write-token"}
# Labeled fixture: the exact JSON shape Radar's GET /api/regime/latest returns.
RADAR_FIXTURE = {
    "date": "2026-09-03",
    "label": "Goldilocks",
    "confidence": 0.71,
    "growth_trend": 0.4,
    "inflation_trend": -0.2,
    "prob_goldilocks": 0.71,
    "prob_overheating": 0.12,
    "prob_stagflation": 0.09,
    "prob_recession": 0.08,
}


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


def test_regime_is_unavailable_until_pushed(client):
    c, _ = client
    body = c.get("/api/regime").json()
    assert body["available"] is False and body["regime"] is None


def test_push_requires_cron_secret_or_write_token(client):
    c, _ = client
    assert c.post("/api/jobs/regime", json=RADAR_FIXTURE).status_code == 401
    assert c.post("/api/jobs/regime", json=RADAR_FIXTURE, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/api/jobs/regime", json=RADAR_FIXTURE, headers=WRITE).status_code == 200


def test_push_then_read_latest(client):
    c, Local = client
    r = c.post("/api/jobs/regime", json=RADAR_FIXTURE, headers=CRON)
    assert r.status_code == 200, r.text
    body = c.get("/api/regime").json()
    assert body["available"] is True
    assert body["regime"] == "Goldilocks" and body["as_of"] == "2026-09-03" and body["source"] == "radar"
    assert body["prob_goldilocks"] == 0.71 and body["confidence"] == 0.71
    assert body["age_days"] >= 0
    with Local() as db:
        label, probs = regime_stamp(db)
    assert label == "Goldilocks" and probs["prob_recession"] == 0.08 and probs["as_of"] == "2026-09-03"


def test_repush_same_day_replaces_not_duplicates(client):
    c, Local = client
    c.post("/api/jobs/regime", json=RADAR_FIXTURE, headers=CRON)
    c.post("/api/jobs/regime", json={**RADAR_FIXTURE, "label": "Overheating"}, headers=CRON)
    from sqlalchemy import func, select

    from api.db.models import RegimeSnapshot

    with Local() as db:
        assert db.execute(select(func.count()).select_from(RegimeSnapshot)).scalar_one() == 1
    assert c.get("/api/regime").json()["regime"] == "Overheating"


def test_latest_is_by_as_of_not_insert_order(client):
    c, _ = client
    c.post("/api/jobs/regime", json=RADAR_FIXTURE, headers=CRON)
    c.post("/api/jobs/regime", json={**RADAR_FIXTURE, "date": "2026-09-01", "label": "Stagflation"}, headers=CRON)
    assert c.get("/api/regime").json()["regime"] == "Goldilocks"


def test_stamp_is_null_when_empty(client):
    _, Local = client
    with Local() as db:
        assert regime_stamp(db) == (None, None)
    assert regime_stamp(None) == (None, None)


def test_push_rejects_bad_payload(client):
    c, _ = client
    assert c.post("/api/jobs/regime", json={"label": "Goldilocks"}, headers=CRON).status_code == 422
    assert c.post("/api/jobs/regime", json={**RADAR_FIXTURE, "date": "yesterday"}, headers=CRON).status_code == 422


def test_date_type_roundtrip():
    assert date.fromisoformat(RADAR_FIXTURE["date"]) == date(2026, 9, 3)
