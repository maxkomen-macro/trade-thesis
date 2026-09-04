from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from api.auth import CronAuth, WriteAuth

router = APIRouter()


@router.post("/write", dependencies=[WriteAuth])
def write():
    return {"ok": True}


@router.post("/cron", dependencies=[CronAuth])
def cron():
    return {"ok": True}


app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_write_requires_bearer_token():
    assert client.post("/write").status_code == 401
    assert client.post("/write", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/write", headers={"Authorization": "Bearer test-write-token"}).status_code == 200


def test_cron_accepts_cron_secret_or_write_token():
    assert client.post("/cron").status_code == 401
    assert client.post("/cron", headers={"Authorization": "Bearer test-cron-secret"}).status_code == 200
    assert client.post("/cron", headers={"Authorization": "Bearer test-write-token"}).status_code == 200
