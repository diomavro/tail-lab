"""Pandera schema for the Minneapolis Fed Market-Based Probability Densities
(MPD) dataset (`docs/DATA_CONTRACTS.md` #9, `AGENT_TODO.md`'s "Minneapolis
Fed MPD adapter" item).

This module is a LEAF: it must never import anything else from
``tail_lab`` (enforced by the import-linter layers contract in
``pyproject.toml``). Every other layer may import from here.

Weekly risk-neutral density statistics the Minneapolis Fed backs out of real
option prices via Breeden-Litzenberger, free and keyless. Unlike VIX (a
single implied-vol number) or the model-priced pricer's flat-vol proxy, this
carries the market's actual **skew** and **kurtosis** -- a genuine
calibration/validation target for `docs/END_STATE.md` §4 Q2 ("how did skew
evolve before crashes") and the skew-aware pricer queued in `AGENT_TODO.md`.
``sp12m``/``sp6m`` (the S&P 500 markets) run 2007-01-12 to date, covering
the whole 2008 crisis; the file also carries several single-name/commodity/
rate/inflation markets, kept as one long-format panel (mirroring
``contracts/cboe_strategy.py``) because the whole file is one fetch.

One quirk from the source's own preamble: for the inflation markets
(``infl1y``/``infl2y``/``infl5y``), ``lg_change_decr``/``lg_change_incr``
mean "<1%"/">3%" rather than a symmetric +/-20% band -- the numeric values
are still comparable across markets (this adapter carries them as floats),
but a reader comparing ``prob_large_decline`` across market families should
know the threshold defining "large" is not the same number everywhere.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: The single bronze dataset the whole file lands in -- one fetch, one panel,
#: mirroring ``contracts/cboe_strategy.py``'s "whole family, one dataset" shape.
DATASET = "mpd"


class MpdRowSchema(pa.DataFrameModel):
    """Shape shared by bronze (raw-but-typed) and silver MPD rows.

    ``maturity_target`` is nullable: the source leaves it blank (``NA``) for
    a real, if uncommon, subset of rows -- not a parse failure.
    """

    market: Series[str] = pa.Field(nullable=False)
    obs_date: Series[pa.Timestamp] = pa.Field(nullable=False)
    maturity_months: Series[float] = pa.Field(nullable=True, gt=0.0)
    mu: Series[float] = pa.Field(nullable=True)
    sd: Series[float] = pa.Field(nullable=True, ge=0.0)
    skew: Series[float] = pa.Field(nullable=True)
    kurt: Series[float] = pa.Field(nullable=True)
    p10: Series[float] = pa.Field(nullable=True)
    p50: Series[float] = pa.Field(nullable=True)
    p90: Series[float] = pa.Field(nullable=True)
    prob_large_decline: Series[float] = pa.Field(nullable=True, ge=0.0, le=1.0)
    prob_large_increase: Series[float] = pa.Field(nullable=True, ge=0.0, le=1.0)

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["market", "obs_date"]


MpdSchema = MpdRowSchema.to_schema()
