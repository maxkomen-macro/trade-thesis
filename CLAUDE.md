# Trade Thesis — project conventions

Public, portfolio-quality trade-idea journal. Sibling of Macro Regime Radar (`../Macro/macro-regime-radar`,
read-only from here; never modify it). Own repo, own Neon database, own Vercel deployment.

## Non-negotiable rules

1. **Never commit or push without the owner's explicit approval.** Work on a local branch, show a diff summary, wait.
   `git init` and branches are fine; `git commit` / `git push` are not until told.
2. **No fabrication.** Never invent prices, chain data, Greeks, or regimes. If EODHD or Radar fails, surface the
   error (UI + logs) and store nothing. Fixtures are allowed only in `api/tests/` and must be labeled as fixtures.
   Every number shown in the UI traces to a stored row with a timestamp (`fetched_at` / `as_of`).
3. **Deterministic math; the LLM only parses.** Anthropic is used for prose → thesis schema and short rationale
   strings computed from numbers we pass in. Pricing, P&L, resolution, scoring are plain Python with tests.
4. **Free tiers only.** Anthropic API is the only paid service. Vercel Hobby + Neon free tier. If Vercel Cron limits
   bite, fall back to a GitHub Actions schedule hitting `POST /api/jobs/resolve`. Never add paid infra.
5. **Public read, protected write.** Writes need `Authorization: Bearer <TT_WRITE_TOKEN>`; cron needs `CRON_SECRET`.
   `PUBLIC_HIDE_DOLLARS=true` hides dollar figures from unauthenticated viewers (percentages only).
6. **Verify before assuming.** Probe an endpoint before wrapping it. Read Radar's code for its routes; do not guess.

## Verified facts (do not re-derive)

- **EODHD plan** (`docs/eodhd-probe.md`): `eod`, `real-time` (batched via `s=`), `search`, `user` all work.
  **Options: AVAILABLE since 2026-09-04** via the UnicornBay marketplace add-on:
  `GET /api/mp/unicornbay/options/contracts|eod|underlying-symbols`. JSON:API body; `filter[underlying_symbol]`
  (bare symbol, `USO`) or `filter[contract]` required; always pass `filter[exp_date_from]` (expired contracts are
  returned otherwise); single-key `sort` only; `page[limit]` ≤ 1000; `fields[options-contracts]=...` trims payload.
  IV field is `volatility` (decimal). `settings.options_enabled = true` (DB row, flipped 2026-09-04).
  Daily limit 100,000 calls, 1,200/min. Legacy `/api/options/{sym}` is retired (404).
- **Radar integration is push-only.** Radar's FastAPI backend is localhost-only (`:8000`) and stays that way, so
  Trade Thesis **never calls Radar at runtime**. Radar's GitHub Action posts its daily regime to
  `POST /api/jobs/regime` (bearer `CRON_SECRET`, or `TT_WRITE_TOKEN`); rows land in `regime_snapshots`
  `(as_of, regime, probs_json, source, created_at)`, unique on `(as_of, source)`. Idea creation stamps the latest
  row; if the table is empty it stores null and continues. Radar's own route for the action to read is
  `GET /api/regime/latest` (verified in `macro-regime-radar/api/main.py`; the spec's `/api/regime/current` does
  not exist). Payload/field names mirror Radar's `Regime` model. See `docs/regime-push.md`. Labels:
  `Goldilocks`, `Overheating`, `Stagflation`, `Recession Risk`; probabilities are 0–1.
- **Vercel Python model (docs, 2026-08):** a FastAPI preset runs the whole app as one function from
  `pyproject.toml` `[tool.vercel] entrypoint = "api.main:app"`; files under `api/` do **not** become separate
  functions when a preset is detected. Frontend + backend in one project uses `services` in `vercel.json`.
  Vercel Python default is 3.12 (`.python-version`).
- **Neon:** project `old-lake-51340142` ("trade-thesis"), branch `production`, Postgres 18, region aws-us-east-2,
  database `neondb`. `neon link` writes `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` into `.env.local`.
  Neon Auth is enabled on the branch (`neon.ts` has `auth: true`) but the app does not use it; writes use the
  shared bearer token.
- **Ports:** API `:8001`, web `:5174`. Radar owns `:8000` / `:5173`.

## Environment variables (`.env.example`)

`DATABASE_URL`, `DATABASE_URL_UNPOOLED`, `EODHD_API_KEY`, `ANTHROPIC_API_KEY`, `TT_WRITE_TOKEN`,
`CRON_SECRET`, `PUBLIC_HIDE_DOLLARS`, `APP_ENV`, `CORS_ORIGINS`. (No Radar URL: Radar pushes to us. `EODHD_API_TOKEN` is accepted as an alias of `EODHD_API_KEY`.)
Local dev reads `.env` then `.env.local` (Neon-managed). Both are gitignored. Local `.env` reuses the owner's EODHD and
Anthropic keys from the Radar checkout for probes only. The owner sets Vercel env vars personally; never push them.

## Stack and layout

- `api/` FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 + httpx + anthropic. Entry `api/main.py`.
  `api/db/` models/schemas/session; `api/services/` eodhd (client), prices (snapshot cache), rules (rule schema),
  resolver (pure decision engine), ledger (DB orchestration + serialization), radar (regime store), parser (Anthropic
  structured output -> thesis schema, EODHD symbol verification, deterministic context tile);
  `api/routers/` system/jobs/ideas/instruments; `api/scripts/` probe_eodhd, seed_ideas; `api/tests/` pytest
  (no network; in-memory SQLite via `JSONType = JSON().with_variant(JSONB, "postgresql")`, `TagsType` likewise).
- `web/` Vite + React 18 + TS + Tailwind v4 (`@tailwindcss/vite`) + React Query + Lightweight Charts 5.
  Tokens live in `web/src/styles/index.css` (`@theme`): bg `#0d1117`, surface `#161b22`, surface-2 `#1c2129`,
  line `#30363d`, accent amber `#f0b429`, right `#2ecc71`, wrong `#e74c3c`, open `#4a9eff`.
  Fonts: Space Grotesk (headings), IBM Plex Sans (body), IBM Plex Mono (numbers/tickers, class `num`).
  Sentence-case labels, no all-caps eyebrows.
- `alembic/` migrations (`0001` settings + seeds, `0002` regime_snapshots, `0003` instruments/ideas/price_snapshots/
  resolution_events, `0004` ideas.spread_at_window_end_pct). `alembic/env.py` uses the unpooled URL.
- `design/trade-thesis-mockup.html` — layout reference (arrived 2026-09-04). Match its screens, not a clone of Radar.

## Resolver semantics (api/services/resolver.py, tested in api/tests/test_resolver.py)

- Bars considered: `max(entry_date, window_start) <= as_of <= window_end`. `entry_date` = date of `entry_price_at`.
- Order per run: invalidation → success → window expiry → `progress` event (one per day, deduped).
  If stop and target both hit, the earlier date wins; a tie goes to the stop.
- `level`: first qualifying close (or low/high for touch). `direction`: only once the window is closed (we hold the
  window-end bar, or today > window_end), right if the signed return at the final bar is > 0. `pct_move`: any close
  inside the window. `relative`: return spread vs benchmark at any close inside the window (path-dependent, like
  pct_move). `all_of` hit date = latest child, `any_of` = earliest.
- Expiry without a hit → `status=expired`, `resolution_reason=expired:direction_right|expired:direction_wrong`,
  so direction hit rate and target hit rate are separate. `direction_right_of()` in ledger.py derives the flag.
- P&L = `capital_assigned × signed return`; `down`/`underperform` invert the sign; relative ideas use the spread;
  `range` has no P&L until an option position exists. Frozen at the resolution bar; marked daily while open.
- Relative ideas additionally store `spread_at_window_end_pct` (owner decision 2026-09-05): resolution stays
  path-dependent, but the daily job backfills the window-end spread for ideas that resolved early (writes a
  `benchmark_update` event) so the detail page shows both numbers.
- Seed/placeholder ideas (`parsed_json.seed = true`) are excluded from the hero sentence, stat strip, hit rate by
  regime, and resolving-soon; they appear in the table with a `placeholder` tag. `StatsOut.seed_count` reports them.
- Manual close (`POST /api/ideas/{id}/close`) → `closed_manual`, reason `manual`, P&L at the latest stored close.
- Reads never call EODHD. Creation stamps `entry_price` from the delayed quote (stored as a `realtime` snapshot),
  pulls 45 days of history, and runs the resolver once. `POST/GET /api/jobs/resolve` is the daily job (Vercel Cron
  sends GET); `POST /api/jobs/refresh-prices` is the Settings button. `hide_dollars` masks `capital_assigned`
  and `hypothetical_pnl_abs` for non-writers; the write token is sent on every request so the owner sees dollars.

## Parser (api/services/parser.py, tested in api/tests/test_parser.py with the model call replaced)

- `POST /api/ideas/parse` (write-protected) → `ParseResponse`. Model `claude-sonnet-4-6` via `client.messages.parse`
  with a Pydantic `output_format` (`ParsedThesisLLM`), so the model can only emit the schema. No thinking, no prefill.
- The model never sees or produces market numbers. It returns queries/guesses for the instrument and benchmark;
  the server verifies them against EODHD search (accept an exact ticker match on the US exchange or a single hit,
  otherwise return `symbol_candidates` plus a question). Suggested rules carry `suggested: true`.
- Missing target / stop / window / conviction stay null and become `questions: [{field, question}]`. The New Thesis
  page shows them inline in amber; nothing can be saved without a success rule, a window end, and a symbol.
- Context tile is computed from stored snapshots: last close (delayed quote stored as a `realtime` snapshot), 20-day
  realized vol (annualized std of log returns over the last 20 closes), distance to target, latest pushed regime.
  EODHD failures appear in `context.errors`; numbers are never substituted.
- Saving posts the edited fields to `POST /api/ideas` with `parsed_json` (model id, timestamp, raw LLM output).

## Commands

`make setup` · `make dev` (API + web) · `make api` · `make web` · `make migrate` · `make migration m="msg"` ·
`make test` · `make lint` · `make typecheck` · `make build` · `make probe-eodhd` · `make seed`

## State of the build

_Updated at the end of every phase so a fresh or compacted session can resume._

- **Phase 1 (scaffold + probe): complete on 2026-09-04.** Repo layout, env handling, Neon linked and reachable,
  Alembic `0001` + `0002` applied (`settings` seeded, `regime_snapshots`), FastAPI with `/api/health`, `/api/status`,
  `/api/regime`, `POST /api/jobs/regime` (Radar push, idempotent), write/cron auth dependencies, EODHD client, regime store, Vite shell with tokens, top bar with regime readout,
  Settings page with connection status, `make dev` working. EODHD probe written to `docs/eodhd-probe.md`: options were 403 at first, then the owner activated the
  UnicornBay add-on the same day and the re-probe returned 200; `options_enabled` is now true and Phases 5–6 are unblocked. Committed as `13f2c61` on branch `phase-1-scaffold`.
- **Phase 2 (ledger core): built 2026-09-04, adjusted and committed 2026-09-05.** Migration `0003`, ideas/
  instruments CRUD, price snapshot cache, resolver for all rule types (18 unit tests on fixture paths), ideas API
  tests on SQLite with EODHD mocked, `POST/GET /api/jobs/resolve` + `POST /api/jobs/refresh-prices`, Ledger page
  (stat strip, filters, progress bars, resolving soon, hit rate by regime) and Idea detail (Lightweight Charts price
  chart with entry/target/stop lines, window shading, event markers, timeline, resolve/close/delete actions).
  Seeded the owner's three past ideas as paper ideas via `make seed` with real EODHD entry closes and placeholder
  windows/rules flagged `seed: true` (MU expired wrong, SCO expired wrong, LMT right).
- **Open items for the owner:** replace the seed placeholders (windows, targets, stops) via PATCH or the UI later;
  wire Radar's GitHub Action to `POST /api/jobs/regime` once deployed.
- **Phase 3 (parser): built 2026-09-05, uncommitted pending owner review.** `POST /api/ideas/parse` with structured
  outputs on `claude-sonnet-4-6`, EODHD symbol verification with candidates, deterministic context tile, New Thesis
  page laid out per the mockup's compose screen (prose + context tile left, editable parsed fields with amber
  questions right, "Log and find an expression" / "Log only"). 7 parser tests with the model call stubbed.
- **Next: Phase 4 (review + settings + deploy)** — Review page breakdowns, settings editing, hide-dollars toggle,
  Vercel deploy (confirm Services on Hobby or split into two projects), cron confirmed firing.
