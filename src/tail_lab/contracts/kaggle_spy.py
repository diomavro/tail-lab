"""Pandera schema for the Kaggle SPY 2014-2025 end-of-day chain (put side).

This module is a LEAF: it must never import anything else from ``tail_lab``
(enforced by the import-linter layers contract in ``pyproject.toml``).

**What this is.** Kaggle dataset ``shankerabhigyan/s-and-p500-options-spy-
implied-volatility-2019-24`` ("S&P500 Options (SPY) Implied Volatility
(2014-25)", v3, 8,687,075,414 bytes): twelve files ``spy_options_data_14.json``
.. ``spy_options_data_25.json``, one per calendar year, 346 MB (2014) to
1,055 MB (2021) each. Each file is ONE line of JSON -- for 2019-2024 an
array of trading days, each an array of records; for 2014-2018 and 2025 an
object keyed by date (holidays as ``[]``) -- of contract records whose 20
keys and string-typed values are character-for-character Alpha Vantage
``HISTORICAL_OPTIONS``
(:data:`AV_COLUMNS`). Confirmed 2026-10-05 from byte-range reads (every
file's opening bytes; records from 2014, 2023 and 2025); no spot / underlying
price is carried.

**OFFLINE CROSS-CHECK ONLY -- decided by the owner 2026-10-05.** It has exactly
two jobs: (1) a 2023 vendor-disagreement check against optionsDX SPY
(``docs/DATA_CONTRACTS.md`` #12) on overlapping dates, and (2) measuring the
Black-Scholes model's pricing residual over 2024-2025, after optionsDX ends.
It must NEVER feed a verdict, a recommendation, or anything the API or the
live page serves. The hard barrier is that it cannot ship: the per-year
parquet lives under the gitignored ``data/vendor/`` tree (not in the lake the
API reads), and ``.dockerignore`` excludes ``data``. Locally, an import-linter
``forbidden`` contract bars ``tail_lab.api`` and ``tail_lab.research`` from
importing this module or its adapter, and a test greps every other module in
``src/tail_lab`` and ``frontend/src`` for the corpus's name or path.

**Licence posture.** Kaggle labels it CC0, but the schema is Alpha Vantage's
verbatim, so the data almost certainly originates from AV's
``HISTORICAL_OPTIONS`` endpoint and **AV's terms of service are the real
constraint, not the CC0 label** -- the uploader cannot waive rights they do
not hold. Same unresolved posture as the lambdaclass file (``HUMAN_TODO.md``):
fine for private offline research, never redistributed, never served.

**The vendor greeks are decorative.** AV's IV is not inverted per contract,
and how badly varies by year (byte-range samples, 2026-10-05): on 2014-01-02,
1,667 puts carry 133 distinct IV values; in the first 2 MB of 2025-01-02,
2,422 puts carry 158 (one expiry: 57 distinct across 111 strikes); on
2023-12-29, 4,143 puts carry 2,244, though within one expiry 222 of 224 are
distinct. Deep-ITM long-dated put deltas plateau near -0.47 where the true
value is near -1. So ``option_pricer.PutGreeks`` does the work, and the
vendor's block is kept only for provenance under the house rule shared with
Cboe's zero-fill and optionsDX's blank ``P_IV``: a block that is absent or
physically impossible is voided whole, never coerced to 0.0. The adapter
records ``iv_distinct_ratio`` per year so the smoothing is a number, not a
memory.
"""

from __future__ import annotations

from typing import ClassVar, cast

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

#: Where the parquet lives. NOT a lake dataset name -- it never enters the lake.
DATASET = "kaggle_spy_chain"

KAGGLE_SLUG = "shankerabhigyan/s-and-p500-options-spy-implied-volatility-2019-24"

#: Calendar years the corpus covers, one file each.
YEARS: tuple[int, ...] = tuple(range(2014, 2026))

#: The vendor's record keys, in file order. Alpha Vantage HISTORICAL_OPTIONS.
AV_COLUMNS: tuple[str, ...] = (
    "contractID",
    "symbol",
    "expiration",
    "strike",
    "type",
    "last",
    "mark",
    "bid",
    "bid_size",
    "ask",
    "ask_size",
    "volume",
    "open_interest",
    "date",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
)

#: The greek block, voided together when any part of it is absent or impossible.
GREEK_COLUMNS: tuple[str, ...] = ("iv", "delta", "gamma", "theta", "vega", "rho")

#: Highest IV kept. Above this the adapter voids the greek block rather than
#: letting the schema quarantine the row (and its real quote) over it.
IV_MAX = 10.0

STRIKE_MAX = 100_000.0
PREMIUM_MAX = 100_000.0


def year_file_name(year: int) -> str:
    """The vendor's file name for ``year``: ``spy_options_data_23.json``."""
    if year not in YEARS:
        raise ValueError(f"the Kaggle SPY corpus covers {YEARS[0]}-{YEARS[-1]}; got {year}")
    return f"spy_options_data_{year % 100:02d}.json"


class KaggleSpyPutSchema(pa.DataFrameModel):
    """One end-of-day SPY put quote from the Kaggle / Alpha Vantage corpus.

    Column names follow this repo's quote datasets (``quote_date``, ``iv``)
    rather than the vendor's (``date``, ``implied_volatility``); ``spot`` is
    absent because the vendor never published it -- consumers join it from
    elsewhere and say so.
    """

    contract_id: Series[str] = pa.Field(nullable=False)
    quote_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    expiration: Series[pa.Timestamp] = pa.Field(nullable=False)
    strike: Series[float] = pa.Field(nullable=False, gt=0.0, le=STRIKE_MAX)
    last: Series[float] = pa.Field(nullable=True, ge=0.0, le=PREMIUM_MAX)
    mark: Series[float] = pa.Field(nullable=True, ge=0.0, le=PREMIUM_MAX)
    bid: Series[float] = pa.Field(nullable=False, ge=0.0, le=PREMIUM_MAX)
    ask: Series[float] = pa.Field(nullable=False, gt=0.0, le=PREMIUM_MAX)
    #: Counts are nullable integers: a blank or garbled size is absent, never
    #: a fabricated 0 (the adapter refuses to invent a zero bid for the same reason).
    bid_size: Series[pd.Int64Dtype] = pa.Field(nullable=True, ge=0)
    ask_size: Series[pd.Int64Dtype] = pa.Field(nullable=True, ge=0)
    volume: Series[pd.Int64Dtype] = pa.Field(nullable=True, ge=0)
    open_interest: Series[pd.Int64Dtype] = pa.Field(nullable=True, ge=0)
    #: The vendor's greek block -- decorative (module docstring). Nullable
    #: because a voided block is an absence, never a zero.
    iv: Series[float] = pa.Field(nullable=True, gt=0.0, le=IV_MAX)
    delta: Series[float] = pa.Field(nullable=True, ge=-1.0, le=0.0)
    gamma: Series[float] = pa.Field(nullable=True, ge=0.0)
    #: As AV publishes it -- magnitudes (``-0.057`` on a $0.01 0-DTE put) read
    #: as per DAY, unlike optionsDX's per-year theta. Unverified against AV's
    #: docs and not converted: a consumer that uses it says which it assumed.
    theta: Series[float] = pa.Field(nullable=True)
    vega: Series[float] = pa.Field(nullable=True, ge=0.0)
    rho: Series[float] = pa.Field(nullable=True)

    @pa.dataframe_check
    @classmethod
    def quote_not_crossed(cls, df: pd.DataFrame) -> Series[bool]:
        """A bid above the ask is a crossed (stale or garbled) quote, not a
        market a put could have traded in."""
        return cast(Series[bool], df["bid"] <= df["ask"])

    @pa.dataframe_check
    @classmethod
    def expiry_not_before_quote(cls, df: pd.DataFrame) -> Series[bool]:
        """An option cannot be quoted after it expired -- such a row is a
        garbled record, not a quote."""
        return cast(Series[bool], df["expiration"] >= df["quote_date"])

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["quote_date", "contract_id"]
