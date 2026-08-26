"""FRED credit-spread ingestion adapter -- bronze layer
(`docs/DATA_CONTRACTS.md` #4).

Source: FRED's series/observations endpoint,
``https://api.stlouisfed.org/fred/series/observations`` -- same keyed
source, same ``FRED_API_KEY`` setting, and the same ALFRED-vintage
point-in-time hazard as ``ingestion/rates.py`` (`docs/DATA_CONTRACTS.md`
#3): a credit-spread series can be revised after first publication, so
pulling only the latest value is a look-ahead bug, not a simplification.
The adapter therefore always requests FRED's full vintage history rather
than the single-vintage default, exactly as the rates adapter does.

Ingests HY OAS (``BAMLH0A0HYM2``) and IG OAS (``BAMLC0A0CM``).

Same three-function split as every other adapter here, so tests never
touch the network:

- :func:`parse_fred_observations` -- pure, parses one series' JSON payload.
- :func:`fetch_series_observations_raw` -- the HTTP call, exercised only by
  ``make ingest-credit`` (human/manually run), never by CI/pytest.
- :func:`ingest_credit` -- orchestrates fetch -> parse -> validate/
  quarantine -> commit-to-bronze across the whole series family.

Kept as its own module rather than sharing code with ``ingestion/rates.py``
-- the two adapters hit an identical endpoint shape, but every dataset in
this package already gets its own adapter module even when structurally
close to a sibling (``vix.py`` / ``cboe_strategy.py`` are both Cboe CSV
adapters and share no code), and each validates against its own schema
(``contracts/credit.py`` vs ``contracts/rates.py``) with its own bounds.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.credit import DATASET, DEFAULT_SERIES_IDS, CreditSchema
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "IngestResult",
    "fetch_series_observations_raw",
    "ingest_credit",
    "parse_fred_observations",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: FRED's own sentinel values meaning "every vintage on record" (ALFRED
#: mode) -- not a real calendar bound. Passing these as realtime_start/
#: realtime_end is how the API is told to return revision history instead
#: of only the latest-known value for each observation date.
_ALFRED_REALTIME_START = "1776-07-04"
_ALFRED_REALTIME_END = "9999-12-31"

#: FRED's missing-value sentinel in the observations payload -- a holiday
#: or not-yet-published print, not a zero.
_MISSING_VALUE = "."


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    series_ids: tuple[str, ...]


def fetch_series_observations_raw(
    series_id: str, api_key: str, *, timeout: float = 30.0
) -> dict[str, Any]:
    """Fetch one series' full vintage history. Network call -- not used by tests."""
    resp = requests.get(
        FRED_OBSERVATIONS_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": _ALFRED_REALTIME_START,
            "realtime_end": _ALFRED_REALTIME_END,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    return payload


def parse_fred_observations(series_id: str, raw: Mapping[str, Any]) -> pd.DataFrame:
    """Parse one series' FRED JSON payload into a typed
    ``(series_id, obs_date, value, vintage_date)`` frame.

    Pure function -- no network, no filesystem. A row whose value is
    FRED's ``"."`` missing-value sentinel is dropped here as "not a row"
    (a holiday/no-print is a legitimate absence, not a zero); anything else
    malformed -- an unparseable date, a non-numeric value -- survives as
    NaT/NaN so :func:`validate_and_quarantine` can catch it, rather than
    being silently discarded.
    """
    observations = raw.get("observations", [])
    if not observations:
        return _empty_frame()

    frame = pd.DataFrame(observations)
    is_present = frame["value"] != _MISSING_VALUE
    values = frame["value"].where(is_present)
    df = pd.DataFrame(
        {
            "series_id": series_id,
            "obs_date": pd.to_datetime(frame["date"], errors="coerce"),
            "value": pd.to_numeric(values, errors="coerce"),
            "vintage_date": pd.to_datetime(frame["realtime_start"], errors="coerce"),
        }
    )
    df = df.loc[is_present]
    return (
        df.sort_values(["obs_date", "vintage_date"])
        .drop_duplicates(subset=["obs_date", "vintage_date"], keep="last")
        .reset_index(drop=True)
    )


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "series_id": pd.Series([], dtype="object"),
            "obs_date": pd.Series([], dtype="datetime64[ns]"),
            "value": pd.Series([], dtype="float64"),
            "vintage_date": pd.Series([], dtype="datetime64[ns]"),
        }
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the credit contract.

    Bad rows are never silently dropped: they come back in the second
    frame so the caller can persist them for inspection.
    """
    try:
        valid = CreditSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = CreditSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_credit(
    store: LakeStore,
    series_ids: Sequence[str] | None = None,
    *,
    ingest_date: dt.date | None = None,
    raw: Mapping[str, Mapping[str, Any]] | None = None,
    api_key: str | None = None,
) -> IngestResult:
    """Fetch (or use supplied) FRED observations, validate, and commit one
    bronze snapshot -- the whole family as a single immutable partition
    (matches ``ingest_rates``: a partial run must not half-overwrite the
    previous complete panel).

    ``raw`` maps ``series_id -> FRED JSON payload``, letting callers (tests)
    inject payloads instead of hitting the network; :func:`fetch_series_observations_raw`
    is called only for series absent from it, and only then does a missing
    ``api_key`` raise -- FRED's endpoint is keyed, unlike most adapters in
    this package.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches rates.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    resolved = tuple(
        s.upper() for s in (series_ids if series_ids is not None else DEFAULT_SERIES_IDS)
    )

    parsed = []
    for series_id in resolved:
        if raw is not None and series_id in raw:
            payload: Mapping[str, Any] = raw[series_id]
        else:
            if not api_key:
                raise ValueError(
                    "ingest_credit needs a FRED api_key for a live fetch of "
                    f"{series_id!r} (see Settings.fred_api_key / HUMAN_TODO.md)"
                )
            payload = fetch_series_observations_raw(series_id, api_key)
        parsed.append(parse_fred_observations(series_id, payload))

    combined = pd.concat(parsed, ignore_index=True) if parsed else _empty_frame()
    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name -- never a raw filesystem write (matches
    # rates.py/vix.py; the lake is the only thing allowed to touch storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        series_ids=resolved,
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f) -- an automated
    action without a runtime record is below the bar."""
    log_event(
        _LOGGER,
        "ingest.credit",
        dataset=DATASET,
        source="fred",
        ingest_date=ingest_date.isoformat(),
        series_ids=",".join(result.series_ids),
        series_count=len(result.series_ids),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_obs_date=_edge(valid, "min"),
        last_obs_date=_edge(valid, "max"),
    )


def _edge(valid: pd.DataFrame, which: str) -> str | None:
    """Window boundary of the committed rows, for the run log. ``None`` on an
    empty frame so the log line drops the field rather than printing "NaT"."""
    if valid.empty:
        return None
    stamp = valid["obs_date"].min() if which == "min" else valid["obs_date"].max()
    return str(pd.Timestamp(stamp).date())
