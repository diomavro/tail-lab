# Prior art: what three trading systems taught this one

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
