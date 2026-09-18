# 26. Paretan tail pricing is relative, and is not an `OptionPricer`

Date: 2026-09-18

## Status

**Proposed.** `docs/adr/0001` allows the agent to propose an ADR but not to
accept its own proposal; this one is accepted by a human merge.

Amends `docs/adr/0004` (model-priced backtest, pluggable pricing) without
superseding it: the `OptionPricer` ABC stands and Black-Scholes remains the
default pricer. Nothing in `docs/adr/0018` changes — `MODEL_PRICED_MAX_MONEYNESS_PCT`
is untouched.

## Context

`docs/MODEL_RESIDUAL.md` measures our Black-Scholes-at-VIX pricer **underpaying**
for puts by +1.34 %/yr at 5 % OTM and **+2.71 %/yr at 10 % OTM**, and concludes
"strike depth dominates, which is what a missing volatility skew looks like."
`docs/adr/0018` quantifies the same thing as a market/model premium ratio of
1.42x at 5 % OTM, 7.38x at 10 %, 180x at 15 % and **21,663x at 20 %** — at which
depth the model "does not merely misprice it, it reports that the option is
free." The response so far has been one hardcoded cutoff.

Taleb, Yarckin, Mann, Delic & Spitznagel, *Tail Option Pricing Under Power Laws*
(arXiv 1908.02347v3, rev. March 2023) gives a different construction. Take one
option that the market actually quotes, assume the tail beyond it is strong
Pareto, and extrapolate to deeper strikes with the tail index `alpha` as the
only parameter. No mean, no volatility, no scale — and no requirement that
variance be finite.

That is worth having as a **second opinion beside** the Black-Scholes number,
because the two disagree most exactly where this platform's thesis lives.

## Decision

**1. Paretan tail pricing lives in `research/surface/` and does not implement
`OptionPricer`.**

The ABC prices a contract from state: `price_put(*, spot, strike, t_years, r,
sigma, q)`. The Paretan heuristic needs `(anchor_strike, anchor_price, alpha,
spot)` and has no `sigma`, no rate and no clock. Those parameters are *absent
from the model*, not merely unused, so an implementation that accepted and
discarded them would not be a subtype.

It is also not merely theoretical. `skew.implied_vol_put` brackets its bisection
on `price_put(sigma=IV_MIN) <= price <= price_put(sigma=IV_MAX)`. Given a
sigma-independent pricer that bracket collapses to `C <= price <= C`: measured,
it returns `None` for every price except exactly `C` (the correct refusal), and
for that one price it returns `IV_MIN = 0.01` — a fabricated implied vol rather
than a refusal, on the self-round-trip path that a ladder would actually take.

`greeks_put` reinforces it. `vega`, `rho` and `theta` are **undefined** under
this model, not zero: there is no sigma, no rate and no clock to differentiate
against. Returning `PutGreeks(vega=0.0, ...)` would assert that a tail option has
no vega, which is a lie about the one instrument this platform exists to study.

Consequence: `ParetanTail` is its own frozen value type. `ARCHITECTURE.md`'s
reserved `research/pricing/` path is for `OptionPricer` implementations and is
deliberately not used.

**2. Everything is relative to an anchor. Today that is a rule, not yet an
invariant.**

The paper is explicit: "our approach isn't about absolute mispricing of tail
options, but relative to a given strike closer to the money." `put_ratio` and
`call_ratio` eliminate `l` and `lambda` entirely, which is the algebraic form of
that claim.

**But `ParetanTail.put_price` will return an absolute number to anyone who
constructs a tail directly, with no anchor in sight**, and calibrating through
`anchor_l_*` is a convention rather than the only door. Making the anchor a
required argument on every entry point is the job of the `Anchor` type in
`research/surface/ladder.py`, which does not exist yet (`AGENT_TODO.md`). Until
it does, this section is a rule a reviewer has to enforce, and saying otherwise
would be exactly the kind of unearned "enforced" claim this ADR exists to
prevent elsewhere. The same caveat applies to §5's "only sanctioned input":
`hill_alpha` accepts any positive sequence, and nothing stops a caller passing
log losses.

**3. The sign convention is `(S_0 - K)`, and the paper's printed put formula is
not what we implement.**

The paper's Put Pricing result prints

    P(K2)/P(K1) = [(K2-S0)^(1-a) - S0^(1-a)((a-1)K2 + S0)] / [ ... at K1 ]

which subtracts `price^(2-a)` from `price^(1-a)` — dimensionally inconsistent —
and raises `(K - S_0)` to a non-integer power when `K < S_0`, which in Python
yields a **complex** number from the float operator, **nan** from numpy, and
raises only under `math.pow`. Two of those three fail silently.

Re-deriving from the paper's own construction (`S = (1-r)S_0`, `r` Pareto on
`[l, 1]` because the underlying cannot go below zero, which is what `lambda`
renormalises) gives

    P(K) = lambda * l^a * [ S_0^a (S_0-K)^(1-a) - (a-1)K - S_0 ] / (a-1)
    lambda = 1 / (1 - l^a)

so the printed `S_0^(1-a)` should be `S_0^(-a)`.

**The discriminator is the boundary condition `P(0) = 0`** — a put struck at zero
can never pay. The derived form gives `S_0^a * S_0^(1-a) - S_0 = 0` exactly. The
printed form is not zero at `K = 0` for any `S_0`: verbatim it gives **-0.0099**
at `S_0 = 100, a = 3`, and **-9900** once rescaled into this module's
normalisation. Do not "correct" the implementation back to the transcription:
`tests/test_research_surface_paretan.py::test_zero_strike_put_is_worthless` is
the guard, and three further independent checks agree, each of them runnable —
quadrature of the defining put integral across a **144-point** grid
(`test_put_price_matches_the_defining_integral`), quadrature of the *call*
integral reproducing the paper's published call formula
(`test_call_price_matches_the_defining_integral`), and **the paper's own `.tex`,
which carries the corrected `S_0^(-alpha)` exponent in a commented-out line
immediately below the printed one** (it also reverses the term order and carries
a `(-1)^(1-alpha)` prefactor, which cancels in the ratio).

There is no normalised-`S_0` escape: the paper's call formula scales exactly
under `(S_0, K) -> (10 S_0, 10 K)`, its `l`-calibration is scale-invariant, and
even the arbitrage bound's `log^2 K + log^2 S_0` recombines into `log^2(K/S_0)`.
`S_0^(1-a)` is a typo, not a convention.

Two further notes for anyone re-deriving this:

* `P(0) = 0` holds in exact arithmetic but **not in IEEE754**: the shape is a
  difference of two quantities equal in exact arithmetic, so for many
  `(alpha, S_0)` it leaves a residual of a few ulps, and for some it is
  *negative*. So `put_price` clamps at zero and the boundary test asserts a
  tolerance rather than equality — demanding equality would fail on correct
  code. Both halves are pinned: `test_zero_strike_put_is_worthless` and
  `test_the_zero_strike_clamp_actually_clamps`, the latter on the
  parameterisations where the raw value really is negative.
* The truncation is a **modelling choice**. A *censored* reading (bankruptcy mass
  at `S = 0`, limited liability) gives `P_cens(K) = l^a [g/(a-1) + K]` and also
  satisfies `P(0) = 0`. The boundary condition separates the derived form from
  the transcription and from the untruncated form, but not from the censored
  one. We implement the paper's truncation; `lambda` is its fingerprint.

**4. `lambda` is negligible; the term it brings with it is not.** Dropping
`-(a-1)K - S_0` moves `P(80)/P(90)` from 0.23045 to 0.25000 at `S_0 = 100,
a = 3` — 8.5 % of the correct value.

**5. Tail indices are fitted on arithmetic returns only.**

The paper proves that if `S` is in `RV_alpha` then `log(S/S_0)` is not. The
arithmetic return is, and carries the same `alpha` — the tail index is scale-free
— so `research/surface/returns.loss_magnitudes` is the only sanctioned input to
a Hill fit here. `put_roll.py:232`'s log returns feed `trailing_realized_vol`,
which is a volatility estimate and correct as it stands; it is **not** a
precedent for a tail fit.

Note the direction of the discrepancy is a property of *which* tail is
transformed, not of the theorem: for down-moves `-log(1-r)` is unbounded while
`r` cannot exceed 1, so the log basis reads a **fatter** tail (lower alpha), not
a thinner one. What is constant is that the two bases estimate different objects.

**6. "Scale-free" does not mean "aggregation-invariant".** `alpha` is invariant
under multiplication by a constant, and `hill` has a property test proving it.
It is **not** invariant under temporal aggregation. Aggregate iid Pareto daily
losses to a 14-, 30- or 90-day horizon and re-estimate, and the reading climbs
steeply with the horizon — by a factor of several even at 30 days. The tail
index of a sum is asymptotically `alpha`, so this is not a contradiction:
convergence is simply far slower than any `k` a practitioner can reach. The
effect is large and robust to the construction; the exact figures are not, and
depend on the aggregation convention (multiplicative vs additive), the sample
size and the choice of `k`, so no specific numbers are quoted here. **Anything
comparing an option-implied `alpha` at a 14-90 day horizon against one measured
on daily moves must horizon-match, or the difference is an artefact of
aggregation rather than a measurement.** Any comparison of an option-implied `alpha` at some horizon against a
`alpha` measured on daily moves must horizon-match, or the difference is an
artefact of aggregation rather than a measurement of anything.

**7. A Hill plateau is not evidence of a tail.** `stable_k` takes
`min_threshold`, and callers pass the Karamata onset. A Hill plot flattens wherever the
log-log slope is locally constant, and for a lognormal-ish *body* that happens
too — so without the gate a plateau-finder returns a body slope with every
appearance of stability. `tests/test_research_surface_karamata.py` demonstrates both directions on
samples whose structure is known by construction: the gate keeps a genuine tail
plateau, and rejects one that lies in the body.

## Consequences

* A second, independent, market-anchored basis exists beside Black-Scholes. It
  is additive: no existing number on the cockpit changes.
* The platform has its first fat-tail machinery — a Hill estimator, a Karamata
  onset, and a return-basis check. There was none before.
* `mpmath` joins the dev dependencies, test-only. It is required because on the
  defining integral at `alpha = 8` near the domain edge (`r^(-alpha-1)` over a
  ~1e27 dynamic range) `scipy.integrate.quad` returns **2.24e-11 against a true
  7.33e-11** — it misses **70 %** of the answer and emits no warning. A
  reference built on it would condemn correct code.
* Two things this ADR deliberately does **not** authorise, each needing its own
  decision: making Paretan a `PricingBasis` the roll engine can run on (amends
  0004 further), and replacing `MODEL_PRICED_MAX_MONEYNESS_PCT` with a measured
  Karamata onset (supersedes 0018). Both are recorded in `AGENT_TODO.md`.
