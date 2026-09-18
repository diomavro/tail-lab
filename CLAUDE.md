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

Frontend (from `frontend/`): `npm run typecheck`, `npm run lint` (oxlint), `npm run build`, `npm run e2e` (Playwright). CI gates on the frontend build **and** on `npm run e2e` — the browser suite is hermetic (it starts its own dev server and mocks every `/api/**` call from `frontend/e2e/fixtures/`), so it needs no lake and no network. A red e2e run blocks the deploy.

## Prior art lives outside this repo

Four third-party systems were read for ideas and are cloned read-only at
`~/Documents/reference/` — `nautilus_trader`, `hftbacktest`,
`kalshimarketmaker`, `quant-portfolio` (~295 MB, shallow). **`docs/PRIOR_ART.md`
is the index**: what each was read for, what was taken, and what was rejected
and why. Read it before mining any of them again — several findings already in
`AGENT_TODO.md` came from there, and re-deriving them costs more than the notes
did. They are reference material, never a dependency; nothing here imports
them.

## Things that will bite you

- **EVERY branch auto-merges, and green main auto-deploys.** `.github/workflows/automerge.yml` squash-merges **any** PR once CI is fully green — not just `agent/*` (`docs/adr/0025` widened it; the workflow's own header says so), and `.github/workflows/deploy.yml` then ships every green `main` commit to Fly (`docs/adr/0016`). **Renaming your branch protects nothing.** Exactly two things stop an auto-merge: marking the PR a **draft**, and touching a guarded path — `^docs/adr/|^.github/|^ARCHITECTURE.md$|^docs/STANDARDS.md$|^README.md$|^docs/AGENT_MISSION.md$|^docs/END_STATE.md$|^pyproject.toml$` (note `pyproject.toml` is on that list, so a dependency bump needs a human too). Open a draft PR whenever a human should look first. The adversarial `agent-review` job likewise runs on **every** pull request now, not only `agent/*`. CI's `constitution-guard` job is the belt-and-braces copy and is still `agent/*`-only — the enforcement point for every other author is automerge's own path check above. It fails any `agent/*` PR that touches `README.md`, `ARCHITECTURE.md`, `docs/STANDARDS.md`, `docs/AGENT_MISSION.md`, `docs/END_STATE.md`, or `docs/adr/` — those changes need a human-merged PR. **`.github/workflows/**` is guarded too** (`docs/adr/0022`): the agent's own prompt, the auto-merge rule and the guard itself all live there, so an unreviewed edit could quietly widen everything else. The principle for the guarded set: *a file is constitutional if changing it changes what the agent is allowed to do* — which is why `AGENT_TODO.md` is deliberately not guarded. Agent PRs are authored by `claude[bot]` (the action mints a GitHub App token), **not** by `diomavro`, and nobody watches this repo — so an agent PR reaches Dio's inbox only because `daily-agent.yml` tells the agent to pass `--assignee diomavro`. Drop that flag and the whole open→merge→deploy chain goes silent.
- **A conflicted PR gets NO CI, and that looks exactly like CI being slow.**
  GitHub runs `pull_request` workflows on the merge commit, so when a branch
  conflicts with `main` it cannot build one and reports *zero* check runs —
  indistinguishable from queued, or from a dropped event. Diagnose with
  `gh api repos/diomavro/tail-lab/pulls/<n> --jq .mergeable` **before** blaming
  the scheduler (2026-09-01: 15 minutes lost to exactly that misdiagnosis). It
  matters here because agent PRs auto-merge or sit: any PR that stalls a day or
  two will conflict as siblings land, and then go quietly CI-less rather than
  red.
- **Lint limits in `pyproject.toml` are a RATCHET, not preferences**
  (`docs/adr/0023`). `max-complexity`, `max-args` and `max-statements` sit at the
  current worst offenders, each naming the function that set it. They may only
  ever go DOWN. If a change trips one, reshape the change — raising the number is
  by definition the edit that makes the codebase worse. The weekly cleanup agent
  (`weekly-cleanup.yml`) is what lowers them.
- **`agent/*` PRs get an adversarial design review in CI**, and it grades
  severity (`docs/adr/0024`): **FINDINGS** are fixed by the loop if it can and
  **merge anyway** if it cannot — a working increment never rots over a quality
  nit, and the daily agent picks the finding up later. **DEFECT** (broken build,
  weakened test, auth or data fault, constitution violation) never auto-merges
  at any number of rounds. If DEFECT stops being a small minority of blocks, the
  reviewer has drifted — tighten the prompt, do not loosen the gate. It looks only at what linters cannot see — duplication, dead
  abstraction, accretion, unearned complexity. Context for why: 40 agent PRs
  added 16,079 lines and deleted 1,020 (15.8:1) while humans on this repo ran
  2.4:1, and nothing in the gate could see it.
- **Everything automated must be logged in detail** (`docs/STANDARDS.md` §f) — ingestion runs, backtests, deploys all leave structured records; an automated behavior without runtime logging is below the bar.
- **Point-in-time correctness is the #1 invariant** (`docs/adr/0009`). Backtest reads go through the lake store's as-of resolution; tests that try to read future data must fail (see `tests/test_point_in_time_clock.py`). Bronze is immutable — re-ingesting an existing `ingest_date` is a no-op, corrections are new partitions.
- **Layering is machine-checked.** `import-linter` (contracts in `pyproject.toml`) enforces `api > research > (ingestion | transforms | feedback) > lake > contracts`, plus: `api`/`research` may never import `ingestion`. A violation fails CI.
- **Coverage floors differ by layer:** 80% overall, but **90% on `research/` and `transforms/`** — new code there needs near-complete tests, typically including a pinned synthetic case with a known analytic answer (`docs/STANDARDS.md`).
- **Tests never touch the network.** Ingestion adapters are tested against committed fixtures; live fetches happen only via the human-run `make ingest-*` targets.
- **Config lives in one place:** `src/tail_lab/config.py` (pydantic-settings, `TAIL_LAB_` prefix, `.env`). Never read `os.environ` elsewhere. `TAIL_LAB_LAKE_BACKEND=local` (default, credential-free — what CI uses, lake at `./data/`) vs `tigris` (S3 on Fly Tigris, plain `AWS_*` env names). The feedback routes 404 unless `TAIL_LAB_FEEDBACK_TOKEN` is set — that's deliberate, not a bug.
- **The daily chain sweep is the one job that must not miss** (`docs/adr/0020`).
  `.github/workflows/daily-chain-snapshot.yml` forward-collects the put wing from
  Cboe's keyless CDN every weekday at 21:30 UTC. Every other source here serves
  history on demand, so a skipped pull is a `make` invocation away from being
  fixed; nobody sells a retroactive option chain, so a skipped session is gone at
  any price. An empty sweep therefore **fails red on purpose** rather than
  no-opping, and a red build here is the one that should interrupt a day. Check
  `GET /api/ingest/option-chain/status` before assuming it is healthy. Two
  gotchas: **GitHub drops scheduled runs** — the 2026-08-27 evening sweep never fired at all, and an absent run produces no red build, so a second catch-up cron at 05:00 UTC now covers it (free, because the write is idempotent); the sweep must write **one partition for all symbols** (bronze is
  immutable, so a per-symbol write persists only the first), and Cboe
  **zero-fills** `iv`/`delta`/`theo` it cannot compute — the adapter maps those to
  null, and a 0.0 in those columns is never a measurement.
- **Surfaces are named after desk functions** (`docs/adr/0021`): Screen, Tape,
  Bake-off, Prior Art, Carry Budget, Regime. Code layers keep their plumbing
  names; anything a human reads takes a desk word or argues for a new one in an
  ADR. "Leaderboard", "dashboard" and "panel" are not desk words.
- **Strikes are picked by fixed moneyness, and that confounds regime comparisons**
  (`docs/PRIOR_ART.md` §1). `put_roll.py` uses `spot * (1 - moneyness_pct/100)`;
  at our own regime bands a "10% OOM 4-week put" is 0.05-delta in calm and
  17.6-delta in crisis. Anything comparing across regimes — the Bake-off, §4 Q4,
  `docs/adr/0015`'s `confirmed` verdict — is partly measuring whether the strike
  was reachable. Delta-based selection is queued; until it lands, say which
  parameterisation a result used.
- **optionsDX coverage is complete; the PRICE series is what limits you**
  (`docs/DATA_CONTRACTS.md` #12). Real 2010-2023 EOD chains in
  `data/vendor/optionsdx/` (gitignored, licence-limited, absent on CI).
  Measured 2026-09-09: **vix 168, spy 168, qqq 144, nvda 96, tsla 96 — all with
  ZERO gaps**; spx is not ingested. An earlier version of this line said "SPY
  has 63 of 168" and it was wrong: it predated the rest of the corpus arriving
  on 2026-09-03, and an adversarial reviewer built a whole finding on it.
  The real constraint is that bronze OHLCV is a **rolling five-year Tiingo
  window** (2021-08..2026-08) while this panel ends 2023-12, so anything
  needing both has only **~589 overlapping trading days**. Two joins are also
  silently wrong: the panel's `spot` is as-traded while OHLCV `close` is
  split-adjusted (**nvda differs by exactly 10**), and VIX cannot be joined at
  all because its `spot` is the index while VIX options settle on futures.
  Call `contracts/optionsdx.month_coverage` before spanning any range anyway —
  today the answer is "nothing missing", and the check is what makes that a
  finding rather than an assumption. Also: a blank
  `P_IV` means the vendor's solver failed and the REST of the greek block is
  garbage (delta pinned to -1.0, which passes the schema) — the same house rule
  as Cboe's zero-fill.
- **Position size is a placeholder, and the metrics cannot fix it.**
  `premium_budget_per_leg` is a fixed $1,000. Replacing it means maximising the
  **time-average growth of the combined portfolio** (`docs/END_STATE.md` §4 Q8),
  not ROI — and every headline metric here (`roi_on_premium`, `hit_rate`,
  `annualized`) describes a put in isolation and cannot say how much to hold.
  Standalone, the growth-optimal size for a negative-EV bet is zero; the position
  is only justifiable as a portfolio hedge. Do not report an "optimal size" for
  the put book alone.
- **The regime classifier has no hysteresis** (`docs/PRIOR_ART.md` §8).
  `contracts/regime.py` is hard VIX thresholds, so a VIX oscillating around 17
  or 28 flips regime on consecutive days — and `docs/adr/0015` keys verdicts on
  regime, so a rule can collect its second regime (and a `confirmed` verdict)
  from a boundary wobble. Second independent defect on the same axis as the
  moneyness confound above.
- **Greeks exist now** (`option_pricer.PutGreeks`) in desk units: **vega per vol
  point, theta per calendar day**. `make greeks-check` scores them against the
  exchange's own from the chain snapshot — the only independent check that the
  *model* is right, as opposed to internally consistent. **It found a live bug**:
  the roll backtest prices every name at `q = 0`, so delta error tracks dividend
  yield (TSLA 0.0006, SPY 0.0043, TLT 0.060, **HYG 0.197**). Put prices and
  greeks on income names are biased cheap until that is fixed.
- **Two backlogs:** the agent owns `AGENT_TODO.md`; anything needing an account/key/money goes to `HUMAN_TODO.md` and is never attempted by the agent.

## ARCHITECTURE.md's tree is partly aspirational

The layer boundaries and dependency rules in `ARCHITECTURE.md` are enforced and real, but its file tree describes the end state, not the current code. As of now:

- `contracts/` is one module per dataset (`vix.py`, `ohlcv.py`, `regime.py`, `options_calendar.py`, `option_quotes.py`, `option_chain.py`, `rates.py`, `credit.py`, `cboe_strategy.py`, `hypothesis.py`), not the single `datasets.py` shown.
- `lake/` is `store.py` (`LakeStore` protocol + `DeltaLakeStore`, including as-of resolution — there is no separate `asof.py`) and `blob_store.py` (JSON blobs, used by feedback).
- `api/` is flat (`main.py`, `putlab_routes.py`, `feedback_routes.py`, `ingest_routes.py`, `schemas.py`), no `routes/` subpackage yet.
- `feedback/` is a real layer (peer of ingestion/transforms in the import-linter contract) not shown in the tree.
- `research/` currently has `metrics/` (downside_beta, downside_capture, tail_beta, co_skewness, co_kurtosis, vol_beta), `option_pricer.py` (one module, not the `pricing/` package `ARCHITECTURE.md` shows), `skew.py`, `accuracy.py`, `cadence.py`, `vix_stretch.py`, `data_quality.py`, `regimes/timeline.py`, `surface/` (hill, karamata, returns, paretan — the Paretan tail analytics, `docs/adr/0026`), and `backtest/` (put_roll, portfolio, ranking, metric_screen, regime_verdict, brokerage, index_replication, sizing, sweep, growth, marks, quote_fills, quote_cache, roll_schedule).

Follow the existing code's shape when extending; update `ARCHITECTURE.md` in the same change if you move it structurally, and add an ADR for consequential decisions.

## Tests mirror `src/tail_lab/` module-for-module

Flat files named `tests/test_<layer>_<module>.py` (e.g. `tests/test_research_backtest_put_roll.py`); property-based tests (hypothesis) get a `_properties` suffix. `tests/conftest.py` holds the shared fixtures.
