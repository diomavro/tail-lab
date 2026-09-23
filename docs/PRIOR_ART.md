# Prior art: what other systems taught this one

Read 2026-08-27. The repos are cloned shallow at `~/Documents/reference/`
(not vendored here, not a dependency — reading material):

| Repo | What it is | Why it was read |
|---|---|---|
| [`nautechsystems/nautilus_trader`](https://github.com/nautechsystems/nautilus_trader) | Production algo-trading platform, Rust core + Python API | Options/greeks modelling, backtest realism, agent practices |
| [`nkaz001/hftbacktest`](https://github.com/nkaz001/hftbacktest) | HFT backtester with queue-position and latency models | The discipline of modelling what you cannot observe |
| [`rodlaf/kalshimarketmaker`](https://github.com/rodlaf/kalshimarketmaker) | Avellaneda-Stoikov market maker for Kalshi | Fair value vs mid; the paper/live interface seam |
| [`AshJha0/quant-portfolio`](https://github.com/AshJha0/quant-portfolio) · [site](https://ashjha0.github.io/quant-portfolio/) | 31-subproject quant portfolio (Python/C++/Rust) | Vol surfaces, regime switching, greeks — §6-§10 below |

**On the GitHub Pages site** (`ashjha0.github.io/quant-portfolio`): it is an
8.8 KB landing page whose seven links all point back into the repo's own
`CONVENTIONS.md`, `ARCHITECTURE.md`, `COOKBOOK.md`, `DIAGRAMS.md`, `LEARN.md`
and `MARKET_RISK.md` — every one of which is in the clone and was read on
2026-08-27. **It carries nothing the clone does not**, which is worth writing
down precisely so that being handed the URL later does not start a re-read of
97k lines.

**Keeping the library current.** These are `--depth 1` clones, so they go stale
silently — `git log` shows a plausible recent commit either way. To check for
new material rather than assume:

```bash
cd ~/Documents/reference/<repo> && git fetch --depth 20 origin \
  && git rev-list --count HEAD..origin/main   # 0 == nothing new
```

Verified 0 for `quant-portfolio` on 2026-09-03.

**Most of these repos is behind our wall.** They exist to place orders;
`docs/adr/0007` says we never do. Execution engines, order routing, live
venue adapters, market making and inventory management are all out of scope
here by constitution, not by backlog. What follows is only what survives that
filter — and the first item is worth more than the rest combined.

---

## 1. Fixed-moneyness strikes confound every cross-regime comparison

**The finding.** `research/backtest/put_roll.py` picks strikes as
`strike = spot * (1 - moneyness_pct/100)`. Nautilus treats that as one of
*four* strike-selection modes, and carries delta as a peer:
`StrikeRange.atm_percent(0.10)` **or** `StrikeRange.delta(0.25, 0.05)`
(`docs/concepts/options.md`). We only have the first.

That is not a style difference. Priced at our own regime bands
(`contracts/regime.py`: calm < 17, elevated < 28, crisis above), a
**"10% OOM, 4-week put"** is not one contract, it is six:

| Regime | VIX | Put delta | ~P(ITM) | Premium as % of notional |
|---|---|---|---|---|
| calm | 12 | -0.0005 | 0.1% | 0.00% |
| calm | 16 | -0.0068 | 0.7% | 0.01% |
| elevated | 22 | -0.0353 | 3.5% | 0.09% |
| elevated | 27 | -0.0687 | 6.9% | 0.23% |
| crisis | 45 | -0.1759 | 17.6% | 1.26% |
| crisis | 65 | -0.2446 | 24.5% | 2.87% |

A **350x** spread in delta between calm and crisis, and a premium that goes
from rounding error to 1.26% of notional. Holding "10% OOM" fixed across
regimes does not hold *the thing being bought* fixed. Holding delta fixed
does: a 10-delta 4-week put sits at 3.83% OOM in calm, 7.06% elevated and
13.85% in crisis.

**Why this matters here specifically, in three places:**

- **`docs/END_STATE.md` §4 Q4** asks which regimes produced the best OOM-put
  payoff. Part of the current answer is mechanical — in calm, a 10% OOM strike
  is barely reachable, so of course it rarely pays.
- **The Bake-off** (`research/backtest/metric_screen.py`) ranks sensitivity
  metrics at a fixed moneyness. A metric that happens to select high-vol names
  is handed contracts with materially more delta than one selecting low-vol
  names — a systematic advantage that has nothing to do with the screen.
- **The memory layer** (`docs/adr/0015`) keys verdicts on `(rule_hash, regime)`
  where the rule carries `moneyness_pct`, and calls a rule `confirmed` when it
  paid in ≥2 regimes. On the table above, that can compare a 0.05-delta
  contract to a 17.6-delta one and call them the same rule. The
  `regime_only` != `confirmed` distinction is the crux of that ADR, and this
  is the most likely way for it to be quietly wrong.

**Fixed-moneyness is not a bug.** It is a well-defined rule, and it is what a
human actually types when buying a put. The defect is using it as the *only*
parameterisation and then comparing across regimes. Both should exist, each
labelled with the question it answers: moneyness answers "what if I always buy
10% OOM", delta answers "what if I always buy the same tail probability".

**Newly buildable.** Cboe publishes `delta` on every contract and
`docs/adr/0020` started storing it on 2026-08-26. Before that snapshot existed
this was not implementable without a greeks engine.

## 2. We have no greeks at all, and theta *is* the North Star's bleed

`research/option_pricer.py` exposes exactly one method: `price_put`. It prices;
it does not differentiate. So the platform cannot currently answer the last
clause of its own North Star — *"how much would the position bleed if nothing
happens?"* — because **that number is portfolio theta**, and nothing computes
it.

Nautilus shows the shape (`docs/concepts/greeks.md`), and it is small:

- `black_scholes_greeks(s, r, b, vol, is_call, k, t)` returns
  `price, vol, delta, gamma, vega, theta, itm_prob` from the same closed form
  we already have. `itm_prob` is a free and directly useful column for a put
  screen — the market's own probability the payoff triggers.
- **Conventions worth copying verbatim** so our numbers are legible to anyone
  who has used a desk tool: vega scaled by 0.01 (per 1 vol *point*), theta by
  1/365.25 (per calendar day).
- **`portfolio_greeks(...)`** aggregates across positions. That is the Carry
  Budget (`docs/adr/0021`'s missing Risk function) in one function: sum theta
  over the recommended basket and you have annualised bleed.
- **Beta-weighted greeks** express delta/gamma in index terms
  (`index_instrument_id`, `beta_weights`). This is the professional form of
  exactly what the Screen already does by hand — everything here is ranked
  *versus SPY* — and it is the missing bridge between the Screen's sensitivity
  ranking and a book-level exposure number.
- **Time-weighted vega** normalises vega to a 30-day base so different
  expiries are comparable. Our sweep spans 1-12 week tenors and currently
  compares them without this.
- **Shock scenarios** (`spot_shock`, `vol_shock`, `time_to_expiry_shock`) are
  first-class arguments, not a separate tool. Carry is what the hedge costs;
  shock is what it buys. We surface neither.

## 3. Model what you cannot observe, and *name* the model

`hftbacktest` cannot see its own queue position or its latency, so both are
first-class trait objects with named implementations —
`QueueModel`/`LatencyModel`, with `RiskAdverseQueueModel` documented as the
conservative choice (advance only on observed trades at your price).
Nautilus does the same for fills: `DefaultFillModel`, `BestPriceFillModel`,
`prob_fill_on_limit`, `prob_slippage`.

Our analogue is the fill price on an illiquid OOM put, currently a half-spread
heuristic inside `research/backtest/brokerage.py` — one hardcoded assumption,
not a named model with a conservative default. `OptionPricer` is already
pluggable (`docs/adr/0004`); the cost model is the other half of the same idea
and is not. Making it swappable would let the Bake-off rank *cost assumptions*
alongside metrics, and would make the model-vs-market residual
(`docs/MODEL_RESIDUAL.md`) attributable to pricing versus execution.

## 4. Backtest-live parity comes from a shared core

Nautilus runs backtest, sandbox and live off one kernel (`crates/system`),
which is how it claims identical strategy behaviour across the three. Our
`docs/adr/0019` deliberately puts the paper executor in a *separate
repository*, buying credential separation at the cost of that parity — the
executor will re-implement strike selection and rolling, and the two can drift.

Noted, not resolved: 0019's separation is the stronger property for us, and
the price is real. If drift shows up, the fix is a shared, versioned schema for
the order intent (`/api/putlab/roll-schedule` already emits a
`schedule_id`), not a shared runtime.

## 5. Nautilus forbids how this repo operates — for reasons that do not apply

Worth writing down because the contrast is instructive, not because we should
change. `nautilus_trader/AI_POLICY.md`:

> Fully autonomous contributions, where an agent acts without meaningful human
> direction and review, are not accepted.

It also bans AI `Co-authored-by:` trailers and "Generated with ..." footers.
tail-lab does all three: the agent opens PRs unattended, auto-merges on green
CI (`docs/adr/0016`), auto-deploys, and stamps a Claude co-author trailer.

**This is a different threat model, not a disagreement.** Nautilus is public
OSS whose scarce resource is *maintainer review time*, and its policy exists so
AI work is not offloaded onto volunteers. Here the maintainer and the reviewer
are the same person, the gate is CI, and the blast radius is one private repo
that never trades. Their rule solves a problem we do not have.

Two things do transfer:

- **Their positive claim**: PR quality rises when the AI workflow "strengthens
  quality assurance and thoroughness across design, implementation, and
  testing instead of stopping at an unrefined first pass", specifically via
  "independent critical review by another person **or a separate agent
  session**". That is an argument for an adversarial review pass on agent PRs.
- **A practical warning**: if tail-lab is ever open-sourced, or if Dio
  contributes to nautilus, the commit trailers and the autonomous merge are
  both policy violations there. Cheap to know in advance.

---

# Part two: `AshJha0/quant-portfolio` (read 2026-08-27)

**What it is, stated plainly.** A portfolio — 31 sub-projects built to
demonstrate breadth to an employer, not a system with users. ~97k lines of
Python, 18k Rust, 10k C++, 26k of markdown, 239 Python test files, a claimed
5,963 passing tests (not run here; this reading covered the docs, module
headers and the specific functions cited below, not all 97k lines).

That provenance cuts *for* it on our particular gaps. Nautilus gives an API to
imitate; this gives a worked derivation with "where it fails" written down,
which is what you want when the thing you are missing is the maths. Two of its
sub-projects — `python/equity/09-vol-surface` and
`python/equity/10-regime-switching` — sit exactly on top of two weaknesses this
platform already knows it has.

## 6. Our model delta would be the wrong delta, which changes yesterday's plan

`09-vol-surface/src/eq_surface/greeks.py` documents the distinction our pricer
silently takes a side on:

- **Sticky-strike**: vol at each fixed strike is unchanged when spot moves, so
  the hedge ratio is the plain Black-Scholes delta at `sigma(K)`.
- **Sticky-delta / sticky-moneyness**: the smile rides with the forward, so
  `dV/dS = delta_BS + vega_BS * dsigma/dS` where
  `dsigma/dS = -(1/S) * dsigma/dk`. With the usual negative equity skew this
  makes the true delta differ materially from the BS one.

`research/option_pricer.py` prices at a flat trailing-realized-vol proxy — no
smile at all — so any delta computed from it is a sticky-strike delta on a
smile that does not exist. That matters because `AGENT_TODO`'s
delta-based-strike-selection item was written to *fix* a regime confound: a
delta that inherits the flat-vol error would import the same class of error it
was meant to remove.

**The resolution is already in the lake, and it changes the plan.** Cboe
publishes its own `delta`, computed off the real market smile, and
`docs/adr/0020` has been storing it since 2026-08-26. So:

- **Forward / live strike selection uses the stored Cboe delta.** No model, no
  smile assumption, no error to inherit.
- **Only the historical backtest needs a model delta**, because the vendor
  back-history predates the collection — and that one must state the
  sticky-strike assumption on the surface where its results appear, exactly as
  `docs/MODEL_RESIDUAL.md` does for the premium.

## 7. A principled replacement for `MODEL_PRICED_MAX_MONEYNESS_PCT`

`research/backtest/sweep.py` carries `MODEL_PRICED_MAX_MONEYNESS_PCT = 10.0` —
one hardcoded number standing for "past here our premium is a rounding
artefact rather than a price" (`docs/adr/0018`). It is a blunt instrument: the
same cutoff for every name, every regime, every tenor.

The vol-surface project enforces two no-arbitrage conditions instead — the
**Durrleman condition** (`g >= 0`, butterfly / positive implied density) across
strikes, and a **calendar-spread check** (total variance non-decreasing in T).
Those are the honest form of the same question. A model is trustworthy exactly
where the density it implies stays positive, and that boundary moves with vol
and tenor rather than sitting at a fixed 10%.

This is a strictly better answer to 0018 and it is testable: a surface that
violates Durrleman deep in the wing tells you *where* the pricer stopped being
a pricer, per name, per day.

## 8. The regime classifier flip-flops, and verdicts are keyed on it

> **Acted on — the hysteresis half of this is fixed (checked 2026-09-19).**
> `contracts/regime.py` now carries `HYSTERESIS_BAND = 1.0` and
> `CREDIT_HYSTERESIS_BAND = 0.5`, wired through `classify_vix_series` and
> `classify_credit_series`, with `tests/test_contracts_regime_hysteresis.py`
> covering them. The section below is kept as the record of what prior art
> suggested and why, not as a description of the code. What it flags that is
> **still true**: `docs/adr/0015` keys verdicts on regime, so a noisy label and
> a `confirmed` verdict remain coupled — the band narrows the noise without
> removing the dependency.

`contracts/regime.py` classifies on VIX level: calm < 17, elevated < 28, crisis
above. Hard thresholds, no state, no hysteresis — so a VIX oscillating
16.9 -> 17.1 -> 16.8 changes regime three times in three days.

`10-regime-switching` uses **hysteresis bands** (enter bear at p > 0.70, exit
at p < 0.30) and reports that this cuts turnover by **67-82%**. The mechanism
is general and does not require an HMM: two thresholds instead of one, with the
current state as tiebreak.

This matters here for the same reason yesterday's delta finding did.
`docs/adr/0015` keys verdicts on `(rule_hash, regime)` and calls a rule
`confirmed` when it paid in >= 2 regimes. Threshold chatter therefore
manufactures regime transitions, and a rule can collect its second regime from
a boundary wobble rather than from a genuine change of state. **That is a
second, independent defect on the same axis as the moneyness confound** — the
regime label and the contract identity are both noisier than the verdict
treats them.

Adding hysteresis is a few lines and needs no new model. Adopting an HMM is a
separate, larger question; if it is ever taken, note the trap this project
flags in bold: **trading on *smoothed* probabilities is look-ahead**. Only
filtered `P(s_t | x_{1..t})` is tradeable.

## 9. A no-lookahead test that can actually fail

The best single technique in any of the four repos, and it is four lines. From
`10-regime-switching/src/eq_regime/detection.py`:

> appending future observations must leave the **filtered** probability at `t`
> bit-identical, while the **smoothed** probability at `t` **must change**
> (sanity contrast).

**Correction, after checking rather than assuming** (2026-08-27): the first
draft of this section claimed we had no such control. That was wrong at the
layer that matters most. `tests/test_lake_store.py` already does it —
`test_restated_value_does_not_leak_into_earlier_asof_read` states outright that
"the revision is constructed so it WOULD change the answer if it leaked", then
asserts the restatement *is* visible at the later as-of date. So does
`test_point_in_time_clock.py`, whose second test proves the naive local clock
would have disagreed. The lake layer is covered.

Where the pattern was genuinely missing is one layer up: anything that computes
a **derived series** over time, where a "harmless denoising step" imports the
future without touching the store at all. Smoothing a series and classifying
the smoothed version reads as tidying and is not causal. That gap is now closed
for the regime timeline (`tests/test_contracts_regime_hysteresis.py` pairs
"appending future observations cannot change an earlier label" with a centred-
window control that *must* move on the same data). Auditing the remaining
derived series for the same pairing is queued.

The general rule is worth keeping regardless of who already follows it: a
no-lookahead assertion with no contrast passes just as happily when the value
being checked is constant, absent, or never computed.

## 10. Two documentation practices worth importing

**A numbered assumptions register.** Their `CONVENTIONS.md` requires every
project to answer six questions in writing, and the load-bearing one for us is
*"What assumptions did you make?" — an explicit, numbered register, each entry
with what breaks if it is violated.* Our constitution already demands that
"the assumptions a number rests on and how much they move it" be visible where
the result is shown; what it lacks is the artifact. The register is the
concrete form of a principle we already hold.

**A model-governance tripwire.** `docs/MARKET_RISK.md` backtests every VaR
series with Kupiec (right *number* of exceptions) and Christoffersen (they are
not *clustered*), and states a rule: a method failing either for two
consecutive quarters is retired. We measure our model-vs-market residual
(`docs/MODEL_RESIDUAL.md`, +1.34%/yr, sign-flipping in crisis) but have **no
stated threshold at which the model-priced backtest stops being trusted**.
`docs/adr/0004`'s caveat is qualitative; a tripwire would make it operational.

## Not for this repo: the question bank

`docs/LEARN.md` carries **481 self-test questions across 18 rounds** covering
options pricing, vol surfaces, risk metrics, execution and regime models. That
is out of scope here, but it is a ready-made seed for a market-microstructure
content domain in the sibling `quizkit` repo, which is where Dio's stated
interview-prep motivation actually belongs.


---

# Part three: an open-source survey (2026-09-23)

A deliberate search rather than a set of clones, run the day the repo went
public. Nothing below is vendored; the licences are recorded because several
are copyleft and two of the best ideas can only be **reimplemented from the
paper**, not copied.

Every claim here was checked against the actual file, not a search snippet —
repo metadata via `gh api`, and the quoted constants read out of the source.

| Repo | Licence | What it answers |
|---|---|---|
| [`lambdaclass/options_portfolio_backtester`](https://github.com/lambdaclass/options_portfolio_backtester) | MIT, 272★ | §11, §12 — delta strikes, walk-forward, the funding question |
| [`bashtage/arch`](https://github.com/bashtage/arch) | NCSA (permissive) | §13 — block-bootstrap CIs, and the right multiple-testing tool |
| [`dcajasn/Riskfolio-Lib`](https://github.com/dcajasn/Riskfolio-Lib) | BSD-3 | §14 — exact Kelly without forming a variance |
| [`marwinsteiner/pysvi`](https://github.com/marwinsteiner/pysvi) | MIT, 3★ | §15 — closes §7's open ask |
| [`m-g-h/R.MFIV`](https://github.com/m-g-h/R.MFIV) | MIT (R) | §16 — the put-wing truncation bias |
| `tea`, `evt0`, EQD (Technometrics 2024) | GPL / none | §17 — read-only; reimplement from the papers |

## 11. Delta-based strike selection is a solved problem, and so is its score

`options_portfolio_backtester` is the nearest neighbour this repo has, from
the **same organisation whose `data-v1` chains** `ingestion/option_quotes.py`
already reads, and built on **the same optionsDX panel** in
`data/vendor/optionsdx/`. Its numbers are therefore reproducible here, which
is rare.

`rust/ob_core/src/convexity_scoring.rs` carries `find_target_put(deltas, dtes,
asks, target_delta, dte_min, dte_max)`, and `convexity/config.py` pins the
parameterisation (read 2026-09-23):

```python
target_delta: float = -0.10
dte_min: int = 14
dte_max: int = 60
tail_drop: float = 0.20
budget_pct: float = 0.005   # 0.5% of portfolio per month
```

That is §1's delta ladder, already written. It also supplies a ranking column
this repo lacks — `convexity_ratio = tail_payoff / annual_cost`, the payoff in
a 20% drop divided by the annualised ask. One number that serves as both a
Screen ranking and `docs/adr/0021`'s missing Carry Budget.

**Two defects not to copy**, both visible in the source: `find_target_put`
breaks delta ties with no liquidity filter, and `annual_cost = ask * 100.0 *
12.0` hardcodes twelve rolls a year regardless of the 14-60 DTE band it just
selected on — internally inconsistent at 60 DTE.

## 12. The depth this platform exists to test has an out-of-sample cliff

`docs/SPITZNAGEL_RECONSTRUCTION.md` (20 KB, 2008-2024 SPY) does the thing this
repo's Bake-off does not yet do: optimise on 2008-2016, evaluate on 2017-2024
with no re-tuning. Its headline is a falsifiable claim about the exact
strategy family here — **45-50% OTM is an overfitting cliff.** It wins
in-sample on the GFC's -52% and goes *negative* out-of-sample, because COVID
only reached -34%. 40% OTM is the depth ceiling that survives. Their rule of
thumb: halve any in-sample excess for a forward estimate.

**The caveat is the more important half.** Their 3.3%/yr premium is
**externally funded** — injected, not debited from the equity sleeve. Under
the AQR framing where premium reduces the sleeve, excess returns fall ~2.5pp/yr
across every budget and **the strategy stops beating SPY**.
`research/backtest/sizing.py`'s `FixedPremium` / `WealthFraction` seam takes a
side on that question silently, and the surface does not say which.

## 13. The Bake-off is using the wrong multiple-testing correction

`arch/bootstrap/` exports `StationaryBootstrap`, `CircularBlockBootstrap`,
`MovingBlockBootstrap` and **`optimal_block_length`** (Politis-White/Patton) —
which is `AGENT_TODO`'s "block-bootstrap CI, never `alpha/sqrt(k)`" off the
shelf, with the block length *chosen* rather than guessed.

The larger win is `arch/bootstrap/multiple_comparison.py`: **`MCS`** (Model
Confidence Set), **`SPA`** (Hansen's Superior Predictive Ability), `StepM`,
`RealityCheck`. `research/backtest/metric_screen.py` currently applies
Benjamini-Hochberg, which controls FDR over tests treated as exchangeable. A
bake-off of nested sensitivity screens on SPY/QQQ correlated ~0.95 is not
exchangeable — it is exactly the "effective N is 15-25, not 2,688" problem
already recorded in the Paretan plan. **MCS returns the set of screens that
cannot be statistically separated**, which is a more honest Bake-off output
than a ranked list with stars.

## 14. Exact Kelly answers the objection §4 Q8 raises against itself

`docs/END_STATE.md` §4 Q8 objects that `l* = (mu-r)/sigma^2` does not transfer
to a put, because a put's variance is mostly upside. Riskfolio-Lib's
`riskfolio/src/Portfolio.py` `kelly="exact"` branch never forms a variance:

```python
ret = 1 / T * cp.sum(cp.log(1 + returns @ w))
```

It maximises empirical mean log-growth directly over the realised joint payoff
matrix, so a bounded-loss, right-skewed column is handled by construction.
Feed it two columns — benchmark and hedge overlay — and `w` is the answer,
with `kelly="approx"` available in the same call as the naive-Kelly contrast
to report beside it. Paper: Cajas, *Kelly Portfolio Optimization: A DCP
Framework* (SSRN 3833617).

For the fractional-Kelly question underneath it, `cvxgrp/kelly_code` (GPL-3,
Busseti-Ryu-Boyd, arXiv 1603.06183) adds a convex **bound on drawdown
probability** — a principled substitute for "half-Kelly because full Kelly is
scary", same conservatism with a stated constraint instead of a folk factor.

## 15. §7's open ask has an implementation, at three stars

§7 wanted a measured replacement for `MODEL_PRICED_MAX_MONEYNESS_PCT = 10.0`
via the Durrleman condition. `marwinsteiner/pysvi` `src/pysvi/diagnostics.py`
is the first implementation surveyed that does it defensibly, and the reason
is its **refusals**:

* the grid defaults to the observed data's own log-moneyness range, so
  verification and the calibration penalty cover the same domain;
* points where total variance is non-finite or non-positive, or where `g` is
  non-finite, are counted as `n_invalid` and **fail** the butterfly check —
  freedom from arbitrage is never certified on an unevaluated region;
* Lee's moment bounds on wing slopes, with `wing_slope_method="grid_edge"`
  flagged where the parametrisation has no closed form (a known underestimate);
* finite-difference densities (SABR, DirectSVI) are documented as carrying FD
  noise above the default tolerance.

The SVI corner of GitHub is the most saturated surveyed — a dozen repos fit a
smile to a Yahoo chain and assert "arbitrage-free" from the parametrisation
rather than checking `g(k)` on the evaluated domain. This was the only one
whose arbitrage module survived reading. The "refuse rather than certify"
shape is also what `ImpliedAlphaFit.refusal` is reaching for.

## 16. A put-wing-only chain makes any naive model-free IV biased low

`m-g-h/R.MFIV` (MIT — the `DESCRIPTION` says so; GitHub's detector reports
NOASSERTION and is wrong) decomposes the Cboe VIX white paper into testable
functions, **and** implements `JandT_2007_smoothing_method` — Jiang & Tian
(2007)'s correction for truncation and discretisation bias when the strike
range is insufficient.

That second part is why it beats the dozen other VIX-replication repos *for
this repo specifically*: `ingestion/option_chain.py` sweeps the **put wing
only**, so a naive variance strip is truncated by construction and a per-name
model-free IV would be biased low by an amount nobody here has measured. It is
the honest prerequisite for the VRP column `AGENT_TODO` wants, since VRP needs
its implied leg to be model-free or the flat-vol error is simply reimported.

## 17. Python has no bias-corrected Hill — that gap is real, and it is ours

The negative result is worth as much as the positive ones. `research/surface/
hill.py` uses a plain Hill estimator with a plateau-finder gated on the
Karamata onset. Serious EVT does better, and **all of it is R**:

* **`tea`** (GPL-3): `danielsson()`, the Danielsson et al. (2001) double
  bootstrap for the optimal `k`; plus Hall (1990), Caeiro & Gomes (2014, 2016).
* **`evt0`** (GPL-2+): mean-of-order-*p*, peaks-over-random-threshold Hill, and
  second-order reduced-bias estimators.
* **EQD** — Murphy, Tawn & Varty, *Automated threshold selection and associated
  inference uncertainty for univariate extremes*, Technometrics (2024),
  [arXiv:2310.17999](https://arxiv.org/abs/2310.17999). Expected quantile
  discrepancy: bootstrap GPD samples over candidate thresholds and pick the one
  whose excesses are most GPD-consistent. **A criterion with a number, not an
  eyeballed plateau** — the direct upgrade to `stable_k`. The reference
  implementation has **no licence file at all** (= all rights reserved).

So: `arch` is usable today for CIs; threshold selection and bias correction
must be **reimplemented from the papers**. Orientation first: Belzile, Dutang,
Northrop & Opitz, *A modeler's guide to extreme value software*,
[arXiv:2205.07714](https://arxiv.org/abs/2205.07714).

`georgebv/pyextremes` (MIT, 280★) is solid but solves a different problem — it
is GPD/POT with *diagnostic plots for a human*, not an automated criterion. The
one thing worth importing outright is its Bayesian/MCMC return-level interval:
a posterior on the tail parameter is a better honest error bar than any point
estimate.

## Two searches that returned nothing usable

**Fengler's arbitrage-free RND spline** (constrained smoothing splines under
linear shape constraints) is the right method for a non-negative implied
density, and has **no open implementation in any language** that this survey
could find. Paper only; it is a QP, so it is buildable, but from scratch.

**Realised-variance libraries** all need intraday data. This repo has daily
bars on a rolling five-year window, so that whole dimension is blocked on
data rather than code.

**One warning for future searches.** `HealthCareVisor/tail-risk-hedging-platform`
and `kruxholdings-max/tail-risk-research` were created eight minutes apart on
2025-12-01 with byte-identical descriptions; one is a README with no code, the
other ships `.pyc` files and two PPO `.zip` models with no training code. A
content-farm pair, and they rank highly on exactly the search terms this repo
invites. Not prior art.

## Postscript: §5's premise expired on 2026-09-23

§5 justified this repo's autonomous merge and AI co-author trailers on the
grounds that "the blast radius is **one private repo** that never trades."
The repo is public as of 2026-09-23 (`HUMAN_TODO.md`). Nothing about that is a
violation — it is Dio's repo and the reasoning about maintainer review time
still does not apply — but the recorded justification no longer describes the
situation, and the fork-PR hole it implies was closed separately in
`automerge.yml`'s `head_repository` clause.


---

# Part four: data engineering and automation (2026-09-23)

Same survey, two more dimensions. Findings marked **[verified here]** were
re-run against this repo or the live API before being written down; the rest
are the surveyor's measurements, marked as such.

## 18. Free historical option chains: nothing better exists, and that is the finding

Every "new" free chain dataset checked was a re-upload of optionsDX or of the
Alpha Vantage feed already identified via lambdaclass. Academic repositories
redistribute *derived* series, essentially never chains, because vendor
licences forbid it. Exchange freebies are dead ends — OCC prices its series
file at $1,750/mo, `nasdaqtrader.com`'s data-products page 302s to a 404.

**Forward-collecting Cboe's delayed CDN remains correct, and is the only
mechanism that grows 2026+ history.** `docs/adr/0020` stands.

One real gap-filler: a Kaggle SPY chain covering **2014-2025**, CC0-asserted,
which would take the OHLCV-chain overlap from the ~589 trading days
`CLAUDE.md` records to ~1,075 and make 2023 a free vendor-disagreement check.
Two caveats that decide whether it is worth it: its schema is
character-for-character Alpha Vantage, so **AV's terms are the real
constraint, not the CC0 label** — the same unresolved posture as lambdaclass;
and its IV is **smoothed, not per-contract inverted** (383 strikes carrying
109 distinct IV values), so the vendor greeks are decorative and
`option_pricer.PutGreeks` must do the work. 8.7 GB uncompressed against
**13 GB free** [verified here] — ingest year-by-year, never unpack whole.

## 19. No FRED client pages vintages. Here is the recipe that works

The `rates` adapter's HTTP 400 is not a bug with a library fix:
`fredapi.get_series_all_releases` hardcodes the same unbounded window and
does not page (its issue #28 is this exact bug, closed without a fix);
`fredr` pages *observations*, not vintages; R's `alfred` has no chunking at
all. The cap is documented: **2000 vintage dates per JSON request**.

**The obvious fix is worse than the bug.** Chunking by realtime window clips
`realtime_start` to the window boundary, so an observation from 2010 comes
back stamped with the chunk's start date — and `rates.py`'s
`drop_duplicates(subset=["obs_date","vintage_date"])` would keep both the
true vintage and the fabricated one. **A manufactured revision in a
point-in-time dataset** is the one failure this repo cannot tolerate.

The working recipe [verified here, against the live API]:

```
GET series/vintagedates?series_id=DGS10&limit=10000      -> 5115 dates, HTTP 200
GET series/observations?vintage_dates=<400 dates>&output_type=3
    &observation_start=<batch[0]-400d>&observation_end=<batch[-1]>
                                                          -> HTTP 200, 8.3 s, 419 rows
```

`output_type=3` returns a **wide** frame with the vintage in the column name
(`{"date": "...", "DGS10_20250214": "..."}`), so clipping is structurally
impossible — melt wide-to-long. Three constraints, none documented by FRED:
batches must stay <=600 dates or **Apache**, not FRED, returns an HTML 400;
the observation window must be bounded or the request times out; 5,115
vintages is 13 requests, well inside the 120/min limit.

## 20. Point-in-time: do not migrate. Steal the clock

**The premise inverts.** `DeltaTable.history()` retains 30 days by default and
vacuum retains files 7, so `load_as_version` is *less* durable than
partition-by-`ingest_date`, where every historical snapshot is still
referenced by the current version. ArcticDB, Iceberg, Dolt, XTDB and lakeFS
offer no correctness guarantee this repo lacks. **Do not "upgrade".**

What is worth copying is zipline's shape (`zipline-reloaded`,
`src/zipline/_protocol.pyx::BarData`, Apache-2.0): `simulation_dt_func` is
injected at construction, and the public `current()` / `history()` **take no
date**. The algorithm physically cannot name a time, so no adversarial
look-ahead test is needed — the unsafe call is unspellable.

`read_bronze_as_of(dataset, as_of)` puts `as_of` in the caller's hands at
**14+ independent call sites in `research/`**, each an independent chance to
pass the wrong date, which is exactly why `docs/adr/0009` needs a per-path
adversarial test. A `BronzeReader(store, as_of)` whose methods take no date,
plus an import-linter `forbidden` contract stopping `research/` importing
`tail_lab.lake` directly, converts the #1 invariant from test-enforced to
**CI-enforced by machinery already running**.

Second, smaller hole: a backfilled FRED vintage lands in a new partition and
is **invisible** to an as-of read resolving to an earlier one. The
`vintage_date` column is carried but never used for resolution.

## 21. Four Delta Lake findings, two of them live bugs

* **The quarantine table needs a rewrite, not a drop.** delta-rs has **no
  column-drop API** (`drop_columns` is an open PR). `mode="overwrite",
  schema_mode="overwrite"` removes the stale `__index_level_0__` and
  **preserves history and partitioning** — four lines.
* **A correction to this repo's own comment.** `lake/store.py` says the index
  leak comes from "validation dropping rows leaves an `Index`, not a
  `RangeIndex`". On pandas 3.0.5 that is **false** — filtering preserves
  `RangeIndex` with a step. The real triggers are `pd.concat` without
  `ignore_index` and named/string indexes. `reset_index(drop=True)` is still
  load-bearing, for a different reason than stated.
* **`write_bronze`'s TOCTOU is not fixed by compare-and-swap.** Delta's
  conflict checker treats a blind append as non-conflicting, so two
  concurrent appends to the same partition **both commit and duplicate rows**;
  S3 conditional-put prevents a lost commit, not a duplicated partition. The
  fix is `merge(...).when_not_matched_insert_all()` keyed on `ingest_date`,
  which makes immutability structural rather than a preceding `if`. Exposure
  today is limited by `daily-chain-snapshot.yml`'s `concurrency:` group.
* **`delta.checkpointInterval` defaults to 100 in delta-rs, not 10.** Every
  un-checkpointed commit is a separate `GetObject` on Tigris, and it compounds
  because `_existing_bronze_ingest_date_strings` does a full snapshot load on
  every write. One `set_table_properties` call per table.
* **DynamoDB locking is gone** (removed in delta-rs 1.6.1, not 1.0), so
  `AWS_S3_ALLOW_UNSAFE_RENAME` is inert and `docs/adr/0013`'s single-writer
  note has lost its premise.

## 22. No finance-specific validation library exists — and pandera is under-used

A sweep across "market data validation", "tick data validation" and "ohlcv
validation" found nothing above 8 stars that touches stale quotes, crossed
bid-ask or sentinel detection. That is the normal state of the art, not a gap
in the search: **this is domain code you write.**

The useful finding is that the optionsDX blank-`P_IV`-with-garbage-greeks case
is *a plain cross-field `pandera` Check nobody has written*, and
`SchemaErrors.failure_cases` (carrying `check`, `failure_case`, `index` per
row) could be persisted into `__quarantine` instead of only the failing rows.
Zero new dependencies.

Vocabulary worth stealing even without adopting the tool: `pointblank`'s
`col_missing_coded(col, value)` names the Cboe zero-fill problem as a
first-class check. `datacompy` (Apache-2.0) has **per-column tolerances**,
which expresses "close must agree to 3e-5, adj_close is a different quantity"
in one call. **Soda Core is disqualified on licence** (Elastic 2.0, not OSS,
and its v4 CLI authenticates to a paid cloud); **whylogs is abandoned** (dead
~20 months); Great Expectations removed auto-profiling in V1 and now
redirects to `fivetran/great_expectations`.

## 23. The crons are delivered hours late, and public repos silently disable them

**[verified here]** Measured over the last eight scheduled runs of
`daily-chain-snapshot.yml`:

| cron | actual delivery | delay |
|---|---|---|
| `30 21 * * 1-5` | 23:27 - 00:12 UTC | **+2h00 to +2h42** |
| `0 5 * * 2-6` | 09:02 - 09:37 UTC | **+4h02 to +4h37** |

Not one run landed near its cron. The workflow's own comment assumes the
catch-up sits safely inside the window to the 13:30 UTC open; the real margin
is **under four hours**, not the ~8.5 implied. Moving the catch-up off the
hour helps — GitHub's docs name the start of every hour as a high-load time.

**New risk from going public:** GitHub disables scheduled workflows in a
public repository after **60 days without repository activity**. That rule did
not apply while private, it kills the one job that cannot be backfilled, and
it disables **both crons together** — so the two-cron redundancy is no
defence. A push-triggered watchdog asserting the workflow is still `active`
and that its newest scheduled run is under 36 hours old closes it, because
push-triggered workflows are never auto-disabled.

Also worth recording in `docs/adr/0016` because it falsifies the obvious
assumption: **going public did not lift the billing block.** A failed payment
blocks Actions account-wide, and public-repo free minutes do not exempt a
delinquent account.

## 24. The single worst supply-chain line, and a linter that finds it

`deploy.yml` pins `superfly/flyctl-actions/setup-flyctl@master` — **a mutable
ref on the one step holding `FLY_API_TOKEN`.** Fly has **no OIDC for Actions**
(requested since Feb 2024, not shipped), so scope and expiry are the entire
available mitigation: `fly tokens create deploy` is app-scoped, and the
default token expiry is **twenty years**.

`zizmor` (`docs.zizmor.sh`, runs offline) found 87 issues, 25 high, including
that pin, seven `actions/checkout` without `persist-credentials: false`, and a
template-injection in `deploy.yml`'s notice line. `automerge.yml`'s
`dangerous-triggers` finding is a **false positive to suppress with a
comment** — the `head_repository` clause is the correct mitigation.

One contradiction worth resolving: `agent-review` checks out
`${{ github.head_ref }}` at workspace root and then runs Claude with
`--allowedTools Bash`, while the action's own security doc says *"do not check
out an untrusted ref into the workspace root before this action."* Three
things save it today — fork PRs get no secrets, the action checks the
triggering user has write access, and `head_ref` does not resolve in the base
repo — but none of them is the workflow's own doing.

## 25. The 15.8:1 accretion number is stale, and the real duplication is exempted

Re-measured: `claude[bot]` is **6.9:1** added-to-deleted across 31 merged PRs,
`diomavro` **5.2:1** across 76. The **15.8:1 vs 2.4:1** figure is quoted as
current fact in three places — `weekly-cleanup.yml`, `pyproject.toml`'s ruff
comment, and `docs/adr/0023`. It no longer holds. (Do not over-celebrate:
restricted to source directories, August was 5.6:1 and September is 7.6:1 —
*worse*. Pick one measurement and keep it in one place.)

**And the exemption is hiding the real case.** `agent-review`'s prompt says
one ingestion adapter per dataset is a sanctioned near-duplicate. Under it:
**[verified here]** `ingestion/credit.py` (257 lines) and `ingestion/rates.py`
(269 lines) differ in **60 lines after normalising the dataset name** — ~78%
identical, both FRED adapters, both by `claude[bot]`, four days apart. That is
not one-adapter-per-dataset; that is one adapter copied. Narrow the exemption
to the fetch/parse seam.

Two gates that install clean today and ratchet forever, same shape as
`docs/adr/0023`'s lint ratchet: **diff coverage at 100%** (pytest's own
`codecov.yml` gates the patch at 100% and deliberately sets `project: false`,
which is exactly the asymmetry this repo's project-level floors lack), and a
**duplication gate** — `pylint --enable=duplicate-code` over `research/ api/
transforms/ lake/ contracts/` currently returns **zero**.
