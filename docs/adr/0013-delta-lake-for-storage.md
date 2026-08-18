# 13. Delta Lake (delta-rs) for lakehouse storage, replacing hand-rolled immutable Parquet

Date: 2026-08-18

## Status

Accepted. Supersedes the *Parquet-format* part of `docs/adr/0012` — Tigris
as the S3-compatible object-storage backend, and the "no versioning
service, application code owns point-in-time + immutability" decision, both
stand unchanged. What changes is the on-disk table format and who
implements the as-of/immutability rules: Delta Lake (via the `deltalake`
Python package, i.e. delta-rs — **no Spark, no JVM**) replaces the
hand-rolled `ParquetSnapshotLakeStore` base class and its two backends
(`LocalParquetLakeStore`, `TigrisLakeStore`).

## Context

`docs/adr/0012` implemented point-in-time correctness and bronze
immutability as application logic on top of bare Parquet files: one file
per `(dataset, ingest_date)`, a "does this key already exist" check before
every bronze write, and an as-of resolver that lists sibling date-named
directories by hand. That logic was correct and well-tested, but it was
reinventing, file by file, guarantees a standard table format already
provides:

- **Atomicity.** A bare `_write_bytes` call has no protection against a
  crash mid-write leaving a partial/corrupt file; nothing here caused a
  problem yet, but it's a real gap for a platform whose #1 invariant is data
  correctness.
- **Concurrent-write safety and schema tracking.** Not needed today
  (single-writer daily ingest), but the hand-rolled layer had no answer for
  it beyond "don't run two ingests at once."
- **A query engine reading the format natively.** DuckDB's `httpfs`
  extension read the Parquet files directly, but the *table* (which files
  make up "the current bronze for this dataset") was a convention enforced
  only by `tail_lab` code — nothing on disk records it.

None of this was broken; it was the standards-first move (`README.md`
"Strict standards") — the same category of decision as choosing DuckDB over
a bespoke query engine in `docs/adr/0005`.

## Decision

Adopt **Delta Lake**, written and read via **delta-rs** (`deltalake` PyPI
package) — explicitly *not* Apache Spark; this stays a single-process,
embedded-engine platform (`docs/adr/0005`'s DuckDB-only stance is
unaffected). One concrete class, `DeltaLakeStore` (`src/tail_lab/lake/store.py`),
replaces `ParquetSnapshotLakeStore` + `LocalParquetLakeStore` + `TigrisLakeStore`,
and works against both roots delta-rs already supports uniformly:

- a **local filesystem root** (default, no credentials — CI and plain local
  dev), and
- an **`s3://tail-lab-lake` root** on Fly Tigris, via delta-rs
  `storage_options` built from the same AWS_* settings `docs/adr/0012`
  introduced.

**Bronze model — unchanged semantics, new mechanism.** Bronze is now one
**Delta table per dataset**, partitioned by an `ingest_date` column instead
of a hand-built `<dataset>/<date>/` directory scheme. Each
`write_bronze(dataset, ingest_date, df)` call appends that ingest's full
snapshot as a new `ingest_date` partition; if the partition already exists,
the write is a no-op — same immutability contract as `docs/adr/0012`, now
because `tail_lab` code checks the Delta log before writing (delta-rs
itself does not offer a built-in "skip if partition exists" write mode).
Each ingest still stores the dataset's **full history for that date** (not
a diff) — same storage-growth tradeoff as before; a future
diff/compaction optimization is tracked in `AGENT_TODO.md`, not built here.

**As-of reads — unchanged contract.** `read_bronze_as_of` lists the table's
`ingest_date` partitions from Delta's transaction log (metadata only, via
`get_add_actions` — no data read), picks the latest one on or before
`as_of`, and reads only that partition. `bronze_snapshot_id` stays
content-addressed (`dataset@YYYY-MM-DD#<hash>`) — the hash is now computed
from the resolved partition's logical row content rather than a single
physical file's bytes, which is *more* robust than before: a future
`OPTIMIZE`/file-compaction pass changing physical file layout without
changing logical content would no longer change the snapshot id.

**Silver/gold — unchanged contract.** A single non-partitioned Delta table
per dataset, overwritten (`mode="overwrite"`) on every write.

**`query()` — DuckDB, via the `delta` extension.** DuckDB reads Delta
tables through `delta_scan(<table_uri>)` instead of `read_parquet(<file>)`.
This surfaced one real integration quirk, solved during this migration:

> **The delta-rs/S3 quirk.** DuckDB's `delta` extension resolves S3
> credentials through DuckDB's own **secrets manager**, not through the
> `httpfs` extension's `SET s3_access_key_id` / `SET s3_secret_access_key`
> pragmas (those only apply to `httpfs`'s own `read_parquet`/`read_csv`).
> Configuring only the `SET s3_*` pragmas and then calling `delta_scan()`
> against Tigris made delta-rs fall through its default AWS credential
> chain to an EC2 instance-metadata (IMDS) lookup, which hangs and fails
> outside AWS. The fix: issue `CREATE OR REPLACE SECRET ... (TYPE S3, KEY_ID
> ..., SECRET ..., REGION ..., ENDPOINT ..., URL_STYLE 'path', USE_SSL true)`
> before any `delta_scan()` call against object storage — `DeltaLakeStore._configure_connection`
> does this whenever it's constructed with `storage_options` (i.e. the
> Tigris backend).

**Single-writer S3 note.** delta-rs writing to S3-compatible storage needs
either `AWS_S3_ALLOW_UNSAFE_RENAME=true` in `storage_options` or the newer
conditional-put commit path; this platform sets the former, since bronze
ingestion is single-writer by construction (the daily agent / `make
ingest-vix`, never concurrent) — the "unsafe" rename race it disables
protection against cannot occur here.

## Consequences

**Point-in-time correctness, bronze immutability, and reproducibility are
unaffected** — same guarantees, same test suite (adversarial no-look-ahead,
restatement-does-not-leak, and immutability tests all pass unchanged in
intent against the Delta backend; a few were adapted to check the Delta
transaction log instead of raw file bytes where the old assertion was
Parquet-file-specific).

**Gained:** ACID commits (no more silently-partial writes), Delta's native
time-travel (`DeltaTable(uri).version()` / `.history()`) as a debugging aid
beyond the platform's own snapshot ids, a standard format any other tool
(Spark, Polars, Trino, …) could read later without `tail_lab`-specific
knowledge, and schema-evolution support if a dataset's shape ever needs to
change.

**Cost:** one new runtime dependency (`deltalake`, i.e. delta-rs — a Rust
binary wheel, no JVM), and the DuckDB `delta` extension is installed/loaded
at query time (same "install at runtime" pattern `httpfs` already used).
No lakeFS, no Spark — this ADR does not reopen `docs/adr/0006`'s decision to
avoid a data-branching service.
