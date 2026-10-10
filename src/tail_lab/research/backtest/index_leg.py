"""The Book's unhedged S&P 500 leg: one builder, two consumers.

The lump-sum overlay (:mod:`hedge_overlay`) and the contributions plan
(:mod:`contribution_plan`) both blend a Cboe program against "the S&P 500 with
its dividends reinvested". Before this module each built that leg itself from
SPX price plus a flat assumed yield. Now :func:`build_index_leg` builds it once,
from the store, and both pass the result into their pure ``run_*`` functions,
so the window clamp, the window-end extension and the degraded-input rule are
the same on both pages by construction.

**Measured** (``tiingo_eod`` has SPY): SPY's total return from Tiingo's
``adj_close`` -- verified dividend-reinvested, so dividends accrue in the price
between ex-dates and nothing is lost at a window's end -- **grossed up by
SPY's dated expense ratio** (:data:`SPY_FEES`) to approximate the S&P 500's
gross total return, which the lake does not hold (SPXT is HTTP 403). Two named
legs come out (``docs/adr/0027`` amendment):

* ``base`` -- SPY total return plus the dated fee add-back.
* ``conservative`` -- base plus SPY's unit-trust **dividend cash drag**: SPY
  cannot reinvest dividends between receipt and its quarterly distribution, so
  about ``y/8`` of the trust sits in cash (``y`` the dividend yield, held on
  average an eighth of a year), costing ``y/8 x (equity return - T-bill)`` a
  year -- roughly 1.5-2bp, and negative when equities trail bills. ``y`` is
  the trailing twelve-month average of PR1's measured SPY ``q``
  (point-in-time: month-end readings on or before the day). Readings with
  no measured basis -- ``unknown``, and ``non_payer`` before SPY's first
  dividend (the trust was already holding its constituents' dividends) --
  are left out, never averaged in as zero. Before SPY's first measured
  reading (1993-06, five months after listing) ``y`` takes that first
  reading: a stated look-ahead exception to ``docs/adr/0009``, reaching
  the conservative leg only through ``y/8``. Bound, measured on the real
  lake (2026-10-10): that leg's full-history CAGR differs by +0.023bp/yr
  from ``y = 0`` in those months and by -0.006bp/yr from ``y = 3%``. If any later
  day has no measured reading in its trailing twelve months, ``y`` is
  unmeasured there and the conservative leg is not built (the Book then
  withholds its size, naming why) -- never a silent ``y = 0``.

SPY total return *understates* the index, which flatters the hedge; adding a
constant ``k`` to the index's return moves a blend's margin by about
``-w*k``, so the leg with the larger add-back always binds. The sizing gate
therefore takes the smaller margin of the two legs.

**T-bills for the cash drag** come from ``rates`` (``DGS3MO``): point-in-time
from its first ALFRED vintage (2005-06-28); before that the same series at its
latest vintage -- a stated look-ahead exception to ``docs/adr/0009``. FRED did
backfill and re-round these values; the bound is that revisions are
basis-point-level and reach the margin only through ``y/8``, so under
0.01bp/yr. With no ``rates`` at all the conservative leg cannot be built, and
the Book withholds its recommended size rather than gating on one leg.

**Window end.** The weekly Tiingo feed can trail the Cboe calendar by 8-9
days. Up to :data:`MAX_EXTENSION_DAYS` the leg is extended to the Cboe
calendar's end with SPX **price** return (no dividend accrual for those days,
labelled with the day count); beyond that it is not, so the window shows as
``clipped`` and the page says why (two missed weekly runs). Nor is it
extended when SPX has no session on Tiingo's last day (chaining from an
earlier day would count that day twice); ``unextended_reason`` tells the two
apart.

**Assumed** (no ``tiingo_eod`` yet -- no key, CI, test lakes): today's flat
yield on SPX price (:func:`total_return_levels`), re-run at each of
:data:`~index_replication.SENSITIVITY_YIELDS`, labelled assumed; the Book then
recommends no size.
"""

from __future__ import annotations

import bisect
import datetime as dt
from dataclasses import dataclass, replace
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from tail_lab.contracts.rates import DATASET as RATES_DATASET
from tail_lab.contracts.tiingo_eod import DATASET as TIINGO_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.index_replication import (
    DAYS_PER_YEAR,
    DEFAULT_DIVIDEND_YIELD,
    SENSITIVITY_YIELDS,
)
from tail_lab.research.dividends import IndexHistory, index_history

#: The FRED series the T-bill legs compound: 3-month constant maturity.
BILL_SERIES = "DGS3MO"

#: SPY's first session: no measured leg exists before it, so a window asked
#: to start earlier starts here, and says so.
SPY_LISTING = dt.date(1993, 1, 29)

#: Calendar days the leg may be carried past Tiingo's last row on SPX price
#: return alone: one missed weekly run plus a long weekend. More is two
#: missed runs, and the window is left clipped instead.
MAX_EXTENSION_DAYS = 14

#: Years an average dividend dollar waits in SPY's cash before distribution
#: (quarterly distributions, receipts spread across the quarter).
CASH_DRAG_YEARS = 1 / 8

#: Month-end readings averaged into the cash drag's ``y``.
_Y_MONTHS = 12

#: Dividend sources that measure SPY's ``y``. Not ``unknown``, and not
#: ``non_payer``: for SPY that is only the weeks before its first dividend,
#: when the trust already held cash dividends it had not yet paid out.
_REAL_Q = frozenset({"measured", "suspended", "short_history", "carried", "stale"})

_NO_BILLS = "sizing needs T-bills for the cash-drag check"

#: Why the conservative leg is missing when ``y`` cannot be measured.
Y_UNMEASURED = "sizing needs SPY's measured dividend yield for the cash-drag check"


class FeeRow(BaseModel):
    """SPY's annual expense ratio in force from ``effective``."""

    effective: dt.date
    annual_fee: float
    citation: str


#: Bumped whenever a row changes, so a result names the table it used.
FEE_TABLE_VERSION = "spy-fees-v1"

#: SPY's dated expense ratio, the add-back that turns SPY's total return into
#: an approximation of the S&P 500's gross total return. The pre-2005 row is
#: NOT verified against a primary SPDR report (``AGENT_TODO.md``); it is set
#: high on purpose, because a larger add-back raises the index leg and so can
#: only count AGAINST the hedge.
SPY_FEES: tuple[FeeRow, ...] = (
    FeeRow(
        effective=SPY_LISTING,
        annual_fee=0.0020,
        citation=(
            "UNVERIFIED upper bound for 1993-2005: SPY's early expense ratio, set at "
            "0.20%; to be replaced by the SPDR annual reports' figures"
        ),
    ),
    FeeRow(
        effective=dt.date(2005, 10, 1),
        annual_fee=0.0010,
        citation=(
            "SPDR Trust fiscal year to 2006-09-30: ordinary expenses 0.1204%, the excess "
            "over 0.1000% waived by the trustee (as reported by ETF Trends)"
        ),
    ),
    FeeRow(
        effective=dt.date(2007, 2, 1),
        annual_fee=0.000945,
        citation=(
            "SPDR Trust from 2007-02-01: ordinary expenses accrue at 0.0945%, net of the "
            "trustee waiver (as reported by ETF Trends)"
        ),
    ),
)


class LegTag(BaseModel):
    """A row computed on one of the measured legs."""

    kind: Literal["leg"] = "leg"
    leg: Literal["base", "conservative"]


class AssumedYieldTag(BaseModel):
    """A row computed on SPX price plus one assumed flat yield (the fallback)."""

    kind: Literal["assumed_yield"] = "assumed_yield"
    dividend_yield: float


#: The shared tag over every per-leg row the Book returns.
LegKey = Annotated[LegTag | AssumedYieldTag, Field(discriminator="kind")]


class DividendSpan(BaseModel):
    """One stretch of the leg and where its dividends came from."""

    start: dt.date
    end: dt.date
    source: Literal["spy_total_return", "spx_price_only", "assumed_yield"]
    #: Calendar days the span covers (the page quotes it for the extension).
    days: int


class DividendBasis(BaseModel):
    """What the index leg is made of -- every page that shows a Book result
    states it beside the result."""

    source: Literal["measured", "assumed"]
    #: The flat yield the headline assumes (``None`` when measured).
    assumed_yield: float | None
    #: Why the leg is assumed (``None`` when measured).
    assumed_reason: str | None
    spans: list[DividendSpan]
    #: The leg's first date (SPY's listing when measured).
    first_date: dt.date | None
    fee_table_version: str | None
    fees: list[FeeRow]
    #: Calendar days the measured leg trails the Cboe calendar and was NOT
    #: extended; 0 otherwise. Why, in ``unextended_reason``.
    unextended_gap_days: int
    #: ``missed_runs``: more than :data:`MAX_EXTENSION_DAYS` behind (two
    #: missed weekly runs); ``no_spx_anchor``: SPX has no session on Tiingo's
    #: last day, so chaining would count a day twice. ``None`` when extended.
    unextended_reason: Literal["missed_runs", "no_spx_anchor"] | None = None
    #: First date the conservative leg exists (``None``: it cannot be built).
    conservative_from: dt.date | None
    #: Why there is no conservative leg (``None`` when there is one).
    conservative_reason: str | None
    #: T-bills are point-in-time from here; before it, latest vintage.
    bill_point_in_time_from: dt.date | None
    snapshot_ids: dict[str, str]


@dataclass(frozen=True)
class IndexLeg:
    """The index leg on its own calendar: the series the headline verdict
    reads, and every per-leg row's series (all on the headline's index; a
    conservative series is NaN before ``basis.conservative_from``)."""

    headline: pd.Series
    rows: tuple[tuple[LegTag | AssumedYieldTag, pd.Series], ...]
    basis: DividendBasis

    @property
    def conservative(self) -> pd.Series | None:
        for key, levels in self.rows:
            if isinstance(key, LegTag) and key.leg == "conservative":
                return levels
        return None

    def cut(self, as_of: dt.date) -> IndexLeg:
        """Every series cut at ``as_of``: no window can read past it."""
        stop = pd.Timestamp(as_of)
        return replace(
            self,
            headline=self.headline[self.headline.index <= stop],
            rows=tuple((k, s[s.index <= stop]) for k, s in self.rows),
        )


def total_return_levels(price: pd.Series, *, dividend_yield: float) -> pd.Series:
    """S&P 500 price levels turned into a total-return index, from a flat yield.

    The yield accrues by calendar days between observations, so a weekend
    earns its three days, and compounds to exactly ``dividend_yield`` a year
    on a flat price. Rebased to 1.0 on the first date.
    """
    gaps = price.index.to_series().diff().dt.days.fillna(0).to_numpy(dtype=float)
    growth = (price / price.shift(1)).fillna(1.0).to_numpy(dtype=float)
    growth = growth + (1.0 + dividend_yield) ** (gaps / DAYS_PER_YEAR) - 1.0
    return pd.Series(np.cumprod(growth), index=price.index)


def assumed_leg(
    spx: pd.Series,
    *,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    reason: str = "measured dividends unavailable",
) -> IndexLeg:
    """The fallback: SPX price plus a flat yield, re-run at each assumed yield."""
    headline = total_return_levels(spx, dividend_yield=dividend_yield)
    rows = tuple(
        (
            AssumedYieldTag(dividend_yield=q),
            headline if q == dividend_yield else total_return_levels(spx, dividend_yield=q),
        )
        for q in SENSITIVITY_YIELDS
    )
    first = spx.index[0].date() if len(spx) else None
    last = spx.index[-1].date() if len(spx) else None
    spans = (
        [DividendSpan(start=first, end=last, source="assumed_yield", days=(last - first).days)]
        if first is not None and last is not None
        else []
    )
    basis = DividendBasis(
        source="assumed",
        assumed_yield=dividend_yield,
        assumed_reason=reason,
        spans=spans,
        first_date=first,
        fee_table_version=None,
        fees=[],
        unextended_gap_days=0,
        conservative_from=None,
        conservative_reason="sizing needs measured dividends",
        bill_point_in_time_from=None,
        snapshot_ids={},
    )
    return IndexLeg(headline=headline, rows=rows, basis=basis)


def fee_in_force(dates: pd.DatetimeIndex) -> np.ndarray:
    """SPY's annual expense ratio in force on each date (the dated table)."""
    starts = np.array([np.datetime64(r.effective, "D") for r in SPY_FEES])
    fees = np.array([r.annual_fee for r in SPY_FEES])
    pos = np.searchsorted(starts, dates.to_numpy(dtype="datetime64[D]"), side="right") - 1
    return fees[np.clip(pos, 0, len(fees) - 1)]


def _first_vintages(rates: pd.DataFrame) -> pd.DataFrame:
    rows = rates[rates["series_id"] == BILL_SERIES]
    return rows.sort_values(["obs_date", "vintage_date"]).drop_duplicates(
        subset="obs_date", keep="first"
    )


def compound(rate: np.ndarray, gaps: np.ndarray) -> np.ndarray:
    """Growth factor of an annual ``rate`` held over calendar-day ``gaps``."""
    return (1.0 + rate) ** (gaps / DAYS_PER_YEAR)


def bill_rates_in_force(rates: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    """The 3-month T-bill yield (percent) in force on each date, point-in-time;
    NaN where none is known yet.

    Each observation's value is its FIRST vintage, published on that
    vintage's date. The rate in force on ``t`` is the **latest-dated
    observation before ``t`` among those already published by ``t``** -- not
    the most recently published row, because FRED publishes backfills in bulk
    (e.g. 2005-06-28, 2020-07-21) that would otherwise drag a decades-old rate
    into the present.
    """
    first = _first_vintages(rates)
    order = np.argsort(pd.to_datetime(first["vintage_date"]).to_numpy(), kind="stable")
    published = pd.to_datetime(first["vintage_date"]).to_numpy()[order]
    observed = pd.to_datetime(first["obs_date"]).to_numpy()[order]
    values = first["value"].to_numpy(dtype=float)[order]

    known_dates: list[np.datetime64] = []  # sorted obs dates published so far
    value_of: dict[np.datetime64, float] = {}
    in_force = np.full(len(dates), np.nan)
    k = 0
    for i, t in enumerate(dates.to_numpy()):
        while k < len(published) and published[k] <= t:
            bisect.insort(known_dates, observed[k])
            value_of[observed[k]] = values[k]
            k += 1
        j = bisect.bisect_left(known_dates, t) - 1  # latest obs strictly before t
        if j >= 0:
            in_force[i] = value_of[known_dates[j]]
    return in_force


def latest_vintage_rates(rates: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    """The 3-month T-bill yield (percent) of the latest observation strictly
    before each date, at its LATEST vintage -- the look-ahead exception used
    only where no point-in-time value exists (before 2005-06-28)."""
    rows = rates[rates["series_id"] == BILL_SERIES]
    latest = rows.sort_values(["obs_date", "vintage_date"]).drop_duplicates(
        subset="obs_date", keep="last"
    )
    observed = pd.to_datetime(latest["obs_date"]).to_numpy(dtype="datetime64[D]")
    values = latest["value"].to_numpy(dtype=float)
    pos = np.searchsorted(observed, dates.to_numpy(dtype="datetime64[D]"), side="left") - 1
    out = np.full(len(dates), np.nan)
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def _trailing_q(spy: IndexHistory, dates: pd.DatetimeIndex) -> np.ndarray:
    """``y`` on each date: the mean of the measured month-end SPY ``q``
    readings among the last twelve dated on or before it. Before the first
    measured reading, that reading (the stated exception); NaN where twelve
    months hold none, and everywhere when SPY has no measured reading."""
    days = pd.DatetimeIndex(spy.rows["trade_date"]).sort_values()
    months = days.to_period("M")
    ends = days[np.r_[np.flatnonzero(months[1:] != months[:-1]), len(days) - 1]]
    readings = []
    for day in ends:
        value = spy.yields.at(day.date())
        readings.append(value.q if value.source in _REAL_Q else np.nan)
    measured = np.flatnonzero(~np.isnan(readings))
    if len(measured) == 0:
        return np.full(len(dates), np.nan)
    first = readings[measured[0]]
    rolling = pd.Series(readings).rolling(_Y_MONTHS, min_periods=1).mean().to_numpy(copy=True)
    rolling[: measured[0]] = first
    pos = np.searchsorted(ends.to_numpy(), dates.to_numpy(), side="right") - 1
    out = np.full(len(dates), first)
    ok = pos >= 0
    out[ok] = rolling[pos[ok]]
    return out


def _extend(
    base: pd.Series, spx: pd.Series, *, as_of: dt.date
) -> tuple[pd.Series, list[DividendSpan], int, Literal["missed_runs", "no_spx_anchor"] | None]:
    """``base`` carried to the Cboe calendar's end on SPX price return, when
    the gap is short enough; the spans, the gap left unextended and why."""
    last = base.index[-1]
    first = base.index[0]
    spans = [
        DividendSpan(
            start=first.date(),
            end=last.date(),
            source="spy_total_return",
            days=(last - first).days,
        )
    ]
    spx = spx[spx.index <= pd.Timestamp(as_of)]
    tail = spx[spx.index > last]
    if tail.empty:
        return base, spans, 0, None
    gap = (tail.index[-1] - last).days
    anchor = spx[spx.index <= last]
    # The anchor must be Tiingo's own last session: an earlier SPX day's move
    # is already inside base[last], and chaining from it would count it twice.
    if gap > MAX_EXTENSION_DAYS:
        return base, spans, gap, "missed_runs"
    if anchor.empty or anchor.index[-1] != last:
        return base, spans, gap, "no_spx_anchor"
    path = pd.concat([anchor.iloc[-1:], tail]).to_numpy(dtype=float)
    extended = float(base.iloc[-1]) * path[1:] / path[0]
    spans.append(
        DividendSpan(
            start=tail.index[0].date(),
            end=tail.index[-1].date(),
            source="spx_price_only",
            days=gap,
        )
    )
    return pd.concat([base, pd.Series(extended, index=tail.index)]), spans, 0, None


def _conservative(
    base: pd.Series, spy: IndexHistory, rates: pd.DataFrame
) -> tuple[pd.Series, dt.date | None, dt.date | None, str | None]:
    """The base leg plus the cash-drag add-back, NaN before the first day a
    T-bill rate is known; that day, the first point-in-time day, and why
    there is no leg (``None`` when there is one)."""
    dates = pd.DatetimeIndex(base.index)
    pit = bill_rates_in_force(rates, dates)
    rate = np.where(np.isnan(pit), latest_vintage_rates(rates, dates), pit) / 100.0
    known = np.flatnonzero(~np.isnan(rate))
    pit_known = np.flatnonzero(~np.isnan(pit))
    pit_from = dates[pit_known[0]].date() if len(pit_known) else None
    if len(known) == 0 or known[0] >= len(dates) - 1:
        return pd.Series(np.nan, index=dates), None, pit_from, _NO_BILLS
    j = int(known[0])
    y = _trailing_q(spy, dates)[:-1]
    if np.isnan(y[j:]).any():
        return pd.Series(np.nan, index=dates), None, pit_from, Y_UNMEASURED
    levels = base.to_numpy(dtype=float)
    gaps = np.diff(dates.to_numpy(dtype="datetime64[D]")).astype(float)
    equity = levels[1:] / levels[:-1] - 1.0
    bill = compound(rate[:-1], gaps) - 1.0
    growth = 1.0 + equity + y * CASH_DRAG_YEARS * (equity - bill)
    out = np.full(len(dates), np.nan)
    out[j] = levels[j]
    out[j + 1 :] = levels[j] * np.cumprod(growth[j:])
    return pd.Series(out, index=dates), dates[j].date(), pit_from, None


def measured_leg(
    spy: IndexHistory,
    spx: pd.Series,
    rates: pd.DataFrame | None,
    *,
    as_of: dt.date,
    snapshot_ids: dict[str, str],
) -> IndexLeg:
    """The measured legs from SPY's Tiingo rows and yields
    (:func:`~tail_lab.research.dividends.index_history`), cut at ``as_of``.
    The yields need no cut: :meth:`DividendYields.at` reads only rows on or
    before the date asked, and every date asked is on or before ``as_of``."""
    rows = spy.rows[pd.to_datetime(spy.rows["trade_date"]) <= pd.Timestamp(as_of)]
    if rates is not None:
        # Defence in depth: a bronze snapshot cannot hold later vintages, but
        # the latest-vintage splice would read any frame it is handed.
        rates = rates[pd.to_datetime(rates["vintage_date"]) <= pd.Timestamp(as_of)]
    rows = rows.sort_values("trade_date").drop_duplicates(subset="trade_date", keep="last")
    spy = replace(spy, rows=rows)
    dates = pd.DatetimeIndex(pd.to_datetime(rows["trade_date"]))
    adj = rows["adj_close"].to_numpy(dtype=float)
    gaps = np.r_[0.0, np.diff(dates.to_numpy(dtype="datetime64[D]")).astype(float)]
    fee = (1.0 + fee_in_force(dates)) ** (gaps / DAYS_PER_YEAR)
    growth = np.r_[1.0, adj[1:] / adj[:-1]] * fee
    base, spans, unextended, why = _extend(
        pd.Series(np.cumprod(growth), index=dates), spx, as_of=as_of
    )
    legs: list[tuple[LegTag | AssumedYieldTag, pd.Series]] = [(LegTag(leg="base"), base)]
    conservative_from: dt.date | None = None
    pit_from: dt.date | None = None
    reason: str | None = _NO_BILLS
    if rates is not None and not rates[rates["series_id"] == BILL_SERIES].empty:
        cons, conservative_from, pit_from, reason = _conservative(base, spy, rates)
        if conservative_from is not None:
            legs.append((LegTag(leg="conservative"), cons))
    basis = DividendBasis(
        source="measured",
        assumed_yield=None,
        assumed_reason=None,
        spans=spans,
        first_date=dates[0].date(),
        fee_table_version=FEE_TABLE_VERSION,
        fees=list(SPY_FEES),
        unextended_gap_days=unextended,
        unextended_reason=why,
        conservative_from=conservative_from,
        conservative_reason=reason,
        bill_point_in_time_from=pit_from,
        snapshot_ids=snapshot_ids,
    )
    return IndexLeg(headline=base, rows=tuple(legs), basis=basis)


def _read_optional(store: LakeStore, dataset: str, as_of: dt.date) -> tuple[str, str] | None:
    try:
        return dataset, store.bronze_snapshot_id(dataset, as_of)
    except KeyError:
        # A LookupError subclass, but a store bug, not an absent snapshot.
        raise
    except LookupError:
        return None


def build_index_leg(store: LakeStore, as_of: dt.date, *, spx: pd.Series) -> IndexLeg:
    """The index leg as known on ``as_of``: measured from ``tiingo_eod`` when
    it holds SPY, else the labelled flat-yield fallback on ``spx``.

    Reads ``tiingo_eod`` and ``rates`` point-in-time; either may be absent
    (that is a value, see the module docstring). A ``KeyError`` from the
    store is a bug, never absence, and propagates.
    """
    spx = spx[spx.index <= pd.Timestamp(as_of)]
    spy = index_history(store, as_of)
    if spy is None or spy.snapshot_id is None:
        return assumed_leg(spx, reason=f"no {TIINGO_DATASET} snapshot known as of {as_of}")
    if len(spy.rows) < 2:
        return assumed_leg(spx, reason=f"no SPY history in {TIINGO_DATASET} as of {as_of}")
    ids = {TIINGO_DATASET: spy.snapshot_id}
    rates_id = _read_optional(store, RATES_DATASET, as_of)
    rates: pd.DataFrame | None = None
    if rates_id is not None:
        rates = store.read_bronze_as_of(RATES_DATASET, as_of)
        ids[RATES_DATASET] = rates_id[1]
    return measured_leg(spy, spx, rates, as_of=as_of, snapshot_ids=ids)
