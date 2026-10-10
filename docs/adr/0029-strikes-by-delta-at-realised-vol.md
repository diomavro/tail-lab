# 29. A strike can be picked by delta, and the served delta is at realised vol

Date: 2026-10-10

## Status

**Accepted.** Dio chose the work on 2026-10-09 ("biased prices and strikes"
from the trading-readiness review), approved the plan after adversarial review,
and instructed the merge. Amends `docs/adr/0028` (the rail) in one place,
stated below; builds on `docs/adr/0015` (regime verdicts), `docs/adr/0018` (the
model's depth limit) and `docs/PRIOR_ART.md` §1 and §6.

## Context

Every roll struck at `spot * (1 - moneyness_pct/100)`. `docs/PRIOR_ART.md` §1
measured what that does across regimes: at our own bands a "10% below spot,
4-week" put has delta 0.0006 in calm (vol 12%) and 0.18 in a crisis (vol 45%) --
a 300x spread (PRIOR_ART writes these as desk points, 0.05 and 17.6). A
verdict that a strategy "only paid in crises" (`docs/adr/0015`'s
`regime_only`) is then partly a statement that the strike was reachable then.

Two backlog items asked for delta selection and disagreed on the delta: one
prescribed the stored Cboe `delta`, the other the optionsDX vendor `delta`.
Neither can serve the backtest. It needs a delta on every past day since 2010
for every name, and the platform holds a smile for none of them (Cboe chains
are collected forward from 2026-08; optionsDX is offline, licence-limited and
kept off the live page by `docs/adr/0027` §5). The vendor columns also do not
share a convention with each other or with us: optionsDX `P_DELTA` differs
from our dividend-adjusted spot delta by a median 0.004 (5-6% relative) on
3-20-delta SPY puts, unexplained by r, q or day count.

## Decision

1. **`StrikeRule = ByMoneyness(pct) | ByDelta(target)`**
   (`research/backtest/strike_rule.py`). `ByMoneyness` is the default and its
   output is unchanged to the cent. `rule` replaces `moneyness_pct` in
   `run_put_roll`, `compute_put_backtest`, `compute_regime_verdict` and
   `compare_metric_screens` (the lint ratchet forbids adding an argument).
   Targets are bounded to 0.01-0.50; a target with no strike
   (`|Δ|e^{qT} ≥ 1`) is refused, never clipped.

2. **One delta convention, everywhere:** the Black–Scholes European spot
   delta with the dividend yield, `Δ = −e^{−qT} N(−d₁)`, with the same r and the
   measured q (`docs/DATA_CONTRACTS.md` #15) the price uses. It inverts in
   closed form, `d₁ = −N⁻¹(|Δ|e^{qT})`, `K = S·exp(−d₁σ√T + (r − q + σ²/2)T)`.
   No vendor `delta` column is read for selection.

3. **The served delta is the model's, at the roll's 20-day realised vol**, and
   is labelled so everywhere ("0.10Δ at realised vol", never a bare "10Δ").
   This answers `docs/PRIOR_ART.md` §6's sticky-strike objection by measuring
   it rather than importing a smile we do not have: the market's strike for
   the same target is our formula on the exchange's implied vol, served beside
   the model's for the latest session by `GET /api/putlab/strike-preview` and
   measured over 2010-2023 by `make delta-strike-gap` (Results below).

4. **Scope.** `/backtest`, `/regime-verdict` and `/metric-screen` take
   `strike_rule` and `target_delta`; a leftover `moneyness_pct` is ignored in
   delta mode and never splits a cache key. The ranking, sweep (its grid and
   `docs/adr/0018` greying are moneyness-keyed), Surface, roll schedule,
   accuracy and portfolio stay moneyness-only, and the Workspace says so beside
   each of them in delta mode.

5. **Hypothesis memory stays moneyness-only.** Only the token-guarded record
   route writes memory, called only by the moneyness sweeps. A delta verdict
   carries `rule_spec = None` and `rule_hash = None`, and the page says "not
   recorded" instead of a hash. Recording delta rules is a backlog item.

6. **The market (quotes) path refuses a delta rule.** `QuoteSource.fill` takes a
   moneyness; extending the protocol is a backlog item, not done here.

7. **Depth.** A delta rule can strike deeper than
   `MODEL_PRICED_MAX_MONEYNESS_PCT` without asking to (a low target in a high
   realised vol), where the flat-vol premium is not a price. The constant moves
   from `sweep.py` to `put_roll.py`, and every model-priced result reports
   `beyond_model_depth_share`, shown as a count beside the headline whenever it is
   above zero.

8. **Amends `docs/adr/0028`:** the strike section's mode switch appears only on
   the tabs whose routes take a delta — Workspace and Bake-off. 0028's "a
   strike set on one tab is the strike on the next" holds per mode: a delta
   target carries Workspace ↔ Bake-off; Regime and Surface read the moneyness
   value, which is kept intact while a delta is in use. A ranking-row or
   sweep-cell click lands on a moneyness cell and switches the rule back.

## Results

`make delta-strike-gap`, first quote date of each month 2010-2023. The model
side is the backtest's own 4 weeks (20 trading days, T = 20/252); the market
side is the preview's rule -- the first listed expiry 28 or more calendar days
out -- capped at 35 so it stays a 4-week option (T = days/365). Months with no
expiry in 28-35 days, 8-23% of them (most often before weekly expiries were
listed), give no market answer. Medians, % below spot (SPY; QQQ sits 1-3pp
deeper on both sides, with gaps within 0.8pp of these); the gap is each day's
own difference:

| target | regime (days, answered) | model, realised vol | market, implied vol | gap |
|---|---|---|---|---|
| 0.05 | calm (86, 77) | 4.2% | 8.2% | 4.0pp |
| 0.05 | crisis (17, 14) | 12.1% | 17.8% | 5.3pp |
| 0.10 | calm (86, 79) | 3.3% | 5.5% | 2.2pp |
| 0.10 | elevated (62, 48) | 5.2% | 8.3% | 2.5pp |
| 0.10 | crisis (17, 14) | 9.4% | 12.3% | 2.9pp |
| 0.20 | calm (86, 79) | 2.1% | 3.0% | 0.9pp |

The model's strike is always closer to spot than the market's -- implied vol
sits above realised and carries skew -- so a "0.10Δ" backtest is a nearer, dearer
put than a desk's 10-delta. The delta rule still does what it is for: the
distance moves 2-3x from calm to crisis on both sides, where a moneyness rule
holds it fixed. Unreachable targets (the deepest put with an implied vol is
still above the target; the panel stops at 0.60 moneyness) were rare: at most
3 of 165 SPY days and 8 of 141 QQQ days per target, mostly at 0.05; they are
counted, not rounded to the panel's edge.

Served-model backtests, 4-week, 4 years to 2026-10-09 (the production lake held
no `tiingo_eod` yet, so q = 0): SPY fails every regime under every rule (5%,
10%, 0.10Δ, 0.20Δ). QQQ's `regime_only` at 5% (calm +231% ROI) survives at
0.10Δ but shrinks to +44%, and becomes `failed` at 0.20Δ; at 10% it was
`failed`. With 2 crisis rolls in the window none of these verdicts is strong.

## Consequences

* A regime comparison can now hold the option fixed instead of the distance,
  and the Bake-off can be run both ways.
* Every delta on the page is a model delta at realised vol. Anyone reading it as
  a market delta overstates the distance by the gap above; the label, the
  concept entry and the preview exist to prevent that.
* Memory records accumulate for moneyness rules only until the backlog item
  lands; delta verdicts are computed and logged but not remembered.
