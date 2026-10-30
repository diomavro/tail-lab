"""Contract for the **forward-collected** Deribit option books
(``docs/DATA_CONTRACTS.md`` #16, ``docs/adr/0032``).

This module is a LEAF: it must never import anything else from ``tail_lab``
(enforced by the import-linter layers contract in ``pyproject.toml``).

**Why collect a book nothing reads yet.** A past order-book snapshot cannot be
had for free -- Tardis.dev sells them -- so, exactly as with the Cboe chain
(``docs/adr/0020``), a day that goes un-snapshotted is a day that can only be
bought back. The collection is cheap; the missing history is not.

**Three books, three datasets.** Deribit lists options in three books: the
inverse BTC and ETH books, premiums in coin, and the linear USDC book
(SOL, HYPE, BTC, ETH, XRP, AVAX, TRX), premiums in USDC per base coin. Each
lands in its own dataset (:data:`DERIBIT_BOOKS`) through its own write, so
one book failing never costs another book its session -- ``docs/adr/0031``'s
lesson. Every row carries ``right``; the columns differ from the equity put
datasets and the schema is ``strict``, so the two can never be unioned by
accident.

**Point-in-time.** Partition D is the UTC date of the capture's earliest
``observed_at``, and the capture can land anywhere from 08:05 to 23:59:59 UTC
(the cron runs up to 12.4 h late, plus retries and manual dispatch); crypto
has no close. A reader deciding before that time must read ``as_of = D - 1``
or filter on ``observed_at``. The date-level as-of read does NOT enforce this:
the first reader adds a test in the style of ``test_point_in_time_clock.py``
(``AGENT_TODO.md``).

**Two writers, one rulebook.** The scheduled path POSTs to
``api/deribit_routes`` and the human path (``--local``) writes directly; both
build the frame with :func:`deribit_frame`, decide with
:func:`plan_deribit_write`, and persist bad rows through
:func:`quarantine_document`. Two writers disagreeing about what a bad row
costs is how a session gets lost (``contracts/option_chain``).

``mark`` is Deribit's model value and **never a market price** -- the same
house rule as Cboe's ``theo``. A null ``bid`` means no resting bid, not zero.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Final, NamedTuple, TypedDict, cast

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors
from pandera.typing import Series

__all__ = [
    "DERIBIT_BOOKS",
    "REFUSAL_MARKER",
    "DeribitApiError",
    "DeribitBook",
    "DeribitBookRow",
    "DeribitInstrumentRow",
    "DeribitOptionsSchema",
    "DeribitRecord",
    "DeribitRefusal",
    "DeribitWritePlan",
    "QuarantineParts",
    "check_book_rows",
    "check_instrument_rows",
    "deribit_frame",
    "name_expiry",
    "plan_deribit_write",
    "prepare_quarantine",
    "quarantine_document",
    "quarantine_key",
    "quarantine_log_records",
    "serialize_quarantine",
]


class DeribitBook(NamedTuple):
    """One Deribit book: the uppercase currency Deribit names it by (the
    ``currency`` query parameter and every row's ``quote_currency``) and the
    bronze dataset it lands in."""

    currency: str
    dataset: str


#: The ONLY place the datasets are named. Keyed by Deribit's uppercase
#: currency; the route's path segment and the status keys are its lowercase.
DERIBIT_BOOKS: Final[Mapping[str, DeribitBook]] = {
    "BTC": DeribitBook("BTC", "deribit_options_btc"),
    "ETH": DeribitBook("ETH", "deribit_options_eth"),
    "USDC": DeribitBook("USDC", "deribit_options_usdc"),
}

#: The half-open window every millisecond timestamp must fall in,
#: [2016-01-01, 2100-01-01). Deribit opened in 2016; anything outside is a
#: garbled value, and masking the integer BEFORE conversion is what keeps it
#: from wrapping into year 3,170,843 (pandas 3's ``to_datetime(unit="ms")``
#: returns ``datetime64[ms]`` and never coerces).
MS_MIN: Final = 1_451_606_400_000
MS_MAX: Final = 4_102_444_800_000
_INT64_MIN: Final = -(2**63)
_INT64_MAX: Final = 2**63 - 1
#: Largest integer magnitude accepted in a float field: past it ``float()``
#: overflows (``10**400``).
_FLOAT_INT_LIMIT: Final = 2**1023

#: Every Deribit expiry settles at 08:00 UTC; nothing is captured before five
#: minutes past, so the day's expiring names are already gone.
EXPIRY_TIME: Final = dt.time(8, 0)
EARLIEST_CAPTURE: Final = dt.time(8, 5)
#: Two-sided freshness. 30 minutes covers the worst retry path (about 15.4
#: min from the book GET to the last POST); 5 minutes of future covers clock
#: skew between Deribit and the app.
MAX_AGE: Final = dt.timedelta(minutes=30)
MAX_FUTURE: Final = dt.timedelta(minutes=5)
#: Above this share of the posted rows carrying another book's currency, the
#: body is for the wrong book. Not 0.01: a guard that refuses a session over
#: two glitched rows is the failure ``contracts/option_chain`` measured.
MAX_WRONG_BOOK_SHARE: Final = 0.5
#: Above this share of the live listed names with no row, the capture is
#: incomplete and refused.
MAX_MISSING_LISTED_SHARE: Final = 0.01
#: Above this quarantined share the book still commits, but the script exits 1.
MAX_QUARANTINE_SHARE: Final = 0.01
EARLIEST_EXPIRY: Final = pd.Timestamp("2016-01-01")

_RIGHTS: Final[Mapping[str, str]] = {"put": "P", "call": "C"}


class DeribitApiError(ValueError):
    """Deribit answered, but not with a usable book: a JSON-RPC ``error``, an
    empty ``result``, or a structural fault in the payload. Defined here, not
    in ingestion, because :func:`check_book_rows` raises it and a contract may
    not import ingestion. Costs that one book, never the run."""


#: Every refusal detail the route answers starts with this, so the client can
#: tell the app's own deterministic 500s ("not attributable to a row", "not
#: serialisable") -- which wrote nothing and would refuse again -- from a
#: transient fault worth retrying.
REFUSAL_MARKER: Final = "deribit refusal: "


class DeribitRefusal(Exception):
    """Every refusal :func:`plan_deribit_write` (and
    :func:`serialize_quarantine`) can make, as ONE exception carrying the HTTP
    status the route answers with -- so the route catches it once and stays
    under the complexity ratchet, and ``--local`` maps it to a per-book
    failure. No refusal writes anything."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---- the payload shape check ------------------------------------------------


class DeribitBookRow(TypedDict):
    """One ``get_book_summary_by_currency`` row after :func:`check_book_rows`:
    every field present, each value of its allowed type or ``None``, and the
    raw value of each field that was not in ``invalid`` (source name -> raw)."""

    instrument_name: str
    creation_timestamp: int
    base_currency: str | None
    quote_currency: str | None
    underlying_index: str | None
    bid_price: float | None
    ask_price: float | None
    mark_price: float | None
    mark_iv: float | None
    underlying_price: float | None
    estimated_delivery_price: float | None
    interest_rate: float | None
    open_interest: float | None
    volume: float | None
    volume_usd: float | None
    invalid: dict[str, object]


class DeribitInstrumentRow(TypedDict):
    """One ``get_instruments`` row after :func:`check_instrument_rows`."""

    instrument_name: str
    expiration_timestamp: int | None
    strike: float | None
    counter_currency: str | None
    option_type: str | None
    contract_size: float | None
    tick_size: float | None
    min_trade_amount: float | None
    invalid: dict[str, object]


#: What a field may hold: a string, a bounded integer timestamp, or a finite
#: number. ``bool`` is excluded everywhere (it is an ``int`` in Python).
_STR, _MS, _NUM = "str", "ms", "num"

_BOOK_SPEC: Final[Mapping[str, str]] = {
    "base_currency": _STR,
    "quote_currency": _STR,
    "underlying_index": _STR,
    "bid_price": _NUM,
    "ask_price": _NUM,
    "mark_price": _NUM,
    "mark_iv": _NUM,
    "underlying_price": _NUM,
    "estimated_delivery_price": _NUM,
    "interest_rate": _NUM,
    "open_interest": _NUM,
    "volume": _NUM,
    "volume_usd": _NUM,
}
_INSTRUMENT_SPEC: Final[Mapping[str, str]] = {
    "expiration_timestamp": _MS,
    "strike": _NUM,
    "counter_currency": _STR,
    "option_type": _STR,
    "contract_size": _NUM,
    "tick_size": _NUM,
    "min_trade_amount": _NUM,
}


def _is_int64(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and _INT64_MIN <= value <= _INT64_MAX
    )


def _clean(value: object, kind: str) -> tuple[object, bool]:
    """``(value, ok)``: the value narrowed to its kind, or ``(None, False)``.
    ``None`` itself is a real absence and is ok."""
    if value is None:
        return None, True
    if kind == _STR:
        return (value, True) if isinstance(value, str) else (None, False)
    if kind == _MS:
        return (value, True) if _is_int64(value) else (None, False)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None, False
    if isinstance(value, int):
        return (float(value), True) if abs(value) < _FLOAT_INT_LIMIT else (None, False)
    return (value, True) if math.isfinite(value) else (None, False)


def _checked_fields(row: Mapping[str, object], spec: Mapping[str, str]) -> dict[str, object]:
    """Every ``spec`` field read with ``.get``, cleaned, and an ``invalid``
    map of the raw value of each field that failed its kind."""
    out: dict[str, object] = {}
    invalid: dict[str, object] = {}
    for field, kind in spec.items():
        raw = row.get(field)
        value, ok = _clean(raw, kind)
        out[field] = value
        if not ok:
            invalid[field] = raw
    out["invalid"] = invalid
    return out


def _payload_list(payload: object, what: str) -> list[object]:
    if not isinstance(payload, list):
        raise DeribitApiError(f"{what} is not a JSON array (got {type(payload).__name__})")
    return payload


def check_payload_rows(
    payload: object, spec: Mapping[str, str], *, what: str, book: bool
) -> tuple[list[dict[str, object]], int]:
    """The ONE shape check, over both payloads: ``(rows, dropped)``.

    Structural faults cost the whole book (:class:`DeribitApiError`): a
    payload that is not a list, and -- for the book only -- a row that is not
    an object, or has no string ``instrument_name``, or no integer
    ``creation_timestamp`` within int64 (rule 1 cannot date the capture
    without it). An instruments row that is not an object or has no string
    name is dropped and counted instead.

    Every other field is a FIELD fault: set to ``None`` and recorded in the
    row's ``invalid`` map, which ``to_records`` turns into ``invalid_fields``
    and the plan into an ``invalid_value`` quarantine. A recurring Deribit
    quirk in one field must cost a row, not the session on the catch-up as
    well (ADR 0020 §5b).
    """
    rows: list[dict[str, object]] = []
    dropped = 0
    for index, raw in enumerate(_payload_list(payload, what)):
        name = raw.get("instrument_name") if isinstance(raw, dict) else None
        if not isinstance(name, str) or not isinstance(raw, dict):
            if book:
                raise DeribitApiError(f"{what}[{index}] has no string instrument_name")
            dropped += 1
            continue
        row = _checked_fields(raw, spec)
        row["instrument_name"] = name
        if book:
            created = raw.get("creation_timestamp")
            if not _is_int64(created):
                raise DeribitApiError(
                    f"{what}[{index}] creation_timestamp is not an int64 ({created!r:.40})"
                )
            row["creation_timestamp"] = created
        rows.append(row)
    return rows, dropped


def check_book_rows(payload: object) -> list[DeribitBookRow]:
    """The book payload, shape-checked (:func:`check_payload_rows`)."""
    rows, _ = check_payload_rows(payload, _BOOK_SPEC, what="book", book=True)
    return cast("list[DeribitBookRow]", rows)


def check_instrument_rows(payload: object) -> tuple[list[DeribitInstrumentRow], int]:
    """The instruments payload, shape-checked, and how many rows were dropped."""
    rows, dropped = check_payload_rows(payload, _INSTRUMENT_SPEC, what="instruments", book=False)
    return cast("list[DeribitInstrumentRow]", rows), dropped


def right_from_option_type(value: str | None) -> tuple[str | None, bool]:
    """``("P"|"C", True)``, ``(None, True)`` for a real absence, or
    ``(None, False)`` for a value outside ``{"put", "call"}`` -- a field
    fault, never a silent guess (``"PUT"`` is not ``"put"``)."""
    if value is None:
        return None, True
    right = _RIGHTS.get(value)
    return right, right is not None


# ---- the wire shape ---------------------------------------------------------


class DeribitRecord(TypedDict):
    """One row on the wire, as ``to_records`` emits it and the route's
    ``DeribitOptionRow`` accepts it. A test pins the two to the same names
    and types. Timestamps travel as raw integer milliseconds; ``mark_iv_pct``
    is Deribit's percent, divided once, in :func:`deribit_frame`.
    ``invalid_fields`` names each field fault as ``"<wire field>=<repr>"``."""

    instrument_name: str
    creation_timestamp_ms: int
    expiration_timestamp_ms: int | None
    underlying: str | None
    premium_currency: str | None
    strike: float | None
    strike_currency: str | None
    right: str | None
    contract_size: float | None
    tick_size: float | None
    min_trade_amount: float | None
    bid: float | None
    ask: float | None
    mark: float | None
    mark_iv_pct: float | None
    underlying_price: float | None
    underlying_index: str | None
    index_price: float | None
    interest_rate: float | None
    open_interest: float | None
    volume: float | None
    volume_usd: float | None
    invalid_fields: list[str] | None


# ---- the stored schema ------------------------------------------------------


class DeribitOptionsSchema(pa.DataFrameModel):
    """One Deribit option, as observed at ``observed_at`` (naive, UTC wall
    time -- the repo convention). Keyed by ``(snapshot_date,
    instrument_name)``. ``premium_currency`` must equal the book's currency,
    but this schema is shared by all three books and cannot know which one it
    is checking: :func:`plan_deribit_write` enforces it."""

    snapshot_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    observed_at: Series[pa.Timestamp] = pa.Field(nullable=False)
    instrument_name: Series[str] = pa.Field(nullable=False)
    underlying: Series[str] = pa.Field(nullable=False)
    premium_currency: Series[str] = pa.Field(nullable=False)
    expiration: Series[pa.Timestamp] = pa.Field(nullable=False, ge=EARLIEST_EXPIRY)
    strike: Series[float] = pa.Field(nullable=False, gt=0.0)
    strike_currency: Series[str] = pa.Field(nullable=False)
    right: Series[str] = pa.Field(nullable=False, isin=["P", "C"])
    contract_size: Series[float] = pa.Field(nullable=False, gt=0.0)
    tick_size: Series[float] = pa.Field(nullable=False, gt=0.0)
    min_trade_amount: Series[float] = pa.Field(nullable=False, gt=0.0)
    bid: Series[float] = pa.Field(nullable=True, ge=0.0)
    ask: Series[float] = pa.Field(nullable=True, gt=0.0)
    mark: Series[float] = pa.Field(nullable=False, ge=0.0)
    mark_iv: Series[float] = pa.Field(nullable=False, gt=0.0, le=10.0)
    underlying_price: Series[float] = pa.Field(nullable=False, gt=0.0)
    underlying_index: Series[str] = pa.Field(nullable=False)
    index_price: Series[float] = pa.Field(nullable=False, gt=0.0)
    interest_rate: Series[float] = pa.Field(nullable=False)
    open_interest: Series[float] = pa.Field(nullable=False, ge=0.0)
    volume: Series[float] = pa.Field(nullable=False, ge=0.0)
    volume_usd: Series[float] = pa.Field(nullable=False, ge=0.0)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["snapshot_date", "instrument_name"]


#: The stored columns, in schema order -- also the precedence order for a
#: row's pandera ``reason``.
STORED_COLUMNS: Final[tuple[str, ...]] = tuple(DeribitOptionsSchema.to_schema().columns)

_STR_COLUMNS: Final = (
    "instrument_name",
    "underlying",
    "premium_currency",
    "strike_currency",
    "right",
    "underlying_index",
)
_FLOAT_COLUMNS: Final = (
    "strike",
    "contract_size",
    "tick_size",
    "min_trade_amount",
    "bid",
    "ask",
    "mark",
    "mark_iv_pct",
    "underlying_price",
    "index_price",
    "interest_rate",
    "open_interest",
    "volume",
    "volume_usd",
)
#: Working columns the plan reads and drops before ``schema.validate``.
_OUT_OF_WINDOW = "_expiry_out_of_window"
_INVALID = "invalid_fields"


def _ms_to_naive_utc(
    records: Sequence[Mapping[str, object]], field: str, index: pd.Index
) -> tuple[pd.Series[pd.Timestamp], pd.Series[bool]]:
    """``(timestamps, out_of_window)``: integer ms masked to
    [:data:`MS_MIN`, :data:`MS_MAX`) BEFORE conversion, so a garbled value is
    NaT (flagged) rather than a wrapped date or an ``OutOfBoundsDatetime``.

    Built straight from the records as nullable ``Int64``, never through
    ``pd.DataFrame(records)``: there, a column of ints plus one ``None`` goes
    float64, a value in [2**63 - 512, 2**63 - 1] rounds to 2**63, and the
    ``Int64`` cast then raises ``TypeError`` -- a 500 before the commit on
    the route, an uncaught crash of the whole run under ``--local``."""
    ms = pd.Series([r.get(field) for r in records], dtype="Int64", index=index)
    inside = (ms.ge(MS_MIN) & ms.lt(MS_MAX)).fillna(False).astype(bool)
    stamps = pd.to_datetime(ms.where(inside), unit="ms").astype("datetime64[ns]")
    return stamps, ms.notna() & ~inside


def deribit_frame(records: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    """The typed frame both writers plan on, keeping the request's positional
    index (0..n-1) so a quarantined row joins back to ``records[i]``.

    An explicit dtype for every float and string column, so an all-null
    column still has a type. ``mark_iv`` is divided by 100 HERE and nowhere
    else (a double division would still pass ``0 < x <= 10``)."""
    frame = pd.DataFrame(list(records), columns=list(DeribitRecord.__annotations__))
    frame = frame.astype({c: "float64" for c in _FLOAT_COLUMNS} | {c: "str" for c in _STR_COLUMNS})
    frame = frame.drop(columns=["creation_timestamp_ms", "expiration_timestamp_ms"])
    frame["observed_at"], _ = _ms_to_naive_utc(records, "creation_timestamp_ms", frame.index)
    frame["expiration"], frame[_OUT_OF_WINDOW] = _ms_to_naive_utc(
        records, "expiration_timestamp_ms", frame.index
    )
    frame["mark_iv"] = frame.pop("mark_iv_pct") / 100.0
    return frame


# ---- the write plan ---------------------------------------------------------


@dataclass(frozen=True)
class DeribitWritePlan:
    """What :func:`plan_deribit_write` decided. ``quarantined`` keeps the
    request's positional index and carries a ``reason`` column."""

    valid: pd.DataFrame
    quarantined: pd.DataFrame
    snapshot_date: dt.date
    already_captured: bool
    expired_dropped: int
    previous_partition_exists: bool | None
    #: The capture's earliest ``observed_at``, in ms: the quarantine key's leaf.
    observed_at_ms: int
    #: Rows left after the expiry drop -- the quarantine share's denominator.
    live_rows: int

    @property
    def quarantine_share(self) -> float | None:
        """``None`` on already-captured, where nothing was computed."""
        if self.already_captured or self.live_rows == 0:
            return None
        return len(self.quarantined) / self.live_rows


def name_expiry(name: str) -> pd.Timestamp | None:
    """The expiry a Deribit name spells (``BTC-1NOV26-...``) at 08:00 UTC, or
    ``None``. Total: a malformed name, or a date before 2016 (``1JAN69``
    parses as 1969), is ``None`` -- quarantined, never dropped as expired."""
    try:
        day = dt.datetime.strptime(name.split("-")[1], "%d%b%y")
    except (ValueError, IndexError):
        return None
    stamp = pd.Timestamp(dt.datetime.combine(day.date(), EXPIRY_TIME))
    return stamp if stamp >= EARLIEST_EXPIRY else None


def _naive_utc(now: dt.datetime) -> pd.Timestamp:
    if now.tzinfo is None or now.utcoffset() is None:
        raise TypeError("now must be timezone-aware (a naive local clock would date every run)")
    return pd.Timestamp(now.astimezone(dt.UTC).replace(tzinfo=None))


def _check_fresh(observed: pd.Series[pd.Timestamp], now: pd.Timestamp) -> None:
    """Rule 1, NaT first: ``min()`` skips NaT, and ``now - NaT`` compares
    False, so a NaT would otherwise pass both bounds silently."""
    if observed.isna().any():
        raise DeribitRefusal(409, f"bad timestamp: {int(observed.isna().sum())} rows have none")
    oldest, newest = observed.min(), observed.max()
    if now - oldest > MAX_AGE:
        raise DeribitRefusal(409, f"stale: observed {oldest} is more than {MAX_AGE} before {now}")
    if newest - now > MAX_FUTURE:
        raise DeribitRefusal(409, f"future: observed {newest} is after {now} + {MAX_FUTURE}")


def _check_book(currencies: pd.Series[str], currency: str) -> None:
    """Rule 1b: refuse when non-null mismatches exceed half of ALL posted rows."""
    mismatched = currencies.notna() & (currencies != currency)
    if int(mismatched.sum()) / len(currencies) > MAX_WRONG_BOOK_SHARE:
        others = sorted(set(currencies[mismatched]))
        raise DeribitRefusal(
            409,
            f"body is for book {','.join(others)}, not {currency}: "
            f"{int(mismatched.sum())} of {len(currencies)} rows mismatched, "
            f"{int(currencies.isna().sum())} with no currency",
        )


def _with_expiry(frame: pd.DataFrame) -> pd.DataFrame:
    """Rule 4's expiry: the instruments' when present, else the name's.

    Two cases stay NaT, and so are quarantined ``unparseable_expiry`` rather
    than ever dropped as expired: a non-null instrument expiry outside the
    window (no name fallback), and an instrument expiry that DISAGREES with
    the date the name spells. A 2017 expiry on a ``27NOV26`` name would
    otherwise drop a live row silently as expired."""
    named = pd.to_datetime(frame["instrument_name"].map(name_expiry).astype(object)).astype(
        "datetime64[ns]"
    )
    instrument = frame["expiration"]
    fallback = instrument.isna() & ~frame[_OUT_OF_WINDOW]
    disagrees = instrument.notna() & named.notna() & (instrument != named)
    out = frame.copy()
    out["expiration"] = instrument.where(~fallback, named).where(~disagrees)
    return out


def _plan_reasons(live: pd.DataFrame, currency: str) -> pd.Series[str]:
    """The plan's own reasons, highest precedence last-written:
    ``invalid_value`` > ``wrong_book`` > ``unparseable_expiry``."""
    reasons = pd.Series(pd.NA, index=live.index, dtype="object")
    reasons[live["expiration"].isna()] = "unparseable_expiry"
    reasons[live["premium_currency"].isna() | (live["premium_currency"] != currency)] = "wrong_book"
    reasons[live[_INVALID].map(lambda v: isinstance(v, list) and len(v) > 0)] = "invalid_value"
    return reasons.dropna()


def _pandera_reasons(stored: pd.DataFrame) -> pd.Series[str]:
    """``"<column>:<check>"`` per failing row: the first failing column check
    by schema order; frame-level failures (``unique``) rank last. A failure
    with no row index is not row-attributable and refuses with 500."""
    try:
        DeribitOptionsSchema.validate(stored, lazy=True)
    except SchemaErrors as err:
        cases = err.failure_cases
    else:
        return pd.Series(dtype="object")
    if cases["index"].isna().any():
        raise DeribitRefusal(500, f"schema failure not attributable to a row: {cases.to_dict()}")
    rank = {c: i for i, c in enumerate(STORED_COLUMNS)}
    cases = cases.assign(
        _rank=[
            rank.get(str(col), len(rank)) if ctx == "Column" else len(rank) + 1
            for col, ctx in zip(cases["column"], cases["schema_context"], strict=True)
        ],
        reason=cases["column"].astype(str) + ":" + cases["check"].astype(str),
    )
    first = cases.sort_values("_rank", kind="stable").drop_duplicates("index")
    return pd.Series(first["reason"].to_numpy(), index=first["index"].to_numpy(), dtype="object")


def _check_coverage(live: pd.DataFrame, listed: Collection[str], oldest: pd.Timestamp) -> None:
    """Rule 6: live listed names (unparseable kept) with no row left after the
    expiry drop. A quarantined row still counts as present."""
    expiries = {name: name_expiry(name) for name in listed}
    alive = [n for n, e in expiries.items() if e is None or e > oldest]
    if not alive:
        raise DeribitRefusal(409, "no live listings: every listed name has expired")
    missing = set(alive) - set(live["instrument_name"])
    if len(missing) / len(alive) > MAX_MISSING_LISTED_SHARE:
        raise DeribitRefusal(
            409,
            f"incomplete: {len(missing)} of {len(alive)} live listed names have no row "
            f"(e.g. {', '.join(sorted(missing)[:5])})",
        )


def _split(live: pd.DataFrame, currency: str, day: dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(valid, quarantined)`` with ``reason`` -- plan reasons win, then
    pandera's. The quarantined frame keeps the positional index."""
    stored = live.assign(snapshot_date=pd.Timestamp(day))[list(STORED_COLUMNS)]
    own = _plan_reasons(live, currency)
    schema = _pandera_reasons(stored)
    reasons = pd.concat([schema.drop(own.index, errors="ignore"), own])
    bad = stored.index.isin(reasons.index)
    quarantined = stored.loc[bad].assign(reason=reasons.reindex(stored.index[bad]).to_numpy())
    kept = stored.loc[~bad]
    valid = DeribitOptionsSchema.validate(kept, lazy=True) if not kept.empty else kept
    return valid, quarantined


def plan_deribit_write(
    frame: pd.DataFrame,
    *,
    currency: str,
    listed: Collection[str],
    now: dt.datetime,
    partition_exists: Callable[[dt.date], bool],
    has_earlier_partition: Callable[[dt.date], bool],
) -> DeribitWritePlan:
    """Decide one book's write. Both writers run THIS, so the rules cannot
    drift between them; every refusal raises :class:`DeribitRefusal` and
    writes nothing.

    In order: (1) freshness, two-sided, NaT first; (1b) the body is for this
    book; (2) an existing partition D is a green no-op -- after 1 and 1b, so
    a replayed captured body is still refused as stale; (3) too early (425);
    (4) expired rows dropped and counted; (5) nothing left (422); (6) live
    listed coverage; (7) bad rows quarantined -- committed anyway, the
    script goes red above 1%; (8) whether D - 1 exists.
    """
    now_utc = _naive_utc(now)
    if frame.empty:
        # The wire's ``min_length=1``, for --local, which has no wire.
        raise DeribitRefusal(422, "no rows submitted")
    _check_fresh(frame["observed_at"], now_utc)
    _check_book(frame["premium_currency"], currency)
    oldest = frame["observed_at"].min()
    day = oldest.date()
    previous = partition_exists(day - dt.timedelta(days=1)) if has_earlier_partition(day) else None
    empty = frame.iloc[0:0]
    observed_ms = int(oldest.value // 1_000_000)
    if partition_exists(day):
        return DeribitWritePlan(empty, empty, day, True, 0, previous, observed_ms, 0)
    if oldest.time() < EARLIEST_CAPTURE:
        raise DeribitRefusal(425, f"too early: observed {oldest}, re-run after 08:05 UTC")
    dated = _with_expiry(frame)
    expired = dated["expiration"].notna() & (dated["expiration"] <= dated["observed_at"])
    live = dated.loc[~expired]
    if live.empty:
        raise DeribitRefusal(422, f"no live rows: all {len(frame)} rows have expired")
    valid, quarantined = _split(live, currency, day)
    _check_coverage(live, listed, oldest)
    return DeribitWritePlan(
        valid, quarantined, day, False, int(expired.sum()), previous, observed_ms, len(live)
    )


# ---- the quarantine ---------------------------------------------------------


def quarantine_key(dataset: str, snapshot_date: dt.date, observed_at_ms: int) -> str:
    """One blob per capture: a second capture of the same day gets its own
    key, and a same-body retry rewrites the same key with the same content."""
    return f"ops/quarantine/{dataset}/{snapshot_date.isoformat()}/{observed_at_ms}.json"


def quarantine_document(
    *,
    dataset: str,
    plan: DeribitWritePlan,
    records: Sequence[Mapping[str, object]],
    code_sha: str,
) -> dict[str, object]:
    """The blob's content. Each row's ``payload`` is the WIRE dict
    ``records[i]``, joined by the frame's positional index -- a frame row is
    never dumped, its Timestamps are not JSON."""
    observed = pd.Timestamp(plan.observed_at_ms, unit="ms")
    return {
        "dataset": dataset,
        "snapshot_date": plan.snapshot_date.isoformat(),
        "observed_at": observed.isoformat(),
        "code_sha": code_sha,
        "rows": [
            {"instrument_name": str(name), "reason": str(reason), "payload": dict(records[i])}
            for i, name, reason in zip(
                plan.quarantined.index,
                plan.quarantined["instrument_name"],
                plan.quarantined["reason"],
                strict=True,
            )
        ],
    }


def serialize_quarantine(doc: Mapping[str, object]) -> str:
    """Prove the document serialises BEFORE anything is written. The blob
    store's own ``json.dumps`` runs only after the dataset commit, where a
    ``TypeError`` would turn into a 500 whose retry answers
    ``already_captured`` green -- and the quarantine would be lost silently."""
    try:
        return json.dumps(doc, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DeribitRefusal(500, f"quarantine not serialisable: {exc}") from exc


class QuarantineParts(NamedTuple):
    """A capture's quarantine, built and proven serialisable."""

    key: str
    doc: dict[str, object]


def prepare_quarantine(
    *,
    dataset: str,
    plan: DeribitWritePlan,
    records: Sequence[Mapping[str, object]],
    code_sha: str,
) -> QuarantineParts | None:
    """``None`` when nothing was quarantined; otherwise the key and the
    document, already through :func:`serialize_quarantine`."""
    if plan.quarantined.empty:
        return None
    doc = quarantine_document(dataset=dataset, plan=plan, records=records, code_sha=code_sha)
    serialize_quarantine(doc)
    return QuarantineParts(quarantine_key(dataset, plan.snapshot_date, plan.observed_at_ms), doc)


def quarantine_log_records(doc: Mapping[str, object], capture: str) -> list[dict[str, str]]:
    """One log record per quarantined row -- the fallback copy when the blob
    write fails. Fields in order, ``payload`` LAST as JSON, so the recovery
    rule (DATA_CONTRACTS §16) is "everything after `` payload=``, outer quotes
    stripped". One line per row: one line holding ~3,168 payloads would be
    ~1.5 MB and might not survive Fly's log limits."""
    rows = cast("list[Mapping[str, object]]", doc["rows"])
    return [
        {
            "capture": capture,
            "instrument_name": str(row["instrument_name"]),
            "reason": str(row["reason"]),
            "payload": json.dumps(row["payload"], allow_nan=False),
        }
        for row in rows
    ]
