# 18. Bound the strategy search to strikes the pricer can price

Date: 2026-08-22

## Status

Accepted

## Context

The fragility ranking reports each name's **best** cell — the argmax of its own
strike × tenor sweep — and a click lands the workspace on exactly those
parameters. That headline was an unconstrained maximum over the grid.

`docs/MODEL_RESIDUAL.md` had already measured why that is a problem. Pricing the
same puts twice over real historical quotes, once by our Black–Scholes-at-VIX
model and once by the market, gives a median market/model premium ratio of:

| Strike | Model IV | Market IV | market/model premium |
|---|---|---|---|
| 5% OTM | 19.8% | 22.0% | 1.42× |
| 10% OTM | 19.8% | 27.0% | 7.38× |
| 15% OTM | 19.8% | 32.4% | 180× |
| 20% OTM | 19.8% | 38.0% | **21,663×** |

In that document's own words, past ~10% the flat-vol model "does not merely
misprice it — **it reports that the option is free**."

Every backtest here fixes the premium **budget** rather than the contract count
(`total_premium = n_cycles × notional`). So a premium that rounds toward zero
buys an absurd number of contracts, and any payoff that does land is inflated by
the same factor. The unconstrained argmax is therefore **systematically drawn to
the deepest strike on the grid** — not because that strike has the best
convexity, but because that is where the pricer is most wrong.

This was not hypothetical. On the live 35-name universe, of the top eight names
by fragility, seven had their headline at 12% OOM or deeper, and the two leading
names — COIN and TSLA — sat on 22%, the deepest column that existed. TSLA's
headline read **+79.3%/yr**, a number produced almost entirely by the model
handing out 22%-OOM puts for nothing.

A second, unrelated flatterer surfaced while widening the universe.
`annualized_return` divides a total ROI by the **requested** lookback, not by
the window actually traded. A name listed two years ago therefore has its
two-year loss annualized as if over four, which shrinks it toward zero — so the
most recently-listed names would arrive at the top of a ranking sorted on that
number, purely for not having been around long enough to bleed.

## Decision

**The strike depth at which the model stops producing a price is a named
constant**, `MODEL_PRICED_MAX_MONEYNESS_PCT = 10.0`, in
`research/backtest/sweep.py` beside the grid it qualifies. 10% is where the
measured market/model ratio is still within one order of magnitude and the
deepest strike whose return is a measurement rather than an artefact.

This is deliberately **not** `accuracy.applicability`. That asks "does the
measured residual of a published Cboe program describe this run" — a distance in
parameter space. This asks "is the model's premium a price at all at this depth"
— a property of the pricer. They are different questions and are kept as
different code.

**`best_point` is bounded to that band by default.** The ranking headline, the
click-through target, and the Recommendations view can therefore never advertise
a strategy whose return is a pricing artefact. `priced_only=False` still gives
the raw grid maximum for anything that wants it.

**The grid itself goes deeper, not shallower** — out to 30% OOM. Seeing the deep
tail is the point of a tail-hedge lab, and hiding the region would be its own
kind of lie. Those cells are shown, hatched, with the reason stated. The
served `SweepResponse` carries the threshold so the client marks them from one
source of truth rather than a second copy of the number.

**A name must cover the window being asked about to be ranked**
(`ranking.MIN_WINDOW_COVERAGE = 0.9`), or it is dropped rather than compared
against names that do.

**Every `best_*` field on `RankedAsset` is measured at the same cell.** The
ranking runs one extra roll at the winning parameters so that a recommendation
row is one strategy rather than an argmax return beside a screened-cell hit
rate.

## Consequences

- Headlines moved. Names whose old best sat at 15–22% now report a shallower,
  smaller, real number. That is the point: the previous figure was not
  conservative-and-wrong, it was optimistic-and-wrong.
- The ranking sweeps only the priced strikes, since `best_point` can never
  return anything else. Across the widened universe this roughly halved the
  cost of the most expensive endpoint (measured: 66s → 36s locally for 70
  names) and paid for most of the universe expansion.
- **Raising the constant is a claim about the pricer, not a config tweak.** The
  thing that would justify it is the skew-aware `OptionPricer` queued in
  `AGENT_TODO.md`, which now has an exact calibration target from the same
  measurement: +2.2 vol points at 5% OTM, +7.2 at 10%, +18.2 at 20%. When
  `make skew`'s gaps are priced in, this bound should move outward on evidence.
- The universe grew to 70 names (`contracts/options_calendar.py`) — the
  commodity complex and the producers levered to it, energy, the AI build-out
  through to the power feeding it, crypto-adjacent equity, international, every
  GICS sector, and the cyclicals that break first. Breadth is what
  `docs/adr/0008` ("screen broad, trade narrow") asks for, and the bounded
  argmax is what makes a wider screen safe to rank on.
- IBIT and ARM are deliberately excluded from that universe for want of
  history, and no leveraged or inverse ETF is eligible at all — their path
  dependence makes a rolled-put backtest meaningless.
- A new **Recommendations** view ranks each name's best strategy against every
  other's and states it as an instruction (ticker, strike, roll cadence). It is
  labelled a screen and not advice, in sample, on the page — `docs/adr/0007`'s
  wall means this platform never places a trade, and a page with that title is
  the single most misreadable surface here.
