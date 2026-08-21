# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read first

`README.md` is the **canonical specification** — the north-star decision, the two strategies, and the "constitution" of hard principles. `ARCHITECTURE.md` covers layout and the machine-checked layer rule; `docs/DATA_FLOW.md` traces where every number on the screen comes from and what breaks if a source dies; `docs/STANDARDS.md` the engineering bar; `docs/DATA_CONTRACTS.md` the dataset schemas; `docs/adr/` the decisions behind all of it. Changing anything the constitution covers requires a human-approved ADR. Do not restate content from those files here — this file is only commands, gotchas, and doc-vs-code deltas.

## Commands

All make targets invoke the project-local `.venv` directly with `env -u PYTHONPATH` — **never activate the venv or run bare `python`/`pytest`**: the global `PYTHONPATH` points at `~/.local/lib/python3.12/site-packages` and silently breaks venv isolation. This repo is isolated, unlike most siblings in `~/Documents/apps/` which use the system Python.

```bash
make setup         # create .venv, install backend [dev] deps + frontend npm deps
make check         # ALL CI gates: ruff + mypy --strict + import-linter + pytest+coverage + cov-floors
make lint          # ruff check          (make format = ruff format + --fix)
make typecheck     # mypy --strict on src/tail_lab
make import-lint   # import-linter — the layer contracts in pyproject.toml
make test          # pytest with coverage (fail_under=80 overall)
make cov-floors    # >=90% on research/ + transforms/ (run after `make test`; reuses .coverage)
make ingest-vix    # live VIX fetch → bronze (network; never in CI)
make ingest-ohlcv SYMBOL=SPY   # live OHLCV fetch → bronze (default AAPL)
make api           # uvicorn on :8000 — collides with tip_app/paper_app; prefer `launch tail-lab` (:8020/:5175)
make frontend      # Vite dev server
```

Single test:

```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_lake_store.py -k asof -x
```

Frontend (from `frontend/`): `npm run typecheck`, `npm run lint` (oxlint), `npm run build`, `npm run e2e` (Playwright). CI runs the frontend build as a gate too.

## Things that will bite you

- **Agent branches auto-merge, and green main auto-deploys.** `.github/workflows/automerge.yml` squash-merges any `agent/*` branch PR automatically once CI is fully green, and `.github/workflows/deploy.yml` then ships every green `main` commit to Fly (`docs/adr/0016`). Pushing to an `agent/*` branch is effectively pushing to production-after-CI; use a differently-named branch when a human should review first. CI's `constitution-guard` job fails any `agent/*` PR that touches `README.md`, `ARCHITECTURE.md`, `docs/STANDARDS.md`, `docs/AGENT_MISSION.md`, `docs/END_STATE.md`, or `docs/adr/` — those changes need a human-merged PR.
- **Everything automated must be logged in detail** (`docs/STANDARDS.md` §f) — ingestion runs, backtests, deploys all leave structured records; an automated behavior without runtime logging is below the bar.
- **Point-in-time correctness is the #1 invariant** (`docs/adr/0009`). Backtest reads go through the lake store's as-of resolution; tests that try to read future data must fail (see `tests/test_point_in_time_clock.py`). Bronze is immutable — re-ingesting an existing `ingest_date` is a no-op, corrections are new partitions.
- **Layering is machine-checked.** `import-linter` (contracts in `pyproject.toml`) enforces `api > research > (ingestion | transforms | feedback) > lake > contracts`, plus: `api`/`research` may never import `ingestion`. A violation fails CI.
- **Coverage floors differ by layer:** 80% overall, but **90% on `research/` and `transforms/`** — new code there needs near-complete tests, typically including a pinned synthetic case with a known analytic answer (`docs/STANDARDS.md`).
- **Tests never touch the network.** Ingestion adapters are tested against committed fixtures; live fetches happen only via the human-run `make ingest-*` targets.
- **Config lives in one place:** `src/tail_lab/config.py` (pydantic-settings, `TAIL_LAB_` prefix, `.env`). Never read `os.environ` elsewhere. `TAIL_LAB_LAKE_BACKEND=local` (default, credential-free — what CI uses, lake at `./data/`) vs `tigris` (S3 on Fly Tigris, plain `AWS_*` env names). The feedback routes 404 unless `TAIL_LAB_FEEDBACK_TOKEN` is set — that's deliberate, not a bug.
- **Two backlogs:** the agent owns `AGENT_TODO.md`; anything needing an account/key/money goes to `HUMAN_TODO.md` and is never attempted by the agent.

## ARCHITECTURE.md's tree is partly aspirational

The layer boundaries and dependency rules in `ARCHITECTURE.md` are enforced and real, but its file tree describes the end state, not the current code. As of now:

- `contracts/` is one module per dataset (`vix.py`, `ohlcv.py`, `regime.py`, `options_calendar.py`), not the single `datasets.py` shown.
- `lake/` is `store.py` (`LakeStore` protocol + `DeltaLakeStore`, including as-of resolution — there is no separate `asof.py`) and `blob_store.py` (JSON blobs, used by feedback).
- `api/` is flat (`main.py`, `putlab_routes.py`, `feedback_routes.py`, `schemas.py`), no `routes/` subpackage yet.
- `feedback/` is a real layer (peer of ingestion/transforms in the import-linter contract) not shown in the tree.
- `research/` currently has `metrics/` (downside_beta, downside_capture, tail_beta, co_skewness, co_kurtosis), `option_pricer.py` (one module, not the `pricing/` package `ARCHITECTURE.md` shows), `vix_stretch.py`, `data_quality.py`, `leaderboard.py`, `regimes/timeline.py`, and `backtest/` (put_roll, portfolio, ranking, metric_screen, regime_verdict, brokerage, index_replication).

Follow the existing code's shape when extending; update `ARCHITECTURE.md` in the same change if you move it structurally, and add an ADR for consequential decisions.

## Tests mirror `src/tail_lab/` module-for-module

Flat files named `tests/test_<layer>_<module>.py` (e.g. `tests/test_research_backtest_put_roll.py`); property-based tests (hypothesis) get a `_properties` suffix. `tests/conftest.py` holds the shared fixtures.
