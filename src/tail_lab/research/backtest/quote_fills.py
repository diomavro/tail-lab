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
from typing import Protocol, cast

import pandas as pd

from tail_lab.contracts.optionsdx import DATASET
from tail_lab.lake.store import LakeStore
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
    #: panel_spot / caller_spot at entry, from guard 2. Carried because `mark`
    #: needs it: `strike` above is in the CALLER's basis while the panel is
    #: keyed in its own. For NVDA those differ by exactly 10 (the 2024 split),
    #: so looking the contract up without converting back missed on 100% of
    #: calls — not the rare mid-roll event `mark`'s docstring assumed, but a
    #: permanent whole-panel offset that turned the whole mark-to-market tape
    #: into a flat line at the entry ask.
    basis: float = 1.0


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
        basis: float = 1.0,
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


#: Columns `fill`/`mark` actually read off a session, once `panel` has been
#: filtered down to this instance's one symbol (`quote_date` itself becomes
#: the `_sessions` dict key rather than a column, since the whole point of
#: the index is to never scan for it again). Built as an intersection with
#: whatever columns are actually present, not asserted present: a caller that
#: only ever exercises `mark()` (which never touches `spot`) can still build
#: a source from a panel that omits that column, exactly as the un-indexed
#: version allowed by simply never reading it until `fill()` did.
_SESSION_COLUMNS: tuple[str, ...] = ("expiration", "strike", "bid", "ask", "spot")


class OptionsDxQuoteSource:
    """Fills a leg request against one symbol's slice of the optionsDX panel.

    ``panel`` is the frame described in ``contracts/optionsdx.py`` (puts
    only, as-traded ``spot``); ``symbol`` selects the ``underlying`` this
    instance answers for. One instance answers for exactly one symbol so the
    basis check (guard 2) is a single number per call, not a per-row lookup.

    **Why this builds an index instead of filtering per call.** Measured on
    the real SPY panel (3,276,579 rows / 351 MB): the naive version — a fresh
    ``(underlying == symbol) & (quote_date == entry_date)`` boolean mask over
    the WHOLE panel inside every ``fill``/``mark`` call — costs 72-120 ms per
    call, ~56 ms of which is the ``underlying`` comparison alone (a `str`
    compare over 3.28M rows for a value that is the same on every single call
    an instance ever makes, since one instance answers for exactly one
    symbol). A single backtest makes on the order of 676 ``fill`` calls plus
    500 ``mark`` calls — 52.9 s measured end to end, against a 120 s route
    cache and a 5 s health-check timeout. None of that per-call cost is
    necessary: the symbol filter is invariant for the life of the instance,
    and the ``quote_date`` filter is an exact-match lookup, which is what a
    dict is for. So ``__init__`` filters to ``symbol`` exactly ONCE, projects
    away every column neither method reads (dropping the panel from 351 MB to
    ~157 MB before it is even grouped), and groups the result into
    ``dict[pd.Timestamp, pd.DataFrame]`` keyed by ``quote_date`` — 3,500
    sessions, built once in ~0.3 s. ``fill``/``mark`` then look a session up
    by key: ~0.0003 ms instead of 72.4 ms, a ~240,000x reduction per call,
    because the cost moved from "scan 3.28M rows" to "hash one timestamp."
    This is a pure performance change — every guard, tolerance and return
    value below is byte-for-byte the same as the version that re-filtered the
    whole panel on every call; only WHERE the filtering happens moved, from
    every call to once.
    """

    def __init__(self, panel: pd.DataFrame, *, symbol: str) -> None:
        self._symbol = symbol.upper()
        by_symbol = panel[panel["underlying"].str.upper() == self._symbol]
        kept = [c for c in _SESSION_COLUMNS if c in by_symbol.columns]
        projected = by_symbol[["quote_date", *kept]]
        self._sessions: dict[pd.Timestamp, pd.DataFrame] = {
            cast(pd.Timestamp, quote_date): session
            for quote_date, session in projected.groupby("quote_date", sort=False)[kept]
        }

    @classmethod
    def from_store(cls, store: LakeStore, *, symbol: str, as_of: dt.date) -> OptionsDxQuoteSource:
        """Build a source straight off the lake, projecting only the columns
        ``__init__`` needs (the symbol filter's ``underlying``, the session
        index's ``quote_date``, and everything in ``_SESSION_COLUMNS``) via
        :meth:`LakeStore.read_bronze_columns_as_of`. This is the constructor
        an HTTP route should use — it never materialises the 351 MB whole
        panel :meth:`LakeStore.read_bronze_as_of` would hand back for a
        dataset this size (class docstring above), on a machine capped at
        1024 MB (``fly.toml``). Not wired to a route yet; that is a later
        step.
        """
        columns = ("underlying", "quote_date", *_SESSION_COLUMNS)
        panel = store.read_bronze_columns_as_of(f"{DATASET}_{symbol.lower()}", as_of, columns)
        return cls(panel, symbol=symbol)

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
        session = self._sessions.get(pd.Timestamp(entry_date))
        if session is None:
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
            basis=basis,
        )

    def mark(
        self,
        *,
        entry_date: dt.date,
        strike: float,
        expiry: dt.date,
        basis: float = 1.0,
    ) -> float | None:
        """The BID of the already-filled contract (``strike``, ``expiry``) on
        the session dated ``entry_date`` — the sell-to-close price on one day
        of an OPEN position, not a new order.

        ``basis`` is the ratio ``fill`` measured at entry, and it is REQUIRED
        rather than re-derived: ``strike`` arrives in the caller's basis and
        the panel is keyed in its own, so the lookup must convert back and the
        returned bid must convert forward.

        An earlier version omitted it, reasoning that a basis change mid-roll
        is rare and that a miss fails safe by returning ``None``. Both halves
        were wrong for a split-adjusted name. The offset is not an event during
        the roll, it is a permanent property of the pair of sources — NVDA's is
        exactly 10 — so the tight match below missed on EVERY call, and
        "carry the last known real mark forward" degraded to "no real mark was
        ever seen, so use the entry ask forever": a daily tape showing a hedge
        with zero carry volatility for weeks and a step at expiry. Plausible,
        smooth, and entirely an artefact.

        The strike match is intentionally TIGHT (``rel_tol=1e-3``), not
        ``fill``'s snap tolerance: this call must land on the exact contract
        already bought, never the nearest one on the board.
        """
        session = self._sessions.get(pd.Timestamp(entry_date))
        if session is None:
            return None
        at_expiry = session[session["expiration"] == pd.Timestamp(expiry)]
        if at_expiry.empty:
            return None

        panel_strike = strike * basis
        row = _select_strike(at_expiry, panel_strike)
        if row is None or not math.isclose(float(row["strike"]), panel_strike, rel_tol=1e-3):
            return None

        bid = float(row["bid"]) / basis
        return bid if bid > 0.0 else None
