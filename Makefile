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

.PHONY: setup lint format typecheck import-lint test check cov-floors ingest-vix ingest-ohlcv ingest-cboe-strategy residual api frontend clean

help:
	@echo "Targets:"
	@echo "  setup        Create .venv and install backend + frontend deps"
	@echo "  lint         ruff check"
	@echo "  format       ruff format"
	@echo "  typecheck    mypy --strict on src/tail_lab"
	@echo "  import-lint  import-linter (module layering)"
	@echo "  test         pytest with coverage"
	@echo "  check        lint + typecheck + import-lint + test (all CI gates)"
	@echo "  ingest-vix   Live VIX fetch (Cboe, Yahoo fallback) -> bronze (network; not run in CI)"
	@echo "  ingest-ohlcv Live OHLCV fetch (Nasdaq, Yahoo fallback) -> bronze for SYMBOL (default AAPL; network; not run in CI)"
	@echo "  ingest-cboe-strategy  Live Cboe strategy-index fetch -> bronze (TICKERS=... ; network; not run in CI)"
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
# CI runs `ruff format --check` as a separate gate (.github/workflows/ci.yml).
# It lives here so `make check` really is every CI gate: without it a file
# appended by a script rather than an editor can pass locally and fail CI,
# which is exactly what happened on 2026-08-21.
	env -u PYTHONPATH $(VENV)/bin/ruff format --check src tests

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
	env -u PYTHONPATH $(VENV)/bin/python -c "from tail_lab.ingestion.vix import ingest_vix; from tail_lab.config import get_lake_store; from tail_lab.observability import configure_logging; configure_logging(); r = ingest_vix(get_lake_store()); print(f'committed {r.valid_rows} rows from {r.source_id} -> {r.bronze_path} ({r.quarantined_rows} quarantined)')"

# Read-only: replicates the published Cboe put-protection indices with our own
# pricer and prints the model-vs-market residual. Touches no lake writes, so
# unlike the ingest-* targets it is safe for an agent to run.
PROGRAMS ?= PPUT PPUT3M
residual:
	env -u PYTHONPATH $(VENV)/bin/python -c "import datetime as dt; from tail_lab.config import get_lake_store; from tail_lab.research.backtest.index_replication import compute_index_replication, format_replication_report; s = get_lake_store(); print(chr(10).join(format_replication_report(compute_index_replication(s, index_symbol=p, as_of=dt.date.today())) for p in '$(PROGRAMS)'.split()))"


SYMBOL ?= AAPL
ingest-ohlcv:
	env -u PYTHONPATH $(VENV)/bin/python -c "from tail_lab.ingestion.ohlcv import ingest_ohlcv; from tail_lab.config import get_lake_store; from tail_lab.observability import configure_logging; configure_logging(); r = ingest_ohlcv(get_lake_store(), '$(SYMBOL)'); print(f'committed {r.valid_rows} rows from {r.source_id} (adj_close basis: {r.adjustment_basis}) -> {r.bronze_path} ({r.quarantined_rows} quarantined)')"

# TICKERS is an optional comma-separated override; empty means the adapter's
# DEFAULT_TICKERS (the tail-hedge family + SPX).
TICKERS ?=
ingest-cboe-strategy:
	env -u PYTHONPATH $(VENV)/bin/python -c "from tail_lab.ingestion.cboe_strategy import ingest_cboe_strategy, ticker_labels; from tail_lab.config import get_lake_store; from tail_lab.observability import configure_logging; configure_logging(); t = ticker_labels('$(TICKERS)'.split(',')) if '$(TICKERS)' else None; r = ingest_cboe_strategy(get_lake_store(), t); print(f'committed {r.valid_rows} rows for {len(r.tickers)} indices -> {r.bronze_path} ({r.quarantined_rows} quarantined)')"

api:
	env -u PYTHONPATH $(VENV)/bin/uvicorn tail_lab.api.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

clean:
	find . -type d -name __pycache__ -not -path './frontend/*' -not -path './.venv/*' -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
