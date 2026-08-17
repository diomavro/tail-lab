# Strict standards

The agent's leash. Full detail behind the headline in `README.md`
§"Strict standards." These are enforced in CI — "enforced" means a
violation fails the build, not that it's a style preference. Nothing
merges red.

---

## (a) Correctness

### No-look-ahead / point-in-time correctness

This is the #1 invariant (`docs/adr/0009`). A backtest may only ever read
data **as it was known on the simulation date.**

- Every backtest read goes through `src/tail_lab/lake/asof.py` — there is
  no other sanctioned path from `research/backtest/` into the lake. A
  direct DuckDB query against silver/gold from backtest code, bypassing
  `asof.py`, is a bug even if it happens to produce a plausible number.
- `asof.py`'s query primitive takes an explicit `as_of: date` (the
  simulation clock) and returns only rows whose lakeFS commit — or,
  equivalently, whose ingestion timestamp — predates it. Point-in-time
  correctness is a property of the *read*, not of a filter applied after
  the fact.
- **Adversarial tests are mandatory, not optional.** For every backtest
  code path, there must be a test that *tries to cheat* — constructs a
  scenario where future data would change the answer if leaked, runs the
  backtest as of a date before that data existed, and asserts the leaked
  value does **not** appear in the result. A backtest module without at
  least one such test does not merge.
- Restated events (e.g. an OHLCV bar revised after the fact) are a new
  bronze write with a later timestamp — `asof.py` reading as of the
  original date must still see the original value, not the revision.

### Numeric results pinned by tests against known cases

Every function that produces a number a human will read on the cockpit
must have at least one test asserting it against an **independently
derivable** answer — not a regression snapshot of the function's own
output.

- Examples: the Black-Scholes put pricer tested against the closed-form
  BS put formula computed by hand/reference implementation for a chosen
  (S, K, T, r, σ); a sensitivity metric tested against a synthetic price
  path constructed so the metric's value is known analytically; a backtest
  engine tested against a synthetic scenario with a known, hand-computable
  payoff.
- A test that merely asserts "the function returns whatever it currently
  returns" (`assert result == snapshot`) does not satisfy this — it locks
  in bugs as often as it catches them.

### Property tests for numeric code

Numeric modules under `research/` and `transforms/` should carry
[Hypothesis](https://hypothesis.readthedocs.io/) property tests alongside
their pinned-case tests, covering invariants the pinned cases don't reach
by construction — e.g.:

- A put price is never negative and never exceeds the strike (discounted).
- Put-call parity holds for any valid (S, K, T, r, σ) the pricer accepts.
- A sensitivity metric computed on a shuffled-but-otherwise-identical
  panel doesn't silently change (order-independence where the metric's
  definition implies it should be).
- `asof.py` never returns a row whose ingestion timestamp is after the
  requested `as_of` date, for arbitrary generated `(as_of, dataset)` pairs.

### Reproducibility

- All randomness (simulation paths, any stochastic backtest component) is
  seeded; the seed is a recorded parameter, not a hidden default.
- Every result the cockpit displays, and every artifact a backtest run
  produces, carries the **lakeFS commit** it read and the **code SHA**
  that computed it. A result without both is not trustworthy and should
  not be surfaced.
- Given the same (lakeFS commit, code SHA, seed), a result must be
  re-runnable bit-for-bit. Non-determinism (unseeded randomness, wall-clock
  reads inside a backtest, unordered set/dict iteration affecting output)
  is a bug.

## (b) Data contracts

- **Every dataset has a schema** in `src/tail_lab/contracts/` — pandera
  for tabular frames, pydantic for structured records (events, DTOs). See
  `docs/DATA_CONTRACTS.md` for the six core datasets' field lists.
- **Ingestion validates and quarantines, never pollutes silver.** The
  bronze → silver transform (`transforms/validate.py`) runs every row
  through its dataset's schema. Rows that fail are written to a quarantine
  location with the validation error attached — they are never dropped
  silently and never allowed into silver malformed. A silver table with a
  passing schema is a hard invariant CI can check by construction (the
  write path itself refuses non-conforming rows).
- Schema changes are versioned; a breaking schema change needs a migration
  note for any gold mart built on the affected dataset, not a silent
  reinterpretation of existing silver rows.

## (c) Typing

- **Backend: mypy strict.** No bare `Any`. Where a genuine dynamic
  boundary exists (e.g. a third-party API response before it's validated
  into a `contracts/` schema), narrow it immediately at the boundary —
  don't let `Any` propagate past the ingestion adapter that produced it.
- **Frontend: TypeScript strict.** No bare `any`. API response types in
  `frontend/src/types.ts` are hand-mirrored from the backend's
  `contracts/` DTOs — see `ARCHITECTURE.md`'s frontend section.
- Both run in CI; a type error fails the build the same as a test failure.

## (d) Boundaries

- **The module dependency direction in `ARCHITECTURE.md`** — import stack
  highest→lowest `api > research > (ingestion, transforms) > lake >
  contracts` (a layer imports only layers below it; `ingestion` and
  `transforms` are peers; **data flows** the opposite way,
  `ingestion→lake→transforms→research→api`), plus the carve-out that
  **neither `api` nor `research` may import `ingestion`** — is
  machine-checked with
  [`import-linter`](https://import-linter.readthedocs.io/) in CI (a
  `layers` contract for the stack and a `forbidden` contract for the
  carve-out), not just documented. A PR that violates either contract
  fails the build regardless of whether the code otherwise works.
- **Frontend/backend types are hand-mirrored, not code-generated** (same
  choice `paper_app` made): when a `contracts/` DTO changes shape, update
  the matching `frontend/src/types.ts` interface in the same PR. A
  drift-check (comparing field names, not full codegen) is a reasonable
  future CI addition but is not required for v1.

## (e) Coverage and CI

- **Coverage floor: ≥90% on `src/tail_lab/research/` and
  `src/tail_lab/transforms/`** — the correctness-critical, numeric code
  that backtest results and cockpit numbers depend on. This is a hard gate
  in CI for those two packages specifically.
- Lower, still-enforced-but-looser coverage elsewhere (`ingestion/`,
  `lake/`, `api/`, `contracts/`) — these are exercised by integration
  tests more than exhaustive unit tests; a reasonable starting floor is
  ~70%, adjustable by ADR if it proves wrong in practice.
- **`ruff check` clean** — no suppressed/ignored rules without a comment
  explaining why.
- **pre-commit** runs the fast subset (lint, format, type-check on changed
  files) before every commit, mirroring `quizkit`'s targeted-hook pattern
  (`scripts/hooks/pre-commit` there) rather than running the full suite on
  every keystroke.
- **CI runs the full gate on every PR:** ruff, mypy/tsc, import-linter,
  pytest (including the adversarial point-in-time tests and Hypothesis
  properties), coverage thresholds, frontend build + vitest. **Nothing
  merges red** — this applies to the agent's PRs exactly as it applies to
  Dio's.
