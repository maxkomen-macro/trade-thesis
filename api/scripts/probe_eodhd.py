"""Re-run the EODHD entitlement probe (`make probe-eodhd`). Prints statuses only; never the token."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services.eodhd import EODHDClient, EODHDError


def main() -> int:
    c = EODHDClient()
    today = datetime.now(UTC).date()
    checks = [
        ("user", lambda: c.user()),
        ("eod USO.US (last 10d)", lambda: c.eod("USO.US", today - timedelta(days=10), today)),
        ("real-time SPY,XLE,FXY", lambda: c.real_time(["SPY.US", "XLE.US", "FXY.US"])),
        ("search 'united states oil'", lambda: c.search("united states oil", limit=3)),
    ]
    for name, fn in checks:
        try:
            data = fn()
            n = len(data) if isinstance(data, list) else 1
            print(f"OK   {name}: {n} record(s)")
        except EODHDError as exc:
            print(f"FAIL {name}: {exc} body={exc.body!r}")
    probe = c.probe_options("AAPL")
    if probe["ok"]:
        print(
            "OK   options (mp/unicornbay/options/contracts): 200 — entitlement present; options_enabled may be set true"
        )
    else:
        print(
            f"NO   options (mp/unicornbay/options/contracts): HTTP {probe.get('status')} — keep options_enabled=false"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
