"""Retail brokerage / transaction-cost model for the Put Lab roll engine.

Each roll BUYS ``contracts`` (share-equivalent) of one OOM put and holds it to
expiry, where it settles at intrinsic value — there is no exit trade, so the
entire trading cost is paid at *entry*: a per-contract commission plus the
bid-ask half-spread you pay by lifting the ask above mid. Both are calibrated
to realistic US retail options figures (2024-2026), so a backtest's net return
reflects what a retail account would actually keep — and, importantly,
less-frequent strategies get the advantage they deserve, because a frequent
short-tenor roll pays these entry costs far more often (and on cheaper, more
share-heavy, less liquid contracts).

Commission
----------
``$0.65 per contract`` is the standard retail options rate across the major US
brokers (Schwab/thinkorswim, Fidelity, E*TRADE, and Interactive Brokers' fixed
tier all quote ~$0.65/contract). One listed option contract = **100 shares**.
This engine's ``contracts`` is share-equivalent (its premium is a *per-share*
model price), so the number of real contracts is ``contracts / 100`` and the
commission is ``0.65 * (contracts / 100)``. This is the dominant frequency
penalty: a short-dated deep-OOM put is cheap, so a fixed premium budget buys a
huge ``contracts`` count → many real contracts → large commission, and short
tenors also roll many more times per year.

Bid-ask half-spread
-------------------
Retail buys at the ask, roughly half the quoted bid-ask spread above mid. The
quoted spread (as a fraction of the option premium) widens sharply as the tenor
shortens and the strike moves further out-of-the-money — both thin out open
interest and market-maker willingness to quote tight. Realistic magnitudes: a
liquid ~monthly, lightly-OOM equity put quotes a ~2-4% half-spread, while a
1-week, deep-OOM (~20%) put can quote 10-15%+. Modeled as a base plus a term
that grows as tenor shrinks, a term that grows with moneyness, and an
interaction term (illiquidity compounds when a put is *both* short-dated and
deep-OOM), clamped to a sane maximum. ``spread_cost = half_spread_frac *
notional`` — the extra fraction you pay on the premium budget spent that roll.
"""

from __future__ import annotations

#: Standard US retail options commission, dollars per real (100-share) contract.
COMMISSION_PER_CONTRACT = 0.65
#: Shares per listed option contract (this engine sizes in share-equivalents).
SHARES_PER_CONTRACT = 100.0

#: Half-spread model constants (fractions of the premium paid). See module
#: docstring for the calibration targets each term reproduces.
SPREAD_BASE_FRAC = 0.010  # a floor even for the most liquid monthly ATM-ish put
SPREAD_TENOR_COEF = 0.020  # grows as 1/tenor_weeks (short tenors quote wider)
SPREAD_MONEYNESS_COEF = 0.10  # grows with moneyness fraction (deeper OOM = wider)
SPREAD_INTERACTION_COEF = 0.30  # short AND deep OOM compounds illiquidity
SPREAD_MAX_FRAC = 0.25  # clamp: even an illiquid contract isn't a 50% spread
#: Tenor floor (weeks) in the 1/tenor term, so a near-zero tenor can't blow up
#: the spread (~1 trading day); the engine already requires tenor_weeks > 0.
_TENOR_WEEKS_FLOOR = 0.2


def half_spread_frac(tenor_weeks: float, moneyness_pct: float) -> float:
    """Bid-ask half-spread as a fraction of the option premium, for a put of
    tenor ``tenor_weeks`` struck ``moneyness_pct`` percent OOM.

    Increasing as the tenor shrinks and the strike moves further OOM (both
    reduce liquidity), with an interaction term so a short-dated deep-OOM put —
    the least liquid corner — is the most expensive. Clamped to
    ``SPREAD_MAX_FRAC``. Realistic magnitudes (see module docstring): ~2-4% for
    a liquid ~4-week lightly-OOM put, ~10-15% for a 1-week 20%-OOM put.
    """
    tw = max(float(tenor_weeks), _TENOR_WEEKS_FLOOR)
    m = float(moneyness_pct) / 100.0
    frac = (
        SPREAD_BASE_FRAC
        + SPREAD_TENOR_COEF / tw
        + SPREAD_MONEYNESS_COEF * m
        + SPREAD_INTERACTION_COEF * m / tw
    )
    return float(min(frac, SPREAD_MAX_FRAC))


def roll_cost(
    contracts: float,
    notional: float,
    tenor_weeks: float,
    moneyness_pct: float,
    *,
    commission_per_contract: float = COMMISSION_PER_CONTRACT,
    spread_scale: float = 1.0,
) -> float:
    """Total entry brokerage cost of one bought-put roll (dollars).

    ``commission = commission_per_contract * (contracts / 100)`` (share-equiv →
    real contracts) plus ``spread_cost = spread_scale * half_spread_frac(...) *
    notional`` (the half-spread paid on the premium budget). ``spread_scale``
    and ``commission_per_contract`` exist so a test can run the same backtest
    cost-free (both zero) to isolate the cost drag. Returns a non-negative
    figure.
    """
    commission = commission_per_contract * (float(contracts) / SHARES_PER_CONTRACT)
    spread_cost = spread_scale * half_spread_frac(tenor_weeks, moneyness_pct) * float(notional)
    return float(commission + spread_cost)
