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

**Three stated exceptions.** The first two (2026-10-10) are in the Book's
conservative index leg and reach it only through its cash drag
`y/8 x (equity - T-bill)`; see `docs/adr/0027`'s amendment. The third
(ADR 0033) is in the regime label: HY OAS observations up to 2025-11-03 use
values as revised by 2025-11-04. The only measured bound is 553 of 553
identical values over 2023-09 -> 2025-11; revisions before 2023-09 are
unmeasured.

1. It reads `DGS3MO` at its *latest* vintage for dates before its first ALFRED
   vintage (2005-06-28), where no point-in-time value exists; bounded below
   0.01bp/yr of margin.
2. Its `y` (SPY's trailing measured dividend yield) is backfilled for the
   months before SPY's first measured reading (1993-06, five months after
   listing) with that first reading, which was not yet known then. Bound,
   measured on the real lake (2026-10-10): the conservative leg's full-history
   CAGR differs by +0.023bp/yr from setting those months' `y` to 0 and by
   -0.006bp/yr from setting it to 3%.
3. The regime's HY OAS history up to 2025-11-03 comes from the one-off
   `credit_archive` dataset (a Wayback capture taken 2025-11-04, ADR 0033), so
   those observations carry revisions published by that date. ALFRED was purged
   when FRED cut the series to a 3-year window, so no earlier vintage exists to
   compare. The archive agrees with the live series on all 553 shared
   observations (2023-09-25 -> 2025-11-03); anything before 2023-09 is
   unmeasured. Labels, not spread values, are what reach the cockpit.
