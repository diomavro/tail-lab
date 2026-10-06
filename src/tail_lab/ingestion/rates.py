"""FRED rates ingestion adapter -- bronze layer (`docs/DATA_CONTRACTS.md` #3).

Source: FRED's ``https://api.stlouisfed.org/fred`` API -- free but keyed,
unlike every other adapter in this package. The key is the ``FRED_API_KEY``
repo secret / local ``.env`` entry, read through
:class:`tail_lab.config.Settings` (never ``os.environ`` directly).
Ingests the Treasury curve (``DGS1MO``...``DGS30``), SOFR and the fed
funds effective rate (``DFF``).

The point-in-time hazard: FRED series *can* be revised after first
publication, so pulling only the latest value is a look-ahead bug, not a
simplification. The adapter stores one row per ``(series, obs_date,
vintage)`` FRED has ever published, each carrying its own ``vintage_date``.

**How the vintage history is fetched** (`docs/PRIOR_ART.md` §19). FRED caps
a request at 2,000 vintage dates, and the Treasury series have ~5,100 (one
per business day since 2005), so asking for the whole realtime window is an
HTTP 400. Chunking the *realtime window* is the obvious fix and is wrong: it
clips ``realtime_start`` to the chunk boundary, fabricating revisions. So:

1. enumerate every vintage via ``series/vintagedates`` (paged by offset);
2. take the FIRST vintage whole with one ``output_type=1`` request at
   ``realtime_start = realtime_end =`` that vintage: its ``realtime_start``
   is exact, not clipped, because no vintage precedes it. (``output_type=3``
   on the first vintage is FRED's slow path: uncached it ran ~60 s and hit
   the gateway's 504 for DFF even over five years.) Request every later
   vintage in batches of at most :data:`MAX_VINTAGES_PER_REQUEST` via
   ``series/observations?vintage_dates=...&output_type=3`` -- "new and
   revised observations only", a WIDE frame whose column names carry the
   vintage (``DGS10_20250214``), so clipping is structurally impossible --
   and melt it wide-to-long. Batches over ~600 make Apache (not FRED) return
   an HTML 400. Each batch bounds the window to
   ``[batch[0] - REVISION_LOOKBACK_DAYS, batch[-1]]``: FRED's gateway times
   out at 60 s, and an uncached unbounded 400-vintage request took 35 s
   (DGS3MO), so every request is kept small;
3. reconcile: the history's latest value per ``obs_date`` must equal FRED's
   own snapshot at the newest enumerated vintage. A mismatch means some
   vintage touched an observation outside its batch's window (measured: FRED
   backfilled DGS3MO's 1981-09..12 observations in a later vintage than the
   first; DFF's 1954-1973 observations were re-rounded to two decimals). The
   vintages that touched the mismatched dates are discovered with
   ``output_type=1`` over <=2,000-vintage realtime windows (used only to
   pick vintages -- its rows are never stored, see
   :func:`discover_revision_vintages`), and those vintages -- plus harmless
   extras: a realtime window's clipped first vintage, a vintage whose cell
   was ``"."`` -- are re-fetched via ``output_type=3``
   over windows narrowed to the mismatched dates; anything still
   mismatched raises.

What reconciliation cannot see, stated rather than hidden: it compares only
the FINAL value per date, so a revision outside a batch's window that was
later reverted (A -> B -> A) is lost -- unless the date happens to fall
inside a window the repair re-fetches. And a FRED ``"."`` (holiday / no print)
is null, never 0.0, and is not a row -- the contract's ``value`` is
non-nullable -- so a vintage that *withdraws* a value (number -> ``"."``) is
not represented: if the withdrawal is the final state reconciliation refuses
the series; if the value was later restored, the gap is silently absent.

Three-function seam as everywhere else, so tests never touch the network:
pure parsers (:func:`parse_vintage_dates_page`,
:func:`parse_fred_vintage_observations`, :func:`parse_fred_observations`),
the HTTP call behind a :data:`FredGet` (:func:`fred_getter`, used only by the
human-run ``make ingest-rates``), and :func:`ingest_rates`, which orchestrates
fetch -> parse -> validate/quarantine -> commit across the whole family into
one long-format ``rates`` dataset keyed by ``(series_id, obs_date,
vintage_date)``.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

import pandas as pd
import requests
from pandera.errors import SchemaErrors

from tail_lab.contracts.rates import DATASET, DEFAULT_SERIES_IDS, RatesSchema
from tail_lab.ingestion.json_payload import json_list, json_object, to_int
from tail_lab.ingestion.sources import retry_transient
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

__all__ = [
    "DATASET",
    "MAX_VINTAGES_PER_REQUEST",
    "QUARANTINE_DATASET",
    "REVISION_LOOKBACK_DAYS",
    "FredGet",
    "IngestResult",
    "VintageBatch",
    "discover_revision_vintages",
    "enumerate_vintage_dates",
    "fetch_first_vintage",
    "fetch_series_history",
    "fred_getter",
    "ingest_rates",
    "parse_fred_observations",
    "parse_fred_vintage_observations",
    "parse_realtime_periods",
    "parse_vintage_dates_page",
    "plan_vintage_batches",
    "unreconciled_obs_dates",
    "validate_and_quarantine",
]

_LOGGER = logging.getLogger(__name__)

FRED_API_URL = "https://api.stlouisfed.org/fred"
QUARANTINE_DATASET = f"{DATASET}__quarantine"

#: Vintages per ``series/observations`` request. FRED's documented cap is
#: 2,000, but past ~600 the URL is long enough that Apache answers with an
#: HTML 400 before FRED sees it (`docs/PRIOR_ART.md` §19).
MAX_VINTAGES_PER_REQUEST = 400

#: ``series/vintagedates`` page size -- FRED's maximum ``limit``.
VINTAGE_PAGE_LIMIT = 10_000

#: How far before a batch's first vintage its observation window reaches.
#: An older revision is caught by reconciliation only if it changed the
#: FINAL value; one later reverted (A -> B -> A) is invisible -- the price of
#: bounded windows, which FRED forces.
REVISION_LOOKBACK_DAYS = 400

#: Widest observation window a repair request takes, however few its
#: vintages. A latency precaution, not a measured limit: uncached requests
#: have run 35-60 s against FRED's 60 s gateway timeout.
MAX_WINDOW_DAYS = 5 * 365

#: FRED's documented cap on vintage dates inside one realtime window.
ALFRED_MAX_VINTAGES = 2000

#: Vintages x observation-days one repair request may span -- the shape the
#: live repair ran in ~5 s (400 vintages x 400 days). A latency precaution
#: like :data:`MAX_WINDOW_DAYS`, not a measured limit.
REPAIR_BUDGET_VINTAGE_DAYS = MAX_VINTAGES_PER_REQUEST * REVISION_LOOKBACK_DAYS

_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_S = 5.0

#: FRED's missing-value sentinel -- a holiday or no print, not a zero.
_MISSING_VALUE = "."

#: ``(endpoint, params) -> parsed JSON``; the api key and ``file_type`` are
#: the getter's business, so tests can inject a fake FRED with no key.
FredGet = Callable[[str, Mapping[str, str]], object]


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one ingestion run: what was committed vs. quarantined."""

    bronze_path: str
    valid_rows: int
    quarantined_rows: int
    quarantine_path: str | None
    series_ids: tuple[str, ...]


@dataclass(frozen=True)
class VintageBatch:
    """One ``output_type=3`` request: which vintages, which obs window."""

    vintage_dates: tuple[dt.date, ...]
    observation_start: dt.date
    observation_end: dt.date


def fred_getter(
    api_key: str, *, timeout: float = 120.0, retry_event: str = "ingest.rates.retry"
) -> FredGet:
    """The live HTTP seam, with the API key redacted from every error.
    Network -- used by ``make ingest-rates`` and ``make ingest-credit``."""

    def call(endpoint: str, params: Mapping[str, str]) -> requests.Response:
        resp = requests.get(
            f"{FRED_API_URL}/{endpoint}",
            params={**params, "api_key": api_key, "file_type": "json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp

    def get(endpoint: str, params: Mapping[str, str]) -> object:
        redacted: requests.exceptions.RequestException | None = None
        try:
            resp = retry_transient(
                lambda: call(endpoint, params),
                attempts=_FETCH_ATTEMPTS,
                backoff_s=_FETCH_BACKOFF_S,
                event=retry_event,
                endpoint=endpoint,
                series_id=params.get("series_id", ""),
            )
        except requests.exceptions.RequestException as exc:
            # requests carries the full URL -- api_key included -- in the
            # message, on .request/.response, and the original would ride
            # along as __context__. Re-raise the same type with the key
            # redacted, FRED's own error text kept, and nothing attached;
            # raised outside the handler so there is no context to leak.
            body = exc.response.text[:300] if exc.response is not None else ""
            redacted = type(exc)(f"{exc} {body}".strip().replace(api_key, "<redacted>"))
        if redacted is not None:
            raise redacted
        # A 200 with no body is named rather than surfacing as a JSON
        # decode error.
        if not resp.content:
            raise ValueError(f"FRED returned an empty 200 body for {endpoint} {dict(params)}")
        payload: object = resp.json()
        return payload

    return get


# ---------------------------------------------------------------- parsers


def parse_vintage_dates_page(raw: object) -> tuple[list[dt.date], int]:
    """One ``series/vintagedates`` page -> (its dates, FRED's total count)."""
    payload = json_object(raw, "FRED vintagedates payload")
    dates = json_list(payload.get("vintage_dates"), "vintage_dates")
    count = to_int(payload.get("count"), "vintagedates count")
    return [dt.date.fromisoformat(str(d)) for d in dates], count


def parse_fred_vintage_observations(series_id: str, raw: object) -> pd.DataFrame:
    """Melt one ``output_type=3`` (wide, vintage-in-column-name) payload into
    ``(series_id, obs_date, value, vintage_date)`` rows.

    A cell absent from an observation means "not new or revised in that
    vintage" and yields no row. A ``"."`` cell is FRED's missing value: null,
    never 0.0, and dropped as "not a row". Anything else malformed -- a bad
    date, a non-numeric value, an unparseable vintage suffix -- survives as
    NaT/NaN for :func:`validate_and_quarantine`. A column for another series
    or a possibly truncated payload (a full ``limit``-sized page) raises.
    """
    payload = json_object(raw, f"FRED payload for {series_id}")
    observations = json_list(payload.get("observations") or [], "observations")
    # FRED's ``count`` is NOT a truncation signal for output_type=3: it counts
    # every obs date in the window, while ``observations`` may omit dates no
    # requested vintage touched (measured: count=88, zero rows). Truncation
    # is only possible when the page is full.
    if "limit" in payload and len(observations) >= to_int(payload["limit"], "observations limit"):
        raise ValueError(f"FRED truncated {series_id}: a full page of {len(observations)} rows")
    if not observations:
        return _empty_frame()

    wide = pd.DataFrame(observations)
    columns = [c for c in wide.columns if c != "date"]
    prefix = f"{series_id}_"
    foreign = [c for c in columns if not str(c).startswith(prefix)]
    if foreign:
        raise ValueError(f"FRED payload for {series_id} carries foreign columns {foreign[:3]}")
    if not columns:
        return _empty_frame()

    long = wide.melt(id_vars="date", var_name="column", value_name="raw")
    long = long.loc[long["raw"].notna() & (long["raw"] != _MISSING_VALUE)]
    df = pd.DataFrame(
        {
            "series_id": series_id,
            "obs_date": pd.to_datetime(long["date"], errors="coerce"),
            "value": pd.to_numeric(long["raw"], errors="coerce"),
            "vintage_date": pd.to_datetime(
                long["column"].str.removeprefix(prefix), format="%Y%m%d", errors="coerce"
            ),
        }
    )
    return _sorted(df)


def parse_fred_observations(series_id: str, raw: object) -> pd.DataFrame:
    """Parse a single-vintage (``output_type=1``) payload -- the snapshot
    reconciliation compares against -- into the same typed frame, with
    ``vintage_date`` from FRED's ``realtime_start``.

    ``"."`` rows are dropped as "not a row"; anything else malformed
    survives as NaT/NaN. An envelope of the wrong shape raises
    ``ValueError`` naming the field.
    """
    payload = json_object(raw, f"FRED payload for {series_id}")
    observations = json_list(payload.get("observations") or [], "observations")
    if not observations:
        return _empty_frame()

    frame = pd.DataFrame(observations)
    is_present = frame["value"] != _MISSING_VALUE
    df = pd.DataFrame(
        {
            "series_id": series_id,
            "obs_date": pd.to_datetime(frame["date"], errors="coerce"),
            "value": pd.to_numeric(frame["value"].where(is_present), errors="coerce"),
            "vintage_date": pd.to_datetime(frame["realtime_start"], errors="coerce"),
        }
    )
    return _sorted(df.loc[is_present])


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["obs_date", "vintage_date"]).reset_index(drop=True)


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


# ---------------------------------------------------------------- planning


def enumerate_vintage_dates(
    series_id: str, get: FredGet, *, page_limit: int = VINTAGE_PAGE_LIMIT
) -> list[dt.date]:
    """Every vintage FRED has for ``series_id``, paging by offset until the
    reported ``count`` is reached. A page that comes back empty early, or a
    list that is not strictly increasing, raises rather than under-report."""
    dates: list[dt.date] = []
    while True:
        params = {"series_id": series_id, "limit": str(page_limit), "offset": str(len(dates))}
        page, count = parse_vintage_dates_page(get("series/vintagedates", params))
        dates.extend(page)
        if len(dates) >= count:
            break
        if not page:
            raise ValueError(f"FRED vintagedates for {series_id} stalled at {len(dates)}/{count}")
    if len(dates) != count or any(a >= b for a, b in pairwise(dates)):
        raise ValueError(
            f"FRED vintagedates for {series_id} are inconsistent ({len(dates)}/{count})"
        )
    return dates


def _chunks(vintages: Sequence[dt.date], size: int) -> list[tuple[dt.date, ...]]:
    return [tuple(vintages[i : i + size]) for i in range(0, len(vintages), size)]


def plan_vintage_batches(
    vintages: Sequence[dt.date],
    *,
    max_per_request: int = MAX_VINTAGES_PER_REQUEST,
    lookback_days: int = REVISION_LOOKBACK_DAYS,
) -> list[VintageBatch]:
    """The main pass over every vintage AFTER the first (which
    :func:`fetch_first_vintage` takes whole): consecutive chunks of at most
    ``max_per_request``, each windowed ``[chunk[0] - lookback_days,
    chunk[-1]]``."""
    lookback = dt.timedelta(days=lookback_days)
    return [
        VintageBatch(chunk, chunk[0] - lookback, chunk[-1])
        for chunk in _chunks(vintages[1:], max_per_request)
    ]


def fetch_first_vintage(series_id: str, get: FredGet, first: dt.date) -> pd.DataFrame:
    """The series' first vintage, whole, from one single-vintage
    ``output_type=1`` request. Exact rather than clipped only because no
    vintage precedes ``first``. The stamp check is defensive: were an
    earlier vintage hidden, FRED would clip its rows to ``first`` (later than
    the truth -- conservative, not look-ahead) rather than trip it."""
    day = first.isoformat()
    frame = parse_fred_observations(
        series_id,
        get(
            "series/observations",
            {"series_id": series_id, "realtime_start": day, "realtime_end": day},
        ),
    )
    stamped = frame["vintage_date"].dropna()
    if not stamped.eq(pd.Timestamp(first)).all():
        raise ValueError(f"FRED stamped {series_id}'s first vintage {day} with other dates")
    return frame


def discover_revision_vintages(
    series_id: str,
    get: FredGet,
    vintages: Sequence[dt.date],
    history: pd.DataFrame,
    gaps: Sequence[pd.Timestamp],
) -> list[dt.date]:
    """The vintages that started a value period, for any date in the span
    of ``gaps``, which ``history`` does not already hold.

    Asks ``output_type=1`` over ``[min(gaps), max(gaps)]`` in realtime
    windows of at most :data:`ALFRED_MAX_VINTAGES` -- the very request shape
    that FABRICATES revisions if its rows are stored, because a value period
    running into a window comes back stamped with the window's first date.
    Here only ``(date, realtime_start)`` pairs are used, to choose which
    vintages to ask ``output_type=3`` about, and pairs the history already
    holds are dropped -- without that, every daily vintage's first print of
    some date in the span is a pair, and DGS30 asked for 941 vintages. A
    clipped pair names the window's first vintage, a real one, so the worst
    case is an extra vintage asked about (as is one whose cell was ``"."``,
    which holds no row), and ``output_type=3`` answers truthfully. The series' first vintage is never named: it was fetched
    whole, and ``output_type=3`` on it is FRED's 504-prone slow path."""
    wanted = {g.date() for g in gaps}
    have = set(zip(history["obs_date"].dt.date, history["vintage_date"].dt.date, strict=True))
    pairs: set[tuple[dt.date, dt.date]] = set()
    for window in _chunks(vintages, ALFRED_MAX_VINTAGES):
        params = {
            "series_id": series_id,
            "realtime_start": window[0].isoformat(),
            "realtime_end": window[-1].isoformat(),
            "observation_start": min(wanted).isoformat(),
            "observation_end": max(wanted).isoformat(),
        }
        pairs |= parse_realtime_periods(get("series/observations", params))
    unknown = {v for _, v in pairs} - set(vintages)
    if unknown:
        raise ValueError(
            f"FRED reported {series_id} periods at non-vintage dates {sorted(unknown)[:3]}"
        )
    return sorted({v for o, v in pairs if (o, v) not in have and v != vintages[0]})


def parse_realtime_periods(raw: object) -> set[tuple[dt.date, dt.date]]:
    """The ``(date, realtime_start)`` pairs of an ``output_type=1`` payload.
    A full ``limit``-sized page may be truncated and raises."""
    payload = json_object(raw, "FRED realtime payload")
    observations = json_list(payload.get("observations") or [], "observations")
    if "limit" in payload and len(observations) >= to_int(payload["limit"], "realtime limit"):
        raise ValueError(f"FRED truncated a realtime payload: a full page of {len(observations)}")
    pairs: set[tuple[dt.date, dt.date]] = set()
    for item in observations:
        row = json_object(item, "observation")
        obs = dt.date.fromisoformat(str(row.get("date")))
        pairs.add((obs, dt.date.fromisoformat(str(row.get("realtime_start")))))
    return pairs


def _repair_batches(
    vintages: Sequence[dt.date], gaps: Sequence[pd.Timestamp], max_per_request: int
) -> list[VintageBatch]:
    """``vintages`` (the discovered revision vintages), re-requested over
    each cluster of unreconciled dates. A cluster spans at most what
    :data:`REPAIR_BUDGET_VINTAGE_DAYS` allows for its vintage chunk, capped
    at :data:`MAX_WINDOW_DAYS`, so no repair request is slower than the
    main pass's."""
    batches: list[VintageBatch] = []
    for chunk in _chunks(vintages, max_per_request):
        span = min(MAX_WINDOW_DAYS, REPAIR_BUDGET_VINTAGE_DAYS // len(chunk))
        clusters: list[list[pd.Timestamp]] = []
        for stamp in sorted(gaps):
            if clusters and (stamp - clusters[-1][0]).days <= span:
                clusters[-1].append(stamp)
            else:
                clusters.append([stamp])
        batches += [VintageBatch(chunk, c[0].date(), c[-1].date()) for c in clusters]
    return batches


def _batch_params(series_id: str, batch: VintageBatch) -> dict[str, str]:
    return {
        "series_id": series_id,
        "output_type": "3",
        "vintage_dates": ",".join(d.isoformat() for d in batch.vintage_dates),
        "observation_start": batch.observation_start.isoformat(),
        "observation_end": batch.observation_end.isoformat(),
    }


# ---------------------------------------------------------------- reconcile


def unreconciled_obs_dates(history: pd.DataFrame, snapshot: pd.DataFrame) -> list[pd.Timestamp]:
    """``obs_date``s where the history's latest-vintage value disagrees with
    FRED's own snapshot: absent from the history, a different value, or
    present in the history but missing (``"."``) in the snapshot."""
    # A row with no parseable vintage cannot be ordered, so it is not
    # "latest"; it goes to quarantine and its date shows up as a gap here.
    latest = (
        history.dropna(subset=["obs_date", "value", "vintage_date"])
        .sort_values("vintage_date")
        .drop_duplicates(subset="obs_date", keep="last")
        .set_index("obs_date")["value"]
    )
    current = snapshot.dropna(subset=["obs_date"]).set_index("obs_date")["value"]
    joined = pd.concat([latest.rename("history"), current.rename("snapshot")], axis=1, sort=True)
    differs = joined["history"].ne(joined["snapshot"])
    return [pd.Timestamp(d) for d in joined.index[differs]]


# ---------------------------------------------------------------- orchestrate


def fetch_series_history(
    series_id: str, get: FredGet, *, max_per_request: int = MAX_VINTAGES_PER_REQUEST
) -> pd.DataFrame:
    """One series' full, reconciled vintage history as a long frame.

    Raises ``ValueError`` if, after the repair pass, the history still
    disagrees with FRED's snapshot at the newest vintage."""
    started = time.monotonic()
    vintages = enumerate_vintage_dates(series_id, get)
    if not vintages:
        return _empty_frame()

    def fetch(batches: Sequence[VintageBatch]) -> list[pd.DataFrame]:
        return [
            parse_fred_vintage_observations(
                series_id, get("series/observations", _batch_params(series_id, b))
            )
            for b in batches
        ]

    newest = vintages[-1].isoformat()
    snapshot = parse_fred_observations(
        series_id,
        get(
            "series/observations",
            {"series_id": series_id, "realtime_start": newest, "realtime_end": newest},
        ),
    )
    main = plan_vintage_batches(vintages, max_per_request=max_per_request)
    history = _dedupe([fetch_first_vintage(series_id, get, vintages[0]), *fetch(main)])
    gaps = unreconciled_obs_dates(history, snapshot)
    revisers: list[dt.date] = []
    if gaps:
        revisers = discover_revision_vintages(series_id, get, vintages, history, gaps)
    repair = _repair_batches(revisers, gaps, max_per_request)
    if repair:
        history = _dedupe([history, *fetch(repair)])
    remaining = unreconciled_obs_dates(history, snapshot)
    log_event(
        _LOGGER,
        "ingest.rates.series",
        series_id=series_id,
        vintages=len(vintages),
        first_vintage=vintages[0].isoformat(),
        last_vintage=newest,
        requests=-(-len(vintages) // VINTAGE_PAGE_LIMIT)
        + 2
        + len(main)
        + (-(-len(vintages) // ALFRED_MAX_VINTAGES) if gaps else 0)
        + len(repair),
        rows=len(history),
        revised_obs=int(history["obs_date"].duplicated().sum()),
        gaps_repaired=len(gaps) - len(remaining),
        revision_vintages=len(revisers),
        unreconciled=len(remaining),
        seconds=round(time.monotonic() - started, 1),
    )
    if remaining:
        raise ValueError(
            f"{series_id}: {len(remaining)} obs_date(s) still disagree with FRED's "
            f"{newest} snapshot after repair (first: {remaining[0].date()}); refusing "
            "to commit an incomplete vintage history"
        )
    return history


def _dedupe(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate, dropping only byte-identical rows (the same cell fetched
    twice). Two values for one ``(obs_date, vintage_date)`` both survive, and
    the contract's uniqueness check quarantines them."""
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return _empty_frame()
    return _sorted(pd.concat(non_empty, ignore_index=True).drop_duplicates())


def validate_and_quarantine(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into (valid, quarantined) against the rates contract.

    Bad rows are never silently dropped: they come back in the second
    frame so the caller can persist them for inspection.
    """
    try:
        valid = RatesSchema.validate(df, lazy=True)
        return valid, df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        valid = df.loc[~df.index.isin(bad_index)]
        valid = RatesSchema.validate(valid, lazy=True)
        return valid, quarantined


def ingest_rates(
    store: LakeStore,
    series_ids: Sequence[str] | None = None,
    *,
    ingest_date: dt.date | None = None,
    get: FredGet | None = None,
    api_key: str | None = None,
) -> IngestResult:
    """Fetch every series' vintage history, validate, and commit one bronze
    snapshot -- the whole family as a single immutable partition (a partial
    run must not half-overwrite the previous complete panel).

    ``get`` injects a FRED stand-in (tests); without it a live
    :func:`fred_getter` is built, and a missing ``api_key`` raises -- FRED's
    endpoint is keyed, unlike every other adapter in this package.
    """
    # UTC, not local: snapshot dates must be timezone-consistent with the
    # as-of clock the API/backtest read with (matches vix.py; docs/adr/0009).
    ingest_date = ingest_date or dt.datetime.now(dt.UTC).date()
    resolved = tuple(
        s.upper() for s in (series_ids if series_ids is not None else DEFAULT_SERIES_IDS)
    )
    if get is None:
        if not api_key:
            raise ValueError(
                "ingest_rates needs a FRED api_key for a live fetch "
                "(see Settings.fred_api_key / HUMAN_TODO.md)"
            )
        get = fred_getter(api_key)

    parsed = [fetch_series_history(series_id, get) for series_id in resolved]
    combined = pd.concat(parsed, ignore_index=True) if parsed else _empty_frame()
    valid, quarantined = validate_and_quarantine(combined)

    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    # Quarantined rows go through the store as an immutable bronze snapshot
    # under a sibling dataset name -- never a raw filesystem write.
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
        "ingest.rates",
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
