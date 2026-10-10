"""One name's Surface, assembled: the implied side beside the realised side
(``AGENT_TODO.md`` P4; ``docs/adr/0026``).

Pure arithmetic over a session of chain quotes and a price series -- the route
(``api/putlab_routes.py``) reads the lake and calls ``read_surface``. Every
outcome that can refuse does so as a value with a reason, so the payload renders
a refusal exactly like a number (``docs/adr/0026`` §7).

Anchors are picked by FIXED moneyness, which carries the regime confound of
``docs/PRIOR_ART.md`` §1; ``parameterisation`` says so in the payload.
"""

from __future__ import annotations

import bisect
import datetime as dt
import math
from dataclasses import dataclass
from typing import Final

import pandas as pd

from tail_lab.research.option_pricer import BlackScholesPricer
from tail_lab.research.skew import implied_vol_put
from tail_lab.research.surface.anchors import AnchorReading, AnchorReadings, read_anchors
from tail_lab.research.surface.hill import MIN_K
from tail_lab.research.surface.ladder import build_ladder
from tail_lab.research.surface.paretan import heuristic_is_valid
from tail_lab.research.surface.realised import RealisedAlpha, gated_realised_alpha
from tail_lab.research.surface.returns import compare_return_bases, loss_magnitudes

#: P5's cap on rungs drawn.
MAX_RUNGS: Final = 8
#: Points kept per survival curve, so the payload stays small.
SURVIVAL_POINTS: Final = 200
#: ``compare_return_bases`` fits at ``min(100, losses // 4)`` down-moves.
_MAX_COMPARISON_K: Final = 100

PARAMETERISATION: Final = (
    "fixed moneyness: each anchor is the listed strike nearest spot*(1 - m/100); "
    "this confounds regime comparisons (docs/PRIOR_ART.md §1)"
)
LOG_BASIS_NOTE: Final = "dismissed -- docs/adr/0026 §5: log returns are not in RV_alpha"
_Q_DEPENDS: Final = "anchor_iv, lambda_guard_ok and the Black-Scholes overlay depend on it"


#: Each measured source in a reader's words -- never the raw label.
_Q_SOURCE_WORDS = {
    "measured": "this name's own dividend yield, from its paid dividends",
    "short_history": "this name's own dividend yield, from fewer payments than a year, scaled up",
    "carried": "this name's own dividend yield, carried from the data's last day",
    "stale": "this name's own dividend yield, carried over three weeks -- Tiingo needs a refresh",
    "non_payer": "zero: this name has never paid a dividend",
    "suspended": "zero: this name has stopped paying dividends",
}


def rate_note_for(q_source: str) -> str:
    """What the reader must know about ``q`` for this reading.

    ``"assumed"`` is the fallback whenever the name's own yield is ``unknown``:
    before the first Tiingo ingest, a name it does not cover, a date before the
    name's data, a single dividend so far or since a long pause (no frequency
    yet), or a dividend the
    close cannot support. It is the old flat index-like 1.9% -- wrong for income
    names, and not what the backtest uses there (q = 0, labelled unknown). Every other source comes
    from the name's own dividend record (`transforms/dividend_yield.py`): a
    measured yield, or a real zero for a name that never paid or stopped.
    """
    if q_source == "assumed":
        return (
            "q is an assumed index-like dividend yield (this name's own yield could not be measured) "
            f"and is WRONG for income names (HYG, TLT); {_Q_DEPENDS}"
        )
    return f"q is {_Q_SOURCE_WORDS.get(q_source, q_source)}; {_Q_DEPENDS}"


@dataclass(frozen=True)
class SurvivalCurve:
    """``(x, P(X > x))`` points of an empirical sample, thinned to a cap."""

    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class LogBasis:
    """The log-basis Hill alpha, shown and dismissed; never gated, never used."""

    alpha: float | None
    k: int | None
    refusal: str | None
    note: str


@dataclass(frozen=True)
class RealisedSide:
    horizon_days: int
    alpha: float | None
    standard_error: float | None
    plateau_k: int | None
    onset: float | None
    n_beyond: int
    is_flat: bool | None
    refusal: str | None
    log_basis: LogBasis
    survival_gross: SurvivalCurve | None
    survival_loss: SurvivalCurve | None


@dataclass(frozen=True)
class SurfaceRung:
    strike: float
    bid: float | None
    ask: float | None
    paretan_price: float
    market_price: float | None
    black_scholes_price: float | None
    paretan_iv: float | None
    market_iv: float | None
    iv_ratio: float | None


@dataclass(frozen=True)
class SurfaceReading:
    underlying: str
    quote_date: dt.date
    expiration: dt.date
    t_days: int
    spot: float
    moneyness_pct: float
    parameterisation: str
    r: float
    q: float
    #: Where ``q`` came from: a ``transforms.dividend_yield`` source, or
    #: ``"assumed"`` for the flat index-like fallback.
    q_source: str
    rate_note: str
    anchor_iv: float | None
    lambda_guard_ok: bool | None
    anchors: AnchorReadings
    ladder: tuple[SurfaceRung, ...]
    ladder_reason: str | None
    realised: RealisedSide | None
    realised_reason: str | None
    alpha_gap: float | None
    alpha_gap_reason: str | None


def survival_curve(values: list[float], *, max_points: int = SURVIVAL_POINTS) -> SurvivalCurve:
    """Empirical ``P(X > x)`` at each sorted observation, evenly thinned.

    Ties report the share strictly above the value, so the largest point is 0.
    """
    ordered = sorted(values)
    n = len(ordered)
    # bisect_right gives the first strictly greater value: the exact tie-aware share.
    points = [(x, (n - bisect.bisect_right(ordered, x)) / n) for x in ordered]
    if len(points) > max_points:
        step = (len(points) - 1) / (max_points - 1)
        points = [points[round(i * step)] for i in range(max_points)]
    return SurvivalCurve(tuple(points))


def _log_basis(stepped: pd.Series, n_losses: int) -> LogBasis:
    k = min(_MAX_COMPARISON_K, n_losses // 4)
    if k < MIN_K:
        return LogBasis(None, None, f"too few losses ({n_losses}) for a Hill fit", LOG_BASIS_NOTE)
    try:
        comparison = compare_return_bases(stepped, k=k)
    except ValueError as exc:
        return LogBasis(None, k, str(exc), LOG_BASIS_NOTE)
    return LogBasis(comparison.alpha_log.alpha, k, None, LOG_BASIS_NOTE)


def read_realised(prices: pd.Series, *, horizon_days: int) -> RealisedSide:
    """The realised side at ``horizon_days`` calendar days (the expiry's own)."""
    realised: RealisedAlpha = gated_realised_alpha(prices, horizon_days=horizon_days)
    stepped = realised.stepped_prices
    try:
        losses = [float(x) for x in loss_magnitudes(stepped)]
    except ValueError:
        losses = []
    gross = [float(x) for x in (stepped / stepped.shift(1)).dropna()]
    plateau, fit = realised.plateau, realised.fit
    return RealisedSide(
        horizon_days=horizon_days,
        alpha=plateau.alpha if plateau else None,
        standard_error=plateau.standard_error if plateau else None,
        plateau_k=plateau.k if plateau else None,
        onset=fit.onset if fit else None,
        n_beyond=realised.n_beyond,
        is_flat=fit.is_flat if fit else None,
        refusal=realised.refusal,
        log_basis=_log_basis(stepped, len(losses)),
        survival_gross=survival_curve(gross) if gross else None,
        survival_loss=survival_curve(losses) if losses else None,
    )


def _rungs(
    reading: AnchorReading, quotes: pd.DataFrame, *, anchor_iv: float | None, r: float, q: float
) -> tuple[tuple[SurfaceRung, ...], str | None]:
    fit = reading.fit
    if fit is None or fit.alpha is None:
        return (), "no accepted implied alpha at the first anchor to price a ladder from"
    anchor = fit.anchor
    deeper = quotes[(quotes["strike"] < anchor.strike) & (quotes["bid"] > 0)]
    deeper = deeper.drop_duplicates("strike").sort_values("strike", ascending=False)
    deeper = deeper.head(MAX_RUNGS)
    by_strike = {float(row["strike"]): row for _, row in deeper.iterrows()}
    mids = {k: (float(v["bid"]) + float(v["ask"])) / 2.0 for k, v in by_strike.items()}
    try:
        ladder = build_ladder(
            anchor, alpha=fit.alpha, strikes=list(mids), market_mids=mids, r=r, q=q
        )
    except ValueError as exc:
        return (), str(exc)
    pricer = BlackScholesPricer()
    rungs = tuple(
        SurfaceRung(
            strike=rung.strike,
            bid=float(by_strike[rung.strike]["bid"]),
            ask=float(by_strike[rung.strike]["ask"]),
            paretan_price=rung.paretan_price,
            market_price=rung.market_price,
            black_scholes_price=None
            if anchor_iv is None
            else pricer.price_put(
                spot=anchor.spot,
                strike=rung.strike,
                t_years=anchor.t_years,
                sigma=anchor_iv,
                r=r,
                q=q,
            ),
            paretan_iv=rung.paretan_iv,
            market_iv=rung.market_iv,
            iv_ratio=rung.iv_ratio,
        )
        for rung in ladder
    )
    return rungs, None


def _gap(
    anchors: AnchorReadings, realised: RealisedSide | None, why_none: str | None
) -> tuple[float | None, str | None]:
    if realised is None:
        return None, why_none
    if realised.alpha is None:
        return None, f"no realised alpha: {realised.refusal}"
    first = anchors.readings[0].fit if anchors.readings else None
    if first is None or first.alpha is None:
        return None, "no accepted implied alpha at the first anchor"
    return first.alpha - realised.alpha, None


def pick_expiry(quotes: pd.DataFrame, tenor_days: float) -> tuple[dt.date, dt.date] | None:
    """``(quote_date, expiration)`` of the listed expiry nearest ``tenor_days``."""
    if quotes.empty:
        return None
    quote_date = pd.Timestamp(quotes["quote_date"].max()).date()
    expirations = sorted({pd.Timestamp(e).date() for e in quotes["expiration"]})
    live = [e for e in expirations if e > quote_date]
    if not live:
        return None
    return quote_date, min(live, key=lambda e: abs((e - quote_date).days - tenor_days))


def read_surface(
    chain: pd.DataFrame,
    prices: pd.Series | None,
    *,
    underlying: str,
    moneyness_pct: float,
    tenor_days: float,
    r: float,
    q: float,
    q_source: str = "assumed",
) -> SurfaceReading | None:
    """Assemble the Surface for ``underlying`` from one session of chain quotes.

    ``chain`` is one ``option_chain_snapshot`` session; ``prices`` the point-in-
    time close series (``None`` when no OHLCV is known). Returns ``None`` when
    the name has no listed expiry in the session -- the route turns that into a
    404, since there is nothing to read.
    """
    own = chain[chain["underlying"].str.upper() == underlying.upper()]
    picked = pick_expiry(own, tenor_days)
    if picked is None:
        return None
    quote_date, expiration = picked
    quotes = own[pd.to_datetime(own["expiration"]).dt.date == expiration]
    spot = float(quotes["spot"].iloc[0])
    t_days = (expiration - quote_date).days
    anchors = read_anchors(
        quotes, underlying=underlying.upper(), spot=spot, moneyness_pct=moneyness_pct, r=r, q=q
    )
    first = anchors.readings[0] if anchors.readings else None
    anchor_iv: float | None = None
    guard: bool | None = None
    if first is not None and first.fit is not None:
        a = first.fit.anchor
        anchor_iv = implied_vol_put(
            a.price, spot=a.spot, strike=a.strike, t_years=a.t_years, r=r, q=q
        )
        if anchor_iv is not None and math.isfinite(anchor_iv):
            guard = heuristic_is_valid(sigma=anchor_iv, t_years=a.t_years)
    rungs, ladder_reason = (
        _rungs(first, quotes, anchor_iv=anchor_iv, r=r, q=q)
        if first is not None
        else ((), "no anchor strike below spot on this expiry")
    )
    realised = None if prices is None else read_realised(prices, horizon_days=t_days)
    realised_reason = "no OHLCV known as of this date" if prices is None else None
    gap, gap_reason = _gap(anchors, realised, realised_reason)
    return SurfaceReading(
        underlying=underlying.upper(),
        quote_date=quote_date,
        expiration=expiration,
        t_days=t_days,
        spot=spot,
        moneyness_pct=moneyness_pct,
        parameterisation=PARAMETERISATION,
        r=r,
        q=q,
        q_source=q_source,
        rate_note=rate_note_for(q_source),
        anchor_iv=anchor_iv,
        lambda_guard_ok=guard,
        anchors=anchors,
        ladder=rungs,
        ladder_reason=ladder_reason,
        realised=realised,
        realised_reason=realised_reason,
        alpha_gap=gap,
        alpha_gap_reason=gap_reason,
    )


__all__ = ["SurfaceReading", "read_realised", "read_surface", "survival_curve"]
