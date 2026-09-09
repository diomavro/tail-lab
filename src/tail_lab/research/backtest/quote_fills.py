"""Quote fills — the REAL listed contract behind an "N% OTM, T-week" request.

``roll_schedule`` and ``marks`` both name a leg by its *target*: an exact
strike no chain lists (``spot x (1 - moneyness/100)``) and a tenor counted in
weeks rather than a listed expiry. ``marks`` snaps that target against the
platform's own forward-collected chain, which only starts on 2026-08-26. This
module does the same snap against ``optionsDX``'s 2010-2023 archive instead,
so a backtest can ask "what would I actually have paid for this, historically"
years before the forward collection existed.

**Why this exists at all, and not just a wider Black-Scholes band.** The
platform's flat-vol pricer is not merely imprecise deep out-of-the-money — it
underflows. On SPY, 2020-01-06, the measured market/model ratio at 20% OTM is
**29,000,000x**: the model prices the put at a number indistinguishable from
zero, and every return figure on that leg divides a fixed premium budget by
it, buying an unbounded position. (``docs/MODEL_RESIDUAL.md`` measures the
same underflow at a shallower 20% OTM as 21,663x on a *monthly* calibration
set — the daily figure above is worse because a single stressed day can sit
even further out on the tail than the median.) A request for a deep-OTM tail
hedge cannot be priced by the model at all; it can only be *filled* against a
real quote. That is what this module does, and nothing else: it does not
touch ``put_roll``, does not wire into the API, and prices at the historical
**ask**, never the model.

**The guards below are not defensive padding — each one is load-bearing and
each has a specific, measured failure mode it closes:**

1. **Point-in-time** (``docs/adr/0009``, the platform's #1 invariant). A fill
   reads only the panel row whose ``quote_date`` equals the requested entry
   date. Reaching one day forward for a better-matching strike is exactly the
   look-ahead bug that invalidates a backtest silently rather than loudly.

2. **Split basis.** ``optionsDX``'s own ``spot`` column is AS-TRADED; a
   caller pricing off split-ADJUSTED OHLCV (the platform's only other spot
   source) is quoting a different number for the same day. Measured
   2026-09-09, panel/OHLCV median: spy 1.0000, qqq 1.0000, tsla 1.0003,
   **nvda 10.0000** (the 2024 10:1 split). Resolving the target strike in the
   caller's basis against a chain quoted in the panel's basis is off by
   exactly that factor with no error raised — a silent 10x on NVDA reads as a
   strike near $12 instead of $120, a completely different trade under the
   same label. So the strike is always resolved in the PANEL's basis, and the
   result is converted back by dividing by the measured ``basis``; a
   ``basis`` that is neither ~1 nor ~a clean split factor is refused outright
   rather than guessed at.

3. **Expiry selection.** Nearest listed expiry AT OR AFTER the target, using
   ``marks._select_expiry`` (and ``marks._select_strike`` below) directly
   rather than a second copy: rounding an expiry DOWN buys a shorter,
   cheaper, different option, and that is the one direction that quietly
   flatters a result compared against it.

4. **Snap tolerance.** Even after point-in-time and basis are both right, the
   nearest LISTED strike is not always close to the REQUESTED one — strikes on
   the real chain are spaced, not continuous. Measured on this panel: at
   1-week tenors, the 5th-percentile realized moneyness for a requested 30%
   OTM cell is 14.58% — a completely different strategy hiding under the same
   label. At 4w/12w tenors the snap is fine (p5 19.4% for a 20% request), so
   ``MAX_SNAP_MONEYNESS_PP`` mostly bites exactly where the chain is too
   sparse to honor the request, which is where it should.

5. **Liquidity.** ``marks._is_liquid`` ported minus its open-interest term —
   this panel has no ``open_interest`` column (``contracts/optionsdx.py``).
   Deliberately NOT filtered on ``volume``: deep-OTM put volume spikes on
   stress days and is thin on calm ones, so a volume floor would delete calm
   days and bias the surviving sample, and the strategy it describes, upward.

6. **Ask, not mid.** ``premium`` is always the ask. A buyer pays the offer,
   and because contracts bought = budget / premium, a higher premium buys
   FEWER contracts — so pricing at the ask biases the reported return DOWN.
   That is the conservative direction, and it is conservative by
   construction, not by tuning.

A ``fill`` that cannot satisfy every one of 1-5 returns ``None`` rather than
the nearest thing it could find. A caller that wants to know *why* has to
re-run the guards itself for now — this module reports success or refusal,
not a reason code, because nothing downstream of it consumes one yet
(``marks.MarkedLeg.quote_status`` is the pattern to follow if that changes).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from tail_lab.research.backtest.marks import (
    MAX_RELATIVE_SPREAD,
    _select_expiry,
    _select_strike,
)

__all__ = [
    "BASIS_TOLERANCE",
    "MAX_SNAP_MONEYNESS_PP",
    "SPLIT_FACTORS",
    "Fill",
    "OptionsDxQuoteSource",
    "QuoteSource",
]

#: Largest gap, in percentage points, between the requested moneyness and the
#: realized moneyness of the nearest LISTED strike before a fill is refused.
#: See guard 4 above for the measured 14.58%-p5 failure this closes.
MAX_SNAP_MONEYNESS_PP = 1.0

#: Whole-number split ratios a measured basis is allowed to match, besides ~1.
#: NVDA's 2024 10:1 split is the one seen in this panel; the rest cover splits
#: that could occur in a wider symbol set without guessing at an exact factor.
SPLIT_FACTORS: tuple[int, ...] = (2, 3, 4, 5, 7, 10, 20)

#: Relative tolerance a measured basis must fall within of 1.0, or of 1.0
#: divided by a clean split factor, to be trusted. Measured medians (spy
#: 1.0000, qqq 1.0000, tsla 1.0003, nvda 10.0000) all sit far inside this; a
#: ratio like 1.5 that fits neither pattern is a data problem, not a split.
BASIS_TOLERANCE = 0.01


@dataclass(frozen=True)
class Fill:
    """A REAL listed contract, priced at the ask, that fills one leg request.

    Every field is in the CALLER's price-series basis (split-adjusted OHLCV),
    already converted from whatever basis the quote panel itself used — see
    guard 2 in the module docstring. There is no "model" field here: unlike
    ``marks.MarkedLeg``, a ``Fill`` never carries a Black-Scholes estimate to
    compare itself against, because the whole reason it exists is that the
    model has already underflowed to ~0 at the depths this is used for.
    """

    #: Per share, the ASK — see guard 6. Never the mid.
    premium: float
    strike: float
    expiry: dt.date
    #: ``(1 - listed_strike / panel_spot) x 100`` at the realized strike, not
    #: the requested one — how far the snap actually landed.
    realized_moneyness_pct: float
    #: Calendar days from ``entry_date`` to the realized ``expiry``.
    realized_dte: int


class QuoteSource(Protocol):
    """Anything that can turn a leg request into a real fill or ``None``."""

    def fill(
        self,
        *,
        entry_date: dt.date,
        spot: float,
        moneyness_pct: float,
        tenor_weeks: float,
    ) -> Fill | None: ...

    def mark(
        self,
        *,
        entry_date: dt.date,
        strike: float,
        expiry: dt.date,
    ) -> float | None:
        """The BID to close an already-open ``(strike, expiry)`` contract on
        the session dated ``entry_date`` (the day being marked -- not
        necessarily the day the contract was bought), or ``None`` if that
        session/contract isn't in this source. Used by
        ``put_roll._mark_to_market_curve`` to mark a market-filled leg
        day-by-day without ever substituting the underflowing model (see the
        module docstring). See ``OptionsDxQuoteSource.mark`` for why this
        takes no caller spot (no basis re-derivation, unlike ``fill``) and
        matches the strike tightly rather than with ``fill``'s snap
        tolerance."""
        ...


def _basis(panel_spot: float, caller_spot: float) -> float | None:
    """``panel_spot / caller_spot``, or ``None`` if either is non-positive.

    A non-positive spot on either side means the day is unusable regardless of
    what the ratio would say — dividing by it later would be arithmetic on a
    number that cannot describe a real trading session.
    """
    if panel_spot <= 0.0 or caller_spot <= 0.0:
        return None
    return panel_spot / caller_spot


def _basis_is_plausible(basis: float) -> bool:
    """Whether ``basis`` looks like "same basis" or "one clean split apart",
    per guard 2. Anything else — 1.5x, a stale spot, a data error — is refused
    rather than divided through, because a silent factor here does not look
    wrong: it looks like a plausible, completely different trade."""
    if abs(basis - 1.0) <= BASIS_TOLERANCE:
        return True
    return any(abs(basis / factor - 1.0) <= BASIS_TOLERANCE for factor in SPLIT_FACTORS)


def _is_liquid(row: pd.Series) -> bool:
    """``marks._is_liquid`` minus the open-interest term (guard 5): this panel
    has no ``open_interest`` column, so a zero bid and a sane relative spread
    are the only signals available that a quote is a price rather than an
    unfilled placeholder."""
    bid = float(row["bid"])
    ask = float(row["ask"])
    if bid <= 0.0 or ask <= 0.0:
        return False
    mid = (bid + ask) / 2
    return mid > 0.0 and (ask - bid) / mid <= MAX_RELATIVE_SPREAD


class OptionsDxQuoteSource:
    """Fills a leg request against one symbol's slice of the optionsDX panel.

    ``panel`` is the frame described in ``contracts/optionsdx.py`` (puts
    only, as-traded ``spot``); ``symbol`` selects the ``underlying`` this
    instance answers for. One instance answers for exactly one symbol so the
    basis check (guard 2) is a single number per call, not a per-row lookup.
    """

    def __init__(self, panel: pd.DataFrame, *, symbol: str) -> None:
        self._panel = panel
        self._symbol = symbol.upper()

    def fill(
        self,
        *,
        entry_date: dt.date,
        spot: float,
        moneyness_pct: float,
        tenor_weeks: float,
    ) -> Fill | None:
        """A real listed put, priced at the ask, for this request — or
        ``None`` if any guard in the module docstring is not satisfied."""
        session = self._panel[
            (self._panel["underlying"].str.upper() == self._symbol)
            & (self._panel["quote_date"] == pd.Timestamp(entry_date))
        ]
        if session.empty:
            return None

        # spot is recorded per (underlying, quote_date) and is constant
        # within a session; any row's value is the session's value.
        panel_spot = float(session["spot"].iloc[0])
        basis = _basis(panel_spot, spot)
        if basis is None or not _basis_is_plausible(basis):
            return None

        target_expiry = entry_date + dt.timedelta(days=round(tenor_weeks * 7))
        expiry = _select_expiry(session, target_expiry)
        if expiry is None:
            return None

        # Resolved in the PANEL's basis (guard 2) — never the caller's.
        target_strike = panel_spot * (1.0 - moneyness_pct / 100.0)
        row = _select_strike(session[session["expiration"] == expiry], target_strike)
        if row is None:
            return None

        realized_moneyness_pct = (1.0 - float(row["strike"]) / panel_spot) * 100.0
        if abs(realized_moneyness_pct - moneyness_pct) > MAX_SNAP_MONEYNESS_PP:
            return None

        if not _is_liquid(row):
            return None

        return Fill(
            premium=float(row["ask"]) / basis,
            strike=float(row["strike"]) / basis,
            expiry=expiry.date(),
            realized_moneyness_pct=realized_moneyness_pct,
            realized_dte=(expiry.date() - entry_date).days,
        )

    def mark(
        self,
        *,
        entry_date: dt.date,
        strike: float,
        expiry: dt.date,
    ) -> float | None:
        """The BID of the already-filled contract (``strike``, ``expiry``) on
        the session dated ``entry_date`` — the sell-to-close price on one day
        of an OPEN position, not a new order.

        Unlike ``fill``, this does not re-derive the split basis (guard 2):
        it has no caller spot to compute one from, ``strike``/``expiry`` name
        the SAME contract ``fill`` already basis-checked once at entry, and a
        split occurring mid-roll is rare enough over a several-week hold that
        re-deriving it here would trade real complexity for a guard against
        an edge case the caller already handles safely -- a basis jump big
        enough to matter moves ``strike`` far enough from anything listed
        that the tight match just below misses, and this returns ``None``,
        which ``_mark_to_market_curve`` already treats as "carry the last
        mark forward" rather than trusting a wrong number.

        The strike match is intentionally TIGHT (``rel_tol=1e-3``), not
        ``fill``'s snap tolerance: this call must land on the exact contract
        already bought, never the nearest one on the board.
        """
        session = self._panel[
            (self._panel["underlying"].str.upper() == self._symbol)
            & (self._panel["quote_date"] == pd.Timestamp(entry_date))
            & (self._panel["expiration"] == pd.Timestamp(expiry))
        ]
        if session.empty:
            return None

        row = _select_strike(session, strike)
        if row is None or not math.isclose(float(row["strike"]), strike, rel_tol=1e-3):
            return None

        bid = float(row["bid"])
        return bid if bid > 0.0 else None
