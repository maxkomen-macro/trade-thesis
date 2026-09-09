# Options expression selector (Phase 5)

Built 2026-09-05. `POST /api/ideas/{id}/options` runs the selector for an open idea and stores one
`option_analyses` row; `GET /api/ideas/{id}/options` returns the latest stored run (public read, dollars masked
for viewers without the write token). The Expression page (`/ideas/:id/options`) renders that row and nothing else.
Feature-flagged on `settings.options_enabled`.

## Data path (nothing invented)

| number | source | stored where |
|---|---|---|
| spot | EODHD delayed quote (`/real-time`), also written as a `realtime` price snapshot | `option_analyses.spot`, `spot_as_of`, `spot_source` |
| chain quotes, IV, OI, volume, delta | EODHD UnicornBay `/mp/unicornbay/options/contracts` (see `eodhd-probe.md`) | every leg inside `candidates_json` keeps `bid/ask/mid/iv/oi/volume/delta` and the row's `contract` |
| chain time | `bid_date` / `ask_date` (quote timestamps) and `tradetime` (last trade date) | `chain_as_of`, `chain_trade_date` |
| 20-day realized vol | annualized std of log returns over stored EOD closes (`parser.realized_vol_pct`) | `realized_vol_20d` (percent) |
| risk-free rate | `settings.risk_free_rate_pct`, a model assumption, shown on the page | `rate_pct` |

The chain request: one page per needed right (`filter[type]`), `filter[exp_date_from]=today`,
`filter[exp_date_to]=today+120d`, strikes ±25% of spot widened to cover target and stop, `page[limit]=1000`,
`sort=strike`, `fields[options-contracts]` trimmed to the 18 fields used. Live on 2026-09-05: 622 USO puts in one
request, 386 candidates, whole run 2.9 s without the rationale call and 12.7 s with it.

## Candidates (`api/services/options.py`)

For every expiry on or after `window_end`, plus the one immediately before it (labelled "no cushion"):

* **long put / long call** (thesis side): strikes from 10% in the money to 5% beyond the target;
* **vertical debit spread**: long leg within 7% of spot (5 nearest), short leg at or beyond the target within 12%
  past it (5 nearest);
* **long straddle / strangle** only when `direction = range` or `conviction_pct` is null (3 nearest strikes).

A directional idea needs a price target: a `level` rule's level, or entry × (1 ± pct) for `pct_move`. `direction`,
`relative` and `range` rules have none, so the run is a `no_trade` with the reason stated. All five structures are
Fidelity Level 2 eligible (`tier_ok` is still recorded per candidate).

## Metrics per candidate (`pricing.py`, Black-Scholes at each leg's chain IV, held constant)

`debit` (structure mid), `structure_bid/ask`, `spread_width_pct` (structure round trip), `leg_widths_pct`,
`open_interest` and `volume` (minimum across legs), `breakevens` (exact, piecewise linear), `breakeven` (thesis side),
`target_to_breakeven`, `move_spent_to_breakeven_pct`, value and return on premium **at target on the window end**
(`eval_date = min(window_end, expiry)`), at target at expiry, and at spot on the window end, `pop_pct`
(risk-neutral lognormal at the long leg's IV, integrated over the profitable intervals), `theta_per_day`,
`theta_week_pct` (decay over the window with the underlying at spot, per week, % of premium), `days_of_theta`
(days at spot before the structure loses `default_stop_loss_pct`), `iv`, `iv_rv_ratio`, `iv_percentile_1y` (null
until chain history exists), `cost_per_contract`, `contracts = floor(capital_assigned / cost)`, `at_risk`,
`max_loss_per_contract`, `max_gain_per_contract`, `model_vs_mid_pct` (how far the model at chain IV is from the mid;
~1% on live USO). Sizing (owner decision 2026-09-05): the risk budget is account-level,
`risk_budget = settings.account_size × default_risk_pct`; it never changes the contract count, it only raises an amber
warning (`capital_exceeds_risk_budget`, also in `params.sizing`) on the Expression and New Thesis pages when the idea's
capital is larger than it. `account_size` is hidden from public viewers everywhere it appears.

Missing chain IV falls back to the 20-day realized vol and is flagged (`iv_fallback`, `iv_source`). No IV and no
realized vol makes the candidate unpriceable rather than guessed.

## Filters, score, verdict

* Hard filters: open interest ≥ 100 on every leg; **widest leg's** bid/ask width ≤ 10% (the structure's own width is
  reported and penalized, but netting two tight legs into a small debit is not illiquidity).
* Score = return at target on the window end − 2.0 × structure width % − 1.0 × theta % per week − IV richness
  (50 × (IV/RV − 1) above parity; 0.5 per percentile point above 50 once the percentile exists) − cushion penalty
  (15 points flat plus return × the fraction of the window the option leaves uncovered, when expiry precedes the
  window end). Weights live in `SCORE_WEIGHTS`; the breakdown is stored per candidate.
* Verdict `no_trade` when: no target, nothing passes the filters, the target sits inside breakeven for every passing
  candidate, or the best return at target on the window end is ≤ 50%.
* Top three carry the scenario grid (rows: stop → 5% past target in 9 steps with target and spot snapped in; columns:
  entry, weekly, window end, expiry) and a 41-point payoff-at-expiry curve.

## Shares comparison

`shares_comparison_json`: the underlying move to target, return on `capital_assigned` for long/short shares, the
optional inverse ETF (symbol and leverage stated by the caller; leverage × move, labelled "before daily-rebalance
drag"), the best option's return on premium and dollars at risk, and a vehicle verdict: "Trade the …" when the run is
`trade` and at least one contract is affordable with the capital, "Option unaffordable at this capital; shares used"
when not, "Shares are the cleaner vehicle" for `no_trade`.

## Rationale strings

`claude-sonnet-4-6` receives only the computed numbers (no chain, no prose) and returns `verdict_text` plus one
`why` per candidate via structured output. Every numeric token in its output must be a rounding of a number in the
payload (`text_uses_only_input_numbers`); a string that fails, or any API error, is replaced by a deterministic
template and `rationale_source` says so.

## Open decisions for the owner

* `account_size` is seeded from the owner's stated figure and edited in Settings; `default_risk_pct` is a share of it.
* `risk_free_rate_pct` is seeded at 4.0 because it reprices UnicornBay's own theoretical values within about 1%;
  set it to the current bill yield in Settings.
* The 1-year IV percentile now comes from `chain_snapshots` once 20 record dates exist (`docs/option-positions.md`);
  backfilling it from EODHD's per-contract `/eod` history is possible but needs roughly one request per historical
  expiry per instrument.
* Dry runs: `make probe-selector args="USO.US down --target 135 --stop 148 --days 21"` (no database writes).
