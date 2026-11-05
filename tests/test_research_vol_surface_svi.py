"""Raw-SVI fitting and its no-arbitrage diagnostics (ADR 0036).

Why these matter: the surface's job is to say which smiles are prices and
which are artefacts. A fit that "passes" by parametrisation alone would
certify the Gatheral-Jacquier slice, which has a negative density; a fit that
ignores Lee's bound would emit wings this module itself refuses.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tail_lab.research.vol_surface import svi

#: Gatheral & Jacquier (2014), "Arbitrage-free SVI volatility surfaces", the
#: slice they show violates butterfly arbitrage. Computed here: g < 0 on
#: k in ~[0.643, 1.257], min ~ -0.0329 at k ~ 0.879, while w stays > 0.
GJ = svi.RawSvi(a=-0.0410, b=0.1331, rho=0.3060, m=0.3586, sigma=0.4153)


def test_the_gatheral_jacquier_slice_fails_the_butterfly_check() -> None:
    result = svi.check_butterfly(GJ, -1.5, 1.5)
    assert not result.ok
    assert result.n_invalid == 0
    assert result.min_g == pytest.approx(-0.0329, abs=5e-4)


def test_the_same_slice_passes_where_its_density_is_positive() -> None:
    """The check is on the OBSERVED range: left of the bad region it holds."""
    assert svi.check_butterfly(GJ, -1.5, 0.5).ok


def test_a_flat_smile_has_g_identically_one() -> None:
    flat = svi.RawSvi(a=0.04, b=0.0, rho=0.0, m=0.0, sigma=0.1)
    g = svi.durrleman_g(flat, np.linspace(-1, 1, 11))
    assert np.allclose(g, 1.0)
    assert svi.check_butterfly(flat, -1, 1).ok


def test_non_positive_variance_counts_as_invalid_and_fails() -> None:
    """pysvi's rule: an unevaluable point is never certified."""
    negative = svi.RawSvi(a=-0.5, b=0.1, rho=0.0, m=0.0, sigma=0.1)
    result = svi.check_butterfly(negative, -0.2, 0.2)
    assert result.n_invalid > 0 and not result.ok


def test_a_known_smile_is_recovered_in_total_variance() -> None:
    """Parameters trade off against each other, so w(k) is the tight assertion."""
    true = svi.RawSvi(a=0.02, b=0.15, rho=-0.6, m=0.02, sigma=0.15)
    k = np.linspace(-0.5, 0.3, 30)
    fit = svi.fit_raw_svi(k, true.w(k))
    assert np.abs(fit.w(k) - true.w(k)).max() < 1e-8
    assert fit.rho == pytest.approx(true.rho, abs=1e-3)


def test_a_concave_smile_is_fitted_on_the_constraint_set() -> None:
    """Unconstrained least squares wants c < 0 (b < 0) here; the fit must
    stay a valid SVI with Lee's bound holding instead."""
    k = np.linspace(-0.3, 0.3, 25)
    w = 0.05 - 0.1 * k**2
    fit = svi.fit_raw_svi(k, w)
    assert fit.b >= 0
    assert abs(fit.rho) <= 1 + 1e-12
    assert svi.lee_ok(fit)


def test_steep_wings_are_held_to_lees_bound() -> None:
    """Total variance rising at slope 3 in the right wing -- past Lee's 2 --
    must not be fitted with b (1 + |rho|) > 2."""
    k = np.linspace(-0.2, 1.0, 40)
    w = 0.04 + 3.0 * np.maximum(k, 0) + 0.2 * np.maximum(-k, 0)
    fit = svi.fit_raw_svi(k, w)
    assert svi.lee_ok(fit)
    assert fit.b * (1 + abs(fit.rho)) == pytest.approx(2.0, abs=1e-6)


def test_lee_ok_flags_a_slice_past_the_bound() -> None:
    assert not svi.lee_ok(svi.RawSvi(a=0.0, b=1.5, rho=0.5, m=0.0, sigma=0.1))


def test_crossing_total_variance_is_a_calendar_violation() -> None:
    short = svi.RawSvi(a=0.02, b=0.1, rho=-0.5, m=0.0, sigma=0.1)
    long_ok = svi.RawSvi(a=0.04, b=0.1, rho=-0.5, m=0.0, sigma=0.1)
    long_bad = svi.RawSvi(a=0.01, b=0.1, rho=-0.5, m=0.0, sigma=0.1)
    assert svi.calendar_violations([(0.1, short, -0.3, 0.3), (0.2, long_ok, -0.3, 0.3)]) == []
    assert svi.calendar_violations([(0.1, short, -0.3, 0.3), (0.2, long_bad, -0.3, 0.3)]) == [
        (0.1, 0.2)
    ]


def test_calendar_is_judged_only_where_both_slices_were_observed() -> None:
    short = svi.RawSvi(a=0.02, b=0.1, rho=-0.5, m=0.0, sigma=0.1)
    # Below `short` only for k > 0.5, where the short slice was never seen.
    long_ = svi.RawSvi(a=0.03, b=0.08, rho=-0.7, m=0.0, sigma=0.1)
    grid = np.linspace(0.6, 0.9, 5)
    assert np.all(long_.w(grid) < short.w(grid))
    assert svi.calendar_violations([(0.1, short, -0.3, 0.3), (0.2, long_, -0.3, 0.9)]) == []


def test_svi_shape_reads_atm_vol_skew_and_a_negative_risk_reversal() -> None:
    t = 0.25
    fit = svi.RawSvi(a=0.004, b=0.04, rho=-0.7, m=0.01, sigma=0.08)
    shape = svi.svi_shape(fit, t, -0.4, 0.3)
    assert shape.atm_vol == pytest.approx(math.sqrt(float(fit.w(np.array([0.0]))[0]) / t))
    assert shape.atm_skew < 0
    assert shape.risk_reversal_25 is not None and shape.risk_reversal_25 < 0


def test_the_risk_reversal_is_none_when_a_25_delta_strike_was_not_observed() -> None:
    fit = svi.RawSvi(a=0.004, b=0.04, rho=-0.7, m=0.01, sigma=0.08)
    assert svi.svi_shape(fit, 0.25, -0.4, 0.001).risk_reversal_25 is None


def test_the_one_wing_quadratic_recovers_a_known_smile() -> None:
    k = np.linspace(-0.2, 0.03, 20)
    vol = 0.2 - 0.5 * k + 1.5 * k**2
    shape, refusal = svi.quadratic_shape(k, vol)
    assert refusal is None and shape is not None
    assert shape.atm_vol == pytest.approx(0.2, abs=1e-9)
    assert shape.atm_skew == pytest.approx(-0.5, abs=1e-9)
    assert shape.atm_curvature == pytest.approx(3.0, abs=1e-6)


def test_the_one_wing_quadratic_refuses_to_extrapolate_to_atm() -> None:
    """optionsDX stores puts to 1.02 x spot, so a long slice's forward can sit
    above every strike; that slice must say so rather than extrapolate."""
    k = np.linspace(-0.3, -0.01, 15)
    shape, refusal = svi.quadratic_shape(k, 0.2 - 0.5 * k)
    assert shape is None and refusal == "no_strikes_above_forward"


def test_data_demanding_negative_variance_is_fitted_with_non_negative_minimum() -> None:
    """The one non-linear constraint (minimum variance >= 0) is what the exact
    face enumeration cannot express; a smile that "wants" negative total
    variance must reach the fallback and still come back valid."""
    k = np.linspace(-0.3, 0.3, 25)
    w = -0.02 + 0.05 * np.abs(k)
    fit = svi.fit_raw_svi(k, w)
    minimum = fit.a + fit.b * fit.sigma * math.sqrt(max(1 - fit.rho**2, 0.0))
    assert minimum >= -1e-9
    assert fit.b >= 0 and svi.lee_ok(fit)
