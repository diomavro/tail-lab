# tail-lab

A personal quantitative research platform for **tail-risk hedging** — collect data, backtest strategies, and drive a put-buying decision. Built to be extended a little every day by an autonomous agent working against a written end-state, under strict architectural guardrails.

> **This README is the canonical specification.** It encodes decisions made deliberately (see `docs/adr/`). Do not deviate without a human-approved ADR. Every other doc and every line of code serves what is written here.

---

## North star — the one decision this platform serves

On any given day, answer:

> **Which sensitive assets right now have the most attractively-priced soon-expiry out-of-the-money puts — under which sensitivity metric, in what market regime, with what historical (model-priced) backtest support, and ahead of which scheduled events — and how much would the position bleed if nothing happens?**

The end-state is a **put-buying cockpit** for that decision. Everything the platform computes, and every change the daily agent proposes, is judged by whether it makes *that* decision sharper or better-founded.

## The two strategies

Two **independent** strategies (not a combined portfolio):

1. **Sensitivity puts (priority — the Universa-spirit thesis).** Variance is the wrong lens. Instead, *screen the universe for the most **sensitive** assets* and buy soon-expiry OOM puts on them. There is no single "sensitivity" metric — the platform computes **many** (downside/tail beta, co-skewness, co-kurtosis, factor sensitivities, cheapness-adjusted variants, …) and **lets the backtest decide which screen actually pays off.**
2. **Conventional strategy (secondary).** A variance-aware, Sharpe-oriented strategy, kept as a comparison surface / secondary tab. Lower priority; a quieter part of the platform.

## Hard principles (the constitution)

These are inviolable. Changing any requires a human-approved ADR.

- **The agent never trades and never touches money or credentials.** It improves the *platform* only — data, backtests, metrics, dashboard, docs, tests. Every trade is placed by a human. There is a permanent wall between research/tooling and execution. (`docs/adr/0007`)
- **No look-ahead / point-in-time correctness is the #1 invariant.** A backtest may only ever read data *as it was known on the simulation date* — enforced architecturally via the lakehouse's as-of reads (`ParquetSnapshotLakeStore`), with tests that try to cheat and must fail. Get this wrong and every result is garbage. (`docs/adr/0009`)
- **Never overwrite historical observations.** Raw ingested data is immutable (bronze — never overwritten, a correction is a new bronze snapshot).
- **Free-data first; account/paid needs go to the human backlog.** The platform is built on free sources. Anything requiring an account, API key, or money is written to `HUMAN_TODO.md` (Dio's queue) — never attempted by the agent, and kept separate from `AGENT_TODO.md`.
- **Reproducibility.** Every result carries the bronze snapshot id it read (`docs/adr/0012`) + code SHA that produced it and can be re-run bit-for-bit.
- **The agent proposes; the human disposes.** Every change is a reviewed PR gated by CI. Nothing auto-merges; nothing self-deploys. The agent may propose *nothing* on a given day.

## Strategy & data at a glance

- **Universe: screen broad, trade narrow.** Screen sensitivity across a wide universe (S&P 500 names, major ETFs, commodities, single stocks) from free daily OHLCV. Live put-buying is confined to the options-liquid subset. Because backtests are **model-priced**, the *broad* universe is backtestable now; the trade-narrow limit only bites for *live* trading.
- **Backtest is model-priced (v1).** OOM puts are priced with a model (Black-Scholes + a vol-surface proxy from the VIX/SKEW complex + FRED rates) on historical underlying paths, behind a **pluggable option-pricing interface**. Real historical option quotes become a second implementation later — an upgrade, not a prerequisite. (`docs/adr/0004`)
  - **Caveat, stated loudly:** the S1 thesis is that the market *misprices* tails; a model-priced backtest cannot see that mispricing. Treat model-priced results as a **relative ranking of sensitivity metrics**, not as P&L truth. Absolute returns are suspect until real quotes arrive.
- **Six core datasets (v1):** underlying OHLCV (deep, broad), the volatility complex (VIX/VIX3M/VIX9D/VVIX/CBOE SKEW + realized vol), rates (Treasury curve/SOFR/fed funds, FRED), credit (HY/IG OAS, FRED), an event calendar (scheduled + a manual unscheduled table), and forward-collected option chains for the narrow tradable set. Everything else is deferred to `AGENT_TODO.md`.
  - **Survivorship-bias caveat, stated loudly:** a free "current constituents" pull omits exactly the names that blew up and delisted — the most tail-sensitive assets of all. A point-in-time-constituents plan (or an explicit, loud caveat on every affected result) is a first-class research-validity requirement, not a footnote. (`docs/adr/0010`)

## Tech stack

- **Storage:** DuckDB (query/compute engine) over a **medallion lakehouse** — bronze (immutable raw) → silver (validated, typed) → gold (research-ready marts) — stored as immutable Parquet on **Fly Tigris** (S3-compatible object storage; no versioning service in front of it). Point-in-time correctness and bronze immutability are enforced in one shared place (`ParquetSnapshotLakeStore`), not by a git-style data-branching layer. (`docs/adr/0005`, `0006`, `0012`)
- **Backend:** Python (pandas/numpy, pydantic, pandera, FastAPI), strictly typed.
- **Frontend:** React + TypeScript (strict).
- **Hosting:** Fly.io.
- **The daily agent:** runs on GitHub Actions (cron + manual dispatch), file/least-privilege, opens reviewed PRs — mirroring the proven pattern from the sibling `quizkit` repo.

## Strict standards (the agent's leash)

Full detail in `docs/STANDARDS.md`. Headline: point-in-time correctness enforced and tested; every number pinned by a test against a known case; full typing (mypy/pyright strict, TS strict, no bare `Any`/`any`); enforced module dependency direction — data *flows* `ingestion → lake → transforms → research → api → dashboard`, while import *dependencies* point downward to the foundation (`api > research > (ingestion, transforms) > lake > contracts`; `api`/`research` never import `ingestion`), machine-checked, no reverse imports; data contracts on every dataset with a validation-or-quarantine gate; high coverage; CI gate + pre-commit; nothing merges red.

## What exists now (the walking skeleton)

The initial setup is **end-state documentation + a deployed walking skeleton** (`docs/adr/0011`): one data source (VIX, keyless) ingested bronze→silver→gold through the lakehouse, one computed metric, one dashboard tile reading it — paper-thin but **complete, typed, tested, CI-gated**, and deployable to Fly. It does almost nothing; it *proves every layer connects correctly* and gives the agent a concrete, correct pattern to extend. The agent's job is to grow this toward the end state one reviewed PR at a time.

## Implementation notes (v1 deviations)

- **VIX source is Yahoo Finance's public chart JSON** (`query1.finance.yahoo.com/v8/finance/chart/%5EVIX`), keyless — *not* the Stooq CSV first considered, which now serves a JavaScript anti-bot challenge to plain HTTP clients. Both are keyless; Yahoo's still returns clean JSON. Per-dataset source choices live in `docs/DATA_CONTRACTS.md`.
- **The lakehouse defaults to local disk; Tigris is available now.** `lake/` exposes a `LakeStore` interface; `LocalParquetLakeStore` (immutable bronze parquet on disk) is the default — no credentials needed, so CI and plain local dev always work. `TigrisLakeStore` (immutable bronze parquet on Fly Tigris, S3-compatible) is the second implementation, selected via `TAIL_LAB_LAKE_BACKEND=tigris` (`tail_lab.config.get_lake_store`) — no other layer changes when switching. (`docs/adr/0012`)

## Documentation map

- `docs/END_STATE.md` — the detailed end-state the agent works toward.
- `docs/AGENT_MISSION.md` — the daily agent's mission, value bar, and guardrails.
- `docs/STANDARDS.md` — the strict engineering + correctness standards.
- `docs/DATA_CONTRACTS.md` — the six core datasets: schemas, sources, cadence, validation rules.
- `docs/adr/` — architecture decision records (the constitution's clauses).
- `AGENT_TODO.md` / `HUMAN_TODO.md` — the two separate backlogs.
- `ARCHITECTURE.md` — layout, layer boundaries, where new code goes.
