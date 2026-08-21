# The model-vs-market residual

**The number this platform exists to produce.** Every Put Lab premium is a
*model* price (`docs/adr/0004`). This file reports how far that model sits
from what the option actually cost, measured against Cboe's own published
put-protection indices.

Regenerate with **`make residual`** — read-only, no lake writes, safe for the
daily agent to run. The harness is
`src/tail_lab/research/backtest/index_replication.py`; the caveats that come
with every figure below are in its module docstring and are not repeated here.

*Measured 2026-08-22 (PPUT3M re-measured after the strike correction below) against bronze `cboe_strategy` + `vix`, Black-Scholes
pricer, VIX as the volatility input, q = 1.9%, r = 4%.*

## Headline

| | PPUT — monthly, **5% OTM** | PPUT3M — quarterly, **10% OTM** |
|---|---|---|
| Window | 1990-01 → 2026-07, **438 rolls**, 36.5y | 2004-03 → 2026-06, **89 rolls**, 22.2y |
| Correlation | 0.9913 | **0.9972** |
| Tracking error | 1.68 %/yr | **0.98 %/yr** |
| Model CAGR | +9.11 %/yr | +11.36 %/yr |
| Actual index CAGR | +7.77 %/yr | +8.65 %/yr |
| **Residual** | **+1.34 %/yr** | **+2.71 %/yr** |
| Mean premium | 0.558% of spot per roll | 0.713% per roll |

**Read PPUT3M's strike carefully.** Cboe markets it as the "S&P 500 Tail Risk
Index", which hides that it is the quarterly sibling of PPUT struck at **10%**
OTM, not 5%. This file first published a +0.43%/yr residual for it, computed
against a 5% strike — the wrong strategy. Correcting the strike moved
correlation from 0.9812 to **0.9972** and tracking error from 2.89%/yr to
**0.98%/yr**, which is itself the strongest evidence the corrected rule is the
right one: the replication now tracks PPUT3M *better than* it tracks PPUT.

**A positive residual means our model prices the put too cheap.** The
replication earns more than the real index because it pays less for its
protection than the market charged.

Scale: PPUT's puts cost ~6.7%/yr of notional, so a +1.34%/yr residual is the
model underpaying by roughly **a fifth of the premium**. That is not a
rounding error, and it is exactly the direction that would flatter every
tail-hedge backtest this platform runs.

## The residual doubles as the strike goes deeper

| Program | Strike | Tenor | Residual |
|---|---|---|---|
| PPUT | 5% OTM | monthly | +1.34 %/yr |
| **PPUT3M** | **10% OTM** | quarterly | **+2.71 %/yr** |

This is the cleanest evidence for the skew explanation in the file, and it
arrived by accident. The two programs differ in both strike and tenor, and
the longer tenor should *shrink* the residual (a 10% strike three months out
is fewer standard deviations from the money than a 5% strike one month out).
The residual doubles anyway. Strike depth dominates, which is what a missing
volatility skew looks like.

**It also changes how the number should be used.** Quoting the 5%-OTM figure
at a 20%-OTM tail hedge — the deep end of the Put Lab's sweep, and the part
of the surface the S1 thesis is actually about — understates its optimism by
at least half. The accuracy panel therefore measures each run against
whichever published program sits closer to its strike and tenor.

## The residual is not a constant — it flips sign by regime

This is the finding that matters, and the reason a single calibration
multiplier would be the wrong fix:

| Regime (VIX at entry) | PPUT rolls | PPUT residual | PPUT3M residual |
|---|---|---|---|
| calm (< 17) | 210 | **+1.55 %/yr** | +2.56 %/yr |
| elevated (17–28) | 185 | **+1.65 %/yr** | +2.90 %/yr |
| **crisis (≥ 28)** | 43 | **−1.46 %/yr** | +2.89 %/yr |

In calm and elevated markets the model underpays for puts. **At 5% OTM it
overpays in a crisis.** A constant fudge factor would fix the first two cases
and make the third worse.

**The flip is a near-the-money phenomenon, not a crisis phenomenon.** PPUT3M's
10%-OTM residual stays firmly positive through crises. That is consistent and
informative: a crisis flattens the surface, so VIX catches up to and overshoots
a 5% strike, but even a flattened surface still prices a 10% strike above ATM
vol. Any explanation of the residual has to produce both facts.

The mechanism is skew, and it is the leading explanation for both the level
and the flip. `sigma` is the VIX — a ~30-day ATM implied vol — but the option
being priced is 5% OTM, where implied vol is *higher*. In calm markets the
skew is steep, so VIX understates the OTM put's vol and the model underpays.
In a crisis the surface flattens as ATM vol catches up, VIX overshoots the
5% OTM strike, and the model overpays.

**Strike depth supports the same reading, and outweighs tenor.** See the
strike table above: doubling the strike depth roughly doubles the residual
even though the longer tenor pushes the other way.

## Skew explains it — measured, not argued

*Added 2026-08-22, from 210 roll dates of **real SPY quotes**, 2008-01 to
2025-11 (`make skew`, `research/skew.py`).*

Everything above this section is an *argument* that skew causes the residual.
With real historical quotes in the lake the same put can be priced twice on the
same day — once by the model at the VIX, once by the market — which turns the
argument into a number.

| Strike | VIX (model input) | Market IV | Gap | Median market/model premium | Annualized underpayment |
|---|---|---|---|---|---|
| 5% OTM | 19.8% | **22.0%** | **+2.2 vol pts** | 1.42× | **+1.57 %/yr** |
| 10% OTM | 19.8% | **27.0%** | **+7.2** | 7.38× | **+2.05 %/yr** |
| 15% OTM | 19.8% | 32.4% | +12.6 | 180× | +1.51 %/yr |
| 20% OTM | 19.8% | **38.0%** | **+18.2** | **21,663×** | +1.01 %/yr |

**At 5% OTM, skew alone accounts for +1.57%/yr against a measured residual of
+1.34%/yr — the whole thing.** The pricing error is not *partly* skew with the
rest hiding in settlement conventions and the flat rate; skew slightly
overshoots, meaning the conventions net out marginally in the other direction.
The argument in this file is now closed.

Three things in that table deserve to be read slowly.

**The market charges 22% vol for a put the model prices at 19.8%.** That 2.2
vol-point gap is small enough to look like noise and large enough to be the
entire residual, because it is paid twelve times a year on every roll.

**At 20% OTM the model's price is essentially zero and the market's is not.**
A 21,663× median ratio is not a precision figure — it is the ratio of a real
premium to a Black-Scholes-at-VIX price that has rounded to nothing. This is
the deep tail, the part of the surface the S1 thesis is actually about, and a
flat-vol model does not merely misprice it: **it reports that the option is
free.** Any backtest of a 20%-OOM hedge priced this way is measuring the cost
of a lottery ticket the model thinks is being given away.

**The cost peaks in the middle, not at the extreme.** Annualized underpayment
rises from 5% to 10% OTM and then *falls* — at 20% OTM the ratio is
astronomical but the absolute dollars are small. So the most expensive place to
be wrong is moderate OTM, not the deep tail, even though the deep tail is where
the model is most embarrassingly wrong in relative terms.

**Caveat on the 10% row.** These quotes are ~30-day; PPUT3M is quarterly. The
+2.05%/yr is therefore not directly comparable to PPUT3M's +2.71%/yr residual,
and the gap between them is a tenor effect this measurement does not yet
isolate.

**Provenance.** The quotes are the licence-limited lambdaclass `data-v1`
extract (`docs/DATA_VERDICTS.md`, cleared in `HUMAN_TODO.md`), priced off
`(bid+ask)/2` — never the vendor's `mark` or its sentinel-filled IV column,
which is why implied vol is re-inverted here by bisection rather than read off
the file.

## How much of this is the dividend assumption?

`SPXT` is not free (HTTP 403), so the equity leg's dividends come from a flat
assumed yield. Moving it ±0.5pp moves the headline:

| Assumed yield | PPUT residual | PPUT3M residual |
|---|---|---|
| 1.4% | +0.88 %/yr | +2.22 %/yr |
| **1.9%** (default) | **+1.34 %/yr** | **+2.71 %/yr** |
| 2.4% | +1.81 %/yr | +3.21 %/yr |

**Read the sign, not the third decimal.** Both residuals stay firmly positive
across the plausible range, and the gap between them — the strike effect —
survives at every yield, which is why the strike finding above is safe to lean
on while the exact levels are not.

## What would move these numbers

- **A skew-aware pricer** (queued in `AGENT_TODO.md`). No longer a
  hypothesis-test but a *calibration target*: the measurement above says
  exactly how many vol points it has to add at each strike (+2.2 at 5%, +7.2
  at 10%, +18.2 at 20%). Success is `make residual` moving toward zero after
  `make skew`'s gaps are priced in. It should shrink **PPUT3M's +2.71%/yr by
  more than PPUT's +1.34%/yr** and close the 5%-OTM crisis flip.
- **A real rate curve.** A flat 4% across a window where rates ran 0%–6.5%
  misprices the discount leg, especially for the quarterly program.
- **AM settlement.** Bronze carries closes only. Measured cost of this exact
  substitution on real quotes: correlation 0.9927 → 0.9847, TE +0.67%/yr
  (`docs/DATA_VERDICTS.md`).
- **Real quotes.** The licence-gated lambdaclass chains would let the
  2008–2025 residual be decomposed into "model vs market IV" and "everything
  else". Blocked on `HUMAN_TODO.md`, and deliberately not a dependency of
  this harness.

## Reading the year table

`make residual` prints a per-year breakdown. Two things to expect in it:

- **2020 reads +9.74 %/yr.** March 2020 combined a violent monthly roll with
  the close-vs-SOQ substitution above. It is the single worst year in the
  series and it is mostly convention, not pricing.
- **Early-1990s years are noisy and occasionally negative.** Fewer strikes,
  wider markets, and a VIX still in its first years.
