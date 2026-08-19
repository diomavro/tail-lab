"""Universe ranking (``docs/END_STATE.md`` §1.1) — "which names' OOM puts got
the best results at this strike/tenor," sorted, with each name's regime
verdict alongside.

Runs one model-priced backtest per universe member at a fixed
strike/tenor/lookback and ranks by return on premium. The VIX regime timeline
is market-wide, so it is read once and reused across every name rather than
re-read per symbol.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.contracts.options_calendar import cadence_for
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import load_asof_series, run_put_roll
from tail_lab.research.backtest.regime_verdict import regime_breakdown
from tail_lab.research.regimes.timeline import compute_regime_timeline


class RankedAsset(BaseModel):
    """One universe member's result at the ranked strike/tenor."""

    asset: str
    name: str
    spot: float
    roi_on_premium: float
    verdict: Verdict
    hit_rate: float
    biggest_payoff_mult: float
    n_cycles: int


class UniverseRanking(BaseModel):
    as_of: dt.date
    moneyness_pct: float
    tenor_weeks: float
    lookback_years: float
    notional: float
    ranked: list[RankedAsset]


def rank_universe(
    store: LakeStore,
    *,
    symbols: tuple[str, ...],
    as_of: dt.date,
    moneyness_pct: float,
    tenor_weeks: float,
    years: float,
    notional: float = 1000.0,
) -> UniverseRanking:
    """Rank ``symbols`` by return on premium at the given strike/tenor, most
    attractive first, each tagged with its cross-regime verdict.

    Point-in-time: the regime timeline and every price path are read as of
    ``as_of``. A symbol with no OHLCV as-of, or too short a window for one
    roll, is skipped (not ranked). Raises ``LookupError`` only if the VIX
    regime timeline itself is missing.
    """
    timeline = compute_regime_timeline(store, as_of=as_of)

    rows: list[RankedAsset] = []
    for symbol in symbols:
        try:
            prices, iv_proxy = load_asof_series(store, symbol, as_of)
            result = run_put_roll(
                prices,
                iv_proxy,
                asset=symbol,
                as_of=as_of,
                notional=notional,
                moneyness_pct=moneyness_pct,
                tenor_weeks=tenor_weeks,
                lookback_years=years,
            )
        except LookupError:
            continue
        _, verdict = regime_breakdown(result.cycles, timeline)
        rows.append(
            RankedAsset(
                asset=symbol,
                name=cadence_for(symbol).name,
                spot=result.spot,
                roi_on_premium=result.roi_on_premium,
                verdict=verdict,
                hit_rate=result.hit_rate,
                biggest_payoff_mult=result.biggest_payoff_mult,
                n_cycles=result.n_cycles,
            )
        )

    rows.sort(key=lambda r: r.roi_on_premium, reverse=True)
    return UniverseRanking(
        as_of=as_of,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=years,
        notional=notional,
        ranked=rows,
    )
