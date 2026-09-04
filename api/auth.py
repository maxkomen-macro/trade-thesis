"""Write protection: a single shared secret passed as `Authorization: Bearer <TT_WRITE_TOKEN>`.
Reads are public. Cron jobs use CRON_SECRET the same way."""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status

from api.config import settings


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _matches(presented: str, expected: str) -> bool:
    return bool(expected) and bool(presented) and hmac.compare_digest(presented, expected)


def is_writer(request: Request) -> bool:
    return _matches(_bearer(request), settings.tt_write_token)


def require_write_token(request: Request) -> None:
    if not settings.tt_write_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TT_WRITE_TOKEN is not configured on the server")
    if not is_writer(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Write token required")


def require_cron_secret(request: Request) -> None:
    if not settings.cron_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "CRON_SECRET is not configured on the server")
    presented = _bearer(request)
    if not (_matches(presented, settings.cron_secret) or _matches(presented, settings.tt_write_token)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Cron secret required")


WriteAuth = Depends(require_write_token)
CronAuth = Depends(require_cron_secret)
