# 27. A hedge works only if the book that pays for it beats the book that does not

Date: 2026-10-05

## Status

**Accepted.** Dio decided each point below on 2026-10-05, in a design
interview over `HUMAN_TODO.md`'s "externally funded or debited?" item and its
Kaggle-corpus item, and instructed the merge. Builds on `docs/adr/0004`
(model-priced backtests), `docs/adr/0021` (desk vocabulary) and the README's
time-average-growth principle; supersedes nothing.

## Context

`HUMAN_TODO.md` asked whether a hedge's premium is **externally funded** or
**debited from the equity sleeve**, because the answer decides what "the hedge
works" means. The two readings disagree on real data:

* `docs/PRIOR_ART.md` §12: `lambdaclass/options_portfolio_backtester`'s
  Spitznagel-style reconstruction (2008-2024 SPY) spends a **3.3%/yr premium
  budget injected from outside**, and reports +6.05pp/yr excess over SPY at
  that budget -- +6.07 in its own table, and +3.32pp in its 2017-2024
  out-of-sample half. It states that
  debiting the premium from the equity sleeve lowers excess ~2.5pp/yr "across
  all budgets" and that the strategy then no longer beats SPY -- a claim its own table appears not to support if "-2.5pp" means a drop (6.07 - 2.5 is still positive; at a 1%
  budget it is about zero, and only the 0.5% budget goes negative). Either way, the injected framing flatters the
  overlay by the premium it never pays.
* The Book tab (PR #138), on Cboe's real-quote programs through 2026-10-02 with
  the premium paid from the book: PPUT and PPUT3M **fail** to out-grow the
  unhedged S&P 500 at every hedge ratio, in the 2005-2016 window and over full
  history, at every tested dividend yield. VXTH (S&P 500 plus VIX calls, not
  puts) holds by +0.125pp/yr in 2006-2016 at the 1.9% assumed yield --
  inconclusive at 2.4% -- and is inconclusive (+0.005pp/yr) over its full
  history at 1.9%, holding at 1.4% and failing at 2.4%. All of this is on growth; on CAGR per unit of volatility an interior
  mix wins every window, the weaker test.

`sizing.py`'s `FixedPremium` / `WealthFraction` seam already takes one side or
the other per call, and no surface says which. The owner intends to trade on the platform's output, so every criterion here
has to be one a real account could be held to. (The agent itself never trades: `docs/adr/0007` stands -- every trade is
placed by Dio, by hand. `docs/adr/0019`'s separate paper executor is still
Proposed, not in force.)

## Decision

**1. The success criterion.** A hedge *works* when **the hedged book beats the
unhedged book with the premium paid from the book** (self-financed), measured
by **time-average growth** for a lump sum and by **money-weighted return
(IRR)** for a contributions plan. The two coincide for a lump sum and
diverge once cash flows are added (+100% then -50%, paying $100 before each
period: 0% time-weighted, -17.7% IRR); IRR is the right yardstick here
because both arms receive identical cash flows, so comparing their IRRs
ranks them exactly as their terminal wealth does. That is
the only accounting a live account can reproduce: nobody funds the carry for
it. "Beats SPY given someone else funds the carry" may be shown only as a
labelled comparison of the prior art's framing, never as the platform's claim.
The README states that current evidence **fails** this criterion for the
Cboe put overlays; the cockpit's job is to find the names, strikes, tenors or
regimes where it passes.

**2. Three accountings, each labelled where its result is shown.**

| Accounting | Where | Priced by |
|---|---|---|
| Self-financed: premium paid from the book | Book tab, lump sum and monthly contributions | Cboe real-quote programs |
| Contribution-funded: a chosen share of each month's `X` buys puts, payoffs reinvested in the S&P 500 | Book tab, monthly contributions, "model" source | Black-Scholes at VIX (below) |
| Standalone: a fixed premium budget, no book | Workspace backtests and sweep, Recommendations, Portfolio, Bake-off (existing) | the backtester's own model |

The Book tab gains a *Plan* switch: lump sum (the existing growth verdicts) or
monthly contributions (starting book `E0`, monthly `X`, hedge ratio).

**3. Contributions mode.**

* **Comparators:** S&P 500 total return, Cash (0%) and T-bills as buttons;
  other Cboe indices (each a real-quote, dividends-reinvested NAV) by picker,
  run on their shared history with the program (the model source compares
  against the S&P 500 only). Stocks and ETFs are not
  offered, and the page says why: bronze OHLCV is split-adjusted only, so a
  price-only series would understate their return. `LTV` is a quoted level,
  never a comparator.
* **T-bills use real rates or nothing.** The button is disabled, with the
  reason, until `rates` ingests (`AGENT_TODO.md`, `docs/PRIOR_ART.md` §19). A
  flat stand-in rate is forbidden here: bills paid ~0% through 2009-2015 and from mid-2020 through 2021, so a flat 4% would flatter "park it in bills" across most of the
  crisis-era windows.
* **Currency:** amounts are in US dollars, the indices' currency; the page says a
  euro investor's result also carries the EUR/USD move.
* **Headline metric:** money-weighted return (IRR) per arm, with terminal
  wealth and each arm's worst drawdown beside it. IRR is what reconciles
  against a real brokerage statement.
* **Prices:** Cboe's real-quote programs by default, parameterised by hedge
  ratio -- Cboe does not publish premium paid, so "spend $X on puts" has no
  real-quote input. The opt-in **model** source spends a chosen share of
  each month's $X on S&P 500 puts (on top of a starting book, which it
  requires -- with none the arm is a standalone put, the Workspace's job),
  5% or 10% OTM monthly only, priced by Black-Scholes **at the VIX plus the
  measured skew gap** for that depth (+2.2 / +7.2 vol points,
  `docs/MODEL_RESIDUAL.md`) at a flat 4% rate. The gap is added to the
  volatility, not applied as a multiple of the price: checked against 214
  real monthly SPY put mids 2008-2025 (`scripts/model_plan_calibration.py`),
  multiplying premiums by the median market/model price ratio (1.42x /
  7.38x) paid a median 4.8x the market at 10% OTM in stress and 0.23x in
  calm months (one roll: 30.6% of spot in October 2008 against a 3.9% mid,
  where the vol gap pays 4.9%), while the vol gap's median ratio is
  0.80-1.13x in every regime. The check used the licence-limited lambdaclass
  corpus offline, under the 2026-08-22 "use it, do not depend on it" call:
  it supplies an error bar, never a served number. The page states the residual error and its direction: calm-market puts are
  still underpriced by 16-20%, which flatters the puts. That figure was
  measured at the same flat 4% rate, so it already includes the rate error,
  which is largest in the zero-rate years (19% / 28% under in calm months
  there). Drawdowns are sampled at monthly rolls, so they read shallower
  than daily ones. Per `docs/adr/0004` a model-priced number is an estimate, not P&L
  truth, so the model source never serves as evidence for the success
  criterion; only the real-quote results do, and the model page says so
  beside its shares. Not `put_roll`: its
  realized-vol proxy floors at 6% and priced a calm-market 5% OTM SPY put at
  ~$0.03 against a ~$1 market. optionsDX stays off the live page.
* **Verdict across start dates:** the headline is the rolling-start
  distribution (the same plan begun in every month), with one chosen window as
  the illustration. The page states that overlapping windows are not
  independent, so the share is descriptive, not a test.

**4. Carry stays reserved.** `docs/adr/0021`'s Carry surface remains the risk
function -- the annual bleed of *recommended* positions against a budget --
and is not used for this historical test.

**5. Data.** The Kaggle SPY 2014-2025 chain corpus is an **offline
cross-check only** (gitignored, ingested year by year, never feeds a verdict
or the live page): a 2023 vendor comparison against optionsDX and the model
residual over 2024-25. Its Alpha Vantage provenance makes its licence too
uncertain for anything that will drive trades. A licensed real-quote source
(ORATS, `HUMAN_TODO.md`) is bought when the first trade-driving feature needs
2024+ real prices, or before the first live trade, whichever comes first.

## Consequences

* The platform's headline answer today is "the Cboe put overlays do not pay
  for themselves", and the README says so. That is a finding, not a defect.
* Every hedge result must say which accounting produced it; a number that
  does not is below the bar set by the README's "accuracy is surfaced"
  principle.
* T-bill comparisons ship disabled until `rates` is fixed, and stay real.
* New dependencies and datasets are judged as if the platform trades:
  Riskfolio-Lib was rejected on the same day for pulling a Commons-Clause
  dependency (`vectorbt`).
