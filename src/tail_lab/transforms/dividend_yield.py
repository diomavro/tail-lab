"""Bronze Tiingo EOD -> the continuous dividend yield ``q`` an option price needs.

Pure by design: frames in, values out; reading bronze as-of lives in
``research/dividends.py``.

**The estimator is the vendor-standard indicated annual dividend**, kept
deliberately simple. Review found every calendar-window variant miscounting
around ex-dates (a 365-day window holds *five* SPY payments on 55 of its 67
ex-dates since 2010, because quarterly ex-dates fall 364 days apart), and
every "regular vs special" heuristic discarding real raises (JPM 2011, NVDA
2026). So, at a date ``t``:

1. **Run.** Seed a period ``P0`` from the median of the last four gaps
   between all payments with ex-date ≤ t. The *run* is the payments since the
   last gap longer than ``3·P0`` -- a suspension -- so a payer that resumes
   starts fresh rather than averaging in decade-old dividends.
2. **Frequency.** ``N`` is the run's MEAN gap over its payments in the last
   700 days (at least its last two), snapped to the nearest of 1, 2, 4 or 12
   payments a year. The mean, not a median of recent gaps, because two extra
   payments in a quarterly payer's year halve the median and would snap to 12
   -- summing three years of dividends, then a false suspension. (A count of
   payments over 730 days failed the other way: an annual payer whose ex-date
   drifts earlier fits three payments; a mean gap still reads ~364 days.)
   Snapping matters too: iShares pays twice in December and not in January
   (still 12).
3. **Annual dividend.** ``D`` is the sum of the run's last ``N`` payments, each
   put on ``t``'s as-traded basis by dividing out the splits after its ex-date.
4. **Yield.** ``q = -ln(1 - D / close_t)`` -- the forward-consistent form
   (``ln(1 + y)`` is off by about ``y²``).

Labelled states, so no number is ever silently zero:

* ``non_payer`` -- no payment on record by ``t`` (``q = 0``, a real basis);
* ``suspended`` -- the last payment is older than twice the period
  (``q = 0``; Boeing, last paid 2020-02-13; twice, not 1.5x, because the
  iShares year-end gap reaches ~49 days on a monthly payer);
* ``short_history`` -- the run has fewer than ``N`` payments, so ``D`` is scaled
  by ``N / count`` (AAPL resuming in 2012);
* ``unknown`` -- a run of a single payment (no gap to read a frequency from),
  a date before the symbol's data, or a dividend the close cannot support;
  priced at ``q = 0`` and labelled;
* ``carried`` / ``stale`` -- see :meth:`DividendYields.at`.

**Disclosed, not hidden.** A frequency change biases ``q`` in both directions
for up to a year, until the new frequency fills the count (TSM 2019-20 reads
high, then low, then high, and settles by mid-2020). A dividend cut lags by up to N payments
(JPM 2009). Tiingo does not flag specials, and one distorts ``q`` for up to N
payments in EITHER direction: a special larger than a regular payment inflates
``D`` (SPY 2004), a smaller one displaces a regular payment and deflates it (an
annual $2 payer with a $0.50 special reads ``D = 0.50`` until its next regular
payment). A special can also move ``N`` itself, depending on where it falls: a
semiannual payer with ONE reads ``N = 4`` for weeks of the following year
(whenever the window holds three regular payments and the special), and an
annual payer can read ``N = 2``. A carried ``short_history`` value is labelled ``carried``; its
``annual`` is still the scaled figure. And a trailing estimate misses a dividend inside a short put's
life: the stock drops a whole quarterly dividend (~0.3% of spot at q ~ 1.3%)
inside a 21-day SPY put that spans an ex-date, where ``q·T`` (T = 21/252) takes
only ~0.1% off the expected price -- that put's model expects the stock ~0.2% of
spot too high (under-priced), the two in three that span no ex-date ~0.1% too low
(over-priced); the premium errors are those shifts times the put's delta, and they
net out over a year, not per roll.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import pandas as pd

__all__ = [
    "CARRY_STALE_DAYS",
    "DividendYield",
    "DividendYields",
    "Payment",
    "YieldSource",
    "build_dividend_yields",
]

YieldSource = Literal[
    "measured", "non_payer", "suspended", "short_history", "carried", "stale", "unknown"
]

#: Past the data's last row the last yield is carried; beyond this many
#: calendar days (two missed weekly runs) it is still carried, but flagged.
CARRY_STALE_DAYS = 21

_FREQUENCIES = (1, 2, 4, 12)
_GAP_WINDOW = 4
_FREQUENCY_WINDOW_DAYS = 700
_SESSION_CACHE_MAX = 2_000
_RESUMPTION_FACTOR = 3.0
_SUSPENSION_FACTOR = 2.0


@dataclass(frozen=True)
class Payment:
    """One dividend as it entered ``D``: paid ``cash`` per share on
    ``ex_date``; ``adjusted`` is that cash on the pricing date's share basis."""

    ex_date: dt.date
    cash: float
    adjusted: float


@dataclass(frozen=True)
class DividendYield:
    """``q`` at one date, with where it came from."""

    q: float
    source: YieldSource
    #: Calendar days the value was carried past the data's last row (0 inside).
    age_days: int
    #: The payments summed into ``D`` -- empty unless the source is
    #: ``measured``/``short_history`` (or carried from one).
    payments: tuple[Payment, ...]
    #: The as-traded close ``q`` was computed against (``None`` when unknown).
    close: float | None
    #: ``D`` exactly as used: the payments' sum, scaled to a full year for
    #: ``short_history`` (so ``q == -ln(1 - annual / close)`` always holds).
    annual: float = 0.0
    #: ``N``, the payments a year ``D`` was built for (0 when not measured).
    per_year: int = 0


def _snap(gap_days: float) -> int:
    per_year = 365.0 / gap_days
    return min(_FREQUENCIES, key=lambda n: abs(math.log(per_year) - math.log(n)))


class DividendYields:
    """One symbol's dividend history, answering :meth:`at` for any date."""

    def __init__(self, symbol: str, rows: pd.DataFrame) -> None:
        ordered = rows.sort_values("trade_date")
        self.symbol = symbol
        self._dates = ordered["trade_date"].to_numpy(dtype="datetime64[D]")
        self._closes = ordered["close"].to_numpy(dtype=float)
        paid = ordered.loc[ordered["div_cash"] > 0]
        self._ex = paid["trade_date"].to_numpy(dtype="datetime64[D]")
        self._cash = paid["div_cash"].to_numpy(dtype=float)
        split = ordered.loc[ordered["split_factor"] != 1.0]
        self._split_dates = split["trade_date"].to_numpy(dtype="datetime64[D]")
        self._split_factors = split["split_factor"].to_numpy(dtype=float)
        # Sessions already computed: a sweep asks for the same roll dates across
        # every cell (~2,000 calls a name), and each is pure in the session.
        self._sessions: dict[int, DividendYield] = {}

    @property
    def last_date(self) -> dt.date | None:
        return None if not len(self._dates) else _to_date(self._dates[-1])

    def at(self, day: dt.date) -> DividendYield:
        """``q`` on ``day``, using only rows dated on or before it.

        A ``day`` between sessions takes the last session's close. A ``day``
        past the data's last row carries that row's value with its age; a
        priced value is never dropped to 0 for being old (that is the bias this
        dataset exists to remove), but past :data:`CARRY_STALE_DAYS` it is
        flagged ``stale``.
        """
        stamp = np.datetime64(day, "D")
        idx = int(np.searchsorted(self._dates, stamp, side="right")) - 1
        if idx < 0:
            return DividendYield(0.0, "unknown", 0, (), None)
        value = self._sessions.get(idx)
        if value is None:
            if len(self._sessions) >= _SESSION_CACHE_MAX:
                self._sessions.clear()  # bounded: ~1 KB a session, 8,400 sessions a name
            value = self._sessions[idx] = self._on_session(idx)
        age = int((stamp - self._dates[idx]).astype(int))
        if age == 0 or self._dates[idx] != self._dates[-1]:
            return value
        if value.source in ("measured", "short_history"):
            source: YieldSource = "stale" if age > CARRY_STALE_DAYS else "carried"
            return replace(value, source=source, age_days=age)
        return replace(value, age_days=age)

    def _on_session(self, idx: int) -> DividendYield:
        t = self._dates[idx]
        close = float(self._closes[idx])
        k = int(np.searchsorted(self._ex, t, side="right"))
        if k == 0:
            return DividendYield(0.0, "non_payer", 0, (), close)
        ex = self._ex[:k]
        if k == 1:
            return DividendYield(0.0, "unknown", 0, (), None)
        gaps = np.diff(ex).astype(float)
        seed = float(np.median(gaps[-_GAP_WINDOW:]))
        breaks = np.nonzero(gaps > _RESUMPTION_FACTOR * seed)[0]
        start = int(breaks[-1]) + 1 if len(breaks) else 0
        run_gaps = gaps[start:]
        if not len(run_gaps):
            # A resumed run of one payment: no gap of its own yet.
            period = 365.0 / _snap(seed)
            if float((t - ex[-1]).astype(float)) > _SUSPENSION_FACTOR * period:
                return DividendYield(0.0, "suspended", 0, (), close)
            return DividendYield(0.0, "unknown", 0, (), None)
        n = _frequency(ex[start:], t)
        if float((t - ex[-1]).astype(float)) > _SUSPENSION_FACTOR * 365.0 / n:
            return DividendYield(0.0, "suspended", 0, (), close)
        run = range(start, k)
        used = list(run)[-n:]
        payments = tuple(
            Payment(_to_date(ex[i]), float(self._cash[i]), self._on_basis(i, t)) for i in used
        )
        annual = sum(p.adjusted for p in payments)
        source: YieldSource = "measured"
        if len(used) < n:
            annual *= n / len(used)
            source = "short_history"
        ratio = annual / close
        if not 0.0 <= ratio < 1.0:
            return DividendYield(0.0, "unknown", 0, (), None)
        return DividendYield(-math.log1p(-ratio), source, 0, payments, close, annual, n)

    def _on_basis(self, payment: int, t: np.datetime64) -> float:
        """Payment ``payment``'s cash on ``t``'s as-traded share basis."""
        ex = self._ex[payment]
        after = (self._split_dates > ex) & (self._split_dates <= t)
        return float(self._cash[payment] / np.prod(self._split_factors[after]))


def _frequency(run_ex: np.ndarray, t: np.datetime64) -> int:
    """Payments a year for a run, snapped to 1, 2, 4 or 12: the MEAN gap
    across the run's payments in the last :data:`_FREQUENCY_WINDOW_DAYS` days
    (at least its last two, however old).

    Why the mean over a window, after two rounds of review broke the
    alternatives: a median of the last four gaps halves when two extra
    payments land (quarterly + two specials -> ~45 days -> "monthly", summing
    three years of dividends, then a false suspension); a count over 730 days
    holds THREE annual payments whenever ex-dates drift a few days earlier each
    year. The mean gap of a quarterly payer with two specials is ~73 days
    (still 4), and an annual payer's gaps average ~365 days however many fall
    in the window. The window only keeps old payments from outvoting a recent
    change of frequency. Same rule for a run of two payments as of twenty."""
    window_start = t - np.timedelta64(_FREQUENCY_WINDOW_DAYS, "D")
    recent = run_ex[run_ex > window_start]
    if len(recent) < 2:
        recent = run_ex[-2:]
    span_days = float((recent[-1] - recent[0]).astype(float))
    return _snap(span_days / (len(recent) - 1))


def _to_date(stamp: np.datetime64) -> dt.date:
    return pd.Timestamp(stamp).date()


def build_dividend_yields(frame: pd.DataFrame) -> Mapping[str, DividendYields]:
    """One :class:`DividendYields` per symbol in a bronze ``tiingo_eod`` frame.

    A symbol absent from ``frame`` (never fetched, or withheld by ingestion
    because a row failed validation) is absent here: its ``q`` is unknown."""
    return {
        str(symbol): DividendYields(str(symbol), rows)
        for symbol, rows in frame.groupby("symbol", sort=True)
    }
