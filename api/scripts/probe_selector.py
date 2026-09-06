"""Dry-run the options selector against the live EODHD chain without touching the database (`make probe-selector`).

    .venv/bin/python -m api.scripts.probe_selector USO.US down --target 130 --stop 150 --days 21 [--capital 1000]

Spot comes from the delayed quote, the chain from UnicornBay, realized vol from 60 days of EOD bars fetched here
(not stored). Rationale strings use the deterministic templates (no Anthropic call). Prints the top three with
their metrics and the shares comparison. Nothing is written anywhere; the token is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from api.services import options
from api.services.eodhd import EODHDClient, EODHDError
from api.services.options import SelectorInputs, fetch_chain, run_selector
from api.services.parser import realized_vol_pct


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="EODHD symbol, e.g. USO.US")
    ap.add_argument("direction", choices=["up", "down", "outperform", "underperform", "range"])
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--stop", type=float, default=None)
    ap.add_argument("--days", type=int, default=21, help="window length in days from today")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--risk-pct", type=float, default=2.0)
    ap.add_argument("--account-size", type=float, default=2342.0)
    ap.add_argument("--rate-pct", type=float, default=4.0)
    ap.add_argument("--conviction", type=float, default=None)
    ap.add_argument("--inverse", default=None, help="inverse ETF symbol for the comparison, e.g. SCO.US")
    ap.add_argument("--leverage", type=float, default=None)
    ap.add_argument("--json", action="store_true", help="dump the full analysis body as JSON")
    args = ap.parse_args()

    options._call_rationale_model = lambda payload: (_ for _ in ()).throw(RuntimeError("dry run: templates only"))
    client = EODHDClient()
    today = datetime.now(UTC).date()
    try:
        quote = client.real_time([args.symbol])[0]
        spot = float(quote["close"])
        bars = client.eod(args.symbol, today - timedelta(days=60), today)
    except EODHDError as exc:
        print(f"FAIL {exc} body={exc.body!r}")
        return 1
    rv = realized_vol_pct([float(b["close"]) for b in bars if b.get("close") is not None])
    inp = SelectorInputs(
        symbol=args.symbol,
        direction=args.direction,
        spot=spot,
        today=today,
        window_end=today + timedelta(days=args.days),
        target=args.target,
        stop=args.stop,
        capital=args.capital,
        risk_pct=args.risk_pct,
        account_size=args.account_size,
        stop_loss_pct=50.0,
        rate_pct=args.rate_pct,
        realized_vol_20d_pct=rv,
        conviction_pct=args.conviction,
        inverse_symbol=args.inverse,
        inverse_leverage=args.leverage,
    )
    try:
        rows, meta = fetch_chain(client, inp)
    except EODHDError as exc:
        print(f"FAIL chain: {exc} body={exc.body!r}")
        return 1
    body = run_selector(inp, rows)
    if args.json:
        print(json.dumps({"inputs": inp.__dict__, "chain": meta, **body}, default=str, indent=1))
        return 0
    print(f"spot {spot} ({datetime.fromtimestamp(quote['timestamp'], tz=UTC).isoformat()}), rv20 {rv}%")
    print(f"chain: {meta['rows']} rows in {meta['requests']} request(s), trade date {meta['chain_trade_date']}")
    print(f"expiries {meta['expiries']}")
    print(
        f"generated {body['counts']['generated']} candidates, {body['counts']['passing']} pass filters; "
        f"verdict {body['verdict']} ({body['verdict_reason']})"
    )
    print(body["verdict_text"])
    for c in body["candidates"][:3]:
        print(
            f"\n#{c['rank']} {c['name']} | {c['kind']} | debit {c['debit']:.2f} | "
            f"ret@target/window {c['ret_at_target_window_end_pct']:.0f}% (expiry {c['ret_at_target_expiry_pct']:.0f}%)"
        )
        print(
            f"   BE {c['breakeven']:.2f} | PoP {c['pop_pct']}% | theta/wk {c['theta_week_pct']}% | "
            f"width {c['spread_width_pct']}% | OI {c['open_interest']} | IV {c['iv']} (IV/RV {c['iv_rv_ratio']}) | "
            f"days of theta {c['days_of_theta']}"
        )
        print(
            f"   contracts {c['contracts']} for {c['capital_assigned']:.0f} (risk budget {c['risk_budget']:.0f}"
            f"{', exceeded' if c['capital_exceeds_risk_budget'] else ''}) | "
            f"score {c['score']} {c['score_breakdown']}"
        )
        g = c["grid"]
        print("   grid dates:", g["dates"])
        for p, row in zip(g["prices"], g["ret_pct"], strict=True):
            print(f"   {p:>9.2f} " + " ".join(f"{v:>7.0f}" for v in row))
        print("   why:", c["why"])
    print("\nshares:", json.dumps(body["shares_comparison"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
