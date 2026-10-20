.PHONY: help install install-frontend install-ml dev dev-backend dev-frontend check \
        lint format format-check typecheck test test-front test-full check-all e2e \
        seed demo seed-db reset-db migrate migration schemas headless live-demo export-schema \
        clean

PY        ?= .venv/Scripts/python.exe
PYTHON    ?= .venv/Scripts/python
PIP       := $(PYTHON) -m pip
NPMPKG    ?= frontend
NPM       ?= npm
HOST      ?= 127.0.0.1
BACKPORT  ?= 8000
FRONTPORT ?= 5173

help:
	@echo "SKYGYM - available commands"
	@echo "  make install         install backend (and frontend) dependencies"
	@echo "  make dev             start backend (:$(BACKPORT)) and frontend (:$(FRONTPORT))"
	@echo "  make check           lint + typecheck + fast tests  (the CI gate)"
	@echo "  make check-all       check + slow/perf sweeps + frontend tests"
	@echo "  make test            fast backend test suite only"
	@echo "  make test-front      frontend unit tests (vitest)"
	@echo "  make test-full       full backend suite incl. 200-seed generator sweep"
	@echo "  make e2e             Playwright smoke test (needs `make dev` running)"
	@echo "  make lint            ruff check + ruff format --check + eslint"
	@echo "  make format          ruff format + ruff --fix + prettier --write"
	@echo "  make format-check    prettier --check"
	@echo "  make typecheck       mypy on backend/app/core  +  tsc on frontend"
	@echo "  make seed            create the SQLite DB, load scenarios, seed demo data"
	@echo "  make migrate         apply Alembic migrations (head)"
	@echo "  make migration       autogenerate a migration: make migration M=\"message\""
	@echo "  make demo            seed, then start the app at the demo scenario"
	@echo "  make headless        run one scenario headless and print entity paths"
	@echo "  make live-demo       play one live session over the API (needs 'make dev')"
	@echo "  make schemas         export Pydantic JSON Schema into docs/schema/"
	@echo "  make reset-db        drop and recreate the SQLite database"

# ---------------------------------------------------------------- install ----
install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt
	cd $(NPMPKG) && $(NPM) install

install-frontend:
	cd $(NPMPKG) && $(NPM) install

install-ml:
	$(PYTHON) -m pip install -r backend/requirements-ml.txt

# -------------------------------------------------------------------- dev ----
dev:
	$(MAKE) seed
	$(MAKE) dev-backend dev-frontend

dev-backend:
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(BACKPORT) --app-dir backend --reload

dev-frontend:
	cd $(NPMPKG) && $(NPM) run dev -- --host $(HOST) --port $(FRONTPORT)

# ------------------------------------------------------------------ check ----
check: lint typecheck test
	@echo ""
	@echo "==== make check: ALL GREEN ===="

check-all:
	$(MAKE) lint format-check typecheck
	$(PYTHON) -m pytest backend/tests -q
	$(MAKE) test-front
	@echo ""
	@echo "==== make check-all: ALL GREEN ===="

lint:
	$(PYTHON) -m ruff check backend
	$(PYTHON) -m ruff format --check backend
	cd $(NPMPKG) && $(NPM) run lint

format:
	$(PYTHON) -m ruff format backend
	$(PYTHON) -m ruff check --fix backend
	cd $(NPMPKG) && $(NPM) run format

format-check:
	cd $(NPMPKG) && $(NPM) run format:check

typecheck:
	$(PYTHON) -m mypy --config-file pyproject.toml
	cd $(NPMPKG) && $(NPM) run typecheck

test:
	$(PYTHON) -m pytest backend/tests -q -m "not slow"

test-front:
	cd $(NPMPKG) && $(NPM) run test

test-full:
	$(PYTHON) -m pytest backend/tests -q

e2e:
	cd $(NPMPKG) && $(NPM) run test:e2e

# ------------------------------------------------------------------- data ----
seed: seed-db

seed-db:
	$(PYTHON) backend/scripts/seed.py

reset-db:
	$(PYTHON) backend/scripts/reset_db.py

# -------------------------------------------------------------- migrations ----
ALEMBIC := $(PYTHON) -m alembic -c backend/alembic.ini

migrate:
	$(ALEMBIC) upgrade head

migration:
	$(ALEMBIC) revision --autogenerate -m "$(M)"

downgrade:
	$(ALEMBIC) downgrade -1

demo: seed
	@echo "SKYGYM demo ready."
	@echo "  briefing : http://localhost:$(FRONTPORT)/briefing/scn_solo_fpv_dusk"
	@echo "  aar      : http://localhost:$(FRONTPORT)/aar"
	@echo "  now run:  make dev"
	$(MAKE) dev

# ------------------------------------------------------------------ extras ---
schemas:
	$(PYTHON) backend/scripts/export_schema.py

export-schema: schemas

headless:
	$(PYTHON) backend/scripts/headless.py

live-demo:
	$(PYTHON) backend/scripts/live_demo.py

clean:
	rm -rf frontend/dist .pytest_cache .ruff_cache backend/data/skygym.db
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +