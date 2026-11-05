"""Assembling a session's surface from chain rows (ADR 0036).

Why these matter: this is where real quotes become the served smile. The
synthetic chain is priced from a KNOWN smile on a known forward, so the
assertions are analytic: parity must find the forward, the SVI fit must give
back the smile's ATM vol, and a session without the call wing must be fitted
as one wing and say so -- never pretend it saw the right side.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.option_chain import CALL_DATASET, DATASET
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.option_pricer import DAYS_PER_YEAR, BlackScholesPricer
from tail_lab.research.vol_surface import read, svi

SESSION = dt.date(2026, 11, 4)
EXPIRY = dt.date(2027, 1, 15)
S, R, Q = 500.0, 0.04, 0.012
T = (EXPIRY - SESSION).days / DAYS_PER_YEAR
F = S * math.exp((R - Q) * T)
SMILE = svi.RawSvi(a=0.004, b=0.05, rho=-0.7, m=0.02, sigma=0.1)
PRICER = BlackScholesPricer()


def _vol(strike: float) -> float:
    return math.sqrt(float(SMILE.w(np.array([math.log(strike / F)]))[0]) / T)


def _put(strike: float) -> float:
    # Black-76 on F: spot F e^{-rT}, no yield.
    return PRICER.price_put(
        spot=F * math.exp(-R * T), strike=strike, t_years=T, r=R, sigma=_vol(strike), q=0.0
    )


def _call(strike: float) -> float:
    return _put(strike) + math.exp(-R * T) * (F - strike)


def _rows(strikes: np.ndarray, price, session: dt.date = SESSION) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    mids = np.array([price(float(k)) for k in strikes])
    return pd.DataFrame(
        {
            "underlying": "SPY",
            "quote_date": pd.Timestamp(session),
            "expiration": pd.Timestamp(EXPIRY),
            "strike": strikes,
            "bid": mids * 0.99,
            "ask": mids * 1.01,
            "volume": 10,
            "open_interest": 100,
            "spot": S,
            "iv": np.nan,
            "delta": np.nan,
            "theo": np.nan,
        }
    )


PUT_STRIKES = np.arange(0.70 * S, 1.05 * S, 5.0)
CALL_STRIKES = np.arange(0.95 * S, 1.30 * S, 5.0)


def _surface(calls: pd.DataFrame, symbol: str = "spy") -> read.VolSurface:
    return read.surface_from_frames(
        _rows(PUT_STRIKES, _put), calls, symbol=symbol, as_of=SESSION, r=R, q=Q, q_source="measured"
    )


def test_two_wings_find_the_forward_by_parity_and_fit_the_smile() -> None:
    surface = _surface(_rows(CALL_STRIKES, _call))
    assert surface.two_wing and surface.refusal is None
    (slice_,) = surface.slices
    assert slice_.forward_source == "parity"
    assert slice_.forward == pytest.approx(F, rel=1e-6)
    assert slice_.fit == "svi" and slice_.refusal is None
    assert slice_.atm_vol == pytest.approx(
        math.sqrt(float(SMILE.w(np.array([0.0]))[0]) / T), abs=2e-3
    )
    assert slice_.atm_skew is not None and slice_.atm_skew < 0
    assert slice_.butterfly_ok and slice_.lee_ok
    assert slice_.rmse_vol_points is not None and slice_.rmse_vol_points < 0.5


def test_only_out_of_the_money_quotes_enter_a_two_wing_smile() -> None:
    surface = _surface(_rows(CALL_STRIKES, _call))
    (slice_,) = surface.slices
    assert all((p.k < 0) == (p.right == "P") for p in slice_.points)


def test_a_session_without_calls_is_fitted_as_one_wing_and_says_so() -> None:
    surface = _surface(pd.DataFrame())
    assert not surface.two_wing
    (slice_,) = surface.slices
    assert slice_.fit == "one_wing_quadratic"
    assert slice_.svi is None and slice_.risk_reversal_25 is None and slice_.butterfly_ok is None
    assert slice_.forward_source == "carry"
    assert slice_.atm_vol == pytest.approx(_vol(F), abs=5e-3)


def test_a_thin_call_wing_refuses_rather_than_fits() -> None:
    surface = _surface(_rows(np.array([1.10 * S, 1.15 * S]), _call))
    (slice_,) = surface.slices
    assert slice_.fit is None
    assert slice_.refusal is not None and slice_.refusal.startswith("too_few_strikes")


def test_vix_is_refused_by_name() -> None:
    surface = _surface(pd.DataFrame(), symbol="vix")
    assert surface.slices == [] and surface.refusal is not None and "futures" in surface.refusal


def test_an_unknown_dividend_labels_the_carry_forward() -> None:
    surface = read.surface_from_frames(
        _rows(PUT_STRIKES, _put),
        pd.DataFrame(),
        symbol="spy",
        as_of=SESSION,
        r=R,
        q=0.0,
        q_source="unknown",
    )
    assert surface.slices[0].forward_source == "carry_q0"


def test_the_read_is_point_in_time(tmp_path: Path) -> None:
    """A session ingested after ``as_of`` must be invisible."""
    store = DeltaLakeStore(tmp_path)
    later = SESSION + dt.timedelta(days=1)
    store.write_bronze(DATASET, SESSION, _rows(PUT_STRIKES, _put))
    store.write_bronze(CALL_DATASET, SESSION, _rows(CALL_STRIKES, _call))
    store.write_bronze(DATASET, later, _rows(PUT_STRIKES, _put, session=later))
    early = read.read_vol_surface(store, "SPY", SESSION)
    assert early.session == SESSION and early.two_wing
    late = read.read_vol_surface(store, "spy", later)
    assert late.session == later
    # The call partition for `later` was never written: one wing, not a mix.
    assert not late.two_wing


def test_an_empty_lake_is_a_refusal_not_an_error(tmp_path: Path) -> None:
    surface = read.read_vol_surface(DeltaLakeStore(tmp_path), "spy", SESSION)
    assert surface.refusal is not None and surface.slices == []


def test_unmarkable_quotes_never_enter_the_smile() -> None:
    """The marks bar: bid > 0 and a relative spread within
    MAX_RELATIVE_SPREAD. A zero bid and a 100%-wide quote must be dropped."""
    puts = _rows(PUT_STRIKES, _put)
    zero_bid, wide = 0.80 * S, 0.85 * S
    puts.loc[puts["strike"] == zero_bid, "bid"] = 0.0
    wide_mid = puts.loc[puts["strike"] == wide, "ask"].iloc[0] / 1.01
    puts.loc[puts["strike"] == wide, ["bid", "ask"]] = [wide_mid * 0.5, wide_mid * 1.5]
    surface = read.surface_from_frames(
        puts, _rows(CALL_STRIKES, _call), symbol="spy", as_of=SESSION, r=R, q=Q, q_source="measured"
    )
    ks = {round(p.k, 9) for p in surface.slices[0].points}
    f = surface.slices[0].forward
    assert round(math.log(zero_bid / f), 9) not in ks
    assert round(math.log(wide / f), 9) not in ks
    assert round(math.log(0.90 * S / f), 9) in ks


def test_the_fit_error_is_reported_in_vol_points() -> None:
    """Recompute the RMSE from the served points and parameters: a units slip
    (fraction vs points) is a 100x error that a loose bound cannot see."""
    rng = np.random.default_rng(0)
    puts = _rows(PUT_STRIKES, _put)
    calls = _rows(CALL_STRIKES, _call)
    for frame in (puts, calls):
        bump = 1 + rng.normal(0, 0.01, len(frame))
        frame["bid"] *= bump
        frame["ask"] *= bump
    (slice_,) = read.surface_from_frames(
        puts, calls, symbol="spy", as_of=SESSION, r=R, q=Q, q_source="measured"
    ).slices
    assert slice_.svi is not None and slice_.rmse_vol_points is not None
    fit = svi.RawSvi(**slice_.svi.model_dump())
    ks = np.array([p.k for p in slice_.points])
    ivs = np.array([p.iv for p in slice_.points])
    expected = float(np.sqrt(np.mean((np.sqrt(fit.w(ks) / slice_.t_years) - ivs) ** 2))) * 100
    assert slice_.rmse_vol_points == pytest.approx(expected, rel=1e-9)
    assert slice_.rmse_vol_points > 0.01


def test_a_too_short_expiry_is_reported_not_dropped() -> None:
    near = SESSION + dt.timedelta(days=1)
    puts = pd.concat(
        [_rows(PUT_STRIKES, _put), _rows(PUT_STRIKES, _put).assign(expiration=pd.Timestamp(near))]
    )
    surface = read.surface_from_frames(
        puts, _rows(CALL_STRIKES, _call), symbol="spy", as_of=SESSION, r=R, q=Q, q_source="measured"
    )
    short = next(s for s in surface.slices if s.expiration == near)
    assert short.refusal == f"tenor_below_{read.MIN_TENOR_DAYS}_days" and short.fit is None


def test_a_one_wing_session_says_why() -> None:
    surface = _surface(pd.DataFrame())
    assert surface.one_wing_reason == f"no call quotes for the {SESSION} session"
    assert _surface(_rows(CALL_STRIKES, _call)).one_wing_reason is None
