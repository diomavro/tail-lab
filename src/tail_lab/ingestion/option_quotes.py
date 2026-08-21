"""Ingest the real historical put smile from a **local vendor file**.

Unlike every other adapter here this one touches no network. Its source is a
parquet the human downloaded and hash-verified by hand — the lambdaclass
``data-v1`` SPY chains, refereed against Cboe's PPUT in
``docs/DATA_VERDICTS.md`` and cleared for private research use in
``HUMAN_TODO.md``. The dataset is licence-limited and can vanish (its own
upstream did), so it is **used, never depended on**: nothing in ``research/``
or ``api/`` requires the resulting bronze snapshot to exist.

**It extracts a slice, not the file.** 632 MB and 24.7M rows go in; ~58k rows
come out — the put wing at each monthly roll date for the expiry that roll
buys. See ``contracts/option_quotes.py`` for why that slice, and why the
vendor's ``mark``/IV/greek columns are dropped on the way through rather than
carried and cautioned about.

Extraction runs in DuckDB against the parquet rather than pandas: the file is
larger than this machine's RAM budget once expanded, and a predicate pushdown
reads only the row groups the filter touches.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
from pandera.errors import SchemaErrors

from tail_lab.contracts.option_quotes import (
    DATASET,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    OptionQuoteSchema,
)
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DEFAULT_VENDOR_DIR",
    "IngestResult",
    "extract_roll_smile",
    "ingest_option_quotes",
    "validate_and_quarantine",
]

logger = logging.getLogger("tail_lab.ingestion.option_quotes")

QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Where the human's hash-verified download lives. Gitignored; absent on CI
#: and on the deployed app, which is the point — this is an optional input.
DEFAULT_VENDOR_DIR = Path("data/vendor/lambdaclass-data-v1")

#: Tenor band that counts as "the next monthly expiry" from a roll date.
#: Wide enough to survive the Saturday-expiration convention that ran until
#: February 2015 and the odd holiday-shifted month.
MIN_TENOR_DAYS = 25
MAX_TENOR_DAYS = 40


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one extraction: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    underlying: str
    source_file: str


def extract_roll_smile(
    options_path: Path | str,
    underlying_path: Path | str,
    *,
    underlying: str,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> pd.DataFrame:
    """The put wing at every monthly roll date, from the vendor parquet pair.

    A *roll date* is a third Friday that the vendor actually quoted on; the
    expiry kept for it is the one 25-40 days out, i.e. the contract a monthly
    roll buys on that day. Rows are filtered to two-sided quotes
    (``bid > 0``, ``ask >= bid``) inside the moneyness band, because a
    one-sided or crossed quote is not a price anyone could have paid.

    Pure with respect to the lake — it reads files and returns a frame.
    """
    con = connection or duckdb.connect()
    frame = con.execute(
        """
        WITH und AS (
            SELECT CAST(date AS DATE) AS d, close AS spot
            FROM read_parquet($und)
        ),
        quotes AS (
            SELECT
                CAST(o.date AS DATE)       AS quote_date,
                CAST(o.expiration AS DATE) AS expiration,
                o.strike, o.bid, o.ask, o.volume, o.open_interest
            FROM read_parquet($opt) o
            WHERE o.type = 'put'
              AND o.bid > 0 AND o.ask >= o.bid
              AND dayofweek(CAST(o.date AS DATE)) = 5
              AND day(CAST(o.date AS DATE)) BETWEEN 15 AND 21
              AND datediff('day', CAST(o.date AS DATE), CAST(o.expiration AS DATE))
                  BETWEEN $min_tenor AND $max_tenor
        )
        SELECT
            $underlying AS underlying,
            q.quote_date, q.expiration, q.strike, q.bid, q.ask,
            q.volume, q.open_interest, u.spot
        FROM quotes q
        JOIN und u ON u.d = q.quote_date
        WHERE q.strike / u.spot BETWEEN $mny_min AND $mny_max
        ORDER BY q.quote_date, q.expiration, q.strike
        """,
        {
            "opt": str(options_path),
            "und": str(underlying_path),
            "underlying": underlying.upper(),
            "min_tenor": MIN_TENOR_DAYS,
            "max_tenor": MAX_TENOR_DAYS,
            "mny_min": MONEYNESS_MIN,
            "mny_max": MONEYNESS_MAX,
        },
    ).df()
    frame["quote_date"] = pd.to_datetime(frame["quote_date"])
    frame["expiration"] = pd.to_datetime(frame["expiration"])
    return frame


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the quote contract.

    Bad rows are never silently dropped: they come back in the second frame so
    the caller can persist them for inspection.
    """
    try:
        valid = OptionQuoteSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = OptionQuoteSchema.validate(df.loc[~df.index.isin(bad_index)], lazy=True)
        return valid, quarantined


def ingest_option_quotes(
    store: LakeStore,
    *,
    underlying: str = "SPY",
    vendor_dir: Path | str = DEFAULT_VENDOR_DIR,
    ingest_date: dt.date | None = None,
    frame: pd.DataFrame | None = None,
) -> IngestResult:
    """Extract the roll smile and commit one immutable bronze snapshot.

    ``frame`` lets a caller inject an already-extracted slice instead of
    reading the vendor files, which is how the tests stay hermetic without a
    632 MB fixture.

    Raises ``FileNotFoundError`` when the vendor files are absent — loudly,
    because the alternative is committing an empty partition that would then
    shadow a good one (``docs/DISCOVERIES.md`` #3).
    """
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    root = Path(vendor_dir)
    options_path = root / f"{underlying.upper()}_options.parquet"
    underlying_path = root / f"{underlying.upper()}_underlying.parquet"

    if frame is None:
        for path in (options_path, underlying_path):
            if not path.exists():
                raise FileNotFoundError(
                    f"{path} not found. This adapter reads a local, hash-verified vendor "
                    "download; see docs/DATA_VERDICTS.md for how to fetch and verify it."
                )
        frame = extract_roll_smile(options_path, underlying_path, underlying=underlying)

    if frame.empty:
        raise ValueError(
            f"extraction produced no rows for {underlying.upper()}; refusing to commit an "
            "empty bronze partition that would shadow a good one"
        )

    valid, quarantined = validate_and_quarantine(frame)
    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        underlying=underlying.upper(),
        source_file=str(options_path),
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    log_event(
        logger,
        "ingestion.option_quotes.run",
        dataset=DATASET,
        underlying=result.underlying,
        ingest_date=ingest_date,
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        roll_dates=int(valid["quote_date"].nunique()) if not valid.empty else 0,
        first_quote=str(valid["quote_date"].min().date()) if not valid.empty else None,
        last_quote=str(valid["quote_date"].max().date()) if not valid.empty else None,
        source_file=result.source_file,
        bronze_path=result.bronze_path,
    )
