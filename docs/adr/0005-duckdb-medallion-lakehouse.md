# 5. DuckDB medallion lakehouse

Date: 2026-08-17

## Status

Accepted

## Context

tail-lab needs an engine that handles growing tabular research data with
fast analytical queries, supports a clean raw→validated→research-ready
progression (a natural home for point-in-time checks and data-quality
gates), and doesn't require operating a database server for a single-user
project. A full server is more operational surface than justified; a bag
of CSVs has no query engine and no validation boundary.

## Decision

Use **DuckDB** — embedded, no server — over a **medallion lakehouse**:
bronze (immutable raw) → silver (validated, typed) → gold (research-ready
marts, one per cockpit consumer). `transforms/validate.py` is the only
bronze→silver path; `transforms/marts/` the only silver→gold path.

## Consequences

Point-in-time correctness, contract validation, and "where does new
derived data go" each have one obvious home. Cost: DuckDB is single-node
— acceptable at this scale, revisited only if a query genuinely outgrows
it, not preemptively.
