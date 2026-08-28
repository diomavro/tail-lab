# End state

This is where tail-lab is going. It is **aspirational and milestone-ordered,
not a spec of what exists today** — see `README.md` §"What exists now" for
the actual starting point (a walking skeleton). The daily agent reads this
document to pick its next small increment; it should always be able to point
at a specific paragraph here and say "this PR moves us toward that."

Everything below serves the north star in `README.md`: sharpening the
put-buying decision. If a proposed increment doesn't trace back to something
on this page (or to a documented research question below), it fails the
value bar in `docs/AGENT_MISSION.md`.

---

## 1. The put-buying cockpit (end state)

A single-page dashboard, read by Dio, that answers the north-star question
at a glance and lets him drill in before placing a trade by hand.

### 1.1 Sensitivity leaderboard (per metric)

- One panel per sensitivity metric (downside/tail beta, co-skewness,
  co-kurtosis, factor sensitivities, cheapness-adjusted variants, ...),
  each ranking the current screening universe by that metric, most-sensitive
  first.
- A metric-picker / tab that also shows **how that metric has backtested
  historically** (link into §1.5) so a ranking is never read without its
  track record alongside it.
- Sortable by sensitivity score, by name, by sector; filterable to the
  options-liquid tradable subset (`docs/adr/0008`).

### 1.2 OOM-put candidates

- For each candidate underlying (from the leaderboard, restricted to the
  tradable subset): the model price and greeks (delta, gamma, vega, theta)
  of a representative soon-expiry OOM put, plus a **cheapness score** —
  how cheap the model thinks this put is relative to the underlying's
  recent realized sensitivity.
- A visible caveat on every row: model-priced, not a real quote
  (`docs/adr/0004`) — the cheapness score is a ranking signal, not a
  trading price.
- A per-candidate "what would this cost to hold if nothing happens" bleed
  estimate (theta decay over N days at current IV proxy).

### 1.3 Regime panel

- Current market regime classification (e.g. low-vol grind, vol expansion,
  crisis, recovery) from the volatility complex + credit spreads + rates.
- Regime history strip so today's regime is legible in context, not just
  as an isolated label.

### 1.4 Event calendar with proximity flags

- Upcoming scheduled events (FOMC, CPI, major earnings for candidate
  names) plus the manual unscheduled table, each flagged by proximity
  (days-to-event) and cross-referenced against the OOM-put candidates whose
  expiry brackets that event.

### 1.5 Backtest comparison across metrics

- Side-by-side historical backtest performance of every sensitivity metric
  as a screen-and-buy-OOM-puts strategy: hit rate, average payoff on
  "something happened" windows, average bleed on quiet windows, by regime.
- This is the answer to research question (a) below, kept live and
  re-runnable, not a one-off analysis.

### 1.6 Secondary tab — conventional strategy

- The variance-aware, Sharpe-oriented strategy (`README.md` §"The two
  strategies", item 2), as an independent comparison surface. Lower
  priority, deliberately a quieter part of the platform — never merged
  into the sensitivity-puts leaderboard as if it were a combined portfolio.

---

## 2. The full data platform

### 2.1 The six core datasets (v1 — see `docs/DATA_CONTRACTS.md` for schemas)

1. Underlying OHLCV — deep, broad universe.
2. Volatility complex — VIX/VIX3M/VIX9D/VVIX/CBOE SKEW + realized vol.
3. Rates — Treasury curve/SOFR/fed funds.
4. Credit — HY/IG OAS.
5. Event calendar — scheduled + manual unscheduled table.
6. Forward-collected option chains — narrow tradable set only.

### 2.2 Deferred backlog (beyond v1)

Not built yet; each is a candidate `AGENT_TODO.md` item once the v1
datasets and their consumers are solid:

- Single-name options at scale (beyond the narrow forward-collected set).
- CDS (credit default swap) spreads, single-name.
- ETF flows.
- CFTC positioning (Commitment of Traders).
- Margin debt.
- Market breadth indicators.
- Point-in-time historical index constituents (closes the survivorship-bias
  gap — `docs/adr/0010`; this one is not merely "nice to have," it is a
  research-validity requirement and should be prioritized once the core
  platform is stable).

### 2.3 Point-in-time constituents

Today's screening universe is "current constituents," which is
survivorship-biased by construction — it omits exactly the names that blew
up and delisted, the most tail-sensitive assets of all
(`docs/adr/0010`). End state: either a genuine point-in-time constituents
feed, or every affected result carries a loud, structural caveat (not a
footnote) until one exists.

---

## 3. The backtest engine

- **v1 — model-priced.** OOM puts priced with Black-Scholes + a
  vol-surface proxy built from the VIX/SKEW complex, on historical
  underlying paths, behind the `OptionPricer` protocol
  (`src/tail_lab/research/pricing/interface.py`, `docs/adr/0004`).
  Point-in-time correctness enforced end-to-end via `lake/asof.py`
  (`docs/adr/0009`).
- **v2 — pluggable real-quote pricing.** A second `OptionPricer`
  implementation backed by real historical option quotes (from a paid
  source once `HUMAN_TODO.md`'s budget item is actioned — see
  `docs/DATA_CONTRACTS.md`). This is an **upgrade**, swapped in behind the
  same interface, not a rewrite of the backtest engine.
- **End state:** the backtest comparison (§1.5) is re-run under both
  pricing implementations once real quotes exist, and the model-priced
  numbers are recast explicitly as what they always were — a relative
  ranking of sensitivity metrics, not P&L truth.

---

## 4. Research questions the platform must answer

These are the standing questions the whole platform serves. Each should
eventually have a live, re-runnable answer surfaced in the cockpit — not a
one-off notebook result that goes stale.

1. **Which sensitivity metric backtests best** as a screen for OOM-put
   buying — by hit rate, payoff, and bleed, broken out by regime? (§1.5)
2. **How did skew evolve before crashes**, historically — is there a
   detectable pre-crash skew signature the leaderboard should weight?
3. **How does implied vol behave before scheduled macro events**
   (FOMC/CPI) — is there a systematic pre-event IV drift worth timing
   entries around?
4. **Which market regimes produced the best OOM-put payoff** historically,
   and does the regime panel (§1.3) predict them in real time or only in
   hindsight?
5. **How does option behavior (IV, skew, volume where available) move
   around FOMC/CPI releases and past crises** specifically — a pattern
   library the event calendar (§1.4) can flag against in real time.
6. **How much volatility risk premium does the screen pay?** Implied vol
   sits above subsequent realized most of the time (Kakushadze & Serur
   §7.4; Carr & Wu 2009; Bakshi & Kapadia 2003) — that premium is the
   headwind S1 fights on every roll, and the platform currently never
   measures it. The sharper form of the question reframes the screen
   itself: rank not by raw sensitivity but by **sensitivity per unit of
   VRP paid**, which is the precise definition the README's
   "cheapness-adjusted variants" has so far been gesturing at.
7. **Is the sensitivity screen finding cheap tails, or just buying beta
   expensively?** Index implied vol runs systematically above the vol
   implied by its own constituents plus their correlations (Kakushadze &
   Serur §6.3; Driessen, Maenhout & Vilkov 2009) — the correlation risk
   premium. Single-name OOM puts do not carry it, so they *should* be
   cheaper per unit of tail than an index put; but in a crash correlations
   go to one, which is exactly what the index put is being paid for. The
   decisive test is a like-for-like backtest of the screened basket
   against the SPY put that costs the same premium. If the basket does not
   beat it, S1's edge is a correlation short wearing a sensitivity
   screen's clothes, and that is a finding worth having early.
8. **Can the bleed be financed without giving up the tail?** The naked
   long put is one point in a family (Kakushadze & Serur §2.9, §2.19,
   §2.21, §2.37). A put ratio backspread — short one near-ATM put, long
   two further OTM — is often structured at zero or negative net debit and
   keeps convexity, and a calendar/diagonal sells shorter-dated puts
   against a longer-dated one. Whether any of these survives the screen's
   ranking *after* costs is an open question, and it is the question the
   Carry Budget (`docs/adr/0021`) exists to make answerable.

---

## 5. Milestone ordering (how the agent should read this document)

Roughly, in the order increments should be picked (later milestones assume
earlier ones exist; within a milestone, order is discretionary):

1. **Walking skeleton** (done — `docs/adr/0011`): one dataset, one metric,
   one tile, bronze→silver→gold, deployed.
2. **Fill out the six core datasets** (§2.1): one adapter + contract at a
   time, each following the VIX adapter's pattern.
3. **Grow the metric library** (§4 Q1 depends on this): each new metric in
   `research/metrics/`, pinned by a synthetic-case test, wired into the
   backtest comparison.
4. **Build out the cockpit tiles** (§1.1–1.5) one at a time, each backed by
   a gold mart + API endpoint.
5. **Point-in-time constituents** (§2.3) — closes the survivorship-bias gap.
6. **Event-pattern research** (§4 Q3, Q5) — feeds the event calendar's
   proximity flags with actual historical pattern data, not just dates.
7. **Conventional strategy tab** (§1.6) — kept deliberately last among
   cockpit work; it is secondary by design.
8. **Deferred datasets** (§2.2) — picked up opportunistically as they
   unblock a specific research question, not for completeness.
9. **Real-quote backtest pricing** (§3 v2) — gated on the `HUMAN_TODO.md`
   budget decision; the agent proposes the plumbing (the second
   `OptionPricer` implementation, the re-run of §1.5) but the paid data
   source itself is a human action.

This ordering is a default, not a straitjacket — the agent should still
apply the value bar (`docs/AGENT_MISSION.md`) to whatever it picks next,
and a smaller, sharper increment out of order beats a large one strictly
in order.

<!-- verification probe for docs/adr/0023 agent-review; delete with the branch -->
