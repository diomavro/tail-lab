"""Assemble one session's implied-vol surface from the forward-collected chain.

Reads ``option_chain_snapshot`` and ``option_chain_call_snapshot`` -- one
session, point-in-time via the store's as-of resolution -- never the optionsDX
archive (v1 scope, ``docs/adr/0036``). Per expiry:

1. keep quotes a desk could mark at: a positive bid and a relative spread
   within ``marks.MAX_RELATIVE_SPREAD``; price at the mid;
2. choose the forward (``coords.choose_forward``: parity, else carry);
3. keep OUT-of-the-money quotes only -- puts below the forward, calls above --
   and invert each mid to a Black implied vol on the forward;
4. two wings -> raw SVI plus diagnostics; a session with no call wing -> the
   one-wing quadratic, which keeps puts up to the stored band edge so it
   straddles ``k = 0``.

A slice that cannot be fitted is returned with a named ``refusal``, never a 0.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Literal, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.option_chain import CALL_DATASET
from tail_lab.contracts.option_chain import DATASET as PUT_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.marks import MAX_RELATIVE_SPREAD
from tail_lab.research.backtest.put_roll import DEFAULT_RATE
from tail_lab.research.dividends import dividend_lookup
from tail_lab.research.option_pricer import DAYS_PER_YEAR
from tail_lab.research.vol_surface import coords, svi

#: Usable OTM strikes a wing needs before its slice is fitted.
MIN_STRIKES_PER_WING = 5
#: Expiries shorter than this are left out: a few days to expiry puts nearly
#: all of the smile's information in the bid-ask, and T near zero divides.
MIN_TENOR_DAYS = 3
#: VIX settles on futures, so ``strike / spot`` against the index is not
#: moneyness and no forward here would be the right one (contracts/optionsdx).
REFUSED_SYMBOLS = {"vix": "VIX options settle on futures; the index is not their underlying"}

FitKind = Literal["svi", "one_wing_quadratic"]


class SviParams(BaseModel):
    a: float
    b: float
    rho: float
    m: float
    sigma: float


class SmilePoint(BaseModel):
    k: float
    iv: float
    right: Literal["P", "C"]


class SmileSlice(BaseModel):
    expiration: dt.date
    t_years: float
    forward: float
    forward_source: coords.ForwardSource
    parity_refusal: str | None
    points: list[SmilePoint]
    fit: FitKind | None
    #: Present only for an SVI fit.
    svi: SviParams | None
    rmse_vol_points: float | None
    atm_vol: float | None
    atm_skew: float | None
    atm_curvature: float | None
    risk_reversal_25: float | None
    butterfly_ok: bool | None
    butterfly_min_g: float | None
    butterfly_n_invalid: int | None
    lee_ok: bool | None
    refusal: str | None


class VolSurface(BaseModel):
    symbol: str
    as_of: dt.date
    #: The session the chain read resolved to (on or before ``as_of``).
    session: dt.date | None
    spot: float | None
    r: float
    q: float
    q_source: str
    two_wing: bool
    slices: list[SmileSlice]
    #: ``(t_short, t_long)`` year-fractions of adjacent SVI slices whose total
    #: variance falls with tenor somewhere on their shared k-range.
    calendar_violations: list[tuple[float, float]]
    refusal: str | None


def _usable(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    mid = (frame["bid"] + frame["ask"]) / 2
    spread = (frame["ask"] - frame["bid"]) / mid
    keep = (frame["bid"] > 0) & (spread <= MAX_RELATIVE_SPREAD)
    out = frame.loc[keep].copy()
    out["mid"] = mid[keep]
    return out


def _empty_slice(
    expiry: dt.date, t: float, forward: coords.Forward, points: list[SmilePoint], refusal: str
) -> SmileSlice:
    return SmileSlice(
        expiration=expiry,
        t_years=t,
        forward=forward.value,
        forward_source=forward.source,
        parity_refusal=forward.parity_refusal,
        points=points,
        fit=None,
        svi=None,
        rmse_vol_points=None,
        atm_vol=None,
        atm_skew=None,
        atm_curvature=None,
        risk_reversal_25=None,
        butterfly_ok=None,
        butterfly_min_g=None,
        butterfly_n_invalid=None,
        lee_ok=None,
        refusal=refusal,
    )


def build_slice(
    puts: pd.DataFrame,
    calls: pd.DataFrame,
    *,
    expiry: dt.date,
    session: dt.date,
    spot: float,
    r: float,
    q: float,
    q_known: bool,
    two_wing: bool,
) -> SmileSlice:
    """One expiry's smile from its usable put and call quotes (mid prices)."""
    t = (expiry - session).days / DAYS_PER_YEAR
    pairs: list[tuple[float, float, float]] = []
    if not calls.empty and not puts.empty:
        merged = puts.merge(calls, on="strike", suffixes=("_p", "_c"))
        pairs = [
            (float(k), float(c), float(p))
            for k, c, p in zip(merged["strike"], merged["mid_c"], merged["mid_p"], strict=True)
        ]
    forward = coords.choose_forward(pairs, spot=spot, t_years=t, r=r, q=q, q_known=q_known)
    f = forward.value

    def invert(frame: pd.DataFrame, right: Literal["P", "C"]) -> list[SmilePoint]:
        out: list[SmilePoint] = []
        for strike, mid in zip(frame["strike"], frame["mid"], strict=True):
            iv = coords.implied_vol(
                float(mid), strike=float(strike), forward=f, t_years=t, r=r, is_call=right == "C"
            )
            if iv is not None and math.isfinite(iv):
                out.append(SmilePoint(k=coords.log_moneyness(float(strike), f), iv=iv, right=right))
        return out

    if not two_wing:
        # The one exception to OTM-only: puts up to the band edge, so the
        # quadratic straddles k = 0 (module docstring).
        points = invert(puts, "P")
        shape, refusal = svi.quadratic_shape([p.k for p in points], [p.iv for p in points])
        if shape is None:
            return _empty_slice(expiry, t, forward, points, refusal or "unfittable")
        return SmileSlice(
            expiration=expiry,
            t_years=t,
            forward=f,
            forward_source=forward.source,
            parity_refusal=forward.parity_refusal,
            points=points,
            fit="one_wing_quadratic",
            svi=None,
            rmse_vol_points=None,
            atm_vol=shape.atm_vol,
            atm_skew=shape.atm_skew,
            atm_curvature=shape.atm_curvature,
            risk_reversal_25=None,
            butterfly_ok=None,
            butterfly_min_g=None,
            butterfly_n_invalid=None,
            lee_ok=None,
            refusal=None,
        )

    points = invert(puts[puts["strike"] < f], "P") + invert(calls[calls["strike"] > f], "C")
    n_put = sum(p.right == "P" for p in points)
    n_call = len(points) - n_put
    if min(n_put, n_call) < MIN_STRIKES_PER_WING:
        return _empty_slice(
            expiry, t, forward, points, f"too_few_strikes ({n_put} puts, {n_call} calls)"
        )
    ks = np.array([p.k for p in points])
    ivs = np.array([p.iv for p in points])
    fit = svi.fit_raw_svi(ks, ivs**2 * t)
    fitted_vol = np.sqrt(np.maximum(fit.w(ks), 0.0) / t)
    k_min, k_max = float(ks.min()), float(ks.max())
    fly = svi.check_butterfly(fit, k_min, k_max)
    shape = svi.svi_shape(fit, t, k_min, k_max)
    return SmileSlice(
        expiration=expiry,
        t_years=t,
        forward=f,
        forward_source=forward.source,
        parity_refusal=forward.parity_refusal,
        points=points,
        fit="svi",
        svi=SviParams(a=fit.a, b=fit.b, rho=fit.rho, m=fit.m, sigma=fit.sigma),
        rmse_vol_points=float(np.sqrt(np.mean((fitted_vol - ivs) ** 2)) * 100),
        atm_vol=shape.atm_vol,
        atm_skew=shape.atm_skew,
        atm_curvature=shape.atm_curvature,
        risk_reversal_25=shape.risk_reversal_25,
        butterfly_ok=fly.ok,
        butterfly_min_g=fly.min_g,
        butterfly_n_invalid=fly.n_invalid,
        lee_ok=svi.lee_ok(fit),
        refusal=None,
    )


def surface_from_frames(
    puts: pd.DataFrame,
    calls: pd.DataFrame,
    *,
    symbol: str,
    as_of: dt.date,
    r: float,
    q: float,
    q_source: str,
) -> VolSurface:
    """Pure: one symbol's surface from its session's put and call rows."""
    symbol = symbol.lower()

    def refused(
        reason: str, session: dt.date | None = None, spot: float | None = None
    ) -> VolSurface:
        return VolSurface(
            symbol=symbol,
            as_of=as_of,
            session=session,
            spot=spot,
            r=r,
            q=q,
            q_source=q_source,
            two_wing=False,
            slices=[],
            calendar_violations=[],
            refusal=reason,
        )

    if symbol in REFUSED_SYMBOLS:
        return refused(REFUSED_SYMBOLS[symbol])
    if puts.empty:
        return refused(f"no {symbol} put quotes on or before {as_of}")
    session = pd.Timestamp(puts["quote_date"].max()).date()
    puts = _usable(puts[pd.to_datetime(puts["quote_date"]).dt.date == session])
    calls = (
        calls[pd.to_datetime(calls["quote_date"]).dt.date == session] if not calls.empty else calls
    )
    two_wing = not calls.empty
    calls = _usable(calls) if two_wing else calls
    spot = float(puts["spot"].median()) if not puts.empty else None
    if spot is None:
        return refused("no usable put quotes", session)

    slices: list[SmileSlice] = []
    for expiry_ts, group in puts.groupby("expiration"):
        expiry = pd.Timestamp(cast("pd.Timestamp", expiry_ts)).date()
        if (expiry - session).days < MIN_TENOR_DAYS:
            continue
        expiry_calls = calls[calls["expiration"] == expiry_ts] if two_wing else calls
        slices.append(
            build_slice(
                group,
                expiry_calls,
                expiry=expiry,
                session=session,
                spot=spot,
                r=r,
                q=q,
                q_known=q_source != "unknown",
                two_wing=two_wing,
            )
        )
    fitted = [
        (
            s.t_years,
            svi.RawSvi(**s.svi.model_dump()),
            min(p.k for p in s.points),
            max(p.k for p in s.points),
        )
        for s in slices
        if s.svi is not None
    ]
    return VolSurface(
        symbol=symbol,
        as_of=as_of,
        session=session,
        spot=spot,
        r=r,
        q=q,
        q_source=q_source,
        two_wing=two_wing,
        slices=slices,
        calendar_violations=svi.calendar_violations(fitted),
        refusal=None,
    )


def _symbol_rows(store: LakeStore, dataset: str, symbol: str, as_of: dt.date) -> pd.DataFrame:
    try:
        frame = store.read_bronze_as_of(dataset, as_of)
    except LookupError:
        return pd.DataFrame()
    return frame[frame["underlying"].str.lower() == symbol]


def read_vol_surface(store: LakeStore, symbol: str, as_of: dt.date) -> VolSurface:
    """The surface for ``symbol`` from the chain session known on ``as_of``."""
    symbol = symbol.lower()
    dividend = dividend_lookup(store, symbol, as_of).lookup(as_of)
    puts = _symbol_rows(store, PUT_DATASET, symbol, as_of)
    calls = _symbol_rows(store, CALL_DATASET, symbol, as_of)
    return surface_from_frames(
        puts,
        calls,
        symbol=symbol,
        as_of=as_of,
        r=DEFAULT_RATE,
        q=dividend.q,
        q_source=dividend.source,
    )
