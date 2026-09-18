"""Paretan tail option pricing -- relative prices for deep strikes from one
market quote and a tail index (Taleb, Yarckin, Mann, Delic & Spitznagel, *Tail
Option Pricing Under Power Laws*, arXiv 1908.02347v3, rev. March 2023).

Given the price of one "anchor" option at a strike where the market actually
quotes, and a tail index ``alpha``, this produces prices for every strike
further out. Nothing else is estimated: no mean, no volatility, no scale. All
the distributional information below the Karamata point is carried by ``l``, and
``l`` cancels out of every ratio.

Assumptions register, in ``option_pricer.py``'s style -- these are the things
that are wrong about this model, stated where someone using it will read them:

1. **Only ``alpha > 1`` is required. Finite variance is NOT.** This is the
   headline difference from the Black-Scholes modification class -- local
   volatility (Dupire), stochastic volatility, jump diffusion -- every member of
   which needs a finite second moment. A finite *first* moment is all that makes
   the integral converge. This is why the model is a different object and not a
   "better" ``OptionPricer``: see ``docs/adr/0026``.
2. **Everything here is relative to an anchor.** The paper is explicit: "our
   approach isn't about absolute mispricing of tail options, but relative to a
   given strike closer to the money." Move the anchor and every number moves.
   There is no absolute-price path in this module that does not take one.
3. **The result holds only beyond the Karamata constant.** For puts that means
   ``strike <= (1 - l) * spot``; inside that bound the strong Pareto law is not
   in force and the extrapolation is unfounded. Enforced, not documented.
4. **The truncation is a modelling choice.** The underlying cannot fall below
   zero, so ``r`` is Pareto on ``[l, 1]`` and ``lambda = 1/(1 - l^alpha)``
   renormalises it. ``lambda`` itself is negligible in practice, but the
   ``(alpha - 1) K + S_0`` term it brings with it is **not**: dropping it moves
   ``P(80)/P(90)`` from 0.2305 to 0.2500 at ``S_0 = 100, alpha = 3``, an 8.5%
   error. A *censored* reading (bankruptcy mass at zero, limited liability) is
   the defensible alternative and gives different numbers; this module
   implements the paper's truncated one.
5. **The paper's printed put formula is dimensionally inconsistent and is not
   what this implements.** It carries ``S_0^(1-alpha)`` where the derivation
   gives ``S_0^(-alpha)``; as printed it subtracts ``price^(2-alpha)`` from
   ``price^(1-alpha)``. The derived form is what satisfies ``P(0) = 0``, matches
   direct quadrature of the defining integral, and appears in the paper's own
   commented-out LaTeX. Do not "correct" it back -- ``docs/adr/0026``.
6. **``sigma * sqrt(t) <= 1/2`` or the heuristic's own error is not
   negligible** (``heuristic_is_valid``). The model has no volatility of its
   own; this is a statement about where the approximation behind it holds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

#: Finite first moment is the only requirement (assumption 1). ``alpha`` must
#: exceed this strictly: at exactly 1 the ``1/(alpha - 1)`` factor diverges.
MIN_ALPHA: Final = 1.0

#: The paper's validity guard on the truncation correction: beyond
#: ``sigma * sqrt(t) = 1/2`` the neglected terms stop being negligible.
LAMBDA_GUARD_MAX_SIGMA_ROOT_T: Final = 0.5

#: Which parameterisation ``karamata_l`` belongs to. ``"price"`` is the paper's
#: first approach, where ``S`` itself is regularly varying and ``l`` is in
#: **price units**. ``"returns"`` is the second, where ``(S - S_0)/S_0`` is
#: regularly varying and ``l`` is **dimensionless**. The tail index is the same
#: in both -- it is scale-free -- but ``L`` reaches its constant at a different
#: rate, which is exactly the confusion the paper's own Remark warns about.
#: Mixing them is not a units nit: a price-basis ``l`` of 10 fed to
#: ``deepest_valid_put_strike`` would imply a strike of -900, which is why that
#: method refuses a price-basis tail outright.
ParetanBasis = Literal["price", "returns"]


def _require_finite(**values: float) -> None:
    """Reject NaN and infinity before any comparison sees them.

    Every ordering guard in this module is written as ``x <= bound``, and that
    is ``False`` for NaN -- so without this, a NaN argument passes every check
    and comes back as a plausible number. The dataclass has always validated
    finiteness; the module-level functions did not.
    """
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number, got {value}")


def _validate(alpha: float, karamata_l: float) -> None:
    if not math.isfinite(alpha) or alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA} for a finite first moment, got {alpha}")
    if not math.isfinite(karamata_l) or karamata_l <= 0.0:
        raise ValueError(f"karamata_l must be positive, got {karamata_l}")


@dataclass(frozen=True)
class ParetanTail:
    """A tail index and its Karamata constant -- the whole model.

    ``karamata_l`` is ``l``, not ``L``: the paper writes the scale as
    ``l^alpha``. Its units depend on ``basis``, which is why ``basis`` is a
    required field rather than a convention.
    """

    alpha: float
    karamata_l: float
    basis: ParetanBasis

    def __post_init__(self) -> None:
        _validate(self.alpha, self.karamata_l)
        if self.basis not in ("price", "returns"):
            raise ValueError(f"basis must be 'price' or 'returns', got {self.basis!r}")
        if self.basis == "returns" and self.karamata_l >= 1.0:
            # In the returns basis l is a fractional distance from spot, so
            # l >= 1 is not merely unusual: lambda = 1/(1 - l^alpha) comes back
            # NEGATIVE -- a renormalising constant cannot be -- or divides by
            # zero at exactly 1, and deepest_valid_put_strike falls to zero.
            raise ValueError(
                "a returns-basis karamata_l is a fraction of spot and must be below 1, "
                f"got {self.karamata_l}"
            )

    def _require_returns_basis(self, what: str) -> None:
        if self.basis != "returns":
            raise ValueError(
                f"{what} needs a returns-basis tail (l dimensionless); this tail is "
                "price-basis, where l is in price units and the two cannot be mixed"
            )

    def lambda_correction(self) -> float:
        """``lambda = 1 / (1 - l^alpha)`` -- the truncation renormaliser.

        Close to 1 for any realistic ``l``, and it cancels out of every ratio.
        It is exposed because "close to 1" is a claim a reader should be able to
        check rather than take on trust.
        """
        self._require_returns_basis("lambda_correction")
        return float(1.0 / (1.0 - self.karamata_l**self.alpha))

    def deepest_valid_put_strike(self, *, spot: float) -> float:
        """``(1 - l) * spot`` -- the deepest strike at which the strong Pareto
        law is in force, and so the shallowest valid anchor."""
        self._require_returns_basis("deepest_valid_put_strike")
        _require_finite(spot=spot)
        if spot <= 0.0:
            raise ValueError(f"spot must be positive, got {spot}")
        return (1.0 - self.karamata_l) * spot

    def put_price(self, *, strike: float, spot: float) -> float:
        """Put price under the truncated Paretan downside.

        ``P(K) = lambda * l^alpha * [S_0^alpha (S_0-K)^(1-alpha)
        - (alpha-1) K - S_0] / (alpha - 1)``

        Valid for ``0 <= K <= (1 - l) * spot``. ``K = 0`` is included on
        purpose: a zero-strike put is worth exactly nothing, and that is the
        boundary condition distinguishing this from the paper's printed form.

        Raises ``ValueError`` outside the valid strike range or for a
        non-positive spot.
        """
        self._require_returns_basis("put_price")
        _require_finite(strike=strike, spot=spot)
        if spot <= 0.0:
            raise ValueError(f"spot must be positive, got {spot}")
        deepest = self.deepest_valid_put_strike(spot=spot)
        if strike < 0.0 or strike > deepest:
            raise ValueError(
                f"strike {strike} is outside the Paretan put domain [0, {deepest}] "
                f"for spot {spot} and l {self.karamata_l}: inside that bound the "
                "strong Pareto law does not hold and this price is not defined"
            )
        raw: float = (
            self.lambda_correction()
            * self.karamata_l**self.alpha
            * _put_shape(strike=strike, spot=spot, alpha=self.alpha)
            / (self.alpha - 1.0)
        )
        # Catastrophic cancellation: as K -> 0 the shape is a difference of two
        # numbers that agree to every digit, so it can land a few ulps below
        # zero. A put price is never negative, and the invariant is worth more
        # than the last bit of a number that is zero anyway.
        #
        # Clamp only NEGATIVES, never `max(0.0, raw)`: `max(0.0, nan)` is 0.0,
        # so that form would launder a NaN into a confident "this strike is
        # worthless" -- strictly worse than returning a visible nan.
        return 0.0 if raw < 0.0 else raw

    def call_price(self, *, strike: float, spot: float | None = None) -> float:
        """Call price, in whichever parameterisation this tail carries.

        Price basis (``spot=None``): ``C(K) = K^(1-alpha) l^alpha / (alpha-1)``,
        the paper's first approach with its scaling constant ``nu`` set to 1 --
        ``nu`` is unidentifiable from a single anchor and cancels from every
        ratio, so the absolute level here is meaningful only up to it.

        Returns basis (``spot`` given): ``C(K, S_0) = (l S_0)^alpha
        (K - S_0)^(1-alpha) / (alpha - 1)``, valid for ``K >= S_0 (1 + l)``.
        """
        _require_finite(strike=strike)
        if spot is not None:
            _require_finite(spot=spot)
        if strike <= 0.0:
            raise ValueError(f"strike must be positive, got {strike}")
        if self.basis == "price":
            if spot is not None:
                raise ValueError(
                    "a price-basis tail prices calls on strike alone; spot is not used"
                )
            if strike < self.karamata_l:
                # The implied survival probability (l/strike)^alpha would say it
                # better, but computing it inside the message can itself raise
                # OverflowError -- from the guard whose contract is ValueError.
                raise ValueError(
                    f"strike {strike} is inside the Karamata constant {self.karamata_l}; "
                    "the strong Pareto law does not hold there, and the survival "
                    "probability it implies at the anchor exceeds 1"
                )
            return float(
                strike ** (1.0 - self.alpha) * self.karamata_l**self.alpha / (self.alpha - 1.0)
            )

        if spot is None:
            raise ValueError("a returns-basis tail needs spot to price a call")
        if spot <= 0.0:
            raise ValueError(f"spot must be positive, got {spot}")
        shallowest = spot * (1.0 + self.karamata_l)
        if strike < shallowest:
            raise ValueError(
                f"strike {strike} is inside the Karamata point {shallowest}; "
                "the strong Pareto law does not hold there"
            )
        return float(
            (self.karamata_l * spot) ** self.alpha
            * (strike - spot) ** (1.0 - self.alpha)
            / (self.alpha - 1.0)
        )


def _put_shape(*, strike: float, spot: float, alpha: float) -> float:
    """``g(K) = S_0^alpha (S_0 - K)^(1-alpha) - (alpha-1) K - S_0``.

    The whole strike-dependence of the put price. ``l`` and ``lambda`` multiply
    it, which is why they cancel from ``put_ratio``.
    """
    return float(spot**alpha * (spot - strike) ** (1.0 - alpha) - (alpha - 1.0) * strike - spot)


def put_ratio(*, k_from: float, k_to: float, spot: float, alpha: float) -> float:
    """``P(k_to) / P(k_from)`` -- the relative price, free of ``l`` entirely.

    This is the operative result: with one market quote at ``k_from`` and a tail
    index, every deeper strike has a price, and nothing about the distribution
    below the Karamata point needs to be known or estimated.

    Both strikes must lie in ``[0, (1-l) * spot]``, which this function cannot
    check because it never sees ``l`` -- that is the caller's job, and
    ``ParetanTail.put_price`` does it.
    """
    _require_finite(k_from=k_from, k_to=k_to, spot=spot, alpha=alpha)
    if alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA}, got {alpha}")
    if spot <= 0.0:
        raise ValueError(f"spot must be positive, got {spot}")
    for strike in (k_from, k_to):
        if strike < 0.0 or strike >= spot:
            raise ValueError(f"put strike {strike} must lie in [0, {spot}) for a downside tail")

    denominator = _put_shape(strike=k_from, spot=spot, alpha=alpha)
    if denominator <= 0.0:
        raise ValueError(
            f"the anchor strike {k_from} carries no Paretan value at alpha {alpha}; "
            "it is at or beyond the point where the model assigns zero"
        )
    # Same ulp-scale cancellation put_price guards against, and the same
    # negatives-only clamp for the same reason: `max(0.0, nan)` is 0.0.
    ratio = float(_put_shape(strike=k_to, spot=spot, alpha=alpha) / denominator)
    return 0.0 if ratio < 0.0 else ratio


def call_ratio(*, k_from: float, k_to: float, alpha: float) -> float:
    """``C(k_to)/C(k_from) = (k_to/k_from)^(1-alpha)`` -- the price basis.

    On log-log axes this is a straight line of slope ``1 - alpha``, which is the
    paper's central picture: Black-Scholes prices fall off a cliff out in the
    wing while Paretan prices stay on a line.
    """
    _require_finite(k_from=k_from, k_to=k_to, alpha=alpha)
    if alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA}, got {alpha}")
    if k_from <= 0.0 or k_to <= 0.0:
        raise ValueError(f"strikes must be positive, got {k_from} and {k_to}")
    return float((k_to / k_from) ** (1.0 - alpha))


def call_ratio_returns(*, k_from: float, k_to: float, spot: float, alpha: float) -> float:
    """``C(k_to)/C(k_from) = ((k_to - S_0)/(k_from - S_0))^(1-alpha)``."""
    _require_finite(k_from=k_from, k_to=k_to, spot=spot, alpha=alpha)
    if alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA}, got {alpha}")
    if k_from <= spot or k_to <= spot:
        raise ValueError(
            f"call strikes must exceed spot {spot} for an upside tail, got {k_from} and {k_to}"
        )
    return float(((k_to - spot) / (k_from - spot)) ** (1.0 - alpha))


def anchor_l_call(*, price: float, strike: float, alpha: float) -> ParetanTail:
    """Calibrate a price-basis tail from one call quote.

    ``l = ((alpha - 1) C_m K^(alpha - 1))^(1/alpha)``, the paper's own
    inversion. With ``alpha``, this ``l`` carries all the information about the
    distribution that pricing further strikes requires.
    """
    _require_finite(price=price, strike=strike, alpha=alpha)
    if alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA}, got {alpha}")
    if price <= 0.0 or strike <= 0.0:
        raise ValueError(f"price and strike must be positive, got {price} and {strike}")
    karamata_l = ((alpha - 1.0) * price * strike ** (alpha - 1.0)) ** (1.0 / alpha)
    if karamata_l > strike:
        raise ValueError(
            f"the anchor strike {strike} sits inside the calibrated Karamata constant "
            f"{karamata_l}: at this alpha the quote is too rich to describe as a tail, "
            "and the survival probability it implies at the anchor exceeds 1"
        )
    return ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="price")


def anchor_l_call_returns(*, price: float, strike: float, spot: float, alpha: float) -> ParetanTail:
    """Calibrate a returns-basis tail from one call quote.

    ``l = ((alpha-1)^(1/alpha) C_m^(1/alpha) (K - S_0)^(1 - 1/alpha)) / S_0``.
    """
    _require_finite(price=price, strike=strike, spot=spot, alpha=alpha)
    if alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA}, got {alpha}")
    if price <= 0.0:
        raise ValueError(f"price must be positive, got {price}")
    if strike <= spot:
        raise ValueError(f"call strike {strike} must exceed spot {spot}")
    karamata_l = (
        (alpha - 1.0) ** (1.0 / alpha)
        * price ** (1.0 / alpha)
        * (strike - spot) ** (1.0 - 1.0 / alpha)
    ) / spot
    shallowest = spot * (1.0 + karamata_l)
    if strike < shallowest:
        raise ValueError(
            f"the anchor strike {strike} sits inside the calibrated Karamata point "
            f"{shallowest} (l={karamata_l}): the strong Pareto law does not hold at the "
            "anchor, so nothing extrapolated from it is valid"
        )
    return ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")


def anchor_l_put(*, price: float, strike: float, spot: float, alpha: float) -> ParetanTail:
    """Calibrate a returns-basis tail from one **put** quote.

    ``lambda`` depends on ``l``, so this looks implicit, but it is not: writing
    ``u = P (alpha - 1) / g(K)`` gives ``l^alpha = u / (1 + u)`` in closed form.

    The calibrated tail is checked against its own domain -- the anchor must
    satisfy ``K <= (1 - l) S_0`` -- and this raises if it does not. An anchor
    inside the Karamata point invalidates the entire extrapolation built on it,
    so it is refused here rather than producing a ladder that looks fine.
    """
    _require_finite(price=price, strike=strike, spot=spot, alpha=alpha)
    if alpha <= MIN_ALPHA:
        raise ValueError(f"alpha must exceed {MIN_ALPHA}, got {alpha}")
    if price <= 0.0:
        raise ValueError(f"price must be positive, got {price}")
    if strike < 0.0 or strike >= spot:
        raise ValueError(f"put strike {strike} must lie in [0, {spot}) for a downside tail")

    shape = _put_shape(strike=strike, spot=spot, alpha=alpha)
    if shape <= 0.0:
        raise ValueError(
            f"put strike {strike} carries no Paretan value at alpha {alpha}; cannot calibrate"
        )
    u = price * (alpha - 1.0) / shape
    karamata_l = (u / (1.0 + u)) ** (1.0 / alpha)

    tail = ParetanTail(alpha=alpha, karamata_l=karamata_l, basis="returns")
    deepest = tail.deepest_valid_put_strike(spot=spot)
    if strike > deepest:
        raise ValueError(
            f"the anchor strike {strike} sits inside the calibrated Karamata point "
            f"{deepest} (l={karamata_l}): the strong Pareto law does not hold at the "
            "anchor, so nothing extrapolated from it is valid"
        )
    return tail


def heuristic_is_valid(*, sigma: float, t_years: float) -> bool:
    """Whether ``sigma * sqrt(t) <= 1/2``, the paper's own validity guard.

    Outside it the truncation correction the model neglects stops being
    negligible. This is not a hard error -- the ladder still renders -- but the
    result must be labelled wherever it is shown.
    """
    _require_finite(sigma=sigma, t_years=t_years)
    if sigma < 0.0 or t_years < 0.0:
        raise ValueError(f"sigma and t_years must be non-negative, got {sigma} and {t_years}")
    return sigma * math.sqrt(t_years) <= LAMBDA_GUARD_MAX_SIGMA_ROOT_T
