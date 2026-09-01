"""Minneapolis Fed MPD ingestion adapter -- bronze layer
(`docs/DATA_CONTRACTS.md` #9, `AGENT_TODO.md`'s "Minneapolis Fed MPD
adapter" item).

Source: `minneapolisfed.org`'s public CSV,
``https://www.minneapolisfed.org/-/media/files/banking/mpd/mpd_stats.csv``
-- keyless, no account, no login, official (a Federal Reserve Bank). One
file carries the whole market family (equity, single-name, commodity, FX,
rate and inflation risk-neutral densities) in long format, so unlike
``ingestion/cboe_strategy.py`` there is no per-ticker fetch loop: one GET,
one parse, one bronze partition.

Two functions, split so tests never touch the network:

- :func:`parse_mpd_csv` -- pure, parses the whole file's text into a typed
  DataFrame. Pinned against a committed real-data fixture
  (``tests/fixtures/mpd_stats_sample.csv``).
- :func:`fetch_mpd_raw` -- the HTTP call. Exercised only by
  ``make ingest-mpd`` (human/manually run), never by CI/pytest.

:func:`ingest_mpd` orchestrates fetch -> parse -> validate/quarantine ->
commit-to-bronze, mirroring ``ingest_cboe_strategy``.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from dataclasses import dataclass

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.mpd import DATASET, MpdSchema
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_mpd_raw",
    "ingest_mpd",
    "parse_mpd_csv",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

MPD_STATS_URL = "https://www.minneapolisfed.org/-/media/files/banking/mpd/mpd_stats.csv"
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: The source serves ``MM/DD/YYYY`` dates, quoted. Parsing with an explicit
#: format (rather than letting pandas infer) avoids the same silent-month-flip
#: risk ``ingestion/cboe_strategy.py`` guards against.
_DATE_FORMAT = "%m/%d/%Y"

#: Source column -> contract column. The source's ``idt``/``lg_change_*``/
#: ``prDec``/``prInc`` names are terse to the point of being ambiguous;
#: everything downstream reads the contract's names instead.
_COLUMN_MAP: dict[str, str] = {
    "market": "market",
    "idt": "obs_date",
    "maturity_target": "maturity_months",
    "mu": "mu",
    "sd": "sd",
    "skew": "skew",
    "kurt": "kurt",
    "p10": "p10",
    "p50": "p50",
    "p90": "p90",
    "prDec": "prob_large_decline",
    "prInc": "prob_large_increase",
}


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    markets: tuple[str, ...]


def fetch_mpd_raw(*, timeout: float = 30.0) -> str:
    """Fetch the whole MPD stats CSV text. Network call -- not used by tests."""
    resp = requests.get(
        MPD_STATS_URL,
        headers={"User-Agent": "Mozilla/5.0 (tail-lab ingestion bot)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def parse_mpd_csv(raw: str) -> pd.DataFrame:
    """Parse the MPD stats CSV text into a typed, contract-named DataFrame.

    Pure function -- no network, no filesystem. The file opens with two
    free-text preamble lines above the header (see the module docstring's
    inflation-threshold note), so the header row is located rather than
    assumed to be line 1, mirroring
    ``ingestion.cboe_strategy.parse_index_history_csv``. ``lg_change_decr``/
    ``lg_change_incr`` (the numeric thresholds defining "large") are read but
    not retained -- the contract carries the resulting probabilities
    (``prob_large_decline``/``prob_large_increase``), which is what
    `AGENT_TODO.md` scoped this adapter to carry.
    """
    header_offset = _find_header_line(raw)
    frame = pd.read_csv(io.StringIO(raw), skiprows=header_offset)
    if frame.empty:
        return _empty_frame()

    df = pd.DataFrame(
        {
            "market": frame["market"].astype(str),
            "obs_date": pd.to_datetime(frame["idt"], format=_DATE_FORMAT, errors="coerce"),
            "maturity_months": pd.to_numeric(frame["maturity_target"], errors="coerce"),
            "mu": pd.to_numeric(frame["mu"], errors="coerce"),
            "sd": pd.to_numeric(frame["sd"], errors="coerce"),
            "skew": pd.to_numeric(frame["skew"], errors="coerce"),
            "kurt": pd.to_numeric(frame["kurt"], errors="coerce"),
            "p10": pd.to_numeric(frame["p10"], errors="coerce"),
            "p50": pd.to_numeric(frame["p50"], errors="coerce"),
            "p90": pd.to_numeric(frame["p90"], errors="coerce"),
            "prob_large_decline": pd.to_numeric(frame["prDec"], errors="coerce"),
            "prob_large_increase": pd.to_numeric(frame["prInc"], errors="coerce"),
        }
    )
    return (
        df.sort_values(["market", "obs_date"])
        .drop_duplicates(subset=["market", "obs_date"], keep="last")
        .reset_index(drop=True)
    )


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "market": pd.Series([], dtype="object"),
            "obs_date": pd.Series([], dtype="datetime64[ns]"),
            "maturity_months": pd.Series([], dtype="float64"),
            "mu": pd.Series([], dtype="float64"),
            "sd": pd.Series([], dtype="float64"),
            "skew": pd.Series([], dtype="float64"),
            "kurt": pd.Series([], dtype="float64"),
            "p10": pd.Series([], dtype="float64"),
            "p50": pd.Series([], dtype="float64"),
            "p90": pd.Series([], dtype="float64"),
            "prob_large_decline": pd.Series([], dtype="float64"),
            "prob_large_increase": pd.Series([], dtype="float64"),
        }
    )


def _find_header_line(raw: str) -> int:
    """Index of the ``"market","idt",...`` header line, so the two preamble
    lines don't shift columns."""
    for offset, line in enumerate(raw.splitlines()):
        if line.strip().lower().startswith('"market"'):
            return offset
    return 0


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the MPD contract.

    Bad rows are never silently dropped: they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        valid = MpdSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = MpdSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_mpd(
    store: LakeStore,
    *,
    ingest_date: dt.date | None = None,
    raw: str | None = None,
) -> IngestResult:
    """Fetch (or use supplied) the MPD stats file, validate, and commit one
    bronze snapshot covering every market the file carries.

    ``raw`` injects the file's text instead of hitting the network, for
    tests. The whole file is committed as a single immutable bronze
    partition, matching ``ingest_cboe_strategy``'s "one panel per run" shape
    -- the source itself is one fetch, so there is no partial-run case to
    guard against.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py/cboe_strategy.py;
    # docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    parsed = parse_mpd_csv(raw if raw is not None else fetch_mpd_raw())
    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name -- never a raw filesystem write (matches
    # cboe_strategy.py; the lake is the only thing allowed to touch storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    markets = tuple(sorted(valid["market"].unique())) if not valid.empty else ()
    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        markets=markets,
    )
    log_event(
        _LOGGER,
        "ingest.mpd",
        dataset=DATASET,
        source="minneapolisfed",
        ingest_date=ingest_date.isoformat(),
        market_count=len(markets),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
    )
    return result
