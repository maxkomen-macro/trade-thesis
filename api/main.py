"""Trade Thesis API. Deployed as one Vercel Python function (pyproject: tool.vercel.entrypoint = api.main:app).
Locally: `make api` -> uvicorn on :8001; Vite proxies /api there."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import APP_VERSION, ROOT, settings
from api.routers import ideas, instruments, jobs, review, system
from api.routers import settings as settings_router

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
app.include_router(ideas.router)
app.include_router(instruments.router)
app.include_router(settings_router.router)
app.include_router(review.router)


@app.get("/api", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "trade-thesis", "version": APP_VERSION, "docs": "/api/docs"}


# Production: the built SPA is served from web/dist as low-priority routes (API routes win). On Vercel the files are
# promoted to the CDN at build time; index.html is the fallback for client-side routes such as /ideas/3.
# Locally Vite serves the UI on :5174, so this only matters if web/dist happens to exist.
FRONTEND_DIR = ROOT / "web" / "dist"
if FRONTEND_DIR.is_dir():
    app.frontend("/", directory=str(FRONTEND_DIR), fallback="index.html")
