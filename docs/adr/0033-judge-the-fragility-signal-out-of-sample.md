# 33. Judge the fragility signal out of sample

Date: 2026-11-01

## Status

**Accepted.**
- Dio asked for this on 2026-10-31: "isn't the point of the fragility index to be a signal for us to run the put strategy in the future?"
- The plan went through ten adversarial review rounds before Dio told us to ship it.
- This ADR amends `docs/adr/0028` (the Bake-off rail and the strip line) and `docs/adr/0021` (the Workspace gains two per-name sections).
- It builds on `docs/adr/0015` (regime at entry), `docs/adr/0018` (the model's depth limit) and `docs/adr/0029` (delta at realised vol).

## Context

The fragility screen ranks names by a composite of sensitivity metrics. The only evidence that this ranking means anything was the Bake-off (`research/backtest/metric_screen.py`). The Bake-off measures each metric and the put payoff over the **same** trailing window. That makes it an in-sample cross-sectional association, and its own docstring says so. It cannot say whether ranking names *today* picks better puts *next month*, and that forward question is the one the strategy depends on.

The in-sample numbers were also misleading on their own terms. The live run on 2026-10-31 (10% below spot, 4-week puts, 4-year window, top 5) showed:
- The composite's Spearman ρ was +0.42, yet its top-5 basket returned −41% against −10% for buying puts on every name.
- Co-kurtosis "won" with +116% on a 1% hit rate.

Review found that at a fixed moneyness the outcome is dominated by cost rules, not by fragility. On low-vol names the premium floor binds, and per-contract commission eats 60–90% of the premium. A floored SPY put in February 2020 paid about 2,122×.

## Decision

1. **A walk-forward signal test** (`research/backtest/walk_forward.py`, `GET /api/putlab/signal-test`). At each rebalance date t, one roll apart:
   - Rank the names using only data up to t. That means the six metrics, the composite and six plain-volatility baselines (20-day, signal-window and ratio vol, each tested high and low).
   - Buy one roll of 0.10Δ puts on the top k.
   - Score the basket against three comparisons: random k-baskets drawn from the same eligible set (a within-period permutation), all eligible names, and an SPY put.

   The composite is the pre-registered hypothesis and is judged on its raw permutation p. The other 12 screens are exploratory and are Benjamini–Hochberg corrected.

2. **The outcome excludes what leaks or what isn't fragility:**
   - Strikes are chosen by delta at realised vol, because fixed moneyness confounds the result with vol (ADR 0029).
   - Commission is excluded. It depends on the split-adjusted contract count, which is future information. The proportional half-spread is kept.
   - Name-periods with σ̂ at the clamp are ineligible.
   - Under delta there is no percent-depth gate. A 0.10Δ strike is 27% below spot at 100% vol, so a gate would have removed exactly the fragile names. Depth is reported as a diagnostic instead.

3. **The verdict uses its own robustness checks:**
   - `insufficient_data`: fewer than 24 periods, or fewer than 3 stress episodes, where a stress episode is a run of periods in which SPY's same-rule put paid.
   - `no_better_than_random`.
   - `crash_carried`: the edge disappears once the best period or the best single name-period is dropped.
   - `beats_random`.

   Whatever the status, the verdict sentence always prints the best of the six plain-volatility screens, labelled as picked after the fact. The test does **not** statistically separate fragility from volatility. Three attempts to do so (quintile matching, linear residualisation, squared-rank residualisation) failed review by simulation and were cut. A Fama–MacBeth test with vol controls is logged in `AGENT_TODO.md`.

4. **The caveats sit beside the verdict:**
   - **Survivorship:** the 70 names are today's list, chosen with hindsight.
   - **Model pricing at realised vol:** a pass means fragile names' next-roll moves beat their trailing vol, not that their real puts paid.
   - **The test window:** a 4-year signal window plus OHLCV from 2016-10 means the test starts around April 2020 (the first live run: 2020-04-30). The February–March 2020 Covid crash is **outside** the test.

5. **Surfaces:**
   - **Bake-off.** It leads with the walk-forward. The in-sample table moves below, collapsed, keeps its caveat, and fetches only when opened.
   - **Rail (amends 0028).** On the Bake-off, *years* drives both the signal window and the in-sample lookback. These pull in opposite directions: more years means a longer in-sample window but a shorter forward test.
   - **Strip line (amends 0028).** It reads the walk-forward verdict at fixed, stated settings (`STRIP_SIGNAL_TEST` = `_WARM_SIGNAL_TEST`: Δ0.10 · 4w · 4y · top 5). It no longer applies a same-settings check against the rail.
   - **Workspace, the per-name page (amends 0021).** It gains *Data coverage* (OHLCV and dividend sessions per year, plus the shared Marks chart, loaded lazily) and *How the signals treated this name*. The second section is descriptive only: "k of n paid, largest payoff", with no p-values. `MemoryTeaser` gains n, hit rate and biggest payoff per regime at entry. The Marks tab stays.
   - **Tooltips.** Every table header and stat label on every Put Lab tab explains itself: a short tooltip on hover or focus, and the full concept on click. A pytest and an e2e test enforce this.

## Consequences

- The platform can now say whether the fragility index is a usable forward signal. The answer is allowed to be "no", and the first live run says so:
  - The composite's p was 0.079, so `no_better_than_random`.
  - The best plain-volatility screen (long/20-day vol ratio, high) beat it by a wide margin.
  - That screen's win is the model-pricing artifact the caveat describes, because the backtest prices puts at the 20-day vol that this ratio is built from.
- A cold run reads every universe name and evaluates about 5,000 name-periods. It is warmed daily after the Bake-off, inside the shared screen slot, never alongside a visitor's screen.
- Real-quote pricing, a point-in-time universe, and a vol-controlled significance test remain the open fixes. They are logged in `AGENT_TODO.md`.
