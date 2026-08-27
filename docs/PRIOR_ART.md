# Prior art: what three trading systems taught this one

Read 2026-08-27. The repos are cloned shallow at `~/Documents/reference/`
(not vendored here, not a dependency — reading material):

| Repo | What it is | Why it was read |
|---|---|---|
| [`nautechsystems/nautilus_trader`](https://github.com/nautechsystems/nautilus_trader) | Production algo-trading platform, Rust core + Python API | Options/greeks modelling, backtest realism, agent practices |
| [`nkaz001/hftbacktest`](https://github.com/nkaz001/hftbacktest) | HFT backtester with queue-position and latency models | The discipline of modelling what you cannot observe |
| [`rodlaf/kalshimarketmaker`](https://github.com/rodlaf/kalshimarketmaker) | Avellaneda-Stoikov market maker for Kalshi | Fair value vs mid; the paper/live interface seam |

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
