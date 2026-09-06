"""Verify a deployment (`make verify-deploy url=https://... [idea=5]`). Read-only except for two owner-approved
writes: the cron summary (runs the daily resolver once) and one options analysis on a real open idea.

Checks: health, status (Neon + EODHD + options flag), public read with dollar masking, settings masking, SPA
fallback, cron summary with CRON_SECRET, a parse call with TT_WRITE_TOKEN, one options analysis (POST) plus its
public read. Tokens come from the environment / .env (api.config.settings) and are never printed.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from api.config import settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="deployment base URL, e.g. https://trade-thesis-xxxx.vercel.app")
    ap.add_argument(
        "--idea", type=int, default=None, help="idea id for the options analysis (default: first open idea)"
    )
    ap.add_argument("--skip-parse", action="store_true")
    ap.add_argument("--skip-options", action="store_true")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    write = {"Authorization": f"Bearer {settings.tt_write_token}"}
    cron = {"Authorization": f"Bearer {settings.cron_secret}"}
    c = httpx.Client(timeout=90.0, follow_redirects=True)
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {name}{(': ' + detail) if detail else ''}")

    r = c.get(f"{base}/api/health")
    h = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("health", r.status_code == 200 and h.get("status") == "ok" and h.get("db") == "ok", f"{r.status_code} {h}")

    r = c.get(f"{base}/api/status")
    st = r.json()
    check(
        "status: neon + eodhd ok, options enabled",
        r.status_code == 200 and st["db"]["ok"] and st["eodhd"]["ok"] and st["options_enabled"] is True,
        f"db={st['db']['detail']} eodhd={st['eodhd']['detail']} options={st['options_enabled']}",
    )
    check("status: account_size hidden for the public", st["settings"].get("account_size") is None)
    check("status: risk_free_rate_pct present", st["settings"].get("risk_free_rate_pct") is not None)

    r = c.get(f"{base}/api/ideas")
    ideas = r.json()
    check(
        "public ideas: dollars masked",
        r.status_code == 200 and all(i["dollars_hidden"] and i["capital_assigned"] is None for i in ideas),
        f"{len(ideas)} ideas",
    )
    r = c.get(f"{base}/api/settings")
    check("public settings: account_size null", r.status_code == 200 and r.json().get("account_size") is None)
    r = c.get(f"{base}/api/settings", headers=write)
    check("writer settings: account_size visible", r.status_code == 200 and r.json().get("account_size") is not None)

    r = c.get(f"{base}/ideas/1")
    check(
        "SPA fallback for /ideas/1", r.status_code == 200 and 'id="root"' in r.text, r.headers.get("content-type", "")
    )

    r = c.get(f"{base}/api/jobs/resolve")
    check("cron without secret is 401", r.status_code == 401)
    r = c.get(f"{base}/api/jobs/resolve", headers=cron)
    js = r.json() if r.status_code == 200 else {}
    check("cron summary with CRON_SECRET", r.status_code == 200 and js.get("job") == "resolve", f"{r.status_code} {js}")

    if not args.skip_parse:
        t0 = time.time()
        r = c.post(
            f"{base}/api/ideas/parse",
            json={"thesis_text": "Oil looks heavy; USO breaks 135 within three weeks."},
            headers=write,
        )
        body = r.json() if r.status_code == 200 else r.text[:200]
        check(
            "parse with TT_WRITE_TOKEN",
            r.status_code == 200 and isinstance(body, dict) and body.get("symbol") == "USO.US",
            f"{r.status_code} in {time.time() - t0:.1f}s "
            f"symbol={body.get('symbol') if isinstance(body, dict) else body}",
        )

    if not args.skip_options:
        idea_id = args.idea
        if idea_id is None:
            open_ideas = [i for i in ideas if i["status"] == "open" and not i["seed"]]
            idea_id = min((i["id"] for i in open_ideas), default=None)
        if idea_id is None:
            check("options analysis", False, "no open non-placeholder idea to analyse")
        else:
            check("options run without token is 401", c.post(f"{base}/api/ideas/{idea_id}/options").status_code == 401)
            t0 = time.time()
            r = c.post(f"{base}/api/ideas/{idea_id}/options", json={}, headers=write)
            a = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            params = a.get("params") or {}
            check(
                f"options analysis on idea {idea_id}",
                r.status_code == 201 and a.get("verdict") in ("trade", "no_trade"),
                f"{r.status_code} in {time.time() - t0:.1f}s verdict={a.get('verdict')} "
                f"reason={params.get('verdict_reason')} candidates={len(a.get('candidates', []))} "
                f"chain_rows={(params.get('chain') or {}).get('rows')} "
                f"rationale={(params.get('rationale') or {}).get('source')}",
            )
            print(f"      verdict_text: {a.get('verdict_text', '')[:300]}")
            r = c.get(f"{base}/api/ideas/{idea_id}/options")
            pub = r.json() if r.status_code == 200 else {}
            check(
                "options public read: dollars masked",
                r.status_code == 200
                and pub.get("dollars_hidden") is True
                and (pub.get("params") or {}).get("sizing", {}).get("account_size") is None,
            )
    print("ALL PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
