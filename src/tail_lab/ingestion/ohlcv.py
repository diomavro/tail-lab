"""Underlying OHLCV ingestion adapter — bronze layer (`docs/DATA_CONTRACTS.md` #1).

**Primary source: Nasdaq** —
``https://api.nasdaq.com/api/quote/{SYMBOL}/historical``. Keyless (a browser
``User-Agent`` and a JSON ``Accept`` header are required), and the only
free, working, broad-universe daily feed found in the 2026-08-21 audit
(`docs/DATA_FINDINGS.md`).

**Fallback source: Yahoo's chart JSON** — the adapter's original primary,
now demoted because it returns HTTP 429 to residential *and* datacenter
clients as of 2026. It is kept in the chain rather than deleted: it is the
only free source here that supplies a genuinely dividend-adjusted close
(see the caveat below), so if it recovers we want it back.

Two source-specific limits the caller must know about, both measured:

1. **Nasdaq caps history at ~2,513 rows (~10 years)** regardless of the
   ``fromdate`` requested — asking for 2006 returns the same window
   starting 2016. Deep history therefore is *not* available from the
   primary source. This is fine for the Put Lab's 4-5 year windows and is
   not fine for a GFC-era backtest; the latter needs `docs/DATA_SOURCING.md`
   §10.1's dataset, not this adapter.
2. **Nasdaq's prices are split-adjusted but not dividend-adjusted.**
   (Verified: TSLA's 2016 rows come back at ~$14.86, i.e. through the later
   15x of splits.) The contract wants an ``adj_close`` with splits *and*
   dividends applied, and Nasdaq cannot supply one — so this adapter sets
   ``adj_close = close`` for Nasdaq rows and records the basis on
   :class:`IngestResult` and in the run log. A partition is internally
   consistent either way (bronze as-of reads one partition, so no
   Yahoo/Nasdaq splice can occur inside a series), but a Nasdaq partition
   and a Yahoo partition are *not* on the same basis, and any comparison
   across them is apples-to-oranges. Say so rather than hide it.

**Why Cboe is not in this chain.** Cboe's CDN serves indices, not ETFs or
single names, so it cannot supply SPY/QQQ/TSLA history. Its
``delayed_quotes`` endpoint does carry a current-day OHLCV bar for those
symbols, but adding it as a source would let a *one-row* frame satisfy the
chain — and because bronze as-of resolution reads the latest partition, a
one-row partition would shadow a five-year one and silently truncate every
backtest reading it. That exact truncation has bitten this platform once
already. A same-day-only source is therefore deliberately excluded.

Functions split so tests never touch the network:

- :func:`parse_nasdaq_historical` / :func:`parse_yahoo_chart_ohlcv` — pure
  parsers, one per source, each pinned to a committed fixture.
- :func:`fetch_nasdaq_raw` / :func:`fetch_ohlcv_raw` — the HTTP calls,
  exercised by ``make ingest-ohlcv`` and (via :func:`latest_market_session`)
  by the chain sweep and the vix / vix-complex / cboe-strategy targets; tests
  reach them only through a patched ``requests.get``.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.ohlcv import OhlcvSchema, dataset_id
from tail_lab.ingestion.sources import Source, first_available, retry_transient
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "IngestResult",
    "dataset_id",
    "fetch_nasdaq_raw",
    "fetch_ohlcv_raw",
    "ingest_ohlcv",
    "latest_market_session",
    "parse_nasdaq_historical",
    "parse_yahoo_chart_ohlcv",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

NASDAQ_HISTORICAL_URL_TEMPLATE = "https://api.nasdaq.com/api/quote/{symbol}/historical"
YAHOO_CHART_URL_TEMPLATE = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
#: Nasdaq rejects the wrong asset class with HTTP 400 rather than an empty
#: result, and gives no way to ask which one a symbol is — so the fetcher
#: tries each in turn. ETFs first: the screening universe is ETF-heavy.
_NASDAQ_ASSET_CLASSES = ("etf", "stocks")
#: Nasdaq's own ceiling, measured 2026-08-21: larger limits return the same
#: 2,513 rows. Requested explicitly so the truncation is visible in code
#: rather than discovered in a short backtest.
_NASDAQ_MAX_ROWS = 2513
_NASDAQ_DATE_FORMAT = "%m/%d/%Y"

#: Retry budget for one Nasdaq request, used by the market-session witness
#: (:func:`latest_market_session`). NOT the ingest path's default: at its 30s
#: timeout, 3 attempts per asset class raise ``ingest_ohlcv``'s hang worst case
#: from 60s to 220s per symbol -- ~64 extra minutes across the daily refresh's
#: 24 symbols, past its TimeoutStartSec=120min (adversarial review,
#: 2026-09-25). Measured 2026-09-25 05:01 UTC: a resume-time DNS failure
#: made a single attempt give up, silently skipping the witness's
#: frozen-feed check while Cboe's own fetch recovered the same fault by
#: retrying. Wrapped around the individual ``requests.get`` per asset class,
#: not around this function as a whole: :func:`fetch_nasdaq_raw` folds every
#: per-asset-class failure into a ``ValueError``, which
#: ``is_transient_fetch_error`` never recognizes as retriable.
_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_S = 10.0

#: What ``adj_close`` means for each source. Recorded on the result and in
#: the run log because the two are not interchangeable.
ADJUSTMENT_BASIS = {
    "nasdaq": "splits_only",
    "yahoo": "splits_and_dividends",
}


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined.

    ``source_id`` and ``adjustment_basis`` record *which* feed served this
    immutable partition and on what basis its ``adj_close`` was computed —
    unanswerable later from the rows alone, and load-bearing for anyone
    comparing two partitions.
    """

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    source_id: str
    adjustment_basis: str


def fetch_nasdaq_raw(
    symbol: str, *, years: int = 10, timeout: float = 30.0, attempts: int = 1
) -> Any:
    """Fetch raw Nasdaq historical JSON for ``symbol``. Network — not used by tests.

    Tries each asset class until one answers with rows: Nasdaq 400s on the
    wrong class and offers no lookup, so trying is the only option. With
    ``attempts`` > 1 each request is retried on its own
    (``sources.retry_transient``), so a flaky attempt costs a backoff, not the
    whole asset class; the default of 1 keeps the ingest path's budget.
    """
    today = dt.datetime.now(dt.UTC).date()
    params = {
        "fromdate": (today - dt.timedelta(days=365 * years + 5)).isoformat(),
        "todate": today.isoformat(),
        "limit": str(_NASDAQ_MAX_ROWS),
    }
    last_error: Exception | None = None
    for asset_class in _NASDAQ_ASSET_CLASSES:
        try:
            payload: Any = retry_transient(
                partial(_get_nasdaq_json, symbol, {**params, "assetclass": asset_class}, timeout),
                attempts=attempts,
                backoff_s=_FETCH_BACKOFF_S,
                event="ingestion.ohlcv.nasdaq_retry",
                symbol=symbol.upper(),
                assetclass=asset_class,
            )
            if _nasdaq_rows(payload):
                return payload
            last_error = ValueError(f"no rows for assetclass={asset_class}")
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Nasdaq returned no usable data for {symbol!r}: {last_error}")


def _get_nasdaq_json(symbol: str, params: dict[str, str], timeout: float) -> Any:
    """One unretried GET against Nasdaq's historical endpoint. Split out of
    :func:`fetch_nasdaq_raw` so the real ``requests.exceptions.RequestException``
    reaches ``retry_transient`` before the caller folds it into a ``ValueError``.
    """
    resp = requests.get(
        NASDAQ_HISTORICAL_URL_TEMPLATE.format(symbol=symbol.upper()),
        params=params,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def fetch_ohlcv_raw(
    symbol: str, *, range_: str = "5y", interval: str = "1d", timeout: float = 15.0
) -> Any:
    """Fetch raw Yahoo chart JSON for ``symbol``. Network call — not used by tests.

    Defaults to a 5-year range: the backtester routinely runs 4-year windows, so
    a shorter default would let a plain ``make ingest-ohlcv SYMBOL=X`` write a
    truncated bronze partition that then shadows a longer one (immutable bronze
    resolves to the latest ingest_date), silently cutting backtest history.
    """
    resp = requests.get(
        YAHOO_CHART_URL_TEMPLATE.format(symbol=symbol.upper()),
        params={"range": range_, "interval": interval},
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload: Any = resp.json()
    return payload


def _nasdaq_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The row list out of Nasdaq's nested envelope, or [] if absent."""
    data = raw.get("data") or {}
    table = data.get("tradesTable") or {}
    rows = table.get("rows") or []
    return list(rows)


def _nasdaq_number(value: Any) -> float | None:
    """Nasdaq renders prices as ``$345.13`` and volume as ``30,766,360``.

    Returns ``None`` for anything unparseable rather than raising, so one
    bad cell demotes its row to quarantine instead of killing the ingest.
    """
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text or text in {"--", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_nasdaq_historical(symbol: str, raw: dict[str, Any]) -> pd.DataFrame:
    """Parse Nasdaq's historical JSON into a typed OHLCV DataFrame.

    Pure function — no network, no filesystem. ``adj_close`` is set equal to
    ``close``: Nasdaq's prices are split-adjusted but carry no dividend
    adjustment and no separate adjusted series, so claiming otherwise would
    be a lie in the data (see the module docstring). A bar missing any of
    open/high/low/close/volume is dropped as "not a row"; a bar present but
    unparseable survives as NaN for the contract to quarantine.
    """
    rows = _nasdaq_rows(raw)
    if not rows:
        return _empty_frame()

    records: list[dict[str, Any]] = []
    for row in rows:
        close = _nasdaq_number(row.get("close"))
        open_ = _nasdaq_number(row.get("open"))
        high = _nasdaq_number(row.get("high"))
        low = _nasdaq_number(row.get("low"))
        volume = _nasdaq_number(row.get("volume"))
        if None in (close, open_, high, low, volume):
            continue
        records.append(
            {
                "symbol": symbol.upper(),
                # str() not the raw value: a missing date arrives as None,
                # which to_datetime rejects outright. Coercing to text lets
                # it become NaT and reach quarantine as a bad row, rather
                # than crashing the whole ingest over one cell.
                "trade_date": pd.to_datetime(
                    str(row.get("date") or ""), format=_NASDAQ_DATE_FORMAT, errors="coerce"
                ),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": int(volume) if volume is not None else 0,
                # Split-adjusted only -- see the module docstring.
                "adj_close": close,
            }
        )
    if not records:
        return _empty_frame()

    df = pd.DataFrame.from_records(records)
    return (
        df.sort_values("trade_date")
        .drop_duplicates(subset="trade_date", keep="last")
        .reset_index(drop=True)
    )


def _empty_frame() -> pd.DataFrame:
    """A correctly-typed zero-row frame, so an empty source still validates."""
    return pd.DataFrame(
        {
            "symbol": pd.Series([], dtype="object"),
            "trade_date": pd.Series([], dtype="datetime64[ns]"),
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="int64"),
            "adj_close": pd.Series([], dtype="float64"),
        }
    )


def parse_yahoo_chart_ohlcv(symbol: str, raw: dict[str, Any]) -> pd.DataFrame:
    """Parse Yahoo's chart JSON payload into a typed OHLCV DataFrame.

    Pure function — no network, no filesystem. A bar is dropped (not
    quarantined) when any of open/high/low/close is null — Yahoo emits
    these for non-trading timestamps in the requested range, so a null bar
    is "not a row," not an invalid one; contract validation downstream
    still catches anything else wrong with a bar that did trade.
    """
    result = raw["chart"]["result"][0]
    timestamps: list[int] = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    opens: list[float | None] = quote["open"]
    highs: list[float | None] = quote["high"]
    lows: list[float | None] = quote["low"]
    closes: list[float | None] = quote["close"]
    volumes: list[int | None] = quote["volume"]
    adjcloses: list[float | None] = result["indicators"]["adjclose"][0]["adjclose"]
    gmtoffset = int(result["meta"].get("gmtoffset", 0))

    dates: list[dt.date] = []
    kept_open: list[float] = []
    kept_high: list[float] = []
    kept_low: list[float] = []
    kept_close: list[float] = []
    kept_volume: list[int] = []
    kept_adjclose: list[float] = []
    for ts, o, h, low_, c, v, adj in zip(
        timestamps, opens, highs, lows, closes, volumes, adjcloses, strict=True
    ):
        if o is None or h is None or low_ is None or c is None or v is None or adj is None:
            continue
        local_dt = dt.datetime.fromtimestamp(ts + gmtoffset, tz=dt.UTC)
        dates.append(local_dt.date())
        kept_open.append(float(o))
        kept_high.append(float(h))
        kept_low.append(float(low_))
        kept_close.append(float(c))
        kept_volume.append(int(v))
        kept_adjclose.append(float(adj))

    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trade_date": pd.to_datetime(dates),
            "open": kept_open,
            "high": kept_high,
            "low": kept_low,
            "close": kept_close,
            "volume": kept_volume,
            "adj_close": kept_adjclose,
        }
    )
    return (
        df.sort_values("trade_date")
        .drop_duplicates(subset="trade_date", keep="last")
        .reset_index(drop=True)
    )


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the OHLCV contract.

    Bad rows are never silently dropped: they come back in the second
    frame so the caller can persist them for inspection.
    """
    try:
        valid = OhlcvSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = OhlcvSchema.validate(valid, lazy=True)
        return valid, quarantined


def _build_sources(
    symbol: str, *, nasdaq_raw: dict[str, Any] | None, raw: dict[str, Any] | None
) -> list[Source]:
    """The ordered source chain: Nasdaq first, Yahoo second.

    **Injection is hermetic** — supplying any payload restricts the chain to
    the injected sources, so a test that injects one source can never reach
    the network for the others (`docs/STANDARDS.md`: tests never touch the
    network). Injecting nothing gives the full live chain.
    """
    nasdaq = Source(
        source_id="nasdaq",
        fetch=lambda: parse_nasdaq_historical(
            symbol, nasdaq_raw if nasdaq_raw is not None else fetch_nasdaq_raw(symbol)
        ),
    )
    yahoo = Source(
        source_id="yahoo",
        fetch=lambda: parse_yahoo_chart_ohlcv(
            symbol, raw if raw is not None else fetch_ohlcv_raw(symbol)
        ),
    )
    if nasdaq_raw is None and raw is None:
        return [nasdaq, yahoo]
    return [src for src, injected in ((nasdaq, nasdaq_raw), (yahoo, raw)) if injected is not None]


def ingest_ohlcv(
    store: LakeStore,
    symbol: str,
    *,
    ingest_date: dt.date | None = None,
    raw: dict[str, Any] | None = None,
    nasdaq_raw: dict[str, Any] | None = None,
) -> IngestResult:
    """Resolve a source, validate, and commit to bronze.

    ``nasdaq_raw`` / ``raw`` inject that source's payload instead of hitting
    the network. The chain is still tried in order, so a supplied ``raw``
    (Yahoo) is used only when the Nasdaq source fails or yields nothing.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    parsed, source_id = first_available(
        _build_sources(symbol, nasdaq_raw=nasdaq_raw, raw=raw),
        dataset=dataset_id(symbol),
        logger=_LOGGER,
    )
    valid, quarantined = validate_and_quarantine(parsed)

    bronze_path = store.write_bronze(dataset_id(symbol), ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name — never a raw filesystem write (matches
    # vix.py; the lake is the only thing allowed to touch storage).
    quarantine_path: str | None = None
    if not quarantined.empty:
        quarantine_path = store.write_bronze(
            f"{dataset_id(symbol)}__quarantine", ingest_date, quarantined
        )

    result = IngestResult(
        bronze_path=bronze_path,
        valid_rows=len(valid),
        quarantined_rows=len(quarantined),
        quarantine_path=quarantine_path,
        source_id=source_id,
        adjustment_basis=ADJUSTMENT_BASIS.get(source_id, "unknown"),
    )
    log_event(
        _LOGGER,
        "ingest.ohlcv",
        dataset=dataset_id(symbol),
        symbol=symbol.upper(),
        source=source_id,
        adjustment_basis=result.adjustment_basis,
        ingest_date=ingest_date.isoformat(),
        valid_rows=result.valid_rows,
        quarantined_rows=result.quarantined_rows,
        bronze_path=result.bronze_path,
        quarantine_path=result.quarantine_path,
        first_trade_date=(
            None if valid.empty else str(pd.Timestamp(valid["trade_date"].min()).date())
        ),
        last_trade_date=(
            None if valid.empty else str(pd.Timestamp(valid["trade_date"].max()).date())
        ),
    )
    return result


#: The instrument whose daily bars stand in for "did the US market trade".
#: SPY because it is the most liquid listed name and already an OHLCV source
#: here; Nasdaq's historical endpoint lists only COMPLETED sessions (measured
#: 2026-09-24 17:36 UTC, mid-session: newest bar 2026-09-23), so a sweep run
#: during market hours cannot mistake a live session for a finished one.
_MARKET_REFERENCE_SYMBOL = "SPY"

#: Shorter than ``fetch_nasdaq_raw``'s 30s default: the daily refresh calls
#: this witness three times, and its unit budget (TimeoutStartSec=120min) is
#: nearly spent in the all-sources-hang worst case -- ~119 min counted by
#: adversarial review on 2026-09-25, including the refresh's 5-minute lock
#: wait. NOMINAL cost here: 2 asset classes x (``_FETCH_ATTEMPTS`` x 10s +
#: (``_FETCH_ATTEMPTS`` - 1) x ``_FETCH_BACKOFF_S``) = 100s per call (measured:
#: 6 requests, 40s of backoff; the budget figure above is ~1 min low). Not
#: a true ceiling -- requests' timeout bounds connect and each read
#: separately, per resolved address, and DNS not at all -- so a genuine hang
#: is bounded by the systemd units, not by this arithmetic.
_WITNESS_TIMEOUT_S = 10.0


def latest_market_session(
    fetch: Callable[[], Mapping[str, Any]] | None = None,
) -> dt.date | None:
    """The newest completed US equity session, from a source that is not Cboe.

    Exists because Cboe cannot testify against itself: when its feed freezes,
    every chain agrees on the stale session and nothing inside the sweep can
    tell. Returns ``None`` -- logged, never raised -- when the reference
    cannot be read: a failed cross-check must not cost the sweep it guards.
    A transient fault -- e.g. the resume-time DNS failure measured
    2026-09-25 05:01 UTC, which made the previous single-shot fetch give up
    and silently skip the frozen-feed check -- is retried because this function asks
    ``fetch_nasdaq_raw`` for ``_FETCH_ATTEMPTS`` per-request attempts (its
    default is 1), so it does not retry a second time on top.
    """
    try:
        raw = (
            fetch()
            if fetch is not None
            else fetch_nasdaq_raw(
                _MARKET_REFERENCE_SYMBOL,
                years=1,
                timeout=_WITNESS_TIMEOUT_S,
                attempts=_FETCH_ATTEMPTS,
            )
        )
        bars = parse_nasdaq_historical(_MARKET_REFERENCE_SYMBOL, dict(raw))
        newest = pd.to_datetime(bars["trade_date"]).max()
    except Exception:
        _LOGGER.exception("event=ingest.market_session_unavailable")
        return None
    if pd.isna(newest):
        _LOGGER.warning("event=ingest.market_session_unavailable reason=no_bars")
        return None
    session: dt.date = newest.date()
    return session
