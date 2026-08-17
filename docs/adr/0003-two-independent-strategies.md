# 3. Two independent strategies, not a combined portfolio

Date: 2026-08-17

## Status

Accepted

## Context

The north star is the Universa-spirit thesis: screen broadly for
*sensitive* assets and buy soon-expiry OOM puts, rather than reason about
variance directly. A variance-aware, Sharpe-oriented strategy is also
useful as a comparison surface, but blending the two into one "portfolio"
would obscure which strategy drives a result. There's also no single
agreed sensitivity metric (downside beta, co-skewness, co-kurtosis,
factor sensitivities, cheapness variants) — picking one from first
principles is exactly the kind of claim this platform should test, not
assert.

## Decision

- **Sensitivity puts (S1) is priority.** Compute many sensitivity metrics;
  let the backtest comparison decide which pays off (`docs/END_STATE.md`
  §1.5, §4 Q1) — none privileged by construction.
- **Conventional strategy (S2) is secondary** — its own cockpit tab
  (§1.6), never merged with S1 into one ranked list.

## Consequences

S1 and S2 stay genuinely independent surfaces; backtest comparison only
ever ranks *within* S1. Costs some duplicated backtest plumbing, accepted
to never obscure which strategy produced a given number.
