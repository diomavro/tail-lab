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

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pandas as pd

from tail_lab.observability import log_event

__all__ = ["AllSourcesFailed", "Source", "first_available"]


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
