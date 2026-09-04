"""EODHD client. Only endpoints verified live on 2026-09-04 are wrapped here (see docs/eodhd-probe.md):

    GET /api/eod/{symbol}?from=&to=&fmt=json                       -> list of daily bars
    GET /api/real-time/{symbol}?s=B,C&fmt=json                     -> delayed quote(s)
    GET /api/search/{query}?limit=                                  -> symbol hits
    GET /api/user?fmt=json                                          -> plan/usage
    GET /api/mp/unicornbay/options/contracts?filter[underlying_symbol]=  -> latest chain snapshot (JSON:API)
    GET /api/mp/unicornbay/options/eod?filter[contract]=                -> per-contract daily history
    GET /api/mp/unicornbay/options/underlying-symbols                   -> symbols with listed options
    (options entitlement confirmed 2026-09-04 after the UnicornBay add-on was activated; the earlier 403 is history)

Anti-fabrication rule: any non-200, non-JSON, or empty response raises EODHDError carrying the status
and a body snippet. Callers surface that error; nothing is ever synthesized in its place.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import httpx

from api.config import settings

log = logging.getLogger("tt.eodhd")

BASE_URL = "https://eodhd.com/api"
OPTIONS_CONTRACTS_PATH = "/mp/unicornbay/options/contracts"  # verified 200 on 2026-09-04
OPTIONS_EOD_PATH = "/mp/unicornbay/options/eod"
OPTIONS_UNDERLYINGS_PATH = "/mp/unicornbay/options/underlying-symbols"


class EODHDError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str | None = None, path: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.path = path

    def to_dict(self) -> dict[str, Any]:
        return {"error": str(self), "status": self.status, "path": self.path, "body": self.body}


class EODHDClient:
    def __init__(self, token: str | None = None, timeout: float = 20.0, client: httpx.Client | None = None):
        self.token = token if token is not None else settings.eodhd_api_token
        self._client = client or httpx.Client(timeout=timeout)

    # -- low level ---------------------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None, *, fmt_json: bool = True) -> Any:
        if not self.token:
            raise EODHDError("EODHD_API_KEY is not configured", path=path)
        q: dict[str, Any] = dict(params or {})
        q["api_token"] = self.token
        if fmt_json:
            q["fmt"] = "json"
        try:
            resp = self._client.get(f"{BASE_URL}{path}", params=q)
        except httpx.HTTPError as exc:
            raise EODHDError(f"EODHD request failed: {type(exc).__name__}: {exc}", path=path) from exc
        body_snippet = resp.text[:300]
        if resp.status_code != 200:
            log.warning("EODHD %s -> HTTP %s: %s", path, resp.status_code, body_snippet.replace("\n", " ")[:120])
            raise EODHDError(
                f"EODHD {path} returned HTTP {resp.status_code}",
                status=resp.status_code,
                body=body_snippet,
                path=path,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise EODHDError("EODHD returned non-JSON body", status=200, body=body_snippet, path=path) from exc

    # -- verified endpoints -------------------------------------------------------------------------
    def user(self) -> dict[str, Any]:
        return self._get("/user")

    def eod(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        """Daily bars, fields: date, open, high, low, close, adjusted_close, volume."""
        data = self._get(f"/eod/{symbol}", {"from": start.isoformat(), "to": end.isoformat()})
        if not isinstance(data, list):
            raise EODHDError(
                "EODHD eod returned unexpected shape", status=200, body=str(data)[:300], path=f"/eod/{symbol}"
            )
        return data

    def real_time(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Delayed quotes. One request for many symbols: first in the path, rest via `s=`.
        Fields: code, timestamp (unix UTC), open, high, low, close, volume, previousClose, change, change_p."""
        if not symbols:
            return []
        first, rest = symbols[0], symbols[1:]
        params = {"s": ",".join(rest)} if rest else None
        data = self._get(f"/real-time/{first}", params)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise EODHDError("EODHD real-time returned unexpected shape", status=200, body=str(data)[:300])
        return data

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Symbol search. Fields: Code, Exchange, Name, Type, Country, Currency, ISIN, isPrimary, previousClose,
        previousCloseDate. EODHD symbol = f"{Code}.{Exchange}"."""
        data = self._get(f"/search/{httpx.URL(query).path or query}", {"limit": limit}, fmt_json=False)
        if not isinstance(data, list):
            raise EODHDError("EODHD search returned unexpected shape", status=200, body=str(data)[:300])
        return data

    def probe_options(self, underlying: str = "AAPL", as_of: date | None = None) -> dict[str, Any]:
        """Report, never raise: does this token have the UnicornBay options entitlement?
        Verified contract (2026-09-04): JSON:API body {meta:{offset,limit,fields}, data:[{id,type,attributes}]};
        page[limit] <= 1000; the endpoint also lists expired contracts unless exp_date_from is set."""
        as_of = as_of or datetime.now(UTC).date()
        try:
            data = self._get(
                OPTIONS_CONTRACTS_PATH,
                {"filter[underlying_symbol]": underlying, "filter[exp_date_from]": as_of.isoformat(), "page[limit]": 2},
                fmt_json=False,
            )
            rows = data.get("data", []) if isinstance(data, dict) else []
            fields = ((data.get("meta") or {}).get("fields", [])) if isinstance(data, dict) else []
            return {
                "ok": bool(rows),
                "status": 200,
                "path": OPTIONS_CONTRACTS_PATH,
                "rows": len(rows),
                "fields": fields,
                "sample": rows[0].get("attributes") if rows else None,
            }
        except EODHDError as exc:
            return {"ok": False, **exc.to_dict()}


def eodhd_symbol(code: str, exchange: str) -> str:
    return f"{code}.{exchange}"
