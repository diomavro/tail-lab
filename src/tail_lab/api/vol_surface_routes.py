"""``GET /api/putlab/vol-surface`` -- one session's implied-vol smiles in
log-forward moneyness (``docs/adr/0036``).

Served under the Surface desk word (``docs/adr/0021``) as its Smile section.
A refusal (an uncollected name, VIX, no chain yet) is a 200 carrying the
reason, like every other surface here: not having the data is an answer.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from fastapi import APIRouter, Depends, Query

from tail_lab.api.memo import RefreshingMemo
from tail_lab.api.putlab_routes import get_lake_store as _putlab_lake_store
from tail_lab.lake.store import LakeStore
from tail_lab.observability import log_event
from tail_lab.research.vol_surface.read import VolSurface, read_vol_surface

router = APIRouter()
logger = logging.getLogger("tail_lab.api.vol_surface")

#: A fitted session never changes (bronze is immutable), but the session an
#: ``as_of`` resolves to does once tonight's sweep lands, so entries go stale
#: and refresh. SPY costs ~5 s to fit (22 expiries), which is why it is
#: memoised -- single-flight, so concurrent cold visitors share one fit.
#: ``symbol`` is free text, so the memo is capped.
_SURFACE_MEMO: RefreshingMemo[tuple[str, str], VolSurface] = RefreshingMemo(
    name="vol_surface", fresh_s=600.0, serve_stale_s=24 * 3600.0, max_entries=64
)


def get_lake_store() -> LakeStore:
    return _putlab_lake_store()


@router.get("/api/putlab/vol-surface")
def putlab_vol_surface(
    symbol: str = Query(description="Underlying ticker, e.g. spy."),
    as_of: dt.date | None = Query(default=None),
    store: LakeStore = Depends(get_lake_store),
) -> VolSurface:
    """Every expiry's smile for ``symbol`` from the chain session known on
    ``as_of``: forward (parity, else carry), the OTM quotes as ``(k, iv)``,
    the SVI fit or the one-wing quadratic, and its no-arbitrage checks."""
    symbol = symbol.lower()
    resolved = as_of or dt.datetime.now(dt.UTC).date()
    return _SURFACE_MEMO.get(
        (symbol, resolved.isoformat()), lambda: _compute(store, symbol, resolved)
    )


def _compute(store: LakeStore, symbol: str, resolved: dt.date) -> VolSurface:
    started = time.monotonic()
    surface = read_vol_surface(store, symbol, resolved)
    log_event(
        logger,
        "api.putlab.vol_surface",
        symbol=symbol,
        as_of=resolved,
        session=surface.session,
        two_wing=surface.two_wing,
        slices=len(surface.slices),
        fitted=sum(s.fit is not None for s in surface.slices),
        calendar_violations=len(surface.calendar_violations),
        q_source=surface.q_source,
        refusal=surface.refusal,
        duration_s=round(time.monotonic() - started, 2),
    )
    return surface
