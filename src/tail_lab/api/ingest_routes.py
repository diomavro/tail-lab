"""The one write endpoint that exists so a *scheduled* job can reach bronze
without holding object-storage credentials (``docs/adr/0020``).

The daily chain sweep has an awkward shape. It has to run every day forever
(a missed live chain is unrecoverable -- ``contracts/option_chain``), the
fetch itself needs no credentials at all, and the only credentialed step is
the final write. Granting the workflow lake credentials to do that write
would put long-lived object-storage keys in CI for the sake of one PUT, and
routing the whole adapter through the API would break the layer contract
that forbids ``api`` from importing ``ingestion`` (``pyproject.toml``).

So the sweep is split at its natural seam, exactly as the verdict sweep is
(``.github/workflows/daily-verdict-sweep.yml``): the workflow does the
credential-free fetch-and-slice, and hands the sliced rows here. The live
app already holds the Tigris keys and does the write. CI holds one bearer
token and no data credentials, and the no-credentials wall stays intact.

This route is deliberately **narrow**. It is not "write rows to bronze" --
it writes one named dataset, validated against one contract, and rejects
anything else. A general lake-write endpoint would be a far larger thing to
have to trust, and nothing here needs one.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException

from tail_lab.api.auth import require_bearer_token as _require_token
from tail_lab.api.schemas import (
    OptionChainSnapshotRequest,
    OptionChainSnapshotResponse,
    OptionChainSnapshotStatus,
)
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.contracts.option_chain import (
    DATASET,
    DEFAULT_SNAPSHOT_SYMBOLS,
    IncompleteSweepError,
    plan_session_write,
    split_valid_and_quarantined,
)
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

router = APIRouter()

_LOGGER = logging.getLogger(__name__)

QUARANTINE_DATASET = f"{DATASET}__quarantine"


@lru_cache(maxsize=1)
def get_lake_store() -> LakeStore:
    return _get_configured_lake_store()


@router.post("/api/ingest/option-chain")
def ingest_option_chain_snapshot(
    body: OptionChainSnapshotRequest,
    authorization: str | None = Header(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> OptionChainSnapshotResponse:
    """Commit one day's put-wing slice to bronze.

    Rows arrive already sliced by the workflow; they are re-validated here
    against the contract regardless, because the caller being trusted is not
    the same as the payload being well-formed, and bronze is immutable once
    written.
    """
    _require_token(authorization)

    if not body.rows:
        raise HTTPException(status_code=400, detail="no rows submitted")

    frame = pd.DataFrame([row.model_dump() for row in body.rows])
    frame["quote_date"] = pd.to_datetime(frame["quote_date"])
    frame["expiration"] = pd.to_datetime(frame["expiration"])

    # Quarantine bad rows; do NOT reject the batch. The first version of this
    # endpoint validated all-or-nothing and threw away a whole 22,006-quote
    # sweep because a few dozen far-OTM strikes had no resting offer -- while
    # the local path, running the same contract, quarantined those rows and
    # kept the rest. Two writers disagreeing about what a bad row costs is
    # how a session gets lost, which is the one thing this dataset cannot
    # afford. A 422 now means nothing at all validated.
    valid, quarantined = split_valid_and_quarantined(frame)
    if valid.empty:
        raise HTTPException(
            status_code=422,
            detail=f"no rows satisfied the contract ({len(quarantined)} quarantined)",
        )

    # The partition IS the session, not the wall clock. Scheduled runners
    # drift -- GitHub ran the 21:30 cron at 00:57 the next day on the very
    # first scheduled sweep -- and a clock-derived partition turns that drift
    # into two bugs at once: the session lands under tomorrow's key, and
    # tomorrow's real sweep then no-ops against it (bronze is immutable) and
    # is lost. Deriving it from the quotes makes a late run land correctly and
    # a re-run of the same session no-op the way immutability intends.
    #
    # Session naming, the off-session split, the symbol floor and the
    # stale-feed refusal are the SAME code the local adapter runs
    # (``contracts.option_chain.plan_session_write``); this route had drifted
    # to none of them.
    requested = sorted({s.upper() for s in body.symbols})
    present = {str(u).upper() for u in frame["underlying"].unique()}
    try:
        plan = plan_session_write(
            valid,
            quarantined,
            requested=requested,
            fetched_ok=[s for s in requested if s in present],
            fetch_failed=[s for s in requested if s not in present],
            partition_exists=lambda day: store.bronze_partition_exists(DATASET, day),
            ingest_date=body.ingest_date,
            market_session=body.market_session,
        )
    except IncompleteSweepError as exc:
        # 409, not 422: the body is well-formed; writing it NOW would lose a
        # session. The sweep script does not retry a 4xx, so this lands as a
        # red run with this message on it.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    bronze_path = store.write_bronze(DATASET, plan.ingest_date, plan.valid)
    if not plan.quarantined.empty:
        # Diagnostic only, and the session is already committed above: a
        # failed quarantine write must not turn a correct capture into a 500
        # the script then retries into a no-op. Same rule as the local path.
        try:
            store.write_bronze(QUARANTINE_DATASET, plan.ingest_date, plan.quarantined)
        except Exception:
            _LOGGER.exception(
                "event=api.ingest.option_chain.quarantine_write_failed ingest_date=%s rows=%d",
                plan.ingest_date.isoformat(),
                len(plan.quarantined),
            )

    symbols = int(plan.valid["underlying"].nunique())
    quote_date = plan.valid["quote_date"].max().date() if not plan.valid.empty else plan.session
    log_event(
        _LOGGER,
        "api.ingest.option_chain",
        dataset=DATASET,
        ingest_date=plan.ingest_date,
        rows=len(plan.valid),
        quarantined=len(plan.quarantined),
        symbols=symbols,
        quote_date=str(quote_date),
        committed=not plan.already_captured,
        symbols_failed=",".join(plan.symbols_failed) or None,
        symbols_off_session=",".join(plan.symbols_off_session) or None,
        market_session=body.market_session,
        bronze_path=bronze_path,
    )
    return OptionChainSnapshotResponse(
        dataset=DATASET,
        ingest_date=plan.ingest_date,
        rows=len(plan.valid),
        quarantined=len(plan.quarantined),
        symbols=symbols,
        quote_date=quote_date,
        bronze_path=bronze_path,
        committed=not plan.already_captured,
        symbols_failed=plan.symbols_failed,
        symbols_off_session=plan.symbols_off_session,
    )


@router.get("/api/ingest/option-chain/status")
def option_chain_snapshot_status(
    store: LakeStore = Depends(get_lake_store),
) -> OptionChainSnapshotStatus:
    """How stale the forward collection is — public, because a freshness
    number is exactly the kind of thing the constitution says must be
    visible where the result is ("accuracy is surfaced, not filed").

    It exists mainly so *staleness is checkable by something other than a
    human remembering to look*: the daily agent reads this before it picks
    any work, and a gap here outranks whatever was next on its backlog.
    Unlike a failed FRED pull, a gap here can never be filled in.
    """
    today = dt.date.today()
    expected = tuple(sorted(s.upper() for s in DEFAULT_SNAPSHOT_SYMBOLS))
    try:
        frame = store.read_bronze_as_of(DATASET, today)
    except LookupError:
        return OptionChainSnapshotStatus(
            dataset=DATASET,
            last_quote_date=None,
            rows=0,
            symbols=0,
            stale_days=None,
            missing_symbols=expected,
        )

    last_quote = frame["quote_date"].max().date()
    present = set(frame["underlying"].str.upper())
    return OptionChainSnapshotStatus(
        dataset=DATASET,
        last_quote_date=last_quote,
        rows=len(frame),
        symbols=int(frame["underlying"].nunique()),
        stale_days=(today - last_quote).days,
        missing_symbols=tuple(s for s in expected if s not in present),
    )
