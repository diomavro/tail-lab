# 12. Replace lakeFS with Tigris object storage

Date: 2026-08-18

## Status

Accepted. Supersedes the lakeFS-Cloud decision originally recorded in
`docs/adr/0006` (that file's content has been revised in place to describe
what this ADR decided, per explicit instruction — see its Status line for
the cross-reference; `docs/adr/0001`'s general "ADRs are immutable once
accepted" rule is deliberately set aside for this one case rather than
left silently broken).

## Context

`docs/adr/0006` planned to version the lakehouse with **lakeFS Cloud** on
S3-compatible object storage, giving the agent git-style data branches
("data PRs") for reviewable data changes. Before that plan was
implemented, two things became clear:

1. **lakeFS Cloud is not free** at this project's scale — it's a paid
   managed service, and tail-lab is built free-data/free-infra-first
   (`docs/adr/0002`).
2. **Self-hosting lakeFS OSS is not free either**, just in a different
   currency: it's a service tail-lab would have to run and operate (its
   own deploy, its own uptime, its own upgrade path) for a single-user
   research platform. That operational cost is not justified by what data
   branching alone buys here.

Meanwhile, a Tigris bucket (`tail-lab-lake`, S3-compatible, on Fly, free at
this scale) was already provisioned, and the platform's actual hard
requirements — immutable bronze, as-of reads, reproducibility — do not
inherently need a versioning service to satisfy; they need a **write path
that never overwrites** and a **read path that resolves point-in-time
correctly**, both of which are ordinary application logic.

## Decision

Drop lakeFS (both the Cloud and self-hosted-OSS options) entirely. Replace
it with:

- **Fly Tigris** (S3-compatible object storage, `tail-lab-lake` bucket) as
  the durable store for bronze/silver/gold Parquet — no versioning service
  between the app and the bucket.
- **DuckDB** stays the query engine, unchanged, reading Parquet from Tigris
  via `httpfs`/S3 configuration instead of through a lakeFS proxy.
- **Point-in-time correctness and bronze immutability move into application
  code**, in exactly one place: `ParquetSnapshotLakeStore`
  (`src/tail_lab/lake/store.py`), a shared base class that implements the
  as-of resolution, the never-overwrite rule, and the medallion path scheme
  once. Two concrete backends sit on top of it:
  - `LocalParquetLakeStore` — local disk, **stays the default** (no
    credentials; CI and plain local dev need none).
  - `TigrisLakeStore` — the same logic against the real bucket, selected
    via `TAIL_LAB_LAKE_BACKEND=tigris` (`tail_lab.config.get_lake_store`).
- **Reproducibility uses a snapshot id, not a lakeFS commit.** Every bronze
  snapshot a result reads can be cited by
  `LakeStore.bronze_snapshot_id(dataset, as_of)`: the resolved ingest-date
  partition plus a content hash of that snapshot's bytes
  (`dataset@YYYY-MM-DD#<hash>`) — simple, backend-agnostic, and sufficient
  to prove "this result was computed from exactly this data."

## Consequences

**Point-in-time correctness, bronze immutability, and reproducibility are
unaffected** — they were always going to be enforced by tested application
logic (`docs/adr/0009`), not by trusting a versioning service to get it
right; that logic now lives in one shared base class both backends use, so
the guarantee holds identically for local dev and production.

**What's genuinely lost: git-style data branching** — there is no reviewed
"data PR" for the agent's bronze writes; they land directly, append-only.
If a future need makes that capability worth its operational cost, the
documented fallback is **self-hosted lakeFS OSS on Fly**, run against this
same Tigris bucket (nothing about the bucket layout or Parquet format
would need to change) — deliberately not adopted now, revisit only if the
agent's data changes actually need a pre-merge review surface, not
speculatively.

**Cost:** one dependency removed (no lakeFS of either flavor to run,
configure, or pay for), one class of failure removed (a lakeFS outage or
migration can no longer block the lakehouse), at the cost of the branching
capability above.
