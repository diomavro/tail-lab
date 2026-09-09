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
Coverage is complete for every symbol ingested, and that is a recent fact worth
dating: this docstring previously carried a table showing SPY with 63 of 168
months and 105 gaps, written when only part of the corpus had been downloaded.
The rest arrived on 2026-09-03. Measured against the lake on 2026-09-09:

    ====  ======  ==================  ====
    sym   months  span                gaps
    ====  ======  ==================  ====
    vix      168  2010-01 .. 2023-12     0
    spy      168  2010-01 .. 2023-12     0
    qqq      144  2012-01 .. 2023-12     0
    nvda      96  2016-01 .. 2023-12     0
    tsla      96  2016-01 .. 2023-12     0
    spx        -  NOT INGESTED           -
    ====  ======  ==================  ====

SPX is the exception and stays absent: ingesting it is OOM-killed at ~3.9 GB
and needs a chunked bronze write (``AGENT_TODO.md``).

**The obligation on consumers is unchanged even though the gaps are gone.**
:func:`month_coverage` still reports present/missing and any consumer spanning
a date range must still consult it and refuse, or say loudly what it skipped --
because a backtest that skips absent months silently draws a smooth equity
curve that is an artefact of the skipping, and no test of the roll engine would
catch it: the engine is behaving correctly on the rows it was given
(``docs/adr/0009``). Today the answer happens to be "nothing is missing"; the
check is what makes that a finding rather than an assumption.

**The binding constraint is now the PRICE series, not this panel.** Bronze
OHLCV is a rolling five-year Tiingo window (2021-08 .. 2026-08), and this panel
ends 2023-12, so a roll backtest that needs both has only ~589 overlapping
trading days -- about a third of a default four-year window. Any consumer
joining the two must report the span it actually traded.

**Two joins that are silently wrong.** The panel's ``spot`` is as-traded; the
OHLCV ``close`` is split-adjusted. Measured 2026-09-09, panel/ohlcv median:
spy 1.0000, qqq 1.0000, tsla 1.0003, **nvda 10.0000** (the 2024 10:1 split). A
strike resolved in one basis against a spot from the other is off by the split
factor with no error raised. And VIX cannot be joined at all: its ``spot`` here
is the INDEX, while VIX options settle on VIX FUTURES, so ``strike/spot`` is
not moneyness against the contract -- and there is no ``ohlcv_vix`` dataset
regardless.

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
