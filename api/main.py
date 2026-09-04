"""Trade Thesis API. Deployed as one Vercel Python function (pyproject: tool.vercel.entrypoint = api.main:app).
Locally: `make api` -> uvicorn on :8001; Vite proxies /api there."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import APP_VERSION, settings
from api.routers import jobs, system

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx logs full request URLs at INFO, which would put the EODHD api_token into server logs. Never.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(
    title="Trade Thesis API",
    version=APP_VERSION,
    description="Public read, token-protected write. Prices from EODHD; regime pushed daily by Macro Regime Radar.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(system.router)
app.include_router(jobs.router)


@app.get("/api", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "trade-thesis", "version": APP_VERSION, "docs": "/api/docs"}
