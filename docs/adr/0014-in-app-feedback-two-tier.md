# 14. In-app feedback, two-tier lifecycle (standing directives vs. transient issues)

Date: 2026-08-18

## Status

Accepted

## Context

Dio has no way to leave the daily agent a note from inside the dashboard —
today that loop runs entirely through `AGENT_TODO.md`/`HUMAN_TODO.md` edits
in a separate editor session. Two distinct kinds of note come up in
practice, and they behave differently over time:

- A **goal / design-direction** note ("always screen the broad universe
  first", "prioritize the sensitivity leaderboard over the conventional
  tab") is a standing instruction. It should shape every future agent run
  until Dio himself retires it — nothing about the agent doing its job
  should make it disappear.
- A **bug / small-fix** note ("VIX tile shows yesterday's date") is
  disposable. Once the agent (or Dio) fixes it, it has no further reason to
  keep showing up.

Treating both the same way — as one flat list the agent reads — either
loses the standing directives once something clears them, or leaves fixed
issues cluttering the list forever. The two need different lifecycles, not
just a label.

## Decision

**One store, one record shape, two `kind`s with different resolution
rules**, not two separate stores:

- `FeedbackRecord { id, text, kind: "big_picture" | "issue", created_at,
  status: "open" | "resolved", resolved_at }`.
- `kind="big_picture"` records are **never auto-resolved by any code path**
  — the only way one leaves `list_open()` is an explicit
  `POST /api/feedback/{id}/resolve` call, which in practice only Dio (or a
  future UI control he uses) makes. The daily agent reads every open
  `big_picture` record on every run as always-on context; it must not call
  resolve on one.
- `kind="issue"` records are the agent's transient backlog: it addresses
  one and resolves it via the same endpoint, dropping it out of
  `list_open()`.
- Resolving is a status flip on the same JSON blob, not a delete — history
  is never lost, only filtered out of the "open" view.

**Storage:** feedback records persist as individual JSON blobs
(`ops/feedback/<id>.json`) on the same root the medallion lakehouse
uses — a local directory in dev/CI, `s3://<bucket>/ops/feedback/` on Tigris
in prod — via a new `BlobStore` (`src/tail_lab/lake/blob_store.py`,
`pyarrow.fs`). This is a deliberate second, much simpler storage primitive
next to `DeltaLakeStore`: Delta tables (partitioned commits, as-of
resolution) are the right shape for the append-only, point-in-time-critical
research datasets, but massive overkill for a handful of individually
addressable records with no backtest-correctness requirement. `BlobStore`
stays inside `lake/` specifically so "what talks to object storage"
doesn't grow a second location outside the module that already owns that
concern (`docs/adr/0012`, `docs/adr/0013`).

**Layering:** the business logic (`FeedbackStore.add/list_open/resolve`,
`src/tail_lab/feedback/store.py`) is a **new `feedback/` package**, a peer
of `ingestion/`/`transforms/` in the import-linter stack — it imports only
`lake/` (for `BlobStore`) and depends on nothing from the research data
pipeline, so `api/` can import it exactly the way it already imports
`ingestion`/`transforms`-level packages. The alternative considered was
folding this logic directly into `lake/`, but that would mix an ops/product
concern (Dio's notes to the agent) into the module whose whole job is the
research lakehouse's correctness invariants — worse for both.

**Endpoints** (`src/tail_lab/api/feedback_routes.py`):
- `POST /api/feedback` — public, unauthenticated (this app has no login to
  gate behind, and only Dio calls it in practice); validates `text`
  (1–4000 chars) and `kind` (enum); 201 with the created record.
- `GET /api/feedback` — Bearer-token gated, fail-closed
  (`TAIL_LAB_FEEDBACK_TOKEN`): unset token → 404 (don't advertise an
  unconfigured route), wrong/missing → 401, constant-time compare. Returns
  `{"standing": [...], "issues": [...]}` — both open-only. This is what the
  daily agent calls at the start of every run.
- `POST /api/feedback/{id}/resolve` — same token gate; 404 on an unknown
  id. The agent calls this for `issue`s it fixes; the UI does not expose it
  (no browser-side token) but could later, for Dio to retire a
  `big_picture` directive himself.

The dashboard's write form only ever calls the public POST — the
token-gated read/resolve routes are deliberately not reachable from the
browser, so the token never needs to reach client-side code.

## Consequences

- `AGENT_MISSION.md`'s workflow gains a step: read `GET /api/feedback`
  first, every run, and treat `standing` as always-on context alongside
  `README.md`/`docs/END_STATE.md`. (That file is constitution-guarded per
  `docs/adr/0001`'s process, so this decision only takes effect once a
  human merges the corresponding `AGENT_MISSION.md` edit — the agent cannot
  self-adopt it.)
- `TAIL_LAB_FEEDBACK_TOKEN` is a new secret Dio must provision (Fly app
  secret + GitHub Actions repo secret) — tracked in `HUMAN_TODO.md`. Until
  it's set, `GET`/`resolve` 404 and the daily agent's feedback-pull step
  gets nothing (a no-op, not a crash).
- A `big_picture` record can only pile up, never silently vanish — if this
  becomes unwieldy in practice, a future increment could add an
  in-dashboard list + resolve control (explicitly deferred here: the task
  that produced this ADR skips the read-back UI to avoid exposing the
  token to the browser).
