# 15. A hypothesis memory layer for Put Lab backtests

Date: 2026-08-19

## Status

Accepted. **Amended 2026-10-10** (below): a rule's identity now includes the
dividend basis it was priced on.

## Context

The Put Lab (`docs/END_STATE.md` §1.2/§1.5) lets Dio — and the daily agent —
run model-priced OOM-put backtests over any asset/strike/tenor. Today that
loop is amnesiac: every run starts blank, the same parameter sets get
re-tested, and a strategy that only paid off in one crash reads exactly like
one that pays off robustly. Dio's brief (the "hypothesis memory" spec) asks
for the missing stage: a persistent memory the loop consults before it runs
and writes to after every verdict, whose single most important modeling
decision is that **`regime_only` is distinct from `confirmed`** — "collapsing
the two is how the system fools itself."

That spec was written for a general research→code→backtest→**live**→post-mortem
loop backed by Postgres + pgvector. tail-lab is not that system, so three
adaptations are load-bearing and are decided here.

## Decision

Add a `memory/` layer — a persistence peer of `feedback/` in the import-linter
stack (imports only `lake/` + `contracts/`) — that records every Put Lab
backtest as a node keyed by `(rule_hash, regime)`.

1. **Scope: backtest verdicts only; the wall holds.** tail-lab never trades
   (`docs/adr/0007`), so the spec's **Live / fills / broker** stages are out.
   The memory remembers research/backtest outcomes and their coverage; it has
   no authority over anything that could risk money. A "rule" is a Put Lab
   **parameter set** (`RuleSpec`: asset, moneyness %, tenor weeks, lookback),
   not a general entry/exit grammar — that richer grammar stays out of scope
   until something produces those richer rules.

2. **`regime_only` needs regimes, so a regime classifier lands first.**
   A rule "passed" a regime if it net-paid there (`roi_on_premium > 0`).
   `verdict_for_rule` aggregates: passing in ≥2 distinct regimes →
   `confirmed`; exactly one → `regime_only`; none → `failed`; no record →
   `untested`. Regimes come from the point-in-time VIX classifier
   (`contracts/regime.py`, `research/regimes/`), built as the prerequisite.

3. **Storage: JSON on Tigris + DuckDB for queries — no new service.** A local
   DuckDB file would not survive Fly's ephemeral containers, and standing up
   managed Postgres/pgvector is a service to run for no need at this scale.
   Instead, each node persists as a JSON blob on the same object storage the
   lakehouse uses (the proven `feedback/` `BlobStore` pattern), and DuckDB is
   the embedded query engine over those blobs for the cross-regime rollups
   (`coverage_by_regime`). Faithful to "DuckDB + object storage."

Recording the same `(rule, regime)` again bumps a `run_count` rather than
forging a second independent confirmation. Negative results are first-class:
nothing is ever pruned from the retrieval path.

## Consequences

- The Put Lab's currently-static "memory" teaser can become real: before a
  re-run, surface the stored verdict, `run_count`, and prior art; after, record
  the outcome under the entry's regime. (Wiring is a follow-up increment.)
- **Deferred, not rejected:** embedding / AST-distance "similar rule"
  retrieval (no model or vector store here yet — exact `rule_hash` collision
  only, for now), preregistration, lineage edges, and the full `slot × regime`
  coverage matrix. Each is a later increment behind this same store.
- Concurrency is last-write-wins per node; a simultaneous `record` from the
  agent and the browser could lose one `run_count` bump. Acceptable at this
  scale; revisit if it ever matters.
- A new `memory` node in the layer contract (`pyproject.toml`); the read/serve
  ban on importing `ingestion` is unaffected.

## Amendment — 2026-10-10: the dividend basis is part of a rule

**Context.** Until October 2026 every Put Lab backtest priced every name at
`q = 0`, which under-priced puts on dividend payers (HYG's delta was off by
0.197 against the exchange's own). Measured dividends now price every Put Lab
model-priced path (`docs/DATA_CONTRACTS.md` #14). A verdict priced at `q = 0`
and one priced at the measured `q` are not the same experiment: merging them
under one `rule_hash` would let a re-run on the new prices silently overwrite,
and `run_count`-bump, evidence gathered on the old ones.

**Decision.** `RuleSpec` gains `dividends: "none" | "measured"`, defaulted to
`"none"` and hashed only when `"measured"` (`model_dump(exclude_defaults=True)`),
so every record made before this keeps its `rule_hash` (pinned by a test). A
run is `"measured"` iff **no roll priced from an unknown yield**
(`PutBacktestResult.q_source`); one roll at an unknown `q = 0` files the whole
run under the old hash. One helper, `rule_spec_for`, builds every spec, and
`RegimeVerdict` carries the spec the recorder stores, so the reported and
stored hashes cannot diverge. `GET /api/putlab/memory` returns the measured
record at the top level and the `"none"` record as a separate `legacy`
section ("priced without dividends") — never merged into one verdict.

**Consequences.**

- §1's "a rule is asset, moneyness, tenor, lookback" now reads: *plus the
  dividend basis it was priced on.*
- Evidence for dividend-measured rules accrues from zero; the top-level verdict
  reads `untested` for every rule until a measured run is recorded, while the
  old evidence stays readable as `legacy`.
- Before the first Tiingo ingest, and for any symbol or window touching an
  unknown yield, runs keep filing under the old hashes. That includes a name
  that only recently started paying: between its first and second dividend
  its yield is `unknown`, so a run whose window covers that stretch files as
  `"none"` for as long as the window reaches back (META and GOOGL, which began
  paying in 2024, do so for several years of lookback).
- The memory `run_id` also names the dividend snapshot, so a re-record after a
  new weekly ingest is a different run.

