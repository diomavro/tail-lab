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
- **No look-ahead / point-in-time correctness is the #1 invariant.** A backtest may only ever read data *as it was known on the simulation date* — enforced architecturally via the lakehouse's as-of reads (`DeltaLakeStore`), with tests that try to cheat and must fail. Get this wrong and every result is garbage. (`docs/adr/0009`)
- **Never overwrite historical observations.** Raw ingested data is immutable (bronze — never overwritten, a correction is a new bronze snapshot).
- **Free-data first; account/paid needs go to the human backlog.** The platform is built on free sources. Anything requiring an account, API key, or money is written to `HUMAN_TODO.md` (Dio's queue) — never attempted by the agent, and kept separate from `AGENT_TODO.md`.
- **Reproducibility.** Every result carries the bronze snapshot id it read (`docs/adr/0012`) + code SHA that produced it and can be re-run bit-for-bit.
- **Everything automated is logged in detail.** Autonomous operation is only reviewable if every automated action — agent runs, merges, deploys, ingestion runs, backtests — leaves a detailed record (`docs/adr/0016`, `docs/STANDARDS.md` §f). An increment that adds automated behavior without logging it does not meet the standard.
- **Accuracy is surfaced, not filed.** Anything that tells the reader how far a result sits from the truth — the model-vs-market residual, the benchmark the strategy should be judged against, the data-quality flags on its inputs, the assumptions a number rests on and how much they move it — must be visible **where the result is shown**, not only in a doc, a log, or a make target. A backtest figure displayed without the known size of its error is a number pretending to be a measurement, and this platform exists to measure a mispricing, so an unqualified number is the specific failure mode it cannot afford. A new result and its accuracy context ship in the same increment; if the context is not yet known, say that on the surface too.
- **The workspace fits one screen; the page never scrolls.** The Put Lab is an app shell, not a document. The fragility ranking stays pinned in view so that clicking a name and seeing what it does are the same gesture — a result you have to scroll to find is a result you will not compare. Only the active view scrolls, and only when its own content exceeds the space left for it. Panels that grow without bound (a 35-row table, an eleven-column screen) are collapsed, capped, or made user-resizable instead of being allowed to push the answer below the fold. **This principle was violated once because it lived only in Dio's head**; it is written here so the next increment has to argue with it rather than forget it.
- **The agent proposes; CI disposes; the human oversees.** Every change is a PR gated by CI. Ordinary agent increments (`agent/*` branches) auto-merge once every CI gate is green (`docs/adr/0016`); anything touching the constitution (this README, `ARCHITECTURE.md`, `docs/STANDARDS.md`, `docs/AGENT_MISSION.md`, `docs/END_STATE.md`, `docs/adr/`) is blocked from auto-merge by CI's constitution-guard and lands only through a human-reviewed PR. Green `main` auto-deploys to Fly (CD workflow, `docs/adr/0016`) — the agent itself never runs a deploy and never holds credentials. The human steers asynchronously: reviewing the deployed state and the audit trail, and leaving feedback (`docs/adr/0014`) that future runs must read. The agent may propose *nothing* on a given day.

## Strategy & data at a glance

- **Universe: screen broad, trade narrow.** Screen sensitivity across a wide universe (S&P 500 names, major ETFs, commodities, single stocks) from free daily OHLCV. Live put-buying is confined to the options-liquid subset. Because backtests are **model-priced**, the *broad* universe is backtestable now; the trade-narrow limit only bites for *live* trading.
- **Backtest is model-priced (v1).** OOM puts are priced with a model (Black-Scholes + a vol-surface proxy from the VIX/SKEW complex + FRED rates) on historical underlying paths, behind a **pluggable option-pricing interface**. Real historical option quotes become a second implementation later — an upgrade, not a prerequisite. (`docs/adr/0004`)
  - **Caveat, stated loudly:** the S1 thesis is that the market *misprices* tails; a model-priced backtest cannot see that mispricing from the inside. Treat model-priced results as a **relative ranking of sensitivity metrics**, not as P&L truth. Absolute returns are suspect until real quotes arrive.
  - **The size of that caveat is now measured, not guessed.** Replicating Cboe's published PPUT rule with our own pricer and differencing the NAVs puts the model-vs-market gap at **+1.34%/yr over 438 monthly rolls since 1990** — the model prices puts too cheap by roughly a fifth of the premium — and, critically, **the error flips sign in a crisis** (`docs/MODEL_RESIDUAL.md`, `make residual`). So the caveat is not "absolute returns are unknown" but "absolute returns are optimistic by a known, regime-dependent amount". That number is part of the result and travels with it.
- **Six core datasets (v1):** underlying OHLCV (deep, broad), the volatility complex (VIX/VIX3M/VIX9D/VVIX/CBOE SKEW + realized vol), rates (Treasury curve/SOFR/fed funds, FRED), credit (HY/IG OAS, FRED), an event calendar (scheduled + a manual unscheduled table), and forward-collected option chains for the narrow tradable set. Everything else is deferred to `AGENT_TODO.md`.
  - **Survivorship-bias caveat, stated loudly:** a free "current constituents" pull omits exactly the names that blew up and delisted — the most tail-sensitive assets of all. A point-in-time-constituents plan (or an explicit, loud caveat on every affected result) is a first-class research-validity requirement, not a footnote. (`docs/adr/0010`)

## Tech stack

- **Storage:** DuckDB (query/compute engine) over a **medallion lakehouse** — bronze (immutable raw) → silver (validated, typed) → gold (research-ready marts) — stored as **Delta Lake** tables (delta-rs, no Spark) on **Fly Tigris** (S3-compatible object storage; no versioning service in front of it). Point-in-time correctness and bronze immutability are enforced in one shared place (`DeltaLakeStore`), not by a git-style data-branching layer. (`docs/adr/0005`, `0006`, `0012`, `0013`)
- **Backend:** Python (pandas/numpy, pydantic, pandera, FastAPI), strictly typed.
- **Frontend:** React + TypeScript (strict).
- **Hosting:** Fly.io.
- **The daily agent:** runs on GitHub Actions (cron + manual dispatch), file/least-privilege, opens CI-gated PRs that auto-merge on green (`docs/adr/0016`) — mirroring the proven pattern from the sibling `quizkit` repo.

## Strict standards (the agent's leash)

Full detail in `docs/STANDARDS.md`. Headline: point-in-time correctness enforced and tested; every number pinned by a test against a known case; full typing (mypy/pyright strict, TS strict, no bare `Any`/`any`); enforced module dependency direction — data *flows* `ingestion → lake → transforms → research → api → dashboard`, while import *dependencies* point downward to the foundation (`api > research > (ingestion, transforms) > lake > contracts`; `api`/`research` never import `ingestion`), machine-checked, no reverse imports; data contracts on every dataset with a validation-or-quarantine gate; high coverage; CI gate + pre-commit; nothing merges red.

## What exists now (the walking skeleton)

The initial setup is **end-state documentation + a deployed walking skeleton** (`docs/adr/0011`): one data source (VIX, keyless) ingested bronze→silver→gold through the lakehouse, one computed metric, one dashboard tile reading it — paper-thin but **complete, typed, tested, CI-gated**, and deployable to Fly. It does almost nothing; it *proves every layer connects correctly* and gives the agent a concrete, correct pattern to extend. The agent's job is to grow this toward the end state one CI-gated PR at a time.

- **In-app feedback (`docs/adr/0014`).** A feedback panel on the dashboard lets Dio write the daily agent a note directly: a permanent **big-picture/goal** directive (never auto-resolved — read as always-on context every run until Dio retires it) or a transient **bug/issue** (the agent fixes and resolves it, then it drops off the list). The agent pulls `GET /api/feedback` (token-gated) at the start of every run per `docs/AGENT_MISSION.md`.

## Implementation notes (v1 deviations)

- **VIX source is Yahoo Finance's public chart JSON** (`query1.finance.yahoo.com/v8/finance/chart/%5EVIX`), keyless — *not* the Stooq CSV first considered, which now serves a JavaScript anti-bot challenge to plain HTTP clients. Both are keyless; Yahoo's still returns clean JSON. Per-dataset source choices live in `docs/DATA_CONTRACTS.md`.
- **The lakehouse defaults to local disk; Tigris is available now.** `lake/` exposes a `LakeStore` interface with one concrete implementation, `DeltaLakeStore` (Delta Lake via delta-rs, immutable bronze partitioned by `ingest_date`), rooted at a local directory by default — no credentials needed, so CI and plain local dev always work — or at `s3://tail-lab-lake` on Fly Tigris (S3-compatible) when `TAIL_LAB_LAKE_BACKEND=tigris` (`tail_lab.config.get_lake_store`) — no other layer changes when switching. (`docs/adr/0012`, `docs/adr/0013`)

## Documentation map

- `docs/END_STATE.md` — the detailed end-state the agent works toward.
- `docs/AGENT_MISSION.md` — the daily agent's mission, value bar, and guardrails.
- `docs/STANDARDS.md` — the strict engineering + correctness standards.
- `docs/DATA_CONTRACTS.md` — the six core datasets: schemas, sources, cadence, validation rules.
- `docs/adr/` — architecture decision records (the constitution's clauses).
- `AGENT_TODO.md` / `HUMAN_TODO.md` — the two separate backlogs.
- `ARCHITECTURE.md` — layout, layer boundaries, where new code goes.
