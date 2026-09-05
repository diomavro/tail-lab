"""Pandera schema for the optionsDX historical end-of-day option chains.

This module is a LEAF: it must never import anything else from ``tail_lab``
(enforced by the import-linter layers contract in ``pyproject.toml``).

**What this is.** Real end-of-day quotes -- bid, ask, IV and greeks -- for six
underlyings over 2010-2023, downloaded by hand from optionsDX. It is the third
quote source in this repo and by far the deepest: ``option_quotes`` is one
vendor's SPY monthly rolls, ``option_chain_snapshot`` is the forward collection
that only began on 2026-08-26, and this reaches back fourteen years.

That matters because ``docs/adr/0004`` bounds every backtest result on this
platform with "model-priced, so treat it as a relative ranking, not P&L truth".
Where this dataset has coverage, that caveat can be replaced with a
measurement.

**Coverage is wildly uneven, and that is the first thing to know about it.**
The download is not a continuous panel; whole months are simply absent, and the
gaps are worst exactly where they hurt most:

    ====  ======  ==================  ====
    sym   months  span                gaps
    ====  ======  ==================  ====
    vix      168  2010-01 .. 2023-12     0
    nvda      93  2016-01 .. 2023-12     3
    tsla      84  2016-01 .. 2023-12    12
    qqq       75  2012-01 .. 2023-12    69
    spx       96  2010-01 .. 2023-12    72
    spy       63  2010-01 .. 2023-12   105
    ====  ======  ==================  ====

**SPY is missing more months than it has**, and SPY is this platform's
benchmark. A roll backtest run across it would skip the absent months silently
and draw a smooth equity curve that is mostly an artefact of the skipping --
the exact silent-wrong-data failure ``docs/adr/0009`` exists to prevent, and one
that no test of the roll engine would catch, because the engine is behaving
correctly on the rows it was given.

So coverage is not a footnote here, it is part of the contract:
:func:`month_coverage` reports what is present and what is missing, and any
consumer spanning a date range **must** consult it and refuse, or say loudly
what it skipped. VIX is the one symbol where a continuous 14-year study is
honestly available.

**The slice.** Puts only, strike within 0.60-1.02 of spot, 0-120 days to
expiry. Chosen to cover the whole existing sweep grid with headroom --
``SWEEP_MONEYNESS`` reaches 30% out (0.70 moneyness) and
``SWEEP_TENORS_WEEKS`` reaches 12 weeks (84 days) -- rather than to be
maximal. The full corpus is 8.2 GB uncompressed against 8.8 GB of free disk, so
storing it whole is not an option; this band keeps ~32% of rows.

**No open interest.** optionsDX does not publish it, so unlike
``option_chain_snapshot`` the liquidity screen here has to lean on ``volume``
and the bid/ask spread. A row with no ask is not a quote and is dropped.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: The dataset's bronze name.
DATASET = "optionsdx_quotes"

#: The six underlyings in the download.
SYMBOLS: tuple[str, ...] = ("spy", "spx", "qqq", "nvda", "tsla", "vix")

STRIKE_MIN = 0.0
STRIKE_MAX = 100_000.0
PREMIUM_MIN = 0.0
PREMIUM_MAX = 100_000.0

#: Moneyness band retained (strike / spot). Covers `SWEEP_MONEYNESS`'s deepest
#: cell (30% out = 0.70) with headroom, and a little way through the money.
MONEYNESS_MIN = 0.60
MONEYNESS_MAX = 1.02

#: Longest tenor retained, in days. `SWEEP_TENORS_WEEKS` reaches 12 weeks (84
#: days); 120 leaves room to widen the grid without re-ingesting 8.2 GB.
MAX_DTE_DAYS = 120


class OptionsDxQuoteSchema(pa.DataFrameModel):
    """One end-of-day put quote from the optionsDX archives.

    The first eight columns are exactly
    :class:`~tail_lab.contracts.option_quotes.OptionQuoteSchema` minus
    ``open_interest`` (which this source does not publish), so the three quote
    datasets in this repo concatenate on their shared columns.
    """

    underlying: Series[str] = pa.Field(nullable=False)
    quote_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    expiration: Series[pa.Timestamp] = pa.Field(nullable=False)
    strike: Series[float] = pa.Field(nullable=False, gt=STRIKE_MIN, le=STRIKE_MAX)
    bid: Series[float] = pa.Field(nullable=False, ge=PREMIUM_MIN, le=PREMIUM_MAX)
    ask: Series[float] = pa.Field(nullable=False, gt=PREMIUM_MIN, le=PREMIUM_MAX)
    volume: Series[int] = pa.Field(nullable=False, ge=0)
    spot: Series[float] = pa.Field(nullable=False, gt=STRIKE_MIN, le=STRIKE_MAX)
    #: The vendor's own greeks. Nullable because the source leaves them blank
    #: on illiquid rows, and a blank is an absence rather than a zero.
    iv: Series[float] = pa.Field(nullable=True, ge=0.0, le=10.0)
    delta: Series[float] = pa.Field(nullable=True, ge=-1.0, le=0.0)
    vega: Series[float] = pa.Field(nullable=True, ge=0.0)
    #: Per YEAR, as the vendor publishes it -- NOT the per-day convention
    #: `research/option_pricer.PutGreeks` uses. Converting here would hide a
    #: provenance difference inside a number; the consumer converts and says so.
    theta: Series[float] = pa.Field(nullable=True)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["underlying", "quote_date", "expiration", "strike"]


def months_in_span(lo: str, hi: str) -> list[str]:
    """Every ``YYYYMM`` from ``lo`` to ``hi`` inclusive.

    The one place this enumeration lives. It had been written three times --
    here, in ``research/accuracy``, and in ``scripts/optionsdx_manifest`` --
    which is the duplication the design reviewer's own first criterion names,
    and it matters beyond tidiness: these three answers are compared against
    each other (the manifest audits the corpus, the accuracy panel reports the
    gaps to a reader), so three implementations of "which months should exist"
    is three chances for the audit and the surface to disagree about a hole.
    """
    lo_y, lo_m = int(lo[:4]), int(lo[4:])
    hi_y, hi_m = int(hi[:4]), int(hi[4:])
    months: list[str] = []
    year, month = lo_y, lo_m
    while (year, month) <= (hi_y, hi_m):
        months.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def month_coverage(quote_dates: list[dt.date]) -> tuple[list[str], list[str]]:
    """Split a symbol's span into ``(present_months, missing_months)``.

    Both as ``YYYYMM``. Exists because this download is not continuous and the
    absence is invisible in the data itself: a backtest handed only the months
    that exist will happily roll straight across a two-year hole and report an
    equity curve for a period it has no quotes for.
    """
    if not quote_dates:
        return [], []
    present = sorted({f"{d.year}{d.month:02d}" for d in quote_dates})
    held = set(present)
    return present, [m for m in months_in_span(present[0], present[-1]) if m not in held]
