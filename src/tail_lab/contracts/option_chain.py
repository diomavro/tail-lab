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

from typing import ClassVar

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
