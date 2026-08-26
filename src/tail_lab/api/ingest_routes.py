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
import secrets
from functools import lru_cache

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException

from tail_lab.api.schemas import (
    OptionChainSnapshotRequest,
    OptionChainSnapshotResponse,
    OptionChainSnapshotStatus,
)
from tail_lab.config import get_lake_store as _get_configured_lake_store
from tail_lab.config import get_settings
from tail_lab.contracts.option_chain import DATASET, OptionChainSnapshotSchema
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event

router = APIRouter()

_LOGGER = logging.getLogger(__name__)

QUARANTINE_DATASET = f"{DATASET}__quarantine"


@lru_cache(maxsize=1)
def get_lake_store() -> LakeStore:
    return _get_configured_lake_store()


def _require_token(authorization: str | None) -> None:
    """Same gate as ``feedback_routes``: unset token -> 404 (an unconfigured
    deploy does not advertise the route), bad bearer -> 401, constant-time
    compare, never logged."""
    configured_token = get_settings().feedback_token
    if not configured_token:
        raise HTTPException(status_code=404, detail="not found")
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("Bearer ") :].strip()
    if not presented or not secrets.compare_digest(presented, configured_token):
        raise HTTPException(status_code=401, detail="unauthorized")


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

    try:
        valid = OptionChainSnapshotSchema.validate(frame, lazy=True)
    except Exception as exc:  # pandera raises SchemaErrors; surface it as a 422
        raise HTTPException(status_code=422, detail=f"contract violation: {exc}") from exc

    ingest_date = body.ingest_date or dt.date.today()
    bronze_path = store.write_bronze(DATASET, ingest_date, valid)

    log_event(
        _LOGGER,
        "api.ingest.option_chain",
        dataset=DATASET,
        ingest_date=ingest_date,
        rows=len(valid),
        symbols=valid["underlying"].nunique(),
        quote_date=str(valid["quote_date"].max().date()),
        bronze_path=bronze_path,
    )
    return OptionChainSnapshotResponse(
        dataset=DATASET,
        ingest_date=ingest_date,
        rows=len(valid),
        symbols=int(valid["underlying"].nunique()),
        quote_date=valid["quote_date"].max().date(),
        bronze_path=bronze_path,
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
    try:
        frame = store.read_bronze_as_of(DATASET, today)
    except LookupError:
        return OptionChainSnapshotStatus(
            dataset=DATASET, last_quote_date=None, rows=0, symbols=0, stale_days=None
        )

    last_quote = frame["quote_date"].max().date()
    return OptionChainSnapshotStatus(
        dataset=DATASET,
        last_quote_date=last_quote,
        rows=len(frame),
        symbols=int(frame["underlying"].nunique()),
        stale_days=(today - last_quote).days,
    )
