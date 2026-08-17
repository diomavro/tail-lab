# tail-lab — development tasks.
#
# Uses a project-local .venv (see README/CLAUDE.md: this repo is isolated,
# unlike most apps in ~/Documents/apps/ which use the system Python). Every
# target below invokes .venv/bin/* directly — no "activate" step needed.
# PYTHONPATH is explicitly cleared: this shell's global PYTHONPATH points at
# ~/.local/lib/python3.12/site-packages, which would otherwise leak into and
# break the venv's isolation.

VENV := .venv
PY := env -u PYTHONPATH $(VENV)/bin/python
PIP := env -u PYTHONPATH $(VENV)/bin/pip

.PHONY: setup lint format typecheck import-lint test check cov-floors ingest-vix api frontend clean

help:
	@echo "Targets:"
	@echo "  setup        Create .venv and install backend + frontend deps"
	@echo "  lint         ruff check"
	@echo "  format       ruff format"
	@echo "  typecheck    mypy --strict on src/tail_lab"
	@echo "  import-lint  import-linter (module layering)"
	@echo "  test         pytest with coverage"
	@echo "  check        lint + typecheck + import-lint + test (all CI gates)"
	@echo "  ingest-vix   Live VIX fetch -> bronze (network; not run in CI)"
	@echo "  api          Run FastAPI on :8000 with auto-reload"
	@echo "  frontend     Run the Vite dev server"
	@echo "  clean        Remove caches and build artifacts"

setup:
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip -q
	$(PIP) install -e ".[dev]" -q
	cd frontend && npm install

lint:
	env -u PYTHONPATH $(VENV)/bin/ruff check src tests

format:
	env -u PYTHONPATH $(VENV)/bin/ruff format src tests
	env -u PYTHONPATH $(VENV)/bin/ruff check --fix src tests

typecheck:
	env -u PYTHONPATH $(VENV)/bin/mypy src/tail_lab

import-lint:
	env -u PYTHONPATH $(VENV)/bin/lint-imports

test:
	env -u PYTHONPATH $(VENV)/bin/python -m pytest --cov=tail_lab --cov-report=term-missing

# STANDARDS §e: >=90% on the correctness-critical layers (research/, transforms/).
# Reuses the .coverage data written by `test`, so run it after `test`.
cov-floors:
	env -u PYTHONPATH $(VENV)/bin/coverage report \
		--include="src/tail_lab/research/*,src/tail_lab/transforms/*" --fail-under=90

check: lint typecheck import-lint test cov-floors

ingest-vix:
	env -u PYTHONPATH $(VENV)/bin/python -c "from tail_lab.ingestion.vix import ingest_vix; from tail_lab.lake.store import LocalParquetLakeStore; from tail_lab.config import get_settings; r = ingest_vix(LocalParquetLakeStore(get_settings().lake_root)); print(f'committed {r.valid_rows} rows -> {r.bronze_path} ({r.quarantined_rows} quarantined)')"

api:
	env -u PYTHONPATH $(VENV)/bin/uvicorn tail_lab.api.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

clean:
	find . -type d -name __pycache__ -not -path './frontend/*' -not -path './.venv/*' -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
