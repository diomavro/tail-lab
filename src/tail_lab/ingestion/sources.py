"""Ordered source fallback for ingestion adapters.

Every adapter in this package reads from a public endpoint that can, and
does, go away without warning. Yahoo's chart endpoint is the worked
example: it answered fine for datacenter *and* residential clients until
mid-2026, then began returning HTTP 429 to both, and nothing in the
platform noticed because each adapter had exactly one source and treated
its failure as a hard error (`docs/DATA_FINDINGS.md`).

This module is the fix. An adapter declares an ordered list of
:class:`Source`\\ s; :func:`first_available` walks them, returns the first
that yields data along with the id of the source that produced it, and
records every attempt through ``observability.log_event`` so a silent
demotion to a fallback is visible in the run log rather than invisible in
the data.

Two deliberate design choices:

- **Every source returns an already-parsed DataFrame**, not a raw payload.
  Sources differ in wire format (CSV here, JSON there), so the only thing
  they can honestly share is the parsed shape their dataset's contract
  validates. Parsing inside the source callable also means a source that
  answers HTTP 200 with unparseable junk is treated as a failed source and
  the chain moves on, which is the behaviour we want.
- **Exhausting the chain raises**, it never returns empty. A silently
  empty ingest writes an empty bronze partition, and because bronze
  as-of resolution reads the *latest* partition, that empty partition
  would shadow good data — the same failure mode that once truncated a
  production backtest. Failing loudly is the only safe option
  (`docs/STANDARDS.md`, Rule "fail loud").
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests

from tail_lab.observability import log_event

__all__ = [
    "AllSourcesFailed",
    "Source",
    "SourceBehindMarket",
    "find_header_line",
    "first_available",
    "is_transient_fetch_error",
    "require_current",
    "retry_transient",
]


_LOGGER = logging.getLogger(__name__)


def find_header_line(raw: str, *, header_prefix: str = "DATE,") -> int:
    """Index of the line starting with ``header_prefix``, so a preamble above
    it doesn't shift a CSV parse's columns.

    Format-agnostic (no dataset schema involved), unlike the rest of this
    module -- it lives here because three Cboe-CDN adapters
    (``vix.py``, ``cboe_strategy.py``, ``mpd.py``) each carried an identical
    copy before this one was extracted; the fourth (``vix_complex.py``) is
    what made the duplication worth removing rather than repeating again.
    """
    prefix = header_prefix.upper()
    for offset, line in enumerate(raw.splitlines()):
        if line.strip().upper().startswith(prefix):
            return offset
    return 0


@dataclass(frozen=True)
class Source:
    """One candidate source: a stable id and a callable that produces rows.

    ``fetch`` must return a parsed DataFrame in the dataset's contract
    shape, or raise. Returning an empty frame counts as a failure — a
    source that has no data for us is not a source we should stop at.
    """

    source_id: str
    fetch: Callable[[], pd.DataFrame]


class AllSourcesFailed(RuntimeError):
    """Every source in the chain failed. Carries the per-source errors.

    Raised rather than returning empty so a dead chain can never be
    mistaken for "the market had no data today".
    """

    def __init__(self, dataset: str, errors: dict[str, str]) -> None:
        self.dataset = dataset
        self.errors = errors
        detail = "; ".join(f"{src}: {err}" for src, err in errors.items())
        super().__init__(f"all sources failed for {dataset!r} -- {detail}")


def first_available(
    sources: Sequence[Source],
    *,
    dataset: str,
    logger: logging.Logger,
) -> tuple[pd.DataFrame, str]:
    """Return ``(rows, source_id)`` from the first source that yields data.

    Sources are tried in order, so the sequence encodes preference: put the
    source whose adjustment basis and history depth the dataset's contract
    describes first, and demote the rest. Each attempt is logged — success
    with its row count, failure with the exception text — because "which
    source did today's partition actually come from" is a question the run
    log must be able to answer months later.

    Raises :class:`AllSourcesFailed` if the chain is exhausted.
    """
    if not sources:
        raise AllSourcesFailed(dataset, {})

    errors: dict[str, str] = {}
    for index, source in enumerate(sources):
        try:
            rows = source.fetch()
        except Exception as exc:
            errors[source.source_id] = f"{type(exc).__name__}: {exc}"
            log_event(
                logger,
                "ingest.source_attempt",
                dataset=dataset,
                source=source.source_id,
                rank=index,
                outcome="failed",
                error=errors[source.source_id],
            )
            continue

        if rows.empty:
            errors[source.source_id] = "returned no rows"
            log_event(
                logger,
                "ingest.source_attempt",
                dataset=dataset,
                source=source.source_id,
                rank=index,
                outcome="empty",
            )
            continue

        log_event(
            logger,
            "ingest.source_attempt",
            dataset=dataset,
            source=source.source_id,
            rank=index,
            outcome="ok",
            rows=len(rows),
            # Loud on purpose: a fallback that quietly becomes the steady
            # state is how a dataset's provenance drifts without anyone
            # deciding it should.
            fallback=index > 0,
        )
        return rows, source.source_id

    log_event(
        logger,
        "ingest.source_chain_exhausted",
        dataset=dataset,
        attempted=len(sources),
    )
    raise AllSourcesFailed(dataset, errors)


def is_transient_fetch_error(exc: Exception) -> bool:
    """Whether retrying the SAME request could plausibly change the answer.

    A 5xx, a 429, or a connection/timeout fault says the host (or the
    network) failed to answer a request it might answer next time. A 4xx
    other than 429 says it understood the request and refused it -- most
    often a symbol it does not list -- and repeating the identical GET cannot
    change that. Anything that is not a ``requests`` fault (an injected test
    double, a KeyError in the caller) is not transient either. Note that
    ``Response.json()`` raises ``requests.exceptions.JSONDecodeError``, a
    ``RequestException``: a truncated or HTML 200 IS retried, which is what a
    blip that serves half a body wants.
    """
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, requests.exceptions.RequestException)


def retry_transient[T](
    call: Callable[[], T],
    *,
    attempts: int,
    backoff_s: float,
    event: str,
    **fields: Any,
) -> T:
    """Run ``call``, retrying only faults :func:`is_transient_fetch_error`
    accepts, sleeping ``backoff_s`` between attempts.

    One implementation for every keyless fetch that can blip: the chain sweep
    grew this first (a per-symbol Cboe 429 used to cost that symbol its whole
    session), and ``options_expiry`` hits the same endpoint with none of it.
    Each retry is logged as ``event`` with ``fields`` so a flaky source shows
    up in the run log before it becomes a failed one.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:
            if not is_transient_fetch_error(exc) or attempt == attempts:
                raise
            detail = " ".join(f"{k}={v}" for k, v in fields.items())
            _LOGGER.warning("event=%s %s attempt=%d/%d", event, detail, attempt, attempts)
            time.sleep(backoff_s)
    raise AssertionError("attempts must be >= 1")


class SourceBehindMarket(RuntimeError):
    """A source answered, parsed and validated -- and is still stale.

    Raised INSTEAD of writing. These adapters key the partition on the day
    they run, so a stale write would occupy today's key and a re-run after
    the source recovers would no-op against it (bronze is immutable). Not
    writing costs nothing: as-of reads fall back to the previous partition,
    which holds the same history.
    """


def require_current(
    frame: pd.DataFrame,
    *,
    date_col: str,
    market_session: dt.date | None,
    dataset: str,
    group_col: str | None = None,
    expected: Sequence[str] = (),
) -> None:
    """Refuse a history whose newest row predates the newest completed session.

    ``market_session`` comes from a source independent of the one being
    checked (``ingestion.ohlcv.latest_market_session``); ``None`` skips the
    check. Measured 2026-09-24: Cboe's old ``cdn.cboe.com`` host kept
    answering 200 with index histories ending 2026-09-22 after the market had
    completed 2026-09-23 -- a feed that fails nothing and parses perfectly.
    A holiday cannot trip this, because the reference did not trade either.
    With ``group_col`` every group (series, ticker) must be current, and the
    laggards are named -- including any name in ``expected`` with no valid
    rows at all (a header-only CSV, or one whose every row was quarantined),
    which is the half-broken-feed case this exists for. A single-series
    caller (no ``group_col``) gets the same "has no rows" coverage by
    passing ``expected=(dataset,)``.
    """
    if market_session is None or (frame.empty and not expected):
        return
    newest = (
        frame.groupby(group_col)[date_col].max()
        if group_col is not None
        else pd.Series({dataset: frame[date_col].max()})
        if not frame.empty
        else pd.Series(dtype=object)
    )
    behind = {
        str(name): f"ends {pd.Timestamp(day).date()}"
        for name, day in newest.items()
        if pd.Timestamp(day).date() < market_session
    }
    present = {str(name) for name in newest.index}
    behind.update({name: "has no rows" for name in expected if name not in present})
    if not behind:
        return
    detail = ", ".join(f"{name} {state}" for name, state in sorted(behind.items()))
    _LOGGER.error(
        "event=ingest.source_behind_market dataset=%s market_session=%s %s",
        dataset,
        market_session.isoformat(),
        detail,
    )
    raise SourceBehindMarket(
        f"{dataset}: the market has completed {market_session} but the source's "
        f"history does not reach it ({detail}); refusing to write, so a re-run "
        "after the source catches up can still claim today's partition."
    )
