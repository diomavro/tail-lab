# 6. lakeFS Cloud + object storage for lakehouse versioning

Date: 2026-08-17

## Status

Accepted

## Context

Reproducibility requires every result to carry the exact data state it was
computed from, and the agent's daily data changes need to be reviewable
the way a code PR is, without a human re-running ingestion by hand to
check the diff. Plain object storage gives immutability and cheap storage
but no branching, no commit history, no way to open a "data PR."

## Decision

Version the lakehouse (bronze/silver/gold, on Fly Tigris or Cloudflare R2)
with **lakeFS Cloud**. The agent's data changes happen on a lakeFS **data
branch**; merging it is the data equivalent of merging a code PR. Every
result records the lakeFS commit it read (`docs/STANDARDS.md`).

## Consequences

Data changes get the same review/audit trail as code. "Reproduce this
result" becomes "check out this commit," not "hope the bucket looks the
same." Cost: one more managed dependency; object storage is never touched
directly by application code, always through lakeFS.
