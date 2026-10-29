"""The volatility risk premium a put buyer pays (``docs/END_STATE.md`` §4 Q6).

Implied vol sits above the vol that is subsequently realised most of the time
(Carr & Wu 2009; Bakshi & Kapadia 2003), and that gap is the headwind every
roll of the sensitivity-puts strategy fights. Nothing on the platform measured
it: the model-priced backtest prices at *realised* vol, so by construction it
pays no premium at all, and the Pitch's real-quote study shows the cost only
as a lost P&L, never as vol points.

This module measures it on real quotes. For every quote date it takes ONE
listed put -- the expiry whose days-to-expiry is nearest ``target_days``, then
the strike nearest ``moneyness x spot`` -- reads that put's own implied vol,
and sets it against the vol the underlying then realised over exactly that
option's life, from the quote date's close to the expiry's::

    vrp = implied_vol - realised_vol        (annualised, in vol units)

Positive means the buyer paid for more movement than came.

**Realised vol is zero-mean**: ``sqrt(252 / n * sum(r_i^2))`` over the ``n``
daily log returns in the window. That is the definition variance swaps settle
on, and the one the VRP literature uses; a demeaned standard deviation would
subtract a drift no option pays out on.

**Point-in-time.** An observation exists only once its window has closed: its
expiry is on or before ``as_of`` AND a close at or after the expiry is known.
Closes after ``as_of`` are dropped before anything is computed, so a series
that runs into the future cannot leak into either the realised leg or the
choice of which observations exist.

**Windows overlap.** One observation per quote date, so a 30-day window
shares ~29 days with the next. The mean and median are fine; a t-statistic
over these observations would overstate its own significance by roughly the
window length, so none is reported.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.optionsdx import DATASET as OPTIONSDX_DATASET
from tail_lab.contracts.optionsdx import month_coverage
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import load_asof_series

#: Trading days per year, the annualisation every realised vol here uses.
TRADING_DAYS = 252

#: Fewest daily returns a window may hold. Below this a "realised vol" is a
#: handful of squared returns, and one gap day dominates it.
MIN_RETURNS = 5

#: The panel columns this module reads -- projected, never the whole panel
#: (SPY is 3.3M rows; ``LakeStore.read_bronze_columns_as_of``).
QUOTE_COLUMNS = ["quote_date", "expiration", "strike", "spot", "iv"]


class VrpObservation(BaseModel):
    """One quote date: one put's implied vol against what its life realised."""

    quote_date: dt.date
    expiration: dt.date
    strike: float
    moneyness: float  # strike / spot, as listed that day
    implied_vol: float
    realised_vol: float
    n_returns: int
    vrp: float  # implied_vol - realised_vol


class VrpSummary(BaseModel):
    """The premium over every observation whose window has closed."""

    underlying: str
    moneyness: float  # the target strike / spot
    target_days: int
    as_of: dt.date
    n_observations: int
    start: dt.date
    end: dt.date
    mean_implied_vol: float
    mean_realised_vol: float
    mean_vrp: float
    median_vrp: float
    #: Share of observations where implied exceeded realised -- the buyer
    #: overpaid for movement.
    share_positive: float
    #: ``YYYYMM`` months inside the quotes' span that hold no quote at all
    #: (``contracts.optionsdx.month_coverage``): a gap here is a gap in the
    #: sample, never a calm month.
    missing_months: list[str]
    observations: list[VrpObservation]


def select_puts(quotes: pd.DataFrame, *, moneyness: float, target_days: int) -> pd.DataFrame:
    """One put per quote date: the expiry nearest ``target_days`` (ties go to
    the shorter), then the strike nearest ``moneyness x spot`` (ties go to the
    lower). Rows without a positive implied vol are not candidates -- a blank
    vendor IV means its solver failed, never a zero-vol option. Pure.
    """
    usable = quotes[quotes["iv"].notna() & (quotes["iv"] > 0)].copy()
    usable["quote_date"] = pd.to_datetime(usable["quote_date"])
    usable["expiration"] = pd.to_datetime(usable["expiration"])
    usable["dte"] = (usable["expiration"] - usable["quote_date"]).dt.days
    usable = usable[usable["dte"] > 0]
    usable["dte_gap"] = (usable["dte"] - target_days).abs()
    expiries = usable.sort_values(["quote_date", "dte_gap", "dte"]).drop_duplicates("quote_date")[
        ["quote_date", "expiration"]
    ]
    chosen = usable.merge(expiries, on=["quote_date", "expiration"])
    chosen["strike_gap"] = (chosen["strike"] - chosen["spot"] * moneyness).abs()
    return (
        chosen.sort_values(["quote_date", "strike_gap", "strike"])
        .drop_duplicates("quote_date")
        .reset_index(drop=True)
    )


def realised_vol(closes: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, int]:
    """Zero-mean annualised vol of the daily log returns from the last close on
    or before ``start`` to the last close on or before ``end``, and how many
    returns that was. ``closes`` must be sorted by date. Pure.
    """
    dates = closes.index
    lo = int(dates.searchsorted(start, side="right")) - 1
    hi = int(dates.searchsorted(end, side="right")) - 1
    if lo < 0 or hi <= lo:
        return math.nan, 0
    window = closes.to_numpy(dtype=float)[lo : hi + 1]
    returns = np.diff(np.log(window))
    return float(math.sqrt(TRADING_DAYS * float(np.mean(returns**2)))), len(returns)


def measure_vrp(
    quotes: pd.DataFrame,
    closes: pd.Series,
    *,
    underlying: str,
    as_of: dt.date,
    moneyness: float = 1.0,
    target_days: int = 30,
) -> VrpSummary:
    """Implied minus subsequently realised vol, per quote date. Pure.

    ``quotes`` carries ``QUOTE_COLUMNS``; ``closes`` is a date-indexed close
    series of the same underlying. Raises ``LookupError`` when no observation's
    window has closed by ``as_of``.
    """
    cutoff = pd.Timestamp(as_of)
    known = closes[closes.index <= cutoff].sort_index()
    last_close = known.index.max() if len(known) else None
    picks = select_puts(
        quotes[pd.to_datetime(quotes["quote_date"]) <= cutoff],
        moneyness=moneyness,
        target_days=target_days,
    )

    observations: list[VrpObservation] = []
    for quote_date, expiration, strike, spot, iv in zip(
        pd.to_datetime(picks["quote_date"]),
        pd.to_datetime(picks["expiration"]),
        picks["strike"].to_numpy(dtype=float),
        picks["spot"].to_numpy(dtype=float),
        picks["iv"].to_numpy(dtype=float),
        strict=True,
    ):
        expiry = pd.Timestamp(expiration)
        if last_close is None or expiry > cutoff or last_close < expiry:
            continue  # the option's life has not finished as of `as_of`
        rv, n = realised_vol(known, pd.Timestamp(quote_date), expiry)
        if n < MIN_RETURNS:
            continue
        observations.append(
            VrpObservation(
                quote_date=pd.Timestamp(quote_date).date(),
                expiration=expiry.date(),
                strike=float(strike),
                moneyness=float(strike / spot),
                implied_vol=float(iv),
                realised_vol=rv,
                n_returns=n,
                vrp=float(iv) - rv,
            )
        )
    if not observations:
        raise LookupError(
            f"no {underlying.upper()} put has a closed window with a known close as of {as_of}"
        )

    vrps = np.array([o.vrp for o in observations])
    _, missing = month_coverage(sorted({o.quote_date for o in observations}))
    return VrpSummary(
        underlying=underlying.upper(),
        moneyness=moneyness,
        target_days=target_days,
        as_of=as_of,
        n_observations=len(observations),
        start=observations[0].quote_date,
        end=observations[-1].quote_date,
        mean_implied_vol=float(np.mean([o.implied_vol for o in observations])),
        mean_realised_vol=float(np.mean([o.realised_vol for o in observations])),
        mean_vrp=float(vrps.mean()),
        median_vrp=float(np.median(vrps)),
        share_positive=float(np.mean(vrps > 0)),
        missing_months=missing,
        observations=observations,
    )


def compute_vrp(
    store: LakeStore,
    *,
    underlying: str,
    as_of: dt.date,
    moneyness: float = 1.0,
    target_days: int = 30,
) -> VrpSummary:
    """:func:`measure_vrp` on the optionsDX panel and the name's OHLCV, both
    read as of ``as_of``.

    The realised leg uses OHLCV ``close`` through the backtest's own price
    chokepoint (``put_roll.load_asof_series``), which is split-adjusted
    (``docs/DATA_CONTRACTS.md`` #12): returns are what a split must not break,
    and the panel's as-traded ``spot`` would read NVDA's and TSLA's splits as
    crashes. So the span is the two datasets' overlap (SPY: 2016-10 onward),
    reported in the summary's ``start``/``end``. Raises ``LookupError`` when
    either dataset is absent -- this is a hand-run measurement, not a surface
    that must never go silent.
    """
    name = underlying.lower()
    quotes = store.read_bronze_columns_as_of(f"{OPTIONSDX_DATASET}_{name}", as_of, QUOTE_COLUMNS)
    closes, _ = load_asof_series(store, name, as_of)
    first_close = closes.index.min()
    overlapping = quotes[pd.to_datetime(quotes["quote_date"]) >= first_close]
    return measure_vrp(
        overlapping,
        closes,
        underlying=name,
        as_of=as_of,
        moneyness=moneyness,
        target_days=target_days,
    )
