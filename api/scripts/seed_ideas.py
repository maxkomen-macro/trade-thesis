"""Seed the ledger with the owner's three past ideas as paper ideas (`make seed`). Idempotent by title.

Rules of this script (owner instructions, 2026-09-04):
  * entry_price is the actual EODHD EOD close on the entry date (or the next trading day), never typed in;
  * windows are approximate placeholders from the owner's description and are flagged `seed: true`;
  * targets/stops are NOT invented: the success rule is a placeholder `direction` rule, no invalidation level;
  * everything a human must replace later is listed in parsed_json.seed_placeholders.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, time

from sqlalchemy import select

from api.db.models import Idea
from api.db.session import get_sessionmaker
from api.routers.system import load_settings
from api.services import ledger, prices
from api.services.eodhd import EODHDClient, EODHDError

SEEDS = [
    {
        "symbol": "MU.US",
        "kind": "stock",
        "title": "MU post-earnings continuation on HBM pricing power",
        "thesis_text": "Post-earnings continuation on HBM pricing power; managed through a gap-up and trimmed.",
        "direction": "up",
        "window_start": date(2026, 7, 15),
        "window_end": date(2026, 8, 5),
        "window_note": "owner said mid-July 2026 to early August 2026, exact dates TBD",
        "tags": ["semis", "earnings", "momentum"],
        "catalyst_note": "post-earnings",
    },
    {
        "symbol": "SCO.US",
        "kind": "commodity_etf",
        "title": "Bearish crude via SCO after USO technical breakdown",
        "thesis_text": (
            "Bearish crude expressed via 2x inverse ETF after USO multi-timeframe technical breakdown. "
            "Executed and closed at target."
        ),
        "direction": "up",
        "window_start": date(2026, 7, 1),
        "window_end": date(2026, 7, 31),
        "window_note": "owner said July 2026, exact dates TBD",
        "tags": ["energy", "technical", "inverse-etf"],
        "catalyst_note": None,
    },
    {
        "symbol": "LMT.US",
        "kind": "stock",
        "title": "LMT bearish, no trade placed (options unaffordable)",
        "thesis_text": "Bearish; options were unaffordable at account size so no trade was placed.",
        "direction": "down",
        "window_start": date(2026, 7, 27),
        "window_end": date(2026, 8, 27),
        "window_note": "owner said late July to late August 2026, exact dates TBD",
        "tags": ["defense", "paper"],
        "catalyst_note": None,
    },
]

PLACEHOLDERS = ["window_start", "window_end", "success_rule_json", "invalidation_rule_json"]


def main() -> int:
    client = EODHDClient()
    values, _ = load_settings()
    capital = float(values.get("default_capital", 1000))
    with get_sessionmaker()() as db:
        for s in SEEDS:
            if db.execute(select(Idea).where(Idea.title == s["title"])).scalar_one_or_none():
                print(f"skip  {s['symbol']}: already seeded")
                continue
            try:
                inst = ledger.get_or_create_instrument(db, s["symbol"], kind=s["kind"], client=client)
                bar = prices.close_on_or_after(db, inst, s["window_start"], client)
            except EODHDError as exc:
                print(f"FAIL  {s['symbol']}: {exc} body={exc.body!r}")
                continue
            if bar is None:
                print(f"FAIL  {s['symbol']}: no EOD bar on or after {s['window_start']}")
                continue
            snap = ledger.latest_snapshot_rows(db, inst.id, 1)
            entry_at = datetime.combine(bar.as_of, time(20, 0), tzinfo=UTC)  # US close on the entry date
            idea = Idea(
                instrument_id=inst.id,
                title=s["title"],
                thesis_text=s["thesis_text"],
                parsed_json={
                    "seed": True,
                    "seed_placeholders": PLACEHOLDERS,
                    "seed_note": (
                        f"Window is a placeholder ({s['window_note']}). entry_price is the EODHD EOD close on "
                        f"{bar.as_of.isoformat()}"
                        + (" (next trading day after the placeholder start)" if bar.as_of != s["window_start"] else "")
                        + ". Success rule is a placeholder direction rule; no stop set. "
                        + "Replace via PATCH /api/ideas/{id}."
                    ),
                    "entry_source": {
                        "source": "eod",
                        "as_of": bar.as_of.isoformat(),
                        "close": bar.close,
                        "fetched_at": snap[0].fetched_at.isoformat() if snap else None,
                    },
                },
                direction=s["direction"],
                success_rule_json={"type": "direction", "seed": True},
                invalidation_rule_json=None,
                invalidation_is_note_only=False,
                window_start=s["window_start"],
                window_end=s["window_end"],
                catalyst_note=s["catalyst_note"],
                conviction_pct=None,
                capital_assigned=capital,
                idea_type="paper",
                entry_price=bar.close,
                entry_price_at=entry_at,
                radar_regime=None,
                radar_probs_json=None,
                tags=s["tags"],
            )
            db.add(idea)
            db.commit()
            db.refresh(idea)
            try:
                res = ledger.resolve_idea(db, idea, client)
            except EODHDError as exc:
                print(f"seeded {s['symbol']} id={idea.id} entry={bar.close} @ {bar.as_of}; resolve failed: {exc}")
                continue
            print(
                f"seeded {s['symbol']} id={idea.id} entry={bar.close} @ {bar.as_of} -> {res['status']} "
                f"({res.get('reason')}) pnl={idea.hypothetical_pnl_pct}%"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
