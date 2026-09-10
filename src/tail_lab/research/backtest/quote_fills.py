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

**A second source, same Protocol.** ``OptionQuotesSource`` below fills the
same request against ``contracts/option_quotes`` — real SPY put quotes,
2008-01-18 to 2025-11-21 — instead of optionsDX's 2010-2023 archive. It
exists because bronze OHLCV is a rolling five-year window (currently
2021-08..2026-08) and optionsDX ends in 2023-12: only ~34% of a default
four-year backtest sits inside optionsDX's coverage, while ``option_quotes``
overlaps the WHOLE current OHLCV window (49 of its 210 monthly roll dates
fall inside it, spread across 2021-08..2025-11). Where the two sources share
a guard the shared part is factored into a module-level helper (``_session``,
``_resolve_basis``, and the ``_fill``/``_mark`` guard pipelines both classes'
public methods delegate to) rather than copied — see ``OptionQuotesSource``'s
own docstring for exactly where the two diverge and why.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

import pandas as pd

from tail_lab.contracts.option_quotes import DATASET as OPTION_QUOTES_DATASET
from tail_lab.contracts.optionsdx import DATASET as OPTIONSDX_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.marks import MAX_RELATIVE_SPREAD, _select_expiry, _select_strike
from tail_lab.research.backtest.marks import _is_liquid as _is_liquid_with_open_interest

__all__ = [
    "BASIS_TOLERANCE",
    "MAX_SNAP_MONEYNESS_PP",
    "MAX_TENOR_GAP_DAYS",
    "SPLIT_FACTORS",
    "Fill",
    "OptionQuotesSource",
    "OptionsDxQuoteSource",
    "QuoteSource",
]

#: The one underlying ``contracts/option_quotes`` holds (guard 2 of
#: ``OptionQuotesSource``). Not a per-instance argument the way
#: ``OptionsDxQuoteSource.symbol`` is, because this dataset is not sliced by
#: symbol -- it only ever contains one.
_SPY = "SPY"

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

#: Largest gap, in calendar days, between a leg's REQUESTED tenor
#: (``round(tenor_weeks * 7)``) and the realized DTE of the nearest listed
#: expiry before a fill is refused as an unserviceable tenor. Only
#: ``OptionQuotesSource`` uses this (``OptionsDxQuoteSource`` passes
#: ``max_tenor_gap_days=None`` to ``_fill``) because ``option_quotes`` lists
#: exactly one expiry per roll date -- the monthly contract being rolled
#: into, DTE 26-40, median 31 (``contracts/option_quotes.py``) -- never a
#: chain of tenors the way optionsDX does. Reusing "nearest listed expiry AT
#: OR AFTER target" alone would happily stretch a 1-week request onto that
#: one 31-day contract and report it as a 1-week fill: wrong, not
#: approximate, since nothing shorter is ever on offer. Set to half the
#: measured DTE band's own width (40 - 26 = 14), i.e. 7 days: a target
#: further from the realized DTE than the band is wide is not being snapped
#: to the listed contract, it is being substituted for a materially
#: different one.
MAX_TENOR_GAP_DAYS = 7


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


#: Columns the guards actually read. Projecting to these is most of the
#: memory win: the full optionsDX SPY panel is 351 MB and these five are
#: 157 MB, against a 1024 MB machine (``fly.toml``).
_SESSION_COLUMNS = ("expiration", "strike", "bid", "ask", "spot")
#: Always needed to build the index itself.
_INDEX_COLUMNS = ("underlying", "quote_date")


def build_session_index(panel: pd.DataFrame, symbol: str) -> dict[pd.Timestamp, pd.DataFrame]:
    """Group a panel into one frame per session, filtered to ``symbol`` once.

    Guard 1 (point-in-time) is a lookup by exact ``quote_date``, and it used to
    be answered by boolean-masking the WHOLE panel on every call -- including a
    ``.str.upper()`` over 3.28M rows, which was half the cost by itself.
    Measured on the real SPY panel: **72 ms per call**, and a single backtest
    makes roughly 1,200 of them, which is where the 52.9-second run came from.

    The symbol is fixed at construction, so that filter is pure waste per call.
    Doing it once and grouping by session costs ~0.3 s and turns each lookup
    into **0.0003 ms** -- 240,000x, and the whole backtest into ~0.0 s.

    Only the columns the guards read are kept, which is separately what brings
    the resident panel under the machine's memory cap.
    """
    if panel.empty:
        return {}
    rows = panel[panel["underlying"].str.upper() == symbol]
    keep = [c for c in (*_SESSION_COLUMNS, "open_interest") if c in rows.columns]
    grouped: dict[pd.Timestamp, pd.DataFrame] = {}
    for key, frame in rows.groupby("quote_date", sort=False):
        # groupby types its key as a broad scalar union; `quote_date` is a
        # Timestamp column, so this narrows rather than converts.
        grouped[cast("pd.Timestamp", key)] = frame[keep]
    return grouped


def _session(index: dict[pd.Timestamp, pd.DataFrame], entry_date: dt.date) -> pd.DataFrame | None:
    """Guard 1 (point-in-time), shared by every quote source in this module:
    ONLY the rows whose ``quote_date`` equals ``entry_date`` exactly. Reaching
    one day forward -- or backward -- for a better-matching row is the
    look-ahead (and stale-quote) bug guard 1 in the module docstring closes,
    and a dict keyed on the exact session cannot express either reach.
    """
    return index.get(pd.Timestamp(entry_date))


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


def _resolve_basis(panel_spot: float, caller_spot: float) -> float | None:
    """``_basis`` plus guard 2's plausibility check, in one call: every
    ``fill`` on every source in this module needs exactly this pair -- the
    ratio and its own refusal -- and never the ratio alone, so the pair lives
    once here rather than being re-inlined per source."""
    basis = _basis(panel_spot, caller_spot)
    if basis is None or not _basis_is_plausible(basis):
        return None
    return basis


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


def _fill(
    index: dict[pd.Timestamp, pd.DataFrame],
    *,
    entry_date: dt.date,
    spot: float,
    moneyness_pct: float,
    tenor_weeks: float,
    is_liquid: Callable[[pd.Series], bool],
    max_tenor_gap_days: int | None,
) -> Fill | None:
    """The shared guard pipeline behind every source's ``fill`` -- guards 1-4
    and 6 from the module docstring, run identically regardless of which
    panel or symbol is behind ``panel``/``symbol``. The two guards that
    genuinely differ between sources are the only two things a caller
    injects: ``is_liquid`` is guard 5 (the module's own open-interest-less
    ``_is_liquid`` for ``OptionsDxQuoteSource``, ``marks._is_liquid`` for
    ``OptionQuotesSource`` -- see that class's docstring for why), and
    ``max_tenor_gap_days`` is ``OptionQuotesSource``'s extra tenor guard
    (``None`` for ``OptionsDxQuoteSource``, which skips it because its
    many-tenor chain makes ``_select_expiry``'s own "nearest listed expiry AT
    OR AFTER target" rule sufficient on its own).
    """
    session = _session(index, entry_date)
    if session is None or session.empty:
        return None

    # spot is recorded per (underlying, quote_date) and is constant within a
    # session; any row's value is the session's value.
    panel_spot = float(session["spot"].iloc[0])
    basis = _resolve_basis(panel_spot, spot)
    if basis is None:
        return None

    target_expiry = entry_date + dt.timedelta(days=round(tenor_weeks * 7))
    expiry = _select_expiry(session, target_expiry)
    if expiry is None:
        return None

    realized_dte = (expiry.date() - entry_date).days
    if max_tenor_gap_days is not None:
        requested_days = round(tenor_weeks * 7)
        if abs(realized_dte - requested_days) > max_tenor_gap_days:
            return None

    # Resolved in the PANEL's basis (guard 2) — never the caller's.
    target_strike = panel_spot * (1.0 - moneyness_pct / 100.0)
    row = _select_strike(session[session["expiration"] == expiry], target_strike)
    if row is None:
        return None

    realized_moneyness_pct = (1.0 - float(row["strike"]) / panel_spot) * 100.0
    if abs(realized_moneyness_pct - moneyness_pct) > MAX_SNAP_MONEYNESS_PP:
        return None

    if not is_liquid(row):
        return None

    return Fill(
        premium=float(row["ask"]) / basis,
        strike=float(row["strike"]) / basis,
        expiry=expiry.date(),
        realized_moneyness_pct=realized_moneyness_pct,
        realized_dte=realized_dte,
        basis=basis,
    )


def _mark(
    index: dict[pd.Timestamp, pd.DataFrame],
    *,
    entry_date: dt.date,
    strike: float,
    expiry: dt.date,
    basis: float,
) -> float | None:
    """The shared guard pipeline behind every source's ``mark``: the BID to
    close an already-open ``(strike, expiry)`` contract on ``entry_date``'s
    session. Both sources in this module implement the identical lookup --
    resolve the session, convert the caller's strike into the panel's basis,
    match it TIGHTLY (unlike ``_fill``'s snap tolerance, this must land on
    the exact contract already bought, never the nearest one on the board),
    and convert the bid back -- so it lives once here rather than twice. See
    ``OptionsDxQuoteSource.mark`` for why ``basis`` is a REQUIRED parameter
    rather than re-derived.
    """
    day = _session(index, entry_date)
    if day is None:
        return None
    session = day[day["expiration"] == pd.Timestamp(expiry)]
    if session.empty:
        return None

    panel_strike = strike * basis
    row = _select_strike(session, panel_strike)
    if row is None or not math.isclose(float(row["strike"]), panel_strike, rel_tol=1e-3):
        return None

    bid = float(row["bid"]) / basis
    return bid if bid > 0.0 else None


class OptionsDxQuoteSource:
    """Fills a leg request against one symbol's slice of the optionsDX panel.

    ``panel`` is the frame described in ``contracts/optionsdx.py`` (puts
    only, as-traded ``spot``); ``symbol`` selects the ``underlying`` this
    instance answers for. One instance answers for exactly one symbol so the
    basis check (guard 2) is a single number per call, not a per-row lookup.
    """

    def __init__(self, panel: pd.DataFrame, *, symbol: str) -> None:
        self._symbol = symbol.upper()
        self._sessions = build_session_index(panel, self._symbol)

    @classmethod
    def from_store(cls, store: LakeStore, *, symbol: str, as_of: dt.date) -> OptionsDxQuoteSource:
        """Read only the columns the guards use, then index.

        The constructor a route would call. Reading the whole partition
        instead costs 351 MB against a 1024 MB machine, and
        ``read_bronze_as_of`` additionally hands back a ``.copy()`` and pins
        the original in a cache that evicts by COUNT, not bytes -- so one
        asset is ~1242 MB resident and the second OOMs the box. The projected
        read is 157 MB and caches nothing.
        """
        panel = store.read_bronze_columns_as_of(
            f"{OPTIONSDX_DATASET}_{symbol.lower()}",
            as_of,
            # No open_interest: contracts/optionsdx does not carry the column,
            # which is exactly why that source drops guard 5's OI term.
            [*_INDEX_COLUMNS, *_SESSION_COLUMNS],
        )
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
        return _fill(
            self._sessions,
            entry_date=entry_date,
            spot=spot,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            is_liquid=_is_liquid,
            max_tenor_gap_days=None,
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
        return _mark(
            self._sessions,
            entry_date=entry_date,
            strike=strike,
            expiry=expiry,
            basis=basis,
        )


class OptionQuotesSource:
    """Fills a leg request against ``contracts/option_quotes`` — real SPY put
    quotes at 210 monthly roll dates, 2008-01-18 to 2025-11-21 (42,131 rows).

    Same job as ``OptionsDxQuoteSource``, same ``QuoteSource`` Protocol, and
    the same six guards from the module docstring, built on the same shared
    ``_fill``/``_mark`` pipelines — but this dataset differs from optionsDX in
    ways that change three of those guards and enrich a fourth:

    - **One underlying, not six** (guard 2). ``option_quotes`` holds SPY
      only. optionsDX answers for a different symbol per instance and would
      return ``None`` forever on one it wasn't built for; that is the wrong
      failure mode here, because a caller passing anything but SPY has a
      configuration bug, not a coverage gap, and should find out at
      construction rather than from a stream of silent ``None``. So this
      class takes no ``symbol`` argument and raises ``ValueError`` if the
      panel itself contains a non-SPY row.

    - **Monthly expiries only — exactly one per roll date, DTE 26-40 (median
      31) — never a chain of tenors** (guard 3). optionsDX's rule alone
      ("nearest listed expiry AT OR AFTER target") is right for a source that
      lists many tenors and wrong here: applied unchanged, a 1-week request
      would happily stretch onto the one 31-day contract on offer and report
      it as a 1-week fill. ``_fill`` additionally refuses whenever the
      realized DTE is more than ``MAX_TENOR_GAP_DAYS`` from the requested one
      (see that constant for the tolerance and its justification) — so
      1-week and 12-week requests are unserviceable BY DESIGN, not merely by
      this particular panel's contents, and return ``None`` rather than the
      nearest thing on the board. Guard 4's snap tolerance
      (``MAX_SNAP_MONEYNESS_PP``, reused unchanged) still does its job on the
      strike axis: strikes within 1pp of both 10% and 20% OTM are listed on
      100% of the 49 roll dates that currently overlap bronze OHLCV's window.

    - **Liquidity is the FULL test, including open interest** (guard 5) —
      ``marks._is_liquid`` unchanged, not the module's own ``_is_liquid``
      above. optionsDX has to drop the open-interest term because its panel
      does not carry the column at all (``contracts/optionsdx.py``);
      ``option_quotes`` does, and it is the one place this source is RICHER
      than the other. Measured on the panel: 0.0% of rows fail on a zero
      bid, but 8.2% fail on open interest alone — a contract with a live
      quote and nobody actually holding it, which the optionsDX guard could
      never have caught for want of the column.

    - **Split basis is measured, never assumed** (guard 2's other half, via
      the same ``_resolve_basis`` helper optionsDX uses). The measured
      panel/OHLCV ratio is exactly 1.0000 on every one of the 49 overlapping
      roll dates, because SPY has not split within this panel's span — but
      that is a measurement of today's data, not a property of the class,
      and a licence-limited panel can be re-cut under it. An implausible
      ratio is refused exactly as it would be for any other symbol.

    Pricing at the ask rather than the mid (guard 6) is identical to
    ``OptionsDxQuoteSource`` and shares its reasoning: a higher premium buys
    fewer contracts, so the ask biases the reported return down.
    """

    def __init__(self, panel: pd.DataFrame) -> None:
        if not panel.empty:
            others = set(panel["underlying"].str.upper().unique()) - {_SPY}
            if others:
                raise ValueError(
                    "OptionQuotesSource is SPY-only (contracts/option_quotes holds a "
                    f"single underlying); panel also contains {sorted(others)}"
                )
        self._symbol = _SPY
        self._sessions = build_session_index(panel, _SPY)

    @classmethod
    def from_store(cls, store: LakeStore, *, as_of: dt.date) -> OptionQuotesSource:
        """Load the ``option_quotes`` bronze snapshot known as of ``as_of``
        and build a source from it.

        Point-in-time safety starts one layer above ``fill``'s own guard:
        ``read_bronze_as_of`` (``docs/adr/0009``) is what stops a session the
        ingest had not yet recorded on ``as_of`` from being in the panel at
        all. ``fill``'s ``_session`` filter then guards the layer below
        that — a later ``quote_date`` already present inside whatever
        snapshot this method did return.

        Reads only the columns the guards use, like its optionsDX sibling.
        It matters far less here -- 42k rows against 3.28M -- but the two
        sources should not differ in how they are constructed, and this panel
        is the one that carries ``open_interest`` for the fuller liquidity
        test.
        """
        return cls(
            store.read_bronze_columns_as_of(
                OPTION_QUOTES_DATASET, as_of, [*_INDEX_COLUMNS, *_SESSION_COLUMNS, "open_interest"]
            )
        )

    def fill(
        self,
        *,
        entry_date: dt.date,
        spot: float,
        moneyness_pct: float,
        tenor_weeks: float,
    ) -> Fill | None:
        """A real listed SPY put, priced at the ask, for this request — or
        ``None`` if any guard in this class's or the module's docstring is
        not satisfied."""
        return _fill(
            self._sessions,
            entry_date=entry_date,
            spot=spot,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            is_liquid=_is_liquid_with_open_interest,
            max_tenor_gap_days=MAX_TENOR_GAP_DAYS,
        )

    def mark(
        self,
        *,
        entry_date: dt.date,
        strike: float,
        expiry: dt.date,
        basis: float = 1.0,
    ) -> float | None:
        """The BID to close the already-filled ``(strike, expiry)`` contract
        on the session dated ``entry_date``. See ``OptionsDxQuoteSource.mark``
        for why ``basis`` is a required parameter rather than re-derived and
        why the strike match is tight rather than ``fill``'s snap tolerance —
        the logic is identical; only the panel and symbol ``_mark`` reads
        differ."""
        return _mark(
            self._sessions,
            entry_date=entry_date,
            strike=strike,
            expiry=expiry,
            basis=basis,
        )
