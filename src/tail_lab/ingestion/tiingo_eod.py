"""Tiingo end-of-day ingestion adapter -- bronze layer (`docs/DATA_CONTRACTS.md` #14).

Source: ``https://api.tiingo.com/tiingo/daily/<symbol>/prices`` -- free but
keyed (``TIINGO_API_KEY``, read through :class:`~tail_lab.config.Settings`).
Its rows carry what no other source in the lake does: the cash dividend paid
on each ex-date and the split factor effective each day, beside the
as-traded close. ``transforms/dividend_yield.py`` turns them into the
dividend yield ``q`` every option price needs; before this dataset the roll
backtest priced every name at ``q = 0`` (`research/option_pricer.py`,
assumption 4).

Budget: the free tier allows about 50 requests an hour. One request per
symbol returns its whole history, so a run is ``len(universe)`` requests;
:func:`tiingo_getter` spaces them :data:`THROTTLE_S` apart (≤ 45/hour), which
makes a 70-symbol run take about 92 minutes. A manual use of the same key in
that hour can still trip a 429; the fetch then retries with a long backoff and,
if the hour is truly spent, fails -- and a failed fetch aborts the write.

Write policy, and why each half exists:

* **A failed fetch aborts the whole write.** An as-of read takes exactly one
  partition, so a partition missing a symbol would hide that symbol's whole
  history behind the newer, incomplete snapshot.
* **Invalid rows follow the repo convention** -- quarantined, never silently
  dropped -- but a symbol with *any* quarantined row is withheld from the valid
  partition entirely. A dividend or split row that failed validation would
  otherwise leave a silently wrong yield; withheld, the symbol is simply
  absent, which every reader treats as ``q`` unknown.
* **A run on a day that already has a partition does nothing** and says so:
  bronze is immutable (a second write would be a no-op anyway), and skipping
  saves the ~92 minutes of rate-limited fetching.

Three-function seam as everywhere else, so tests never touch the network:
the pure parser :func:`parse_tiingo_prices`, the validator
:func:`validate_and_quarantine`, and :func:`ingest_tiingo_eod`, which takes an
injectable ``get``.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.options_calendar import universe_symbols
from tail_lab.contracts.tiingo_eod import DATASET, TiingoEodSchema
from tail_lab.ingestion.json_payload import json_list, json_object
from tail_lab.ingestion.sources import retry_transient
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "QUARANTINE_DATASET",
    "START_DATE",
    "THROTTLE_S",
    "IngestResult",
    "TiingoGet",
    "ingest_tiingo_eod",
    "parse_tiingo_prices",
    "tiingo_getter",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

TIINGO_PRICES_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: First date requested. SPY lists on 1993-01-29, the earliest of the
#: universe; asking from the start of 1993 takes every symbol's full history.
START_DATE = dt.date(1993, 1, 1)

#: Seconds between requests: 3600 / 80 = 45 an hour, under the free tier's
#: ~50, leaving a little room for a human using the same key.
THROTTLE_S = 80.0

_FETCH_ATTEMPTS = 3
#: A 429 means the hourly allocation is spent; a short backoff cannot help.
_FETCH_BACKOFF_S = 300.0

_COLUMNS = ["symbol", "trade_date", "close", "adj_close", "div_cash", "split_factor"]

#: ``get(symbol, params) -> parsed JSON``. Building the URL and holding the
#: key are the getter's business, so tests inject a fake with no key.
TiingoGet = Callable[[str, Mapping[str, str]], object]


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run."""

    bronze_path: str | None
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    symbols: tuple[str, ...]
    #: Symbols left out of the valid partition because a row of theirs failed
    #: validation -- readers see them as absent, i.e. ``q`` unknown.
    withheld_symbols: tuple[str, ...]
    #: True when today's partition already existed and nothing was fetched.
    skipped: bool


def tiingo_getter(
    api_key: str,
    *,
    throttle_s: float = THROTTLE_S,
    timeout: float = 120.0,
    sleep: Callable[[float], None] = time.sleep,
) -> TiingoGet:
    """The live HTTP seam: spaced ``throttle_s`` apart, with the API key
    redacted from every error. Network -- used by ``make ingest-tiingo-eod``."""
    calls = 0

    def call(symbol: str, params: Mapping[str, str]) -> object:
        resp = requests.get(
            TIINGO_PRICES_URL.format(symbol=symbol),
            params={**params, "token": api_key, "format": "json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        if not resp.content:
            # A requests fault, so retry_transient treats it as the blip it is
            # -- the same as a truncated body below.
            raise requests.exceptions.ContentDecodingError("Tiingo returned an empty 200 body")
        # Parsed INSIDE the retried call: a truncated or HTML 200 raises
        # requests' JSONDecodeError, which retry_transient treats as a blip --
        # as every sibling adapter does -- rather than killing a 92-minute run.
        payload: object = resp.json()
        return payload

    def get(symbol: str, params: Mapping[str, str]) -> object:
        nonlocal calls
        if calls:
            sleep(throttle_s)
        calls += 1
        redacted: requests.exceptions.RequestException | None = None
        try:
            payload = retry_transient(
                lambda: call(symbol, params),
                attempts=_FETCH_ATTEMPTS,
                backoff_s=_FETCH_BACKOFF_S,
                event="ingest.tiingo_eod.retry",
                symbol=symbol,
            )
        except requests.exceptions.RequestException as exc:
            # requests carries the full URL -- token included -- in the
            # message; re-raise the same type with it redacted, outside the
            # handler so the original cannot ride along as __context__.
            # The body that failed: the response's, or for a body that never
            # parsed (no response attached) the document the parser saw.
            if isinstance(exc, requests.exceptions.JSONDecodeError):
                body = exc.doc[:300]
            else:
                body = exc.response.text[:300] if exc.response is not None else ""
            # Same type where its constructor takes a message; JSONDecodeError
            # needs (msg, doc, pos), so a body that stayed unparseable is
            # re-raised as the decoding fault it is.
            kind = (
                requests.exceptions.ContentDecodingError
                if isinstance(exc, requests.exceptions.JSONDecodeError)
                else type(exc)
            )
            message = f"Tiingo fetch failed for {symbol}: {exc} {body}".strip()
            redacted = kind(message.replace(api_key, "<redacted>"))
        if redacted is not None:
            raise redacted
        return payload

    return get


def parse_tiingo_prices(symbol: str, raw: object) -> pd.DataFrame:
    """Tiingo's price array -> rows in the contract's columns.

    A malformed cell becomes NaN/NaT so :func:`validate_and_quarantine` can
    route its row to quarantine. A payload that is not an array, or an empty
    one, raises: that is a failed fetch, not a bad row, and it must abort the
    write rather than leave the symbol out of the partition.
    """
    rows = json_list(raw, f"Tiingo prices for {symbol}")
    if not rows:
        raise ValueError(f"Tiingo returned no rows for {symbol}")
    records = [json_object(row, f"Tiingo row for {symbol}") for row in rows]
    frame = pd.DataFrame.from_records(records)
    missing = {"date", "close", "adjClose", "divCash", "splitFactor"} - set(frame.columns)
    if missing:
        raise ValueError(f"Tiingo rows for {symbol} lack {sorted(missing)}")
    # The session is the date AS WRITTEN: converting a "...T00:00+09:00" stamp
    # to UTC first would move it to the previous day.
    dates = pd.to_datetime(frame["date"].astype(str).str[:10], format="%Y-%m-%d", errors="coerce")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "trade_date": dates,
            "close": _numeric(frame["close"]),
            "adj_close": _numeric(frame["adjClose"]),
            "div_cash": _numeric(frame["divCash"]),
            "split_factor": _numeric(frame["splitFactor"]),
        },
        columns=_COLUMNS,
    )


def _numeric(cells: pd.Series) -> pd.Series:
    """Numbers as floats; anything else -- a string, null, or a JSON boolean,
    which ``to_numeric`` would quietly read as 1.0 -- as NaN for quarantine."""
    return pd.to_numeric(cells.map(lambda v: None if isinstance(v, bool) else v), errors="coerce")


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the contract; bad rows
    come back in the second frame so the caller can persist them."""
    try:
        valid = TiingoEodSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = TiingoEodSchema.validate(df.loc[~df.index.isin(bad_index)], lazy=True)
        return valid, quarantined


def ingest_tiingo_eod(
    store: LakeStore,
    symbols: Sequence[str] | None = None,
    *,
    api_key: str | None = None,
    get: TiingoGet | None = None,
    ingest_date: dt.date | None = None,
) -> IngestResult:
    """Fetch every symbol's full history and commit one bronze partition.

    ``get`` injects a Tiingo stand-in (tests); without it a live
    :func:`tiingo_getter` is built, and a missing ``api_key`` raises.
    """
    # UTC, like every adapter: snapshot dates must match the as-of clock.
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    resolved = tuple(s.lower() for s in (symbols if symbols is not None else universe_symbols()))
    if store.bronze_partition_exists(DATASET, ingest_date):
        result = IngestResult(None, 0, 0, None, resolved, (), skipped=True)
        _log_run(result, ingest_date, pd.DataFrame(columns=_COLUMNS))
        return result
    if get is None:
        if not api_key:
            raise ValueError(
                "ingest_tiingo_eod needs a Tiingo api_key for a live fetch "
                "(see Settings.tiingo_api_key / HUMAN_TODO.md)"
            )
        get = tiingo_getter(api_key)

    params = {"startDate": START_DATE.isoformat()}
    # Any exception here propagates and nothing is written: see the module
    # docstring -- a partition missing a symbol would hide its history.
    parsed = [parse_tiingo_prices(symbol, get(symbol, params)) for symbol in resolved]
    combined = pd.concat(parsed, ignore_index=True)
    valid, quarantined = validate_and_quarantine(combined)

    withheld = tuple(sorted(set(quarantined["symbol"].dropna().astype(str))))
    if withheld:
        valid = valid.loc[~valid["symbol"].isin(withheld)].reset_index(drop=True)
    if valid.empty:
        # write_bronze of an empty frame creates no partition, so the previous
        # snapshot would keep serving every symbol -- including the withheld
        # ones -- while the run looked successful. Fail it instead.
        raise ValueError(
            f"every Tiingo symbol was withheld for invalid rows ({len(withheld)}); "
            "nothing written -- see the quarantine rows in the run log"
        )

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(QUARANTINE_DATASET, ingest_date, quarantined)

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        symbols=resolved,
        withheld_symbols=withheld,
        skipped=False,
    )
    _log_run(result, ingest_date, valid)
    return result


def _log_run(result: IngestResult, ingest_date: dt.date, valid: pd.DataFrame) -> None:
    """Emit the run's full surface (`docs/STANDARDS.md` §f)."""
    dividends = int((valid["div_cash"] > 0).sum()) if not valid.empty else 0
    splits = int((valid["split_factor"] != 1.0).sum()) if not valid.empty else 0
    log_event(
        _LOGGER,
        "ingest.tiingo_eod",
        dataset=DATASET,
        source="tiingo",
        ingest_date=ingest_date.isoformat(),
        skipped=result.skipped,
        symbol_count=len(result.symbols),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        withheld_symbols=",".join(result.withheld_symbols) or None,
        dividend_rows=dividends,
        split_rows=splits,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_trade_date=None if valid.empty else str(valid["trade_date"].min().date()),
        last_trade_date=None if valid.empty else str(valid["trade_date"].max().date()),
    )
