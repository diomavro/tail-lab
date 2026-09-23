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
