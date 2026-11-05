"""The surface's coordinates: the forward, log-forward moneyness ``k`` and total
implied variance ``w`` (``docs/adr/0036``).

Every other module here measures moneyness as ``K / S``. A smile is not
comparable across tenors in that unit -- the forward drifts away from spot by
``(r - q) T`` -- and its slope is per dollar of strike, so it is not comparable
across names either. In ``k = ln(K / F)`` the at-the-money point is ``k = 0`` at
every tenor, and the smile's shape (skew, curvature, wing slopes) is the
quantity a regime moves.

**Two forwards, in order of preference.**

1. *Parity*, from the call wing: ``F = K + e^{rT} (C - P)`` at strikes quoted on
   both sides. It needs no dividend assumption -- which matters here, because
   production has no ``tiingo_eod`` snapshot and every carry forward would be
   at ``q = 0``. It is a **European** identity applied to American options, so
   it is biased low; an American binomial check (S=100, r=4.3%, q=1.3%,
   sigma=18%) put it 1-3 bp low at 30 days and 10-19 bp low at six months,
   varying by strike. The strike-disagreement tolerance therefore grows with
   ``T`` (:func:`parity_tolerance`).
2. *Carry*, ``F = S e^{(r - q) T}``: the only option for a session without the
   call wing (everything before 2026-10-27).

A call's implied vol is read through the same put inverter: under the European
identity a call at ``K`` is a put at ``K`` plus ``e^{-rT}(F - K)``, so no second
solver exists to drift from the first.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tail_lab.research.skew import implied_vol_put

ForwardSource = Literal["parity", "carry", "carry_q0"]

#: How many strikes nearest the carry forward vote on the parity forward.
PARITY_STRIKES = 3
#: Relative disagreement allowed between those strikes' forwards, as
#: ``base + per_year * T``. The per-year term covers the American bias's spread
#: across strikes (~8 bp at six months in the tree check); the base covers mid
#: noise from the half-spread at ATM (a few bp on a liquid name).
PARITY_TOLERANCE_BASE = 0.0020
PARITY_TOLERANCE_PER_YEAR = 0.0040


@dataclass(frozen=True)
class Forward:
    value: float
    source: ForwardSource
    #: Why parity was not used, when it was not (``None`` for a parity forward).
    parity_refusal: str | None


def carry_forward(spot: float, *, t_years: float, r: float, q: float) -> float:
    return spot * math.exp((r - q) * t_years)


def parity_tolerance(t_years: float) -> float:
    return PARITY_TOLERANCE_BASE + PARITY_TOLERANCE_PER_YEAR * t_years


def parity_forward(
    pairs: Sequence[tuple[float, float, float]],
    *,
    t_years: float,
    r: float,
    near: float,
) -> tuple[float | None, str | None]:
    """The parity forward from ``(strike, call_mid, put_mid)`` pairs.

    Uses the :data:`PARITY_STRIKES` strikes nearest ``near`` (the carry
    forward) and their median. Returns ``(None, reason)`` rather than a number
    when fewer strikes than that are quoted on both sides, or when they
    disagree by more than :func:`parity_tolerance` -- a forward the strikes
    cannot agree on is not a measurement.
    """
    if len(pairs) < PARITY_STRIKES:
        return None, f"only {len(pairs)} strikes quoted on both sides"
    closest = sorted(pairs, key=lambda p: abs(p[0] - near))[:PARITY_STRIKES]
    growth = math.exp(r * t_years)
    forwards = sorted(k + growth * (call - put) for k, call, put in closest)
    median = forwards[len(forwards) // 2]
    if median <= 0:
        return None, "non-positive parity forward"
    spread = (forwards[-1] - forwards[0]) / median
    tolerance = parity_tolerance(t_years)
    if spread > tolerance:
        return None, f"strikes disagree by {spread:.4%} (tolerance {tolerance:.4%})"
    return median, None


def choose_forward(
    pairs: Sequence[tuple[float, float, float]],
    *,
    spot: float,
    t_years: float,
    r: float,
    q: float,
    q_known: bool,
) -> Forward:
    """Parity when the strikes agree, else carry (labelled ``carry_q0`` when
    the dividend yield is unknown and was taken as zero)."""
    carry = carry_forward(spot, t_years=t_years, r=r, q=q)
    parity, refusal = parity_forward(pairs, t_years=t_years, r=r, near=carry)
    if parity is not None:
        return Forward(parity, "parity", None)
    return Forward(carry, "carry" if q_known else "carry_q0", refusal)


def log_moneyness(strike: float, forward: float) -> float:
    return math.log(strike / forward)


def implied_vol(
    price: float, *, strike: float, forward: float, t_years: float, r: float, is_call: bool
) -> float | None:
    """Black implied vol of a European option on the forward, or ``None``.

    Priced through :func:`implied_vol_put` with spot ``F e^{-rT}`` and no
    yield, which is Black-76 on the forward. A call is first mapped to its
    parity-equivalent put.
    """
    discount = math.exp(-r * t_years)
    put_price = price - discount * (forward - strike) if is_call else price
    if put_price <= 0:
        return None
    return implied_vol_put(
        put_price, spot=forward * discount, strike=strike, t_years=t_years, r=r, q=0.0
    )
