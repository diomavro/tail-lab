# 9. No-look-ahead / point-in-time is the #1 invariant

Date: 2026-08-17

## Status

Accepted

## Context

A backtest that can, even accidentally, read data from after its
simulation date will look better than any real strategy could have been —
invisibly. Every other bug here is loud; look-ahead is the single most
likely way this platform produces a confident, wrong answer to its own
north-star question.

## Decision

Point-in-time correctness ranks above feature completeness:

- Every backtest read goes through `lake/asof.py`, which takes an
  explicit `as_of: date` and returns only knowable-by-then data.
- Every dataset's contract records what actually determines "knowable"
  (`ingested_at`, `vintage_date`, or `announced_at` — see
  `docs/DATA_CONTRACTS.md`).
- Every backtest path ships an **adversarial** test that tries to leak
  future data and asserts it fails.

## Consequences

Backtest results mean what they claim to mean, at the cost of real
discipline: no read may bypass `asof.py`, and no new dataset or backtest
path merges without its point-in-time field or adversarial test.
