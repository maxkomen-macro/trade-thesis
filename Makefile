# Trade Thesis — local dev. API on :8001, web on :5174 (Radar owns :8000 / :5173).
PY := .venv/bin/python
.PHONY: setup dev api web migrate migration test typecheck build probe-eodhd probe-selector verify-deploy lint seed

setup:
	uv venv .venv --python 3.12
	uv pip install --python $(PY) -r pyproject.toml --extra dev
	cd web && npm install

api:
	$(PY) -m uvicorn api.main:app --reload --port 8001

web:
	cd web && npm run dev

dev:
	@trap 'kill 0' INT TERM EXIT; $(MAKE) api & $(MAKE) web & wait

migrate:
	$(PY) -m alembic upgrade head

migration:
	$(PY) -m alembic revision --autogenerate -m "$(m)"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check api

typecheck:
	cd web && npm run typecheck

build:
	cd web && npm run build

probe-eodhd:
	$(PY) -m api.scripts.probe_eodhd

seed:
	$(PY) -m api.scripts.seed_ideas

# Dry-run the options selector on the live chain, no database writes. Example:
#   make probe-selector args="USO.US down --target 135 --stop 148 --days 21"
probe-selector:
	$(PY) -m api.scripts.probe_selector $(args)

# Verify a deployment URL (health, status, masking, SPA, cron, parse, one options analysis). Example:
#   make verify-deploy url=https://trade-thesis-xxxx.vercel.app idea=5
verify-deploy:
	$(PY) -m api.scripts.verify_deploy $(url) $(if $(idea),--idea $(idea),)
