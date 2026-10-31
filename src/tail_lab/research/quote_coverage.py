"""Where the lake holds a real put quote: depth x tenor x time, by regime.

The Marks surface (``docs/adr/0021``'s desk word for the prices a book is
marked at) answers one question before any backtest is believed: *for this
name, at this depth out of the money, how many sessions of real quotes do we
have -- and in which regimes?* A strategy tested at 16% OOM on a week of
quotes is a model result wearing a market label.

Two quote sources feed it, and they never overlap in time:

- ``optionsdx_quotes_<sym>`` -- the 2010-2023 vendor archive, stored only for
  moneyness 0.60-1.02 and 0-120 days (``contracts/optionsdx.py``'s slice), so
  nothing deeper than 40% OOM was ever kept.
- ``option_chain_snapshot`` -- the forward sweep since 2026-08-26, one
  partition per session, moneyness 0.40-1.05.

A session counts for a cell when at least one put in that depth bucket and
tenor band carried a **usable** quote: a positive bid and a relative spread
within ``marks.MAX_RELATIVE_SPREAD`` -- the same bar a quote must clear to
mark a position. ``traded`` is the stricter "volume > 0" reading.

Point-in-time: every read is ``bronze_relation_as_of``, so a session ingested
after ``as_of`` is never counted, and the regime each session is filed under
comes from ``compute_regime_timeline`` at the same ``as_of`` (causal labels).
The aggregation runs inside DuckDB; only the per-day presence rows (a few
hundred thousand small ints for SPY) are materialised.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Literal, cast

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.ohlcv import dataset_id as ohlcv_dataset_id
from tail_lab.contracts.option_chain import DATASET as CHAIN_DATASET
from tail_lab.contracts.option_chain import DEFAULT_SNAPSHOT_SYMBOLS
from tail_lab.contracts.optionsdx import DATASET as OPTIONSDX_DATASET
from tail_lab.contracts.optionsdx import SYMBOLS as OPTIONSDX_SYMBOLS
from tail_lab.contracts.regime import REGIME_LABELS
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.marks import MAX_RELATIVE_SPREAD
from tail_lab.research.regimes.timeline import compute_regime_timeline

Source = Literal["optionsdx", "chain"]

#: Width of one depth bucket, in percentage points out of the money.
OOM_STEP = 2
#: Tenor bands, as (label, last day-to-expiry inclusive). 120 is the deepest
#: tenor optionsDX stored, so the chain is cut there too: like for like.
TENOR_BANDS: tuple[tuple[str, int], ...] = (("<=2w", 14), ("2-6w", 42), ("6-17w", 120))
#: How deep each source ever stored a put (``1 - min moneyness``, in %).
STORED_DEPTH_PCT: dict[Source, int] = {"optionsdx": 40, "chain": 60}

#: VIX options settle on futures, so ``strike / spot`` against the index is
#: not moneyness (``contracts/optionsdx.py``) and a depth grid would lie.
#: SPX was never ingested.
_DEPTH_MEANINGLESS = frozenset({"vix", "spx"})
_OPTIONSDX_NAMES = frozenset(s for s in OPTIONSDX_SYMBOLS if s not in _DEPTH_MEANINGLESS)

#: Every name the surface can be asked about, in a stable order.
COVERAGE_SYMBOLS: tuple[str, ...] = tuple(
    sorted(_OPTIONSDX_NAMES | {s.lower() for s in DEFAULT_SNAPSHOT_SYMBOLS})
)

#: One (month index, oom bucket, band index, usable sessions, traded sessions).
Cell = tuple[int, int, int, int, int]


class SourceCoverage(BaseModel):
    source: Source
    first: dt.date
    last: dt.date
    sessions: int
    stored_depth_pct: int
    #: ``YYYY-MM`` of every month with at least one session, ascending.
    months: list[str]
    month_sessions: list[int]
    #: Sparse: only cells with at least one usable or traded session.
    cells: list[Cell]


class DepthRow(BaseModel):
    oom: int
    band: str
    usable_sessions: int
    traded_sessions: int
    usable_by_regime: dict[str, int]


class QuoteCoverage(BaseModel):
    symbol: str
    as_of: dt.date
    oom_step: int
    bands: list[str]
    max_relative_spread: float
    sources: list[SourceCoverage]
    #: Both sources pooled (they never overlap in time).
    depth: list[DepthRow]
    #: Quote sessions per regime, both sources pooled.
    regime_sessions: dict[str, int]
    #: First/last OHLCV close: a roll backtest needs a price AND a quote.
    price_span: tuple[dt.date, dt.date] | None
    symbols: list[str]


def presence_sql(relation: str, symbol: str) -> str:
    """Per (session, depth bucket, tenor band): did any put carry a usable
    quote, and did any trade. ``symbol`` must come from
    :data:`COVERAGE_SYMBOLS` -- it is interpolated, never user text."""
    band_case = " ".join(f"WHEN dte <= {last} THEN {i}" for i, (_, last) in enumerate(TENOR_BANDS))
    return f"""
        WITH q AS (
            SELECT CAST(quote_date AS DATE) AS d,
                   date_diff('day', CAST(quote_date AS DATE), CAST(expiration AS DATE)) AS dte,
                   strike / spot AS m, bid, ask, volume
            FROM {relation}
            WHERE lower(underlying) = '{symbol}' AND spot > 0 AND strike <= spot
        )
        SELECT d,
               -- Rounded before the floor: 1 - 90/100 is 0.0999... in binary, and a
               -- strike exactly 10% out must not land in the 8% bucket.
               CAST(FLOOR(ROUND((1 - m) * 100, 6) / {OOM_STEP}) * {OOM_STEP} AS INTEGER) AS oom,
               CASE {band_case} END AS band,
               MAX(CASE WHEN bid > 0 AND ask > 0
                         AND (ask - bid) / ((ask + bid) / 2) <= {MAX_RELATIVE_SPREAD}
                        THEN 1 ELSE 0 END) AS usable,
               MAX(CASE WHEN volume > 0 THEN 1 ELSE 0 END) AS traded
        FROM q
        WHERE dte BETWEEN 1 AND {TENOR_BANDS[-1][1]}
        GROUP BY 1, 2, 3
    """


def summarise_source(
    source: Source, presence: pd.DataFrame, sessions: list[dt.date]
) -> SourceCoverage:
    """Fold per-day presence rows into the month x depth x band grid.

    Pure: ``presence`` has columns ``d, oom, band, usable, traded`` (one row
    per session/bucket/band), ``sessions`` every session the source holds.
    Raises ``ValueError`` on an empty ``sessions`` -- an absent source is the
    caller's to skip, not a zero-row grid to draw.
    """
    if not sessions:
        raise ValueError(f"{source}: no sessions")
    ordered = sorted(set(sessions))
    month_counts = Counter(d.strftime("%Y-%m") for d in ordered)
    months = sorted(month_counts)
    index = {m: i for i, m in enumerate(months)}

    cells: list[Cell] = []
    if not presence.empty:
        frame = presence.assign(month=pd.to_datetime(presence["d"]).dt.strftime("%Y-%m"))
        grouped = frame.groupby(["month", "oom", "band"], sort=True)[["usable", "traded"]].sum()
        for month, oom, band, usable, traded in grouped.reset_index().itertuples(
            index=False, name=None
        ):
            if usable or traded:
                cells.append((index[str(month)], int(oom), int(band), int(usable), int(traded)))

    return SourceCoverage(
        source=source,
        first=ordered[0],
        last=ordered[-1],
        sessions=len(ordered),
        stored_depth_pct=STORED_DEPTH_PCT[source],
        months=months,
        month_sessions=[month_counts[m] for m in months],
        cells=cells,
    )


def depth_rows(presence: pd.DataFrame, regimes: pd.Series) -> list[DepthRow]:
    """Usable/traded sessions per (depth, band), and usable sessions per
    regime. A session that predates the regime timeline is left out of the
    regime split (not mislabelled) but still counts in the totals."""
    if presence.empty:
        return []
    labels = regime_by_day(regimes, set(presence["d"]))
    rows: list[DepthRow] = []
    for key, group in presence.groupby(["oom", "band"], sort=True):
        oom, band = cast(tuple[int, int], key)
        usable = group.loc[group["usable"] > 0, "d"]
        by_regime = Counter(labels[d] for d in usable if labels[d] is not None)
        rows.append(
            DepthRow(
                oom=int(oom),
                band=TENOR_BANDS[int(band)][0],
                usable_sessions=len(usable),
                traded_sessions=int((group["traded"] > 0).sum()),
                usable_by_regime={r: by_regime.get(r, 0) for r in REGIME_LABELS},
            )
        )
    return rows


def regime_by_day(regimes: pd.Series, days: set[dt.date]) -> dict[dt.date, str | None]:
    """The regime in force on each day (``regime_on_or_before``'s rule,
    vectorised): the latest label on or before it, ``None`` before the
    timeline starts."""
    ordered = sorted(days)
    if regimes.empty:
        return dict.fromkeys(ordered)
    aligned = regimes.sort_index().reindex(pd.DatetimeIndex(ordered), method="ffill")
    return {d: (None if pd.isna(v) else str(v)) for d, v in zip(ordered, aligned, strict=True)}


def _read_source(
    store: LakeStore, source: Source, symbol: str, as_of: dt.date
) -> tuple[pd.DataFrame, list[dt.date]] | None:
    """The presence rows and sessions of one source, or ``None`` when the
    lake holds nothing for this name as of ``as_of``."""
    if source == "optionsdx":
        if symbol not in _OPTIONSDX_NAMES:
            return None
        dataset, history = f"{OPTIONSDX_DATASET}_{symbol}", False
    else:
        dataset, history = CHAIN_DATASET, True
    try:
        relation = store.bronze_relation_as_of(dataset, as_of, history=history)
    except LookupError:
        return None
    presence = store.query(presence_sql(relation, symbol))
    presence["d"] = [pd.Timestamp(d).date() for d in presence["d"]]
    # A partition never holds a session dated past its own ingest, but the
    # filter makes the as-of bound hold on the rows themselves too.
    presence = presence.loc[presence["d"] <= as_of]
    # A session is a day the source holds at least one OTM put inside the
    # tenor window -- one scan, and a day without one has nothing to count.
    sessions = sorted(set(presence["d"]))
    if not sessions:
        return None
    return presence, sessions


def _price_span(store: LakeStore, symbol: str, as_of: dt.date) -> tuple[dt.date, dt.date] | None:
    try:
        dates = store.read_bronze_column_as_of(ohlcv_dataset_id(symbol), as_of, "trade_date")
    except (LookupError, KeyError):
        return None
    if dates.empty:
        return None
    stamps = pd.to_datetime(dates)
    return stamps.min().date(), stamps.max().date()


def compute_quote_coverage(store: LakeStore, *, symbol: str, as_of: dt.date) -> QuoteCoverage:
    """The Marks grid for ``symbol`` as of ``as_of``. Raises ``ValueError``
    for a name outside :data:`COVERAGE_SYMBOLS` and ``LookupError`` when
    neither source holds a quote for it."""
    symbol = symbol.lower()
    if symbol not in COVERAGE_SYMBOLS:
        raise ValueError(f"no quote coverage for {symbol!r}")

    sources: list[SourceCoverage] = []
    presences: list[pd.DataFrame] = []
    all_sessions: set[dt.date] = set()
    for source in ("optionsdx", "chain"):
        read = _read_source(store, source, symbol, as_of)
        if read is None:
            continue
        presence, sessions = read
        sources.append(summarise_source(source, presence, sessions))
        presences.append(presence)
        all_sessions.update(sessions)
    if not sources:
        raise LookupError(f"no put quotes for {symbol} as of {as_of.isoformat()}")

    try:
        regimes = compute_regime_timeline(store, as_of=as_of)
    except LookupError:
        regimes = pd.Series([], dtype=object, index=pd.DatetimeIndex([]))
    pooled = pd.concat(presences, ignore_index=True)
    session_regimes = Counter(regime_by_day(regimes, all_sessions).values())

    return QuoteCoverage(
        symbol=symbol,
        as_of=as_of,
        oom_step=OOM_STEP,
        bands=[label for label, _ in TENOR_BANDS],
        max_relative_spread=MAX_RELATIVE_SPREAD,
        sources=sources,
        depth=depth_rows(pooled, regimes),
        regime_sessions={r: session_regimes.get(r, 0) for r in REGIME_LABELS},
        price_span=_price_span(store, symbol, as_of),
        symbols=list(COVERAGE_SYMBOLS),
    )
