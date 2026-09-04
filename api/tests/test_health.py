from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_reports_missing_database_instead_of_pretending():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "DATABASE_URL not configured"


def test_root_points_at_docs():
    r = client.get("/api")
    assert r.status_code == 200
    assert r.json()["docs"] == "/api/docs"
