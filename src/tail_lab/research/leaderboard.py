"""The sensitivity leaderboard (``docs/END_STATE.md`` §1.1) — ranks the
screening universe by downside beta against a benchmark, most-sensitive
first. This is Dio's standing directive (in-app feedback, 2026-08-18):
"prioritize the sensitivity leaderboard so I can see put candidates ranked
daily."

Orchestration point, mirroring ``research/vix_stretch.py``: reads
point-in-time OHLCV for the benchmark and every universe symbol via
``lake/`` (``LakeStore.read_bronze_as_of`` — no look-ahead, ``docs/adr/0009``),
computes each symbol's downside beta (``research/metrics/downside_beta.py``),
and hands the raw scores to ``transforms/marts/sensitivity_leaderboard.py``
to shape into the ranked gold table. The API layer calls only this function.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.research.metrics.downside_beta import downside_beta
from tail_lab.transforms.marts.sensitivity_leaderboard import build_gold

#: The only sensitivity metric wired into the leaderboard so far
#: (``docs/END_STATE.md`` §1.1 wants many; downside beta is the first —
#: see ``AGENT_TODO.md``).
METRIC = "downside_beta"

#: The screening universe: the names with OHLCV already ingested to prod
#: (``AGENT_TODO.md``, ~5y spy/qqq/iwm/tsla/gld/eem), same set as the Put
#: Lab's asset picker (``frontend/src/components/putlab/types.ts``).
DEFAULT_BENCHMARK = "spy"
DEFAULT_UNIVERSE: tuple[str, ...] = ("spy", "qqq", "iwm", "tsla", "gld", "eem")

#: Downside beta is only estimable with at least this many aligned
#: benchmark-down-day observations (the metric's own floor is 2; this is a
#: leaderboard-level quality bar, not a repeat of that mathematical minimum).
MIN_DOWNSIDE_OBSERVATIONS = 5


class LeaderboardRow(BaseModel):
    """One ranked entry — an asset's score under a single sensitivity metric."""

    rank: int
    symbol: str
    score: float
    metric: str


class SensitivityLeaderboardResult(BaseModel):
    """The gold-layer sensitivity leaderboard for a single as-of date."""

    as_of: dt.date
    metric: str
    benchmark: str
    rows: list[LeaderboardRow]


def _adj_close_returns(bronze: pd.DataFrame) -> pd.Series:
    """Daily simple returns of adjusted close, sorted and de-duplicated."""
    ordered = bronze.sort_values("trade_date").drop_duplicates(subset="trade_date", keep="last")
    prices = pd.Series(
        ordered["adj_close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["trade_date"]),
    )
    return prices.pct_change().dropna()


def compute_sensitivity_leaderboard(
    store: LakeStore,
    *,
    as_of: dt.date,
    universe: tuple[str, ...] = DEFAULT_UNIVERSE,
    benchmark: str = DEFAULT_BENCHMARK,
) -> SensitivityLeaderboardResult:
    """Point-in-time downside-beta leaderboard as of ``as_of``.

    Reads only the bronze OHLCV snapshot known on or before ``as_of`` for the
    benchmark and each ``universe`` symbol (no-look-ahead via
    ``LakeStore.read_bronze_as_of``), aligns each symbol's daily returns to
    the benchmark's trading calendar (inner join, so a symbol's own listing
    gaps don't corrupt the ratio), and ranks by downside beta.

    A symbol that can't be scored (no bronze snapshot yet, or too little
    aligned downside-day history) is skipped, not failed — the leaderboard
    degrades gracefully to whatever of the universe is currently scorable.
    Raises ``LookupError`` if the benchmark itself has no snapshot, or if
    nothing in the universe is scorable.
    """
    try:
        bench_bronze = store.read_bronze_as_of(dataset_id(benchmark), as_of)
    except LookupError as exc:
        raise LookupError(
            f"no OHLCV for benchmark {benchmark} known as of {as_of.isoformat()}"
        ) from exc
    if bench_bronze.empty:
        raise LookupError(f"no OHLCV for benchmark {benchmark} known as of {as_of.isoformat()}")
    bench_returns = _adj_close_returns(bench_bronze)

    scores: list[tuple[str, float]] = []
    for symbol in universe:
        try:
            bronze = store.read_bronze_as_of(dataset_id(symbol), as_of)
        except LookupError:
            continue
        if bronze.empty:
            continue
        asset_returns = _adj_close_returns(bronze)
        aligned = pd.concat(
            {"asset": asset_returns, "benchmark": bench_returns}, axis=1, join="inner"
        ).dropna()
        try:
            beta = downside_beta(
                aligned["asset"],
                aligned["benchmark"],
                min_observations=MIN_DOWNSIDE_OBSERVATIONS,
            )
        except ValueError:
            continue
        scores.append((symbol.upper(), beta))

    if not scores:
        raise LookupError(f"no scorable symbols in the universe as of {as_of.isoformat()}")

    gold = build_gold(
        pd.DataFrame(scores, columns=["symbol", "score"]),
        metric=METRIC,
    )
    rows = [
        LeaderboardRow(rank=int(rank), symbol=str(symbol), score=float(score), metric=str(metric))
        for rank, symbol, score, metric in zip(
            gold["rank"], gold["symbol"], gold["score"], gold["metric"], strict=True
        )
    ]
    return SensitivityLeaderboardResult(
        as_of=as_of, metric=METRIC, benchmark=benchmark.upper(), rows=rows
    )
