# Trade Thesis — local dev. API on :8001, web on :5174 (Radar owns :8000 / :5173).
PY := .venv/bin/python
.PHONY: setup dev api web migrate migration test typecheck build probe-eodhd lint

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
