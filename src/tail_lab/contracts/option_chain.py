"""Pandera schema for the **forward-collected** daily put-wing snapshot
(``docs/DATA_CONTRACTS.md`` #6).

This module is a LEAF: it must never import anything else from ``tail_lab``
(enforced by the import-linter layers contract in ``pyproject.toml``).

**Why this dataset exists at all.** Every other dataset here can be
backfilled: FRED, Yahoo and Cboe all serve history, so a missing day is a
missing ``make`` invocation, not a missing fact. A live option chain is the
one exception. Nobody publishes a free retroactive chain, so a day that goes
un-snapshotted is gone permanently — the only way to own five years of quotes
is to have started five years ago. That asymmetry is why this adapter is
scheduled rather than run on demand, and why the workflow that drives it
treats a skipped day as a failure instead of a no-op.

**Schema-compatible with the historical slice on purpose.** The first nine
columns are exactly ``contracts/option_quotes.OptionQuoteSchema``, so the
vendor's back-history (SPY monthly rolls, 1990-2023) and this forward
collection union on their shared columns without a translation layer. The
long-run intent is one continuous quote history with a seam, not two
datasets that need reconciling every time someone asks a question.

**The three extra columns are nullable, and that is the whole point.** Cboe
publishes its own ``iv``/``delta``/``theo``, which is strictly better than a
vendor's — it is the exchange's own mark. But it **zero-fills** rather than
omits when it cannot compute one (expiring contracts, no two-sided market),
and a 0.0 implied vol read as a number rather than as a sentinel is exactly
the class of error ``docs/DATA_VERDICTS.md`` caught in the vendor file. The
adapter therefore maps Cboe's zeros to ``None`` on the way in, so the absence
is typed as absence. ``bid``/``ask`` remain the load-bearing columns;
anything that needs an IV can compute its own from them.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import ClassVar
from zoneinfo import ZoneInfo

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors
from pandera.typing import Series

#: The dataset's bronze name. Deliberately *not* ``option_quotes`` — that is
#: the hand-verified vendor back-history with a different provenance and a
#: different licence. Same shape, different source, so: different dataset.
DATASET = "option_chain_snapshot"

#: Bounds on a listed strike, in dollars. Loose on purpose: these catch unit
#: errors and garbled rows, not unusual-but-real strikes.
STRIKE_MIN = 0.0
STRIKE_MAX = 100_000.0

#: Bounds on a quoted premium. A zero *bid* is a real market state (nobody
#: bids for a far-OTM put), but a row with no ask is not a quote at all.
PREMIUM_MIN = 0.0
PREMIUM_MAX = 100_000.0

#: Moneyness band retained around spot, matching ``contracts/option_quotes``:
#: the OTM put wing plus a little way through the money. Deep-ITM puts are
#: just discounted stock and add rows without adding information about skew.
MONEYNESS_MIN = 0.40
MONEYNESS_MAX = 1.05

#: Longest tenor retained, in calendar days. The North Star asks about
#: *soon-expiry* puts; six months is wide enough to see a term structure
#: form around that, and cuts the LEAPS tail that would otherwise be most of
#: the rows and none of the answers.
MAX_TENOR_DAYS = 180


class OptionChainSnapshotSchema(pa.DataFrameModel):
    """One delayed end-of-day put quote, as observed on ``quote_date``.

    Keyed by ``(underlying, quote_date, expiration, strike)`` — the same key
    as :class:`~tail_lab.contracts.option_quotes.OptionQuoteSchema`, so a
    union of the two is a concat rather than a join.
    """

    underlying: Series[str] = pa.Field(nullable=False)
    quote_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    expiration: Series[pa.Timestamp] = pa.Field(nullable=False)
    strike: Series[float] = pa.Field(nullable=False, gt=STRIKE_MIN, le=STRIKE_MAX)
    bid: Series[float] = pa.Field(nullable=False, ge=PREMIUM_MIN, le=PREMIUM_MAX)
    ask: Series[float] = pa.Field(nullable=False, gt=PREMIUM_MIN, le=PREMIUM_MAX)
    volume: Series[int] = pa.Field(nullable=False, ge=0)
    open_interest: Series[int] = pa.Field(nullable=False, ge=0)
    spot: Series[float] = pa.Field(nullable=False, gt=STRIKE_MIN, le=STRIKE_MAX)
    iv: Series[float] = pa.Field(nullable=True, ge=0.0, le=10.0)
    delta: Series[float] = pa.Field(nullable=True, ge=-1.0, le=0.0)
    theo: Series[float] = pa.Field(nullable=True, ge=PREMIUM_MIN, le=PREMIUM_MAX)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["underlying", "quote_date", "expiration", "strike"]


#: The names forward-collected every day. A deliberate subset of the 70-name
#: screening universe, for two reasons that happen to agree.
#:
#: The constitution's own scoping is the first: ``README.md`` lists dataset #6
#: as chains "for the narrow tradable set" — screen broad, trade narrow
#: (``docs/adr/0008``). Model-priced backtests already cover the broad
#: universe, so forward collection only has to serve the names a position
#: could actually be put on.
#:
#: The second is mechanical. Bronze is immutable and re-ingesting an
#: ``ingest_date`` is a no-op (``lake/store.py``), so a day's sweep has to
#: land as ONE write — which makes it one HTTP body, sized to be parsed on a
#: small Fly machine without an OOM. SPY alone slices to ~3.6k rows.
#:
#: Chosen for options liquidity first, then to span the thesis: the
#: benchmarks, the credit/rates/commodity hedges, and the high-beta single
#: names where a sensitivity screen should actually bite.
#:
#: Measured 2026-08-26: these 24 slice to 20,882 rows / 4.68 MB / 25.5s.
#: Widening toward the full 70 is queued (``AGENT_TODO.md``) and affordable
#: — the additions are thin chains, and SPY+QQQ+GLD are already a third of
#: the payload. The constraint that bites first is POST body size, not fetch
#: time.
DEFAULT_SNAPSHOT_SYMBOLS: tuple[str, ...] = (
    "spy",
    "qqq",
    "iwm",
    "xlf",
    "xle",
    "smh",
    "kre",
    "xbi",
    "hyg",
    "tlt",
    "gld",
    "slv",
    "uso",
    "gdx",
    "eem",
    "fxi",
    "arkk",
    "aapl",
    "msft",
    "nvda",
    "tsla",
    "coin",
    "mstr",
    "smci",
)


def split_valid_and_quarantined(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split ``df`` into ``(valid, quarantined)`` against this contract.

    Lives here, next to the schema, rather than in the adapter as its
    siblings do -- because this dataset has **two** writers. The adapter
    writes it on the local path, and ``api/ingest_routes`` writes it on the
    scheduled path (``docs/adr/0020`` splits the sweep at its credential
    seam). They must agree on what a bad row costs, and the first time they
    did not, they disagreed expensively: the endpoint rejected an entire
    22,006-quote sweep because a few dozen far-OTM strikes had no resting
    offer overnight. For a dataset whose whole premise is "capture it today
    or lose it forever", one unquotable strike must never cost the session.

    Bad rows are never silently dropped -- they come back in the second frame
    so the caller can persist them for inspection.
    """
    try:
        return OptionChainSnapshotSchema.validate(df, lazy=True), df.iloc[0:0]
    except SchemaErrors as err:
        bad_index = pd.Index(err.failure_cases["index"].dropna().unique())
        quarantined = df.loc[df.index.isin(bad_index)]
        kept = df.loc[~df.index.isin(bad_index)]
        return OptionChainSnapshotSchema.validate(kept, lazy=True), quarantined


#: Fraction of the REQUESTED symbols that must return quotes before a sweep is
#: allowed to create a partition. Bronze is immutable, so the first write of a
#: session is the only one: a partial sweep does not merely under-report, it
#: permanently defines that session. Measured 2026-09-19 on this workstation,
#: Cboe returned 429 for 9 of 24 symbols and the run still exited 0 with
#: 14,091 rows -- far above the driving script's 200-row floor, which checks
#: rows and therefore cannot see a missing symbol at all. Every one of the 24
#: chains clears 200 rows on its own (thinnest: FXI at 213), so that floor
#: tolerated losing 23 of 24 names.
#:
#: 0.9 rather than 1.0 deliberately: demanding every symbol would let one
#: delisted or permanently-dead chain block the other 23 from ever being
#: captured, which is the same unrecoverable loss in the other direction.
#:
#: Note what the ceiling does to that argument on SMALL universes. Tolerated
#: losses are ``n - ceil(n * 0.9)``: 2 at the production 24, 7 at the 70 the
#: docstring above imagines -- but **0 for any n below 10**, where this floor
#: is effectively 1.0 and the paragraph above does not hold. That only
#: reaches a human running ``make ingest-option-chain CHAIN_SYMBOLS=spy,qqq``
#: by hand, who gets a loud refusal and can re-run; the scheduled sweep
#: always passes all 24. It is called out because the rationale reads as
#: universal and is not.
MIN_SYMBOL_FRACTION = 0.9


class IncompleteSweepError(RuntimeError):
    """Too few chains returned to define a session, and none exists yet.

    Raised INSTEAD of writing. A partial partition cannot be completed later
    (bronze is immutable), so refusing is strictly better than committing: the
    caller exits non-zero, the operator is alerted, and the session is still
    recoverable until the next US open. Never raised when the partition
    already exists -- that write would be a no-op anyway.
    """


class IngestDateMismatch(ValueError):
    """A caller-supplied ``ingest_date`` disagrees with what the quotes elect.

    The partition key is supposed to come from the quotes themselves
    (``session_from_quotes``), not from a caller's say-so -- that is the
    whole point of deriving it. Found by adversarial review 2026-09-25:
    posting a session's quotes with an ``ingest_date`` one day ahead of them
    returned 200 and filed them under tomorrow's key, so that evening's real
    sweep read the already-claimed partition and no-opped. Refusing instead
    of honouring the override keeps the partition key unspellable-wrong
    rather than merely untested.
    """


def session_from_quotes(valid: pd.DataFrame) -> dt.date:
    """The session an ABSOLUTE MAJORITY of symbols agree on -- one vote each.

    NOT ``max(quote_date)``, which this used to be and which inverts on a
    single bad record. ``parse_cboe_chain`` falls back to the payload's
    ``timestamp`` when a symbol carries no ``last_trade_time``, and that field
    is Cboe's CDN **wall clock**, not a session: measured live on 2026-09-21
    it read ``09:11:43`` on a session of ``2026-09-18``, three days apart. One
    symbol missing that field was enough to elect a session nobody traded in,
    whereupon every correct symbol looked off-session and was quarantined --
    a one-row partition stamped with a FUTURE date, permanent by
    immutability, which also pre-claimed the next session's key so the next
    real sweep no-opped.

    **More than half** is required, not merely the most votes, and there is
    deliberately no tie-break. A plurality rule left the degenerate
    distributions writing tiny partitions under a future key -- measured at
    24 symbols: a 12/12 tie wrote 12 rows dated to the wall clock, an 8/8/8
    split wrote 8, and 24 distinct dates wrote **1**. An earlier version broke
    ties toward the later date, which is precisely the side this function
    exists to distrust.

    An absolute majority is unique, so requiring one removes the tie-break
    rather than repairing it. When no date commands one, the session is not
    identifiable from this sweep and ``IncompleteSweepError`` is raised:
    bronze is immutable, so writing a partition whose own session is in doubt
    is the one thing that cannot be undone.
    """
    # One vote per symbol: its own freshest quote date.
    per_symbol = valid.groupby("underlying")["quote_date"].max().dt.date
    tally: dict[dt.date, int] = {}
    for raw in per_symbol.tolist():
        day = raw if isinstance(raw, dt.date) else dt.date.fromisoformat(str(raw))
        tally[day] = tally.get(day, 0) + 1

    voters = sum(tally.values())
    for day, votes in tally.items():
        if votes * 2 > voters:
            return day

    spread = ", ".join(f"{d}:{n}" for d, n in sorted(tally.items()))
    raise IncompleteSweepError(
        f"no session commands a majority of the {voters} chains that returned "
        f"quotes ({spread}); refusing to write, because the partition would be "
        "named for a session this sweep cannot identify and bronze is immutable."
    )


def quarantine_off_session_rows(
    valid: pd.DataFrame, quarantined: pd.DataFrame, session: dt.date
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...], dt.date | None]:
    """Move rows that do not belong to ``session`` out of ``valid``.

    Catches both directions, which is the point: a LAGGARD (Cboe still
    serving a symbol's previous session) and a WALL-CLOCK outlier (a symbol
    with no ``last_trade_time``, dated from the CDN's clock) are the same
    defect seen from either side, and only one of them is "stale".

    The laggard case is not hypothetical: ``ingest_date=2026-09-08`` in
    production holds 412 ARKK rows stamped ``2026-09-04``, ARKK's last trade
    before Labor Day. ARKK's 2026-09-08 session was never captured, and
    nothing noticed, because the partition looks healthy in aggregate.

    Off-session rows go to quarantine rather than the bin: they are real
    quotes, just not this session's. One honest limit -- quarantine is written
    with the same ``write_bronze``, so on a re-run of an already-captured
    session that write no-ops too and these rows are NOT persisted. That is
    immutability working as intended (the first run's quarantine stands), but
    it makes "preserved" a claim about the run that creates the partition.
    """
    if valid.empty:
        return valid, quarantined, (), None
    on_session = valid["quote_date"].dt.date == session
    if bool(on_session.all()):
        return valid, quarantined, (), None
    off = valid[~on_session]
    off_symbols = tuple(sorted({str(u).upper() for u in off["underlying"].unique()}))
    newest_off = off["quote_date"].max().date()
    return (
        valid[on_session].reset_index(drop=True),
        pd.concat([quarantined, off], ignore_index=True),
        off_symbols,
        newest_off,
    )


def require_enough_symbols(
    responded: int,
    requested: int,
    min_fraction: float,
    session: dt.date,
    missing: Sequence[str] = (),
) -> None:
    """Refuse to define a session from too few chains. See ``MIN_SYMBOL_FRACTION``.

    **What this actually bounds, precisely, because the loose reading is
    wrong:** it bounds the number of *recoverable* losses, NOT the number of
    symbols missing from the partition. ``responded`` counts every symbol Cboe
    answered with usable rows for SOME session, so an off-session symbol
    (quarantined by ``quarantine_off_session_rows``) does not count against
    the floor and the partition can therefore hold fewer names than
    ``requested - tolerated``. Measured: two fetch failures plus nine
    laggards writes a 13-of-24 partition, while three fetch failures and no
    laggards refuses at 21-of-24.

    That asymmetry is deliberate, and it is the whole design:

    * a fetch failure (429, timeout) or a symbol whose rows all fail the
      schema is **recoverable** -- a re-run before the next US open gets it.
      Writing now would lock that symbol out permanently, so refusing is the
      cheaper mistake;
    * a laggard, where Cboe serves a symbol's previous session, is **not** --
      the re-run returns the same stale chain. Counting it against the floor
      would discard every healthy chain alongside it, permanently, to avoid a
      partial that is already the best answer obtainable.

    Measured before the distinction existed: three laggards refused a sweep in
    which 21 of 24 chains were fresh, and no re-run could have recovered them.
    Off-session symbols are surfaced through ``symbols_off_session`` and the
    caller's ``::warning::`` instead.

    The separate guarantee that the partition is named for a session this
    sweep can actually identify lives in ``session_from_quotes``, which
    requires an absolute majority -- that is what stops a handful of symbols
    defining a session between them.
    """
    required = max(1, math.ceil(requested * min_fraction))
    if responded >= required:
        return
    raise IncompleteSweepError(
        f"only {responded} of {requested} chains returned usable quotes for session "
        f"{session.isoformat()} (need {required}); refusing to create the partition, "
        "because bronze is immutable and a partial session can never be completed. "
        f"Missing: {', '.join(missing) if missing else '(unknown)'}. Re-run before the "
        "next US open -- and if the same names fail every day they are delisted or "
        "dead, and belong out of DEFAULT_SNAPSHOT_SYMBOLS rather than blocking every "
        "future session."
    )


class SessionInProgress(IncompleteSweepError):
    """Cboe is serving a session that has not closed yet, and nothing is lost.

    BENIGN, unlike its parent: the previous session is already in the lake,
    and the post-close sweep will capture this one. Callers should skip, not
    alarm. Raised only when the witness confirms the previous session landed;
    otherwise the parent is raised, because that session is gone for good.
    """


#: When a session's delayed quotes are final: SPX options trade until 16:15 ET
#: and the feed is 15 minutes delayed. A sweep before this sees a session that
#: is still moving. Measured against the schedules: the local 21:35 UTC sweep
#: is 16:35 EST in winter, and GitHub's 21:30 cron has never been delivered
#: less than 99 minutes late (2026-09-04, 23:09 UTC = 19:09 EDT).
SESSION_SETTLED_ET = dt.time(16, 30)
_NEW_YORK = ZoneInfo("America/New_York")


def session_in_progress(session: dt.date, now: dt.datetime) -> bool:
    """Whether quotes dated ``session`` cannot yet be a settled close.

    True for today in New York before :data:`SESSION_SETTLED_ET`, and for any
    date AFTER today -- which only a quote dated from Cboe's UTC-stamped
    fallback clock can carry, and which must never name a partition.
    """
    now_et = now.astimezone(_NEW_YORK)
    today = now_et.date()
    return session > today or (session == today and now_et.time() < SESSION_SETTLED_ET)


def _refuse_unsettled(
    session: dt.date,
    now: dt.datetime,
    market_session: dt.date | None,
    partition_exists: Callable[[dt.date], bool],
) -> None:
    """Raise the right refusal for quotes dated ``session`` that have not settled.

    A date AFTER today in New York is never a real session -- only Cboe's
    UTC-stamped fallback clock produces one -- so it is always red. Otherwise
    the question is whether the PREVIOUS session made it in. Skipping is
    benign only when the lake holds the witness's session; anything else -- no
    witness, or a missing partition -- is red, because nothing has vouched for
    the previous session. A witness that already lists THIS session names no
    earlier date, so that case is red too -- deliberately: it costs a false
    alarm on a catch-up between Nasdaq posting the day's bar and 16:30 ET,
    never a lost session, and a green there once vouched for nothing.
    """
    if session > now.astimezone(_NEW_YORK).date():
        raise IncompleteSweepError(
            f"Cboe's quotes elect {session}, a date that has not begun in New York -- a "
            "majority is dated from Cboe's UTC fallback clock, not a trade. Nothing "
            "written; this must never name a partition."
        )
    if market_session is not None and market_session < session and partition_exists(market_session):
        raise SessionInProgress(
            f"Cboe's quotes are dated {session}, which has not settled (final after "
            f"{SESSION_SETTLED_ET:%H:%M} ET); nothing written. The previous session "
            f"{market_session} is in the lake, and the post-close sweep will take this one."
        )
    raise IncompleteSweepError(
        f"Cboe's quotes are dated {session}, which has not settled, and the previous "
        "session could not be confirmed in the lake (check the latest partitions) -- if "
        "it is missing and Cboe has moved on, it is lost for good. Nothing written; the "
        "post-close sweep will take the current one."
    )


@dataclass(frozen=True)
class SessionPlan:
    """What a sweep should write, decided identically by both writers.

    This dataset has two writers -- ``ingestion/option_chain`` on the local
    path and ``api/ingest_routes`` on the scheduled one (``docs/adr/0020``) --
    and they drifted. The local path gained the symbol floor, the off-session
    split and an honest ``committed`` flag; the endpoint kept none of them,
    and for weeks reported every re-run of an already-captured session as
    "committed N rows". Everything between "rows validated" and "write" lives
    here, once, so the next protection lands in both places or neither.
    """

    valid: pd.DataFrame
    quarantined: pd.DataFrame
    session: dt.date
    ingest_date: dt.date
    #: The partition already exists, so the write will be a no-op.
    already_captured: bool
    symbols_ok: tuple[str, ...]
    symbols_failed: tuple[str, ...]
    symbols_off_session: tuple[str, ...]


def plan_session_write(
    valid: pd.DataFrame,
    quarantined: pd.DataFrame,
    *,
    requested: Sequence[str],
    fetched_ok: Sequence[str],
    fetch_failed: Sequence[str],
    partition_exists: Callable[[dt.date], bool],
    min_symbol_fraction: float = MIN_SYMBOL_FRACTION,
    ingest_date: dt.date | None = None,
    market_session: dt.date | None = None,
    now: dt.datetime | None = None,
) -> SessionPlan:
    """Name the session, split off-session rows, and refuse unsafe writes.

    ``fetched_ok`` are the symbols whose chains fetched and parsed;
    ``fetch_failed`` those that did not. ``market_session`` is the newest
    COMPLETED US equity session according to a source independent of Cboe
    (``None`` when that source could not be read -- the check is then
    skipped, never guessed).

    Raises ``IncompleteSweepError`` instead of returning a plan whose write
    would lose a session silently.
    """
    # Whether ``session`` was read from quotes at all: with nothing valid it is
    # a clock fallback, which the in-progress guard below must not mistake for
    # a session Cboe is serving -- a total outage would then read as "still
    # trading" and skip green (adversarial review, 2026-09-25).
    have_quotes = not valid.empty
    session = session_from_quotes(valid) if have_quotes else dt.date.today()
    valid, quarantined, off_session, newest_off = quarantine_off_session_rows(
        valid, quarantined, session
    )
    off_set = set(off_session)
    # `fetched_ok` is decided on PARSE success, BEFORE validation, so a symbol
    # whose every row failed the schema counted as captured and counted toward
    # the floor while contributing nothing to the partition. Measured: nine
    # symbols served with a null ask produced `symbols_ok=24` on a partition
    # holding 15 -- the same 15-of-24 outcome the floor refuses by the fetch
    # route, reached silently by the validation route.
    #
    # They belong with the fetch failures, not with the laggards: a symbol
    # that returned unusable rows may well return usable ones on a re-run, so
    # the loss is recoverable and SHOULD count against the floor.
    landed = {str(u).upper() for u in valid["underlying"].unique()} if not valid.empty else set()
    ok = tuple(s.upper() for s in fetched_ok if s.upper() in landed)
    failed = tuple(s.upper() for s in fetch_failed) + tuple(
        s.upper() for s in fetched_ok if s.upper() not in landed and s.upper() not in off_set
    )
    # A caller-supplied date that disagrees with what the quotes themselves
    # elect must be refused, not honoured: honouring it is exactly how a
    # correctly-dated batch gets filed under the wrong partition key (see
    # IngestDateMismatch). Only checked when the session came from real
    # quotes -- with none, ``session`` is already a clock fallback the
    # symbol-floor refusal below will catch on its own terms.
    if ingest_date is not None and have_quotes and ingest_date != session:
        raise IngestDateMismatch(
            f"supplied ingest_date {ingest_date.isoformat()} does not match the session "
            f"{session.isoformat()} the quotes themselves elect; refusing rather than "
            "filing this batch under the wrong partition key. Send no ingest_date, or "
            "one equal to the session."
        )
    ingest_date = ingest_date or session

    # Order matters: the floor is checked ONLY when this run would create the
    # partition. On a re-run, weekend or holiday the write is a no-op anyway,
    # and raising there would turn a correct, benign outcome into a red alert.
    if now is not None and have_quotes and session_in_progress(session, now):
        # systemd fires a missed timer the moment the laptop wakes, so a
        # catch-up can land mid-session. Writing then would file intraday
        # quotes as the day's close, and the real post-close sweep would
        # no-op against them (adversarial review, 2026-09-25).
        _refuse_unsettled(session, now, market_session, partition_exists)
    already = partition_exists(ingest_date)
    if (
        already
        and newest_off is not None
        and newest_off > session
        and not partition_exists(newest_off)
    ):
        # The majority elected a session we have ALREADY captured, while a
        # minority carried FRESHER quotes. Cboe serves per-symbol CDN
        # snapshots of wildly different ages -- measured 2026-09-21, the
        # `timestamp` field spanned ten hours across the universe -- so when
        # most chains are still serving yesterday, the majority vote elects
        # yesterday, `write_bronze` no-ops on its existing partition, and the
        # run reports "already captured" and exits 0. Today is then gone,
        # silently, with the fresh chains discarded as "off-session".
        #
        # Measured at 24 symbols with yesterday already in the lake: 13 stale
        # chains lost the day, 20 stale chains lost the day, both exit 0.
        # This is the one combination where the majority rule is worse than
        # the max() it replaced, so it is refused explicitly rather than
        # rebalanced -- the operator can re-run once the CDN catches up, and
        # the session is recoverable until the next US open.
        raise IncompleteSweepError(
            f"the majority of chains still report {session}, which is already captured, "
            f"but {len(off_session)} chain(s) carry quotes as new as {newest_off} "
            f"({', '.join(off_session)}); refusing rather than no-opping, because that "
            f"would silently discard {newest_off} and exit 0. Re-run once Cboe's "
            "per-symbol caches catch up, before the next US open."
        )
    if (
        already
        and market_session is not None
        and market_session > session
        and not partition_exists(market_session)
    ):
        # The same silent loss with NO fresh minority to betray it: every
        # chain is behind. Measured 2026-09-23..24: Cboe moved /api/global/ to
        # a new host and the old one froze at 03:55 UTC on the 23rd, still
        # answering 200 (`Last-Modified` stuck), so each sweep read session
        # 2026-09-22 -- already
        # captured -- and reported a green no-op while 2026-09-23 was lost
        # for good. Cboe cannot testify against itself, so the evidence has to come
        # from outside it. A market holiday cannot trip this: the reference
        # did not trade either, so it reports the same session Cboe does.
        # Nor can a CDN that steps BACK to an older snapshot after the newer
        # session landed: then nothing is missing, and an alarm claiming a
        # loss would be the cry-wolf this job cannot afford.
        raise IncompleteSweepError(
            f"Cboe is serving session {session}, which is already captured, but the "
            f"market has since completed session {market_session}; refusing rather "
            "than no-opping, because Cboe's feed is behind and that session is not "
            "in the lake. Re-run once Cboe catches up, before the next US open -- "
            "if it does not, the session is lost."
        )
    if not already:
        require_enough_symbols(
            len(ok) + len(off_session),
            len(requested),
            min_symbol_fraction,
            session,
            missing=sorted(failed),
        )
    return SessionPlan(
        valid=valid,
        quarantined=quarantined,
        session=session,
        ingest_date=ingest_date,
        already_captured=already,
        symbols_ok=ok,
        symbols_failed=failed,
        symbols_off_session=off_session,
    )
