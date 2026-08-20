"""The fragility screen (``docs/END_STATE.md`` §1.1, §4 Q1).

The strategy is timing-free: don't predict *when* disorder comes — hold OOM-put
convexity continuously on the **most fragile** names, and the fragility itself
produces the payoff when the (untimed) dislocation hits. So this ranks the
universe by fragility, not by market-timing.

Fragility is measured against the market (SPY) by five complementary
sensitivity metrics — downside beta (how far it falls with the market),
co-skewness (crash-direction co-movement), co-kurtosis (tail amplification),
tail beta (beta restricted to the market's worst days), and downside
capture (the ratio of mean returns on the market's down days) — combined
into a cross-sectional composite. Alongside each
name's fragility, it runs the model-priced put backtest, so you can see whether
fragility actually translates into put payoff (the §4 Q1 question) and spot
"cheap fragility": fragile names whose puts the market still underprices.

The VIX regime timeline and the market benchmark are read once and reused
across every name.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.contracts.options_calendar import cadence_for
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import load_asof_series, run_put_roll
from tail_lab.research.backtest.regime_verdict import regime_breakdown
from tail_lab.research.metrics.co_kurtosis import co_kurtosis
from tail_lab.research.metrics.co_skewness import co_skewness
from tail_lab.research.metrics.downside_beta import downside_beta
from tail_lab.research.metrics.downside_capture import downside_capture
from tail_lab.research.metrics.tail_beta import tail_beta
from tail_lab.research.regimes.timeline import compute_regime_timeline

#: The market benchmark the fragility metrics are measured against.
BENCHMARK = "spy"

#: Metrics that feed the composite fragility score. Co-kurtosis is computed and
#: shown on the screen but DELIBERATELY EXCLUDED here: co-kurtosis *with the
#: benchmark* rewards names that co-move with SPY's own tails, so it scores
#: broad indices (SPY/DIA/QQQ) as most "fragile" -- backwards for a single-name
#: OOM-put screen, which wants names that fall *harder* than the market. The
#: metric bake-off (``metric_screen.py``, END_STATE §4 Q1) confirmed it as the
#: worst screen in-sample, but the exclusion is structural, not curve-fit. The
#: remaining four are equal-weighted. One place, so the screen and the bake-off
#: composite can never diverge.
COMPOSITE_METRICS: tuple[str, ...] = (
    "downside_beta",
    "co_skewness",
    "tail_beta",
    "downside_capture",
)


class RankedAsset(BaseModel):
    """One universe member: its fragility vs the market + the put backtest."""

    asset: str
    name: str
    spot: float
    # fragility (vs SPY); None if too little overlapping history to estimate
    downside_beta: float | None
    co_skewness: float | None
    co_kurtosis: float | None
    tail_beta: float | None
    downside_capture: float | None
    fragility_score: float | None  # cross-sectional composite, 0..1 (1 = most fragile)
    # put backtest at the screened strike/tenor
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


def _returns(prices: pd.Series) -> pd.Series:
    return prices.pct_change().dropna()


def _frac_rank(values: list[float | None], *, fragile_high: bool) -> dict[int, float]:
    """Cross-sectional fragility rank of one metric, 0..1 (1 = most fragile).
    ``fragile_high`` = a higher raw value means more fragile."""
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(present) < 2:
        return {i: 0.5 for i, _ in present}
    ordered = sorted(present, key=lambda x: x[1], reverse=fragile_high)
    return {i: 1.0 - pos / (len(ordered) - 1) for pos, (i, _) in enumerate(ordered)}


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
    """Rank ``symbols`` by fragility (most fragile first), each with its put
    backtest at the given strike/tenor.

    Point-in-time: the regime timeline, the benchmark, and every price path are
    read as of ``as_of``. A symbol with no OHLCV as-of, or too short a window
    for one roll, is skipped. Raises ``LookupError`` only if the VIX regime
    timeline itself is missing.
    """
    timeline = compute_regime_timeline(store, as_of=as_of)
    lookback_days = round(years * 252)

    try:
        bench_prices, _ = load_asof_series(store, BENCHMARK, as_of)
        bench_ret: pd.Series | None = _returns(bench_prices)
    except LookupError:
        bench_ret = None

    def _fragility(
        asset_prices: pd.Series,
    ) -> tuple[float | None, float | None, float | None, float | None, float | None]:
        if bench_ret is None:
            return None, None, None, None, None
        aligned = pd.concat({"a": _returns(asset_prices), "b": bench_ret}, axis=1).dropna()
        window = aligned.iloc[-lookback_days:]
        a, b = window["a"], window["b"]

        def _safe(fn: Callable[[pd.Series, pd.Series], float]) -> float | None:
            try:
                return fn(a, b)
            except ValueError:
                return None

        return (
            _safe(downside_beta),
            _safe(co_skewness),
            _safe(co_kurtosis),
            _safe(tail_beta),
            _safe(downside_capture),
        )

    def _rank_one(symbol: str) -> RankedAsset | None:
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
            return None  # no data / too short a window for this name -> skip
        db, cs, ck, tb, dc = _fragility(prices)
        _, verdict = regime_breakdown(result.cycles, timeline)
        return RankedAsset(
            asset=symbol,
            name=cadence_for(symbol).name,
            spot=result.spot,
            downside_beta=db,
            co_skewness=cs,
            co_kurtosis=ck,
            tail_beta=tb,
            downside_capture=dc,
            fragility_score=None,  # filled in cross-sectionally below
            roi_on_premium=result.roi_on_premium,
            verdict=verdict,
            hit_rate=result.hit_rate,
            biggest_payoff_mult=result.biggest_payoff_mult,
            n_cycles=result.n_cycles,
        )

    # Each name is an independent lake read + roll; the S3 read releases the
    # GIL, so a bounded thread pool cuts the cold warm-up.
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(_rank_one, symbols) if r is not None]

    # Composite fragility: higher downside beta / tail beta / downside capture,
    # and *lower* (more negative) co-skewness, all mean more fragile. Average
    # the cross-sectional ranks of the COMPOSITE_METRICS present (co-kurtosis is
    # ranked for display but excluded from the blend -- see COMPOSITE_METRICS).
    ranks = {
        "downside_beta": _frac_rank([r.downside_beta for r in rows], fragile_high=True),
        "co_skewness": _frac_rank([r.co_skewness for r in rows], fragile_high=False),
        "tail_beta": _frac_rank([r.tail_beta for r in rows], fragile_high=True),
        "downside_capture": _frac_rank([r.downside_capture for r in rows], fragile_high=True),
    }
    for i, r in enumerate(rows):
        parts = [ranks[m][i] for m in COMPOSITE_METRICS if i in ranks[m]]
        r.fragility_score = sum(parts) / len(parts) if parts else None

    # Most fragile first (names with no fragility estimate sort last).
    rows.sort(
        key=lambda r: r.fragility_score if r.fragility_score is not None else -1.0, reverse=True
    )
    return UniverseRanking(
        as_of=as_of,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=years,
        notional=notional,
        ranked=rows,
    )
