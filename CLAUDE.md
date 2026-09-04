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
  `api/db/` models/schemas/session, `api/services/` eodhd/radar(regime store)/(parser/resolver/options/pricing),
  `api/routers/` system/jobs/(ideas/instruments/analyses/settings), `api/tests/` pytest (no network; in-memory
  SQLite via `JSONType = JSON().with_variant(JSONB, "postgresql")`).
- `web/` Vite + React 18 + TS + Tailwind v4 (`@tailwindcss/vite`) + React Query + Lightweight Charts 5.
  Tokens live in `web/src/styles/index.css` (`@theme`): bg `#0d1117`, surface `#161b22`, surface-2 `#1c2129`,
  line `#30363d`, accent amber `#f0b429`, right `#2ecc71`, wrong `#e74c3c`, open `#4a9eff`.
  Fonts: Space Grotesk (headings), IBM Plex Sans (body), IBM Plex Mono (numbers/tickers, class `num`).
  Sentence-case labels, no all-caps eyebrows.
- `alembic/` migrations (`0001_baseline` settings + seeds, `0002_regime_snapshots`). `alembic/env.py` uses the unpooled URL.
- `design/trade-thesis-mockup.html` — layout reference (owner to supply; not yet in repo).

## Commands

`make setup` · `make dev` (API + web) · `make api` · `make web` · `make migrate` · `make migration m="msg"` ·
`make test` · `make lint` · `make typecheck` · `make build` · `make probe-eodhd`

## State of the build

_Updated at the end of every phase so a fresh or compacted session can resume._

- **Phase 1 (scaffold + probe): complete on 2026-09-04.** Repo layout, env handling, Neon linked and reachable,
  Alembic `0001` + `0002` applied (`settings` seeded, `regime_snapshots`), FastAPI with `/api/health`, `/api/status`,
  `/api/regime`, `POST /api/jobs/regime` (Radar push, idempotent), write/cron auth dependencies, EODHD client, regime store, Vite shell with tokens, top bar with regime readout,
  Settings page with connection status, `make dev` working. EODHD probe written to `docs/eodhd-probe.md`: options were 403 at first, then the owner activated the
  UnicornBay add-on the same day and the re-probe returned 200; `options_enabled` is now true and Phases 5–6 are unblocked. Nothing committed yet (branch `phase-1-scaffold`).
- **Open items for the owner:** supply `design/trade-thesis-mockup.html` (arriving later); supply three past ideas for
  the Phase 2 seed; wire Radar's GitHub Action to `POST /api/jobs/regime` once deployed; set Vercel env vars personally.
- **Next: Phase 2 (ledger core)** — models + migrations for instruments/ideas/price_snapshots/resolution_events,
  CRUD, price snapshot fetch, resolver for all rule types with fixture tests, cron wiring, Ledger + Idea detail pages.
