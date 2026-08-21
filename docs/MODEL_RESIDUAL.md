# The model-vs-market residual

**The number this platform exists to produce.** Every Put Lab premium is a
*model* price (`docs/adr/0004`). This file reports how far that model sits
from what the option actually cost, measured against Cboe's own published
put-protection indices.

Regenerate with **`make residual`** — read-only, no lake writes, safe for the
daily agent to run. The harness is
`src/tail_lab/research/backtest/index_replication.py`; the caveats that come
with every figure below are in its module docstring and are not repeated here.

*Measured 2026-08-22 against bronze `cboe_strategy` + `vix`, Black-Scholes
pricer, VIX as the volatility input, q = 1.9%, r = 4%.*

## Headline

| | PPUT (monthly) | PPUT3M (quarterly) |
|---|---|---|
| Window | 1990-01 → 2026-07, **438 rolls**, 36.5y | 2004-03 → 2026-06, **89 rolls**, 22.2y |
| Correlation | **0.9913** | 0.9812 |
| Tracking error | 1.68 %/yr | 2.89 %/yr |
| Model CAGR | +9.11 %/yr | +9.07 %/yr |
| Actual index CAGR | +7.77 %/yr | +8.65 %/yr |
| **Residual** | **+1.34 %/yr** | **+0.43 %/yr** |
| Mean premium | 0.558% of spot per roll | 1.636% per roll |

**A positive residual means our model prices the put too cheap.** The
replication earns more than the real index because it pays less for its
protection than the market charged.

Scale: PPUT's puts cost ~6.7%/yr of notional, so a +1.34%/yr residual is the
model underpaying by roughly **a fifth of the premium**. That is not a
rounding error, and it is exactly the direction that would flatter every
tail-hedge backtest this platform runs.

## The residual is not a constant — it flips sign by regime

This is the finding that matters, and the reason a single calibration
multiplier would be the wrong fix:

| Regime (VIX at entry) | Rolls | Residual | TE |
|---|---|---|---|
| calm (< 17) | 210 | **+1.55 %/yr** | 0.89% |
| elevated (17–28) | 185 | **+1.65 %/yr** | 1.79% |
| **crisis (≥ 28)** | 43 | **−1.46 %/yr** | 3.28% |

In calm and elevated markets the model underpays for puts. **In crisis it
overpays.** A constant fudge factor would fix the first two and make the
third worse.

The mechanism is skew, and it is the leading explanation for both the level
and the flip. `sigma` is the VIX — a ~30-day ATM implied vol — but the option
being priced is 5% OTM, where implied vol is *higher*. In calm markets the
skew is steep, so VIX understates the OTM put's vol and the model underpays.
In a crisis the surface flattens as ATM vol catches up, VIX overshoots the
5% OTM strike, and the model overpays.

**Tenor supports the same reading.** PPUT3M's residual (+0.43%/yr) is a third
of PPUT's. A 5% OTM strike three months out is far closer to the money in
standard deviations than the same strike one month out, so it sits nearer the
part of the surface VIX actually describes.

## How much of this is the dividend assumption?

`SPXT` is not free (HTTP 403), so the equity leg's dividends come from a flat
assumed yield. Moving it ±0.5pp moves the headline:

| Assumed yield | PPUT residual | PPUT3M residual |
|---|---|---|
| 1.4% | +0.88 %/yr | +0.01 %/yr |
| **1.9%** (default) | **+1.34 %/yr** | **+0.43 %/yr** |
| 2.4% | +1.81 %/yr | +0.84 %/yr |

**Read the sign, not the third decimal.** PPUT's residual stays firmly
positive across the plausible range; PPUT3M's does not — at a 1.4% yield it
is indistinguishable from zero. Any claim resting on PPUT3M's *level* is
resting on the dividend assumption.

## What would move these numbers

- **A skew-aware pricer** (queued in `AGENT_TODO.md`, needs `SKEW` in the
  lake, which needs the vol complex finished). This is the direct test: if
  skew is the mechanism, a VIX+SKEW pricer should shrink the calm/elevated
  residual *and* the crisis flip together. If it shrinks only one, the story
  is wrong.
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
