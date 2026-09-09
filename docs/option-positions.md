# Option positions, daily marks and the divergence table (Phase 6)

Built 2026-09-09. Tables `option_positions`, `option_snapshots` and `chain_snapshots` (migration `0007`), the
take-this-expression flow, a position resolver with exit rules, dual P&L on Idea detail, and the four-cell
thesis-versus-expression matrix on Review. Feature-flagged on `settings.options_enabled` like the selector.

## Data path (nothing invented)

| number | source | stored where |
|---|---|---|
| entry debit | fresh quote of every leg, `GET /mp/unicornbay/options/contracts?filter[contract]=` (one request per leg, verified live 2026-09-09), structure mid = long mid − short mid; or the owner's stated fill | `option_positions.entry_debit`, `entry_source` (`chain_mid` / `fill`), `entry_as_of` (quote timestamp), the leg quotes in `legs_json` |
| daily mark | the stored chain band for the record date (`chain_snapshots`), long legs at mid, short legs at mid; liquidation bid/ask alongside | `option_snapshots` (`value`, `bid`, `ask`, `pnl_pct`, `pnl_abs`, `spot`, `iv`, `delta`, `theta`, `legs_json`, `source = chain_mid`) |
| settlement after expiry | payoff at the underlying's EOD close on the last trading day at or before the expiry (the contracts vanish from the chain) | `option_snapshots` with `source = expiry_intrinsic`, `legs_json` carrying the close used |
| underlying at each mark | `price_snapshots` EOD close for the record date (else the realtime quote that day) | `option_snapshots.spot` |
| chain history | the storage band, 120 days and ±25% of spot widened to the idea's levels, one row per contract per record date | `chain_snapshots` (unique on instrument, record date, contract) |

Record date: EODHD's chain is a snapshot "as of the last close" whose `bid_date` / `ask_date` are UTC, so the
23:59:59 ET close stamp lands on the next UTC day. The record date is the ET date of the latest quote timestamp
(`chains.record_date`; live check 2026-09-09: `2026-09-09T03:59:59Z` is the 2026-09-08 close).

## Taking an expression

`POST /api/ideas/{id}/positions` (write) with `candidate_name` or `rank` from the latest stored analysis, optional
`contracts` (default `floor(capital_assigned / cost)` at the fresh debit), optional `fill_price`, and the exit rules
(`take_profit_pct`, `stop_loss_pct`, `time_stop_days_before_expiry`; the Expression page prefills them from
Settings). Preconditions: options enabled (409), idea open (409), window not ended (422), no open position on the
idea (409), an analysis exists (404), the candidate is in it (422). Any EODHD failure is a 502 and nothing is
stored. The first mark (the entry-day chain mid) and a `position_opened` timeline event are written with it.

Related endpoints: `GET /api/ideas/{id}/positions`, `GET /api/positions/{pid}` (public, dollars masked: contracts,
entry cost, `pnl_abs` on the position and every mark), `PATCH /api/positions/{pid}` (exit rules of an open position,
re-applied at once), `POST /api/positions/{pid}/close` (`exit_price` optional, else the latest mark),
`POST /api/positions/{pid}/mark` (pull today's chain now; returns the job summary with any errors),
`DELETE /api/positions/{pid}` (its marks and its `position_opened` / `position_closed` timeline events go with it).

## Position resolver (`api/services/positions.py`, tested in `api/tests/test_positions.py`)

Marks are walked in date order; on each day the rules are tested in the spec's order and the first hit closes the
position at that day's value:

1. **idea resolution**: the idea is no longer open and the mark date is on or after the idea's terminal bar
   (target/stop/expiry/manual-close event date) → `idea_resolved:<idea reason>`;
2. **stop loss**: value ≤ entry × (1 − stop_loss_pct/100);
3. **take profit**: value ≥ entry × (1 + take_profit_pct/100);
4. **time stop**: mark date ≥ expiry − time_stop_days_before_expiry;
5. **expiry**: mark date ≥ expiry.

A settlement mark (`expiry_intrinsic`) always closes as `expiry`: the contracts are gone, so no rule could have acted.
Manual closes are `manual`. Closing writes a `position_closed` event on the idea's timeline, freezes `pnl_pct`
(return on premium) and `pnl_abs` (= contracts × 100 × (exit value − entry debit)); the idea's own P&L is untouched.

The daily job (`GET/POST /api/jobs/resolve`, after the ideas so a same-day resolution closes the position on the same
run) and the Settings refresh button both call `mark_positions`: per instrument with an open position, one band
request per right (puts and calls, stored into `chain_snapshots`), a per-contract request for any leg the band missed,
one mark per position for the chain's record date (idempotent: no duplicate for a date already marked), then the exit
rules. Expired positions settle from the EOD close. Per-position EODHD failures land in `JobSummary.positions.errors`.
Budget: two chain requests per instrument per day plus missing legs.

## Dual P&L and divergence

`IdeaOut.position` is the open position, else the most recent closed one; `IdeaOut.divergence` carries both P&Ls,
`thesis_right` (the idea's direction flag), `option_won` (return on premium > 0), `option_beat_thesis`, the matrix
`cell` once both sides are resolved, and a sentence. Idea detail shows "Thesis P&L, underlying" next to "Expression
P&L, on premium", the callout, and the position panel (legs as quoted at entry, editable exit rules, every mark, the
actions). The Ledger has an Option column and an expression filter; the hero adds "the option beat the thesis in X of
N trades" (`StatsOut.option_beat_thesis` / `positions_closed`).

Review's `divergence` has the four cells (count, average return on premium, average thesis return, the positions),
`resolved_positions`, `open_positions`, `option_beat_thesis` and a note. Thesis right/wrong = `direction_right_of`
(ideas with no direction flag are left out); option won/lost = sign of `pnl_pct`. Placeholder ideas are excluded.

## Retention (owner decision 2026-09-09)

Full band rows stay in `chain_snapshots` for `CHAIN_RETENTION_DAYS = 30`. Every band store also writes one
`chain_daily_summary` row per (instrument, record date): `spot`, `atm_iv`, `realized_vol_20d` (from stored EOD
closes to that date), `row_count`. The daily cron, after the position marks, rolls every older record date down to
that row (re-computed from the band rows, `rolled_up_at` stamped) and deletes the band rows; record dates holding only
per-contract rows (a position's legs quoted outside the band) are deleted without a summary. The percentile reads the
summary table only, so it is identical before and after a rollup (`test_rollup_keeps_thirty_days_and_leaves_the_
percentile_unchanged`). Marks of open positions are unaffected: they only need the current day's rows.

## IV percentile

`chains.iv_percentile_1y`: for every record date in the trailing year `chain_daily_summary.atm_iv` is the mean
call/put IV at the strike nearest the stored spot on the expiry nearest 30 days out (at least 7), computed when the
band was stored. Today's at-the-money IV from the live chain is ranked against those days. The selector writes it to `option_analyses.iv_percentile_1y`, every
candidate, and `params.iv_percentile` (`days`, `min_days`, `atm_iv`, `note`), and the score uses the percentile
penalty (0.5 per point above 50) instead of the IV-versus-realized stand-in once **20** record dates exist
(`IV_PERCENTILE_MIN_DAYS`; owner decision pending). Below that the stand-in remains and the note says how many days
are stored. History accrues from every selector run and every daily mark; the selector run on 2026-09-09 is day one.

## Decisions (owner, 2026-09-09)

* `IV_PERCENTILE_MIN_DAYS = 20` before the percentile replaces the stand-in.
* Marks use the structure mid; the liquidation bid is stored beside it but the stop is tested on the mid.
* One open position per idea; earlier closed ones stay on the record and each counts once in the matrix.
* Thesis right in the matrix is the direction flag.
* Retention as above: 30 days of band rows, then the daily summary.
