"""One expiry's smile: a raw-SVI fit, its no-arbitrage diagnostics, and the
one-wing fallback (``docs/adr/0036``).

**Raw SVI** (Gatheral 2004) in total variance and log-forward moneyness::

    w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))

fitted by the quasi-explicit method (Zeliade 2009): for fixed ``(m, sigma)``,
with ``y = (k - m) / sigma``, the smile is LINEAR in ``(a, d, c)``::

    w = a + d * y + c * sqrt(y^2 + 1),   d = b rho sigma,   c = b sigma

so an outer search over two parameters wraps an inner problem that is a small
constrained least squares. The constraints are ``0 <= c``, ``|d| <= c``
(``|rho| <= 1``), ``c + |d| <= 2 sigma`` (Lee's moment bound ``b (1 + |rho|) <= 2``
-- tighter than Zeliade's ``4 sigma``, which would let the fit emit slices this
module's own Lee check refuses), and ``a + sqrt(c^2 - d^2) >= 0`` (non-negative
minimum variance).

**A fit is not a certificate.** Being SVI-shaped does not make a slice free of
butterfly arbitrage, so Durrleman's ``g(k) >= 0`` is evaluated on the OBSERVED
k-range, never inferred from the parametrisation; a non-finite or non-positive
point there counts as ``n_invalid`` and fails the check (pysvi's refusal rule,
``docs/PRIOR_ART.md`` §15).

**One wing.** A session with no call wing (all of them before 2026-10-27) only
sees ``k`` below or near zero, where SVI's ``rho``, ``b`` and right wing are not
identified. Those slices get a local quadratic in ``k`` instead -- ATM vol, skew
and curvature, nothing about the wings -- and say so.
"""

from __future__ import annotations

import contextlib
import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import norm

#: Points on which ``g(k)`` and the calendar check are evaluated.
GRID_POINTS = 201
#: ``g`` below ``-BUTTERFLY_TOLERANCE`` is a violation; the slack absorbs float
#: noise on a slice that touches zero (a flat smile has ``g == 1`` exactly).
BUTTERFLY_TOLERANCE = 1e-9
#: Total variance a later slice may fall below an earlier one before it is a
#: calendar violation, absorbing fit noise rather than hiding arbitrage.
CALENDAR_TOLERANCE = 1e-6
#: Bounds of the outer search: ``m`` within the observed range widened by one
#: unit of ``k``; ``sigma`` from near-zero (a kink) to a smile wider than any
#: equity chain's strike range.
SIGMA_BOUNDS = (1e-4, 2.0)
#: Strikes either side of ``k = 0`` the one-wing quadratic uses.
QUADRATIC_STRIKES = 8
#: Strikes the one-wing quadratic needs at ``k > 0`` so it straddles ATM
#: rather than extrapolating to it.
QUADRATIC_MIN_ABOVE = 2
QUADRATIC_MIN_BELOW = 3
#: The 25-delta wings of the risk reversal.
RR_DELTA = 0.25


@dataclass(frozen=True)
class RawSvi:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def w(self, k: np.ndarray) -> np.ndarray:
        x = k - self.m
        return self.a + self.b * (self.rho * x + np.sqrt(x * x + self.sigma**2))

    def w_prime(self, k: np.ndarray) -> np.ndarray:
        x = k - self.m
        return self.b * (self.rho + x / np.sqrt(x * x + self.sigma**2))

    def w_second(self, k: np.ndarray) -> np.ndarray:
        x = k - self.m
        return self.b * self.sigma**2 / (x * x + self.sigma**2) ** 1.5


@dataclass(frozen=True)
class Butterfly:
    ok: bool
    min_g: float | None
    n_invalid: int


def durrleman_g(fit: RawSvi, k: np.ndarray) -> np.ndarray:
    """Gatheral & Jacquier (2014) eq. 2.1: the implied density is non-negative
    exactly where ``g(k) >= 0``."""
    w, w1, w2 = fit.w(k), fit.w_prime(k), fit.w_second(k)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = (1 - k * w1 / (2 * w)) ** 2 - (w1**2 / 4) * (1 / w + 0.25) + w2 / 2
    return np.asarray(g, dtype=float)


def check_butterfly(fit: RawSvi, k_min: float, k_max: float) -> Butterfly:
    grid = np.linspace(k_min, k_max, GRID_POINTS)
    w = fit.w(grid)
    g = durrleman_g(fit, grid)
    invalid = ~np.isfinite(g) | ~np.isfinite(w) | (w <= 0)
    n_invalid = int(invalid.sum())
    valid = g[~invalid]
    min_g = float(valid.min()) if valid.size else None
    ok = n_invalid == 0 and min_g is not None and min_g >= -BUTTERFLY_TOLERANCE
    return Butterfly(ok=ok, min_g=min_g, n_invalid=n_invalid)


def lee_ok(fit: RawSvi) -> bool:
    """Both wing slopes of ``w`` within Lee's bound of 2."""
    return fit.b * (1 + abs(fit.rho)) <= 2 + 1e-12


#: The polytope's linear faces as rows ``g`` of ``g @ (a, d, c) <= h * sigma``:
#: ``d <= c``, ``-d <= c``, ``c + d <= 2 sigma``, ``c - d <= 2 sigma``. Together
#: they are ``|d| <= c`` (so ``c >= 0``) and Lee's ``c + |d| <= 2 sigma``.
_FACES = np.array([[0.0, 1.0, -1.0], [0.0, -1.0, -1.0], [0.0, 1.0, 1.0], [0.0, -1.0, 1.0]])
_FACE_RHS = np.array([0.0, 0.0, 2.0, 2.0])
#: Every set of faces that can be active at a least-squares optimum on a
#: 3-D polytope with these four faces: each face alone, and each pair.
_ACTIVE_SETS: tuple[tuple[int, ...], ...] = (
    (0,),
    (1,),
    (2,),
    (3,),
    (0, 1),
    (0, 2),
    (0, 3),
    (1, 2),
    (1, 3),
    (2, 3),
)


def _inner(y: np.ndarray, w: np.ndarray, sigma: float) -> tuple[np.ndarray, float]:
    """Best ``(a, d, c)`` on the constraint set, and its squared error.

    Exact for the linear faces: a convex least squares on a polytope has its
    optimum either inside (the free solution) or on some face set, so trying
    the free solution and each equality-constrained solve (a 3x3 KKT system)
    and keeping the best FEASIBLE one is the optimum. Only the non-linear
    minimum-variance constraint is handled approximately (below). (The first
    version called SLSQP on every outer step and took 140 s for one SPY
    session; this takes ~5 s.)
    """
    design = np.column_stack([np.ones_like(y), y, np.sqrt(y * y + 1)])
    gram, rhs = design.T @ design, design.T @ w
    bound = _FACE_RHS * sigma

    def sse(p: np.ndarray) -> float:
        r = design @ p - w
        return float(r @ r)

    def on_polytope(p: np.ndarray) -> bool:
        return bool(np.all(_FACES @ p <= bound + 1e-12))

    candidates: list[np.ndarray] = []
    with contextlib.suppress(np.linalg.LinAlgError):
        candidates.append(np.linalg.solve(gram, rhs))
    for active in _ACTIVE_SETS:
        g = _FACES[list(active)]
        n = len(active)
        kkt = np.block([[gram, g.T], [g, np.zeros((n, n))]])
        try:
            sol = np.linalg.solve(kkt, np.concatenate([rhs, bound[list(active)]]))
        except np.linalg.LinAlgError:
            continue
        candidates.append(sol[:3])
    on_faces = [p for p in candidates if on_polytope(p)]
    if not on_faces:
        # Only if every KKT solve was singular: the constrained optimum is
        # always one of the candidates otherwise. Zero is on the polytope.
        on_faces = [np.zeros(3)]
    # The minimum-variance constraint a + sqrt(c^2 - d^2) >= 0 is the one the
    # faces cannot express. Where a candidate violates it, raise ``a`` to its
    # bound: feasible, and the best ``a`` for that (d, c) under the bound,
    # since the error is convex in ``a`` alone. Not the exact constrained
    # optimum -- the outer search over (m, sigma) absorbs the difference --
    # but it keeps every step a few 3x3 solves. (An SLSQP fallback here made
    # one such smile take 80 s.)
    projected: list[np.ndarray] = []
    for p in on_faces:
        a, d, c = float(p[0]), float(p[1]), max(float(p[2]), 0.0)
        d = max(-c, min(c, d))
        a = max(a, -math.sqrt(max(c * c - d * d, 0.0)))
        projected.append(np.array([a, d, c]))
    best = min(projected, key=sse)
    return best, sse(best)


def fit_raw_svi(k: Sequence[float] | np.ndarray, w: Sequence[float] | np.ndarray) -> RawSvi:
    """Least-squares raw SVI on total variance, every constraint enforced."""
    ks, ws = np.asarray(k, dtype=float), np.asarray(w, dtype=float)
    span = float(ks.max() - ks.min()) or 1.0
    m_lo, m_hi = float(ks.min()) - span, float(ks.max()) + span

    def outer(params: np.ndarray) -> float:
        m, log_s = params
        sigma = math.exp(log_s)
        if not (m_lo <= m <= m_hi and SIGMA_BOUNDS[0] <= sigma <= SIGMA_BOUNDS[1]):
            return 1e9
        return _inner((ks - m) / sigma, ws, sigma)[1]

    best: tuple[float, np.ndarray] | None = None
    # Six starts: the outer surface is low-dimensional but not convex (a
    # kink-like small sigma and a wide one can both fit a short smile).
    for m0 in np.linspace(m_lo / 2, m_hi / 2, 3):
        for s0 in (0.08, 0.4):
            res = minimize(
                outer,
                np.array([m0, math.log(s0)]),
                method="Nelder-Mead",
                options={"xatol": 1e-7, "fatol": 1e-14, "maxiter": 2000},
            )
            if best is None or res.fun < best[0]:
                best = (float(res.fun), res.x)
    assert best is not None
    m, sigma = float(best[1][0]), math.exp(float(best[1][1]))
    (a, d, c), _ = _inner((ks - m) / sigma, ws, sigma)
    b = c / sigma
    rho = d / c if c > 0 else 0.0
    return RawSvi(a=float(a), b=float(b), rho=float(rho), m=m, sigma=sigma)


def calendar_violations(
    slices: Sequence[tuple[float, RawSvi, float, float]],
) -> list[tuple[float, float]]:
    """Adjacent ``(t_years, fit, k_min, k_max)`` pairs whose total variance
    DECREASES with tenor somewhere on the intersection of their observed
    ranges -- a calendar arbitrage. Returned as ``(t_short, t_long)``."""
    ordered = sorted(slices, key=lambda s: s[0])
    out: list[tuple[float, float]] = []
    for (t1, f1, lo1, hi1), (t2, f2, lo2, hi2) in itertools.pairwise(ordered):
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        if lo >= hi:
            continue
        grid = np.linspace(lo, hi, GRID_POINTS)
        if bool(np.any(f2.w(grid) < f1.w(grid) - CALENDAR_TOLERANCE)):
            out.append((t1, t2))
    return out


@dataclass(frozen=True)
class SmileShape:
    """ATM numbers in k-units, plus the risk reversal where both wings exist."""

    atm_vol: float
    #: d sigma / d k at k = 0 (vol per unit of log-moneyness).
    atm_skew: float
    #: d^2 sigma / d k^2 at k = 0.
    atm_curvature: float
    #: sigma(25-delta call) - sigma(25-delta put), forward Black delta; ``None``
    #: when either strike falls outside the observed range.
    risk_reversal_25: float | None


def svi_shape(fit: RawSvi, t_years: float, k_min: float, k_max: float) -> SmileShape:
    def vol(k: float) -> float:
        return math.sqrt(max(float(fit.w(np.array([k]))[0]), 0.0) / t_years)

    h = 1e-4
    v0, vp, vm = vol(0.0), vol(h), vol(-h)
    return SmileShape(
        atm_vol=v0,
        atm_skew=(vp - vm) / (2 * h),
        atm_curvature=(vp - 2 * v0 + vm) / h**2,
        risk_reversal_25=_risk_reversal(vol, t_years, k_min, k_max),
    )


def _risk_reversal(
    vol_at: Callable[[float], float], t_years: float, k_min: float, k_max: float
) -> float | None:

    def call_delta(k: float) -> float:
        total = vol_at(k) * math.sqrt(t_years)
        if total <= 0:
            return float("nan")
        return float(norm.cdf(-k / total + total / 2))

    def strike_for(target: float, lo: float, hi: float) -> float | None:
        if lo >= hi:
            return None
        f_lo, f_hi = call_delta(lo) - target, call_delta(hi) - target
        if not (math.isfinite(f_lo) and math.isfinite(f_hi)) or f_lo * f_hi > 0:
            return None
        res = minimize_scalar(
            lambda k: abs(call_delta(k) - target),
            bounds=(lo, hi),
            method="bounded",
            options={"xatol": 1e-10},
        )
        return float(res.x)

    # A put's forward delta is N(d1) - 1, so the 25-delta put sits where the
    # call delta is 0.75: on the k < 0 side.
    k_call = strike_for(RR_DELTA, 0.0, k_max)
    k_put = strike_for(1 - RR_DELTA, k_min, 0.0)
    if k_call is None or k_put is None:
        return None
    return float(vol_at(k_call)) - float(vol_at(k_put))


def quadratic_shape(
    k: Sequence[float] | np.ndarray, vol: Sequence[float] | np.ndarray
) -> tuple[SmileShape | None, str | None]:
    """ATM vol, skew and curvature from a local quadratic in ``k`` on one wing.

    Uses the :data:`QUADRATIC_STRIKES` strikes nearest ``k = 0`` on each side
    and refuses unless at least :data:`QUADRATIC_MIN_ABOVE` sit above it, so
    the fit straddles ATM rather than extrapolating to it.
    """
    ks, vs = np.asarray(k, dtype=float), np.asarray(vol, dtype=float)
    above, below = ks[ks > 0], ks[ks <= 0]
    if above.size < QUADRATIC_MIN_ABOVE:
        return None, "no_strikes_above_forward"
    if below.size < QUADRATIC_MIN_BELOW:
        return None, "too_few_strikes_below_forward"
    order = np.argsort(np.abs(ks))
    keep = np.concatenate(
        [order[ks[order] > 0][:QUADRATIC_STRIKES], order[ks[order] <= 0][:QUADRATIC_STRIKES]]
    )
    c2, c1, c0 = np.polyfit(ks[keep], vs[keep], 2)
    return SmileShape(
        atm_vol=float(c0), atm_skew=float(c1), atm_curvature=float(2 * c2), risk_reversal_25=None
    ), None
