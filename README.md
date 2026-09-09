# Trade Thesis

Live: https://trade-thesis-max-d0ec.vercel.app · Source: https://github.com/maxkomen-macro/trade-thesis

A public trade-idea journal that keeps score. Log an idea in plain English, let the parser turn it into a
machine-checkable success condition, invalidation level, and time window, and a daily job marks it Right, Wrong,
or Expired from end-of-day prices. Every idea carries a hypothetical capital amount, so P&L is a paper-portfolio
figure, never a brokerage balance. A feature-flagged options module scores contract structures against each thesis.

Sibling project of [Macro Regime Radar](../Macro/macro-regime-radar): Radar pushes its daily regime here
(`docs/regime-push.md`), and the regime in force when an idea was logged is stamped on it, so hit rates can be
broken down by regime.

## Status

Phases 1 (scaffold + data-source probe), 2 (ledger core: ideas, price snapshots, resolver, Ledger and Idea
detail pages), 3 (Anthropic-backed thesis parser and the New Thesis page) and 4 (Review, Settings, deploy) are live.
Phase 5, the options expression selector, is live: Black-Scholes pricing with tests written first, candidate
generation from the live EODHD chain, scoring, scenario heatmaps, a payoff chart, and a shares comparison, gated
behind `settings.options_enabled` (`docs/options-selector.md`). Phase 6 (taking a position, daily marks from the
stored chain, exit rules in a fixed order, dual P&L, and the thesis-versus-expression matrix) is built and awaiting
review (`docs/option-positions.md`). See `CLAUDE.md` → *State of the build* and `docs/eodhd-probe.md`.

## Stack

- API: FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, httpx, anthropic — one Vercel Python function.
- Web: Vite, React 18, TypeScript, Tailwind v4, React Query, Lightweight Charts.
- Data: Neon Postgres (free tier), EODHD for prices, Anthropic for parsing only.

## Run locally

```bash
cp .env.example .env         # fill EODHD_API_KEY, ANTHROPIC_API_KEY, TT_WRITE_TOKEN, CRON_SECRET
neon link                     # writes DATABASE_URL / DATABASE_URL_UNPOOLED to .env.local
make setup                    # uv venv + deps, npm install
make migrate                  # alembic upgrade head
make seed                     # optional: the owner's three past ideas as paper ideas
make dev                      # API :8001 + web :5174
```

Then open http://localhost:5174. API docs at http://localhost:8001/api/docs.

## Verify data access

```bash
make probe-eodhd
```

Dry-run the options selector against the live chain without writing anything:

```bash
make probe-selector args="USO.US down --target 135 --stop 148 --days 21"
```

## Deploy (Vercel Hobby, one Python function)

```bash
make migrate                                   # schema changes go to Neon first, from your machine
vercel deploy --prod --skip-domain --yes       # staged production deployment on its own URL
make verify-deploy url=<deployment-url>        # health, status, masking, SPA deep links, cron, parse, one options run
vercel promote <deployment-url> --yes
```

Env vars (`EODHD_API_KEY`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `TT_WRITE_TOKEN`, `CRON_SECRET`) are set in the
Vercel project's Production environment. The daily cron calls `GET /api/jobs/resolve` at 21:30 UTC on weekdays.

## Tests

```bash
make test
```
