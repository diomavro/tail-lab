# 6. Object-storage-backed immutable Parquet on Tigris (free); no versioning service

Date: 2026-08-17 (decision revised 2026-08-18 — see `docs/adr/0012`)

## Status

Accepted (content revised by `docs/adr/0012`, which records why and
supersedes this ADR's original lakeFS-Cloud decision; this file now
describes what actually shipped)

## Context

Reproducibility requires every result to carry the exact data state it was
computed from, and every backtest read needs a cheap, reliable way to
resolve "the data as known on date X" (`docs/adr/0009`). The lakehouse
(bronze/silver/gold, `docs/adr/0005`) needs somewhere durable and free to
live, since this is a single-user research platform with no budget for a
managed data-versioning service.

## Decision

Store the medallion lakehouse as **immutable Parquet directly on Fly
Tigris** (S3-compatible object storage, free at this project's scale) —
**no versioning service in front of it**. Point-in-time correctness and
bronze immutability are implemented in application code, in one shared
place (`ParquetSnapshotLakeStore` in `src/tail_lab/lake/store.py`), not
delegated to a git-like data-versioning layer:

- Bronze writes are content-addressed by `(dataset, ingest_date)` and never
  overwritten — a re-ingest of the same day is a no-op.
- A read "as of" a simulation date resolves to the latest bronze snapshot
  ingested on or before that date (`docs/adr/0009`).
- Every result cites a **snapshot id** — the ingest-date partition plus a
  content hash of that snapshot's bytes — in place of a lakeFS commit id
  (`docs/adr/0012`).
- `LocalParquetLakeStore` (local disk, the default — no credentials, what
  CI and plain local dev use) and `TigrisLakeStore` (the same logic against
  the real bucket) share this base class, so the guarantees are identical
  by construction, not by two independent implementations agreeing.

## Consequences

Reproducibility and no-look-ahead hold exactly as before — the invariant
was never about *which* storage system enforces it, only that it's
enforced somewhere with a test that tries to cheat.

**What's genuinely given up: git-style data branching.** There is no data
equivalent of a reviewed "data PR" — the agent's bronze writes land
directly (append-only, immutable) rather than on a branch a human merges.
This is the one capability lakeFS Cloud would have bought that plain object
storage does not.

**Fallback if that capability is ever needed:** self-hosted **lakeFS
OSS** on Fly, running against this same Tigris bucket, is the documented
path back to data branching — it's one more service to operate (a real
cost for a single-user platform), so it's deliberately not adopted
preemptively. Revisit only if the agent's data-producing PRs actually need
a reviewable diff before merging, not because branching is a nice-to-have.

Object storage is never touched directly except through `lake/` — the same
boundary this ADR always specified, just enforced by
`ParquetSnapshotLakeStore` rather than by a versioning service sitting
between the app and the bucket.
