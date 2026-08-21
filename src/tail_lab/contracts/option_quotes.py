"""Pandera schema for real historical option quotes — the put smile at each
monthly roll date (``docs/DATA_CONTRACTS.md``, ``docs/DATA_VERDICTS.md``).

This module is a LEAF: it must never import anything else from ``tail_lab``
(enforced by the import-linter layers contract in ``pyproject.toml``).

**Why a slice and not the whole chain set.** The source is 24.7M rows and
632 MB (``docs/DATA_VERDICTS.md``). Committing that to bronze would cost real
object storage to answer questions nobody asks: the platform prices one put
per roll, so what it actually needs is the **put wing at each roll date for
the expiry it rolls into**. That is ~58k rows, and it is strictly more useful
than a single quote per roll because the whole smile is what a skew-aware
pricer has to be calibrated against.

**Only the columns that were refereed.** ``docs/DATA_VERDICTS.md`` found the
source's quotes trustworthy (0.9927 correlation against Cboe's PPUT, zero
unpriceable rolls in 207) but its *derived* columns — ``mark``,
``implied_volatility``, the greeks — sentinel-filled before 2011: ``mark`` is
$0.01 in 87-91% of 2008-2009 rows and IV sits on a 0.01488 floor in 60% of
them. Those columns are therefore **deliberately absent from this contract**.
A schema is the cheapest place to make a bad column unavailable; anything
downstream computes its own IV from ``bid``/``ask``.
"""

from __future__ import annotations

from typing import ClassVar

import pandera.pandas as pa
from pandera.typing import Series

#: The dataset's bronze name.
DATASET = "option_quotes"

#: Bounds on a listed strike, in dollars. Loose on purpose: these catch unit
#: errors and garbled rows, not unusual-but-real strikes.
STRIKE_MIN = 0.0
STRIKE_MAX = 100_000.0

#: Bounds on a quoted premium. A zero *bid* is a real market state (nobody
#: bids for a far-OTM put), but a row with no ask is not a quote at all.
PREMIUM_MIN = 0.0
PREMIUM_MAX = 100_000.0

#: Moneyness band retained around spot: the OTM put wing plus a little way
#: through the money. Deep-ITM puts are just discounted stock and add rows
#: without adding information about the skew.
MONEYNESS_MIN = 0.40
MONEYNESS_MAX = 1.05


class OptionQuoteSchema(pa.DataFrameModel):
    """One end-of-day put quote, on a roll date, for the expiry rolled into.

    Keyed by ``(underlying, quote_date, expiration, strike)``. ``spot`` is
    carried on every row rather than joined later so a quote is
    self-describing: moneyness is the only thing anyone asks of it, and
    recovering it should not require a second dataset that may have been
    re-ingested from a different vendor since.
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

    class Config:
        coerce = True
        strict = True
        unique: ClassVar[list[str]] = ["underlying", "quote_date", "expiration", "strike"]
