"""Fit the tail index ``alpha`` that a quoted put ladder implies -- and refuse
when the quotes cannot support one (``AGENT_TODO.md`` P2; ``docs/adr/0026``).

The realised side refuses everywhere today: five years of daily moves show no
Karamata region on any name (``docs/DISCOVERIES.md`` §12). This is the other
side, and it does not depend on that gate. Given one anchor quote, the Paretan
price of every deeper strike is ``anchor.price * put_ratio(...)`` -- a function
of ``alpha`` alone -- so the ``alpha`` that best matches the market's deeper mids
is a measurement of how fat a tail the market is paying for.

**Refusal is a normal return value, never an exception.** An ``ImpliedAlphaFit``
with ``alpha=None`` and a ``refusal`` reason is the expected outcome whenever
the chain is too thin, too narrow, too noisy, or better described by something
other than a power law. The one exception is a caller bug -- quotes from a
different session or expiry than the anchor -- which raises ``ValueError``.

**The anchor is the caller's choice** (fixed moneyness on the Surface, delta in
the experiment). This module fits whatever anchor it is given.

**Thresholds were fixed before any real chain was fitted** (the rule
``AGENT_TODO.md`` sets, because every quote bias measured so far points toward
the paper's headline). Each constant below says why it has its value. Changing
one after seeing real output is exactly the selection the rule forbids.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import pandas as pd

from tail_lab.research.backtest.marks import MAX_RELATIVE_SPREAD, MIN_OPEN_INTEREST
from tail_lab.research.surface.ladder import Anchor
from tail_lab.research.surface.paretan import anchor_l_put, put_ratio

#: Golden-section stops when the alpha bracket is narrower than this. Tighter
#: than every tolerance a test or consumer asserts (the loosest is 1e-6).
ALPHA_TOLERANCE: Final = 1e-8

#: The fit window: strikes from the anchor down to ``FIT_SPAN * spot`` below it.
#: FIXED rather than "as deep as the chain goes", because the span alone moves a
#: fitted alpha by 0.68 (AGENT_TODO P2), and letting it float makes the estimate
#: depend on how deep each day's chain happens to be quoted.
FIT_SPAN: Final = 0.15

#: The deepest usable strike must reach at least this far below the anchor
#: (as a fraction of spot). Two thirds of ``FIT_SPAN``: a fit on the shallow
#: third of the window alone is mostly measuring the anchor's neighbourhood.
MIN_STRIKE_SPAN: Final = 0.10

#: Fewest usable deeper strikes for a fit. One free parameter leaves
#: ``n - 1`` residual degrees of freedom, and the RMSE ceiling is only a test of
#: the power-law SHAPE if there are enough of them: with one strike any alpha
#: fits exactly, and with two or three a curved non-power-law chain can still
#: pass. Four leaves three.
MIN_STRIKES: Final = 4

#: Largest RMS log-price miss a fit may have. The widest spread hygiene admits is
#: ``MAX_RELATIVE_SPREAD = 0.50``, i.e. a mid uncertain by +/-25 %: in logs
#: +0.223 / -0.288. 0.20 sits just inside the smaller of the two, so a fit that
#: misses the mids by more than the quotes themselves are uncertain is refused.
#:
#: **This is NOT a power-law test on its own.** Measured on synthetic flat-vol
#: Black-Scholes chains (spot 1000, anchor 900): refused at 30 days / 35 % vol
#: (RMS miss ~0.40), but ACCEPTED at 90 days / 35 % vol (alpha ~2.9, miss
#: ~0.16) -- what matters is sigma * sqrt(T), not vol. What gives a thin tail
#: away there is anchor dispersion: its fitted alpha rises steadily with anchor
#: depth, where a true power law's is the same at every anchor. That is PX1's
#: primary diagnostic; a single fit cannot make it.
MAX_RMSE_LOG_PRICE: Final = 0.20

#: Smallest anchor price. One tick on a sub-dollar anchor moves the fitted alpha
#: by +/-0.27 (AGENT_TODO P2); below a quarter it is mostly tick.
MIN_ANCHOR_PRICE: Final = 0.25

#: A bid at or below this is tick-pinned, not a price. Source: Cboe's
#: minimum-increment rule for options (the Cboe Options rulebook's "Minimum
#: Increments" rule; its number was not verified when this was written) --
#: classes outside the Penny Interval Program quote in $0.05 increments below
#: $3, while penny-program classes (SPY, QQQ among them) tick at $0.01 below
#: $3, so $0.05 is the conservative choice for both. Tick-pinned deep quotes bias alpha +0.25 to
#: +0.33 toward the paper's result (AGENT_TODO P2).
MIN_TICK: Final = 0.05

#: Narrowest alpha range worth searching once the bracket is cut to where the
#: anchor is inside its own calibrated tail. Narrower, and "the best alpha" is
#: mostly the domain boundary.
MIN_BRACKET_WIDTH: Final = 0.5

#: A fitted alpha this close to either end of the (cut) bracket is the bracket
#: talking, not the quotes.
EDGE_TOLERANCE: Final = 1e-4

#: Underlyings whose chain ``spot`` is on the wrong basis for the model: VIX's
#: panel spot is the index while its options settle on futures (``CLAUDE.md``).
REFUSED_UNDERLYINGS: Final = frozenset({"VIX"})

_REQUIRED_COLUMNS: Final = ("strike", "bid", "ask", "quote_date", "expiration")
_INVERSE_PHI: Final = (math.sqrt(5.0) - 1.0) / 2.0


@dataclass(frozen=True)
class ImpliedAlphaFit:
    """One fit, or one refusal. ``alpha`` is ``None`` exactly when ``refusal`` is
    set. ``n_strikes`` and ``strike_span`` describe the strikes that survived
    hygiene inside the fit window, whether or not a fit followed."""

    anchor: Anchor
    alpha: float | None
    rmse_log_price: float | None
    n_strikes: int
    strike_span: float
    refusal: str | None


def fit_implied_alpha(
    anchor: Anchor,
    quotes: pd.DataFrame,
    *,
    alpha_lo: float = 1.05,
    alpha_hi: float = 10.0,
) -> ImpliedAlphaFit:
    """Fit ``alpha`` to the hygienic strikes of ``quotes`` below ``anchor``.

    ``quotes`` holds one ``(quote_date, expiration)`` -- the anchor's -- with
    columns ``strike, bid, ask, quote_date, expiration`` and optionally
    ``open_interest`` and ``iv``. Raises ``ValueError`` only for a caller bug
    (missing columns, another session or expiry, duplicate strikes, an
    inverted bracket); every data-quality problem is a refusal.
    """
    _require_caller_contract(anchor, quotes, alpha_lo=alpha_lo, alpha_hi=alpha_hi)
    strikes, mids = _usable_strikes(anchor, quotes)
    span = (anchor.strike - min(strikes)) / anchor.spot if strikes else 0.0

    def refuse(reason: str, rmse: float | None = None) -> ImpliedAlphaFit:
        return ImpliedAlphaFit(anchor, None, rmse, len(strikes), span, reason)

    reason = _anchor_refusal(anchor)
    if reason is None and len(strikes) < MIN_STRIKES:
        reason = f"only {len(strikes)} usable strikes below the anchor (need {MIN_STRIKES})"
    if reason is None and span < MIN_STRIKE_SPAN:
        reason = (
            f"usable strikes reach {span:.3f} of spot below the anchor (need {MIN_STRIKE_SPAN})"
        )
    if reason is not None:
        return refuse(reason)

    hi = _valid_upper_bound(anchor, alpha_lo=alpha_lo, alpha_hi=alpha_hi)
    if hi is None or hi - alpha_lo < MIN_BRACKET_WIDTH:
        return refuse(
            "the anchor lies inside its own calibrated tail for almost every alpha in "
            f"[{alpha_lo}, {alpha_hi}] (valid up to {hi}); no range left to search"
        )

    def sse(alpha: float) -> float:
        return _sum_squared_log_miss(anchor, strikes, mids, alpha)

    alpha = _golden_section(sse, alpha_lo, hi)
    rmse = math.sqrt(sse(alpha) / len(strikes))
    if alpha - alpha_lo < EDGE_TOLERANCE or hi - alpha < EDGE_TOLERANCE:
        return refuse(
            f"the best alpha {alpha:.4f} sits on the search boundary [{alpha_lo}, {hi:.4f}]", rmse
        )
    if not rmse <= MAX_RMSE_LOG_PRICE:
        return refuse(f"RMS log-price miss {rmse:.3f} exceeds {MAX_RMSE_LOG_PRICE}", rmse)
    return ImpliedAlphaFit(anchor, alpha, rmse, len(strikes), span, None)


def _require_caller_contract(
    anchor: Anchor, quotes: pd.DataFrame, *, alpha_lo: float, alpha_hi: float
) -> None:
    missing = [c for c in _REQUIRED_COLUMNS if c not in quotes.columns]
    if missing:
        raise ValueError(f"quotes are missing columns: {', '.join(missing)}")
    if not (math.isfinite(alpha_lo) and math.isfinite(alpha_hi) and 1.0 < alpha_lo < alpha_hi):
        raise ValueError(f"need finite 1 < alpha_lo < alpha_hi, got [{alpha_lo}, {alpha_hi}]")
    for column, expected in (("quote_date", anchor.quote_date), ("expiration", anchor.expiration)):
        stamps = pd.to_datetime(quotes[column])
        if stamps.isna().any():
            raise ValueError(f"quotes carry a missing {column}")
        dates = set(stamps.dt.date)
        if dates - {expected}:
            raise ValueError(
                f"quotes carry {column} {sorted(dates)} but the anchor's is {expected}"
            )
    if quotes["strike"].duplicated().any():
        raise ValueError("quotes carry duplicate strikes for one session and expiry")


def _anchor_refusal(anchor: Anchor) -> str | None:
    if anchor.underlying.upper() in REFUSED_UNDERLYINGS:
        return f"{anchor.underlying}: chain spot is not the settlement basis of its options"
    if anchor.price < MIN_ANCHOR_PRICE:
        return (
            f"anchor price {anchor.price} is below {MIN_ANCHOR_PRICE}; one tick moves alpha too far"
        )
    if not _is_clean(bid=anchor.bid, ask=anchor.ask):
        return "the anchor quote itself fails hygiene (tick-pinned bid or spread too wide)"
    return None


def _usable_strikes(anchor: Anchor, quotes: pd.DataFrame) -> tuple[list[float], list[float]]:
    """Strikes inside the fit window whose quotes pass hygiene, deepest last."""
    floor = anchor.strike - FIT_SPAN * anchor.spot
    ordered = quotes.sort_values("strike", ascending=False)
    strike_col = ordered["strike"].to_numpy(dtype=float)
    bid_col = ordered["bid"].to_numpy(dtype=float)
    ask_col = ordered["ask"].to_numpy(dtype=float)
    oi_col = (
        ordered["open_interest"].to_numpy(dtype=float)
        if "open_interest" in ordered.columns
        else None
    )
    # A null IV marks a quote the exchange (Cboe zero-fill) or the vendor
    # (optionsDX solver failure) could not solve; its greeks are garbage and
    # the quote is not trusted either.
    iv_missing = ordered["iv"].isna().to_numpy() if "iv" in ordered.columns else None

    strikes: list[float] = []
    mids: list[float] = []
    for i, strike in enumerate(strike_col):
        if not floor <= strike < anchor.strike:
            continue
        if oi_col is not None and not oi_col[i] >= MIN_OPEN_INTEREST:
            continue
        if iv_missing is not None and iv_missing[i]:
            continue
        bid, ask = float(bid_col[i]), float(ask_col[i])
        if not _is_clean(bid=bid, ask=ask):
            continue
        strikes.append(float(strike))
        mids.append((bid + ask) / 2.0)
    return strikes, mids


def _is_clean(*, bid: float, ask: float) -> bool:
    """The one hygiene predicate: a bid above the minimum tick, an ask above
    the bid, and a relative spread inside ``marks.MAX_RELATIVE_SPREAD``.
    Unlike ``marks._is_liquid``, a LOCKED quote (ask == bid) is refused: a
    zero-width market on a deep put is a stale print, not a price."""
    if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= MIN_TICK or ask <= bid:
        return False
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid <= MAX_RELATIVE_SPREAD


def _anchor_is_valid(anchor: Anchor, alpha: float) -> bool:
    try:
        anchor_l_put(price=anchor.price, strike=anchor.strike, spot=anchor.spot, alpha=alpha)
    except (ValueError, OverflowError):
        # OverflowError: S0^alpha overflows for large alpha at large spot
        # (alpha ~100 at spot 1000) -- outside the tail just the same.
        return False
    return True


def _valid_upper_bound(anchor: Anchor, *, alpha_lo: float, alpha_hi: float) -> float | None:
    """Largest alpha in the bracket at which the anchor is inside its own tail.

    Measured (AGENT_TODO P2): the valid alphas always form one block starting
    at ``alpha_lo``, so bisecting the boundary is sufficient. ``None`` when even
    ``alpha_lo`` is invalid.
    """
    if not _anchor_is_valid(anchor, alpha_lo):
        return None
    if _anchor_is_valid(anchor, alpha_hi):
        return alpha_hi
    good, bad = alpha_lo, alpha_hi
    while bad - good > ALPHA_TOLERANCE:
        mid = 0.5 * (good + bad)
        if _anchor_is_valid(anchor, mid):
            good = mid
        else:
            bad = mid
    return good


def _sum_squared_log_miss(
    anchor: Anchor, strikes: list[float], mids: list[float], alpha: float
) -> float:
    total = 0.0
    for strike, mid in zip(strikes, mids, strict=True):
        ratio = put_ratio(k_from=anchor.strike, k_to=strike, spot=anchor.spot, alpha=alpha)
        if ratio <= 0.0:
            return math.inf
        total += (math.log(anchor.price * ratio) - math.log(mid)) ** 2
    return total


def _golden_section(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Minimise a unimodal ``f`` on ``[lo, hi]`` to ``ALPHA_TOLERANCE``.
    Deterministic: the same inputs give bit-identical output."""
    c = hi - _INVERSE_PHI * (hi - lo)
    d = lo + _INVERSE_PHI * (hi - lo)
    fc, fd = f(c), f(d)
    while hi - lo > ALPHA_TOLERANCE:
        if fc <= fd:
            hi, d, fd = d, c, fc
            c = hi - _INVERSE_PHI * (hi - lo)
            fc = f(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + _INVERSE_PHI * (hi - lo)
            fd = f(d)
    return 0.5 * (lo + hi)
