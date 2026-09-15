"""The metric bake-off (``docs/END_STATE.md`` §4 Q1): *which* of the six
fragility metrics best sorted realized OOM-put payoffs over the lookback, and
does the composite beat any single one?

The fragility screen (``ranking.py``) ranks the universe by a composite of five
sensitivity metrics. This module answers the prior question the composite
assumes away — which metric is actually worth ranking on. For each of the seven
candidate screens (the six raw metrics plus the composite ``fragility_score``)
it holds an equal-weight basket of OOM puts on that screen's ``top_k`` most
fragile names and reports the basket's blended put return, hit rate, bleed
(max drawdown), per-regime breakdown/verdict, and a Spearman rank correlation
between the metric's fragility ordering and each name's realized put ROI. The
metric whose basket earned the best return-on-premium wins the bake-off.

HONESTY — read this before trusting a winner. This is an **in-sample,
cross-sectional association measured over the historical lookback**, not a
walk-forward / out-of-sample predictive backtest. Both the fragility metric and
the realized put payoff are computed over the *same* trailing window, so the
result describes how well each measure *sorted realized put payoffs over this
window* — legitimate evidence for **choosing** which screen to rank on, but
**not** a forward guarantee that the winning metric will keep predicting
payoffs. Point-in-time correctness is still respected end to end (every price
path, the benchmark, and the VIX regime timeline are read as of ``as_of``; no
future data leaks in), but "sorted payoffs well over 2022-2026" must never be
read as "will sort payoffs well next year". Treat the winner as a screen
chooser, not a signal.

Testing seven screens against the same window is also a multiple-comparisons
problem (Harvey, Liu & Zhu, *...and the Cross-Section of Expected Returns*,
Review of Financial Studies 29(1), 2016): a conventional per-test hurdle names
a winner far more often than it should once this many candidates are tried.
Each entry's ``significant_raw`` is that uncorrected per-test reading;
``significant_corrected`` is the same test after a Benjamini-Hochberg
false-discovery-rate correction across every screen with a defined p-value in
the comparison (``multiple_testing.benjamini_hochberg``) -- read the corrected
column, not the raw one, before calling any screen a real winner.

Efficiency: each name is loaded and backtested **once**; the seven screens differ
only in how they *rank and select* from that shared pass, so 35 names cost 35
backtests, not 35x7.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from pydantic import BaseModel
from scipy.stats import spearmanr

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.multiple_testing import benjamini_hochberg
from tail_lab.research.backtest.portfolio import _max_drawdown, _scaled
from tail_lab.research.backtest.put_roll import (
    PutBacktestResult,
    PutRollCycle,
    annualized_return,
    load_asof_series,
    run_put_roll,
)
from tail_lab.research.backtest.ranking import (
    BENCHMARK,
    COMPOSITE_METRICS,
    _frac_rank,
    _returns,
)
from tail_lab.research.backtest.regime_verdict import RegimeSlice, regime_breakdown
from tail_lab.research.metrics.co_kurtosis import co_kurtosis
from tail_lab.research.metrics.co_skewness import co_skewness
from tail_lab.research.metrics.downside_beta import downside_beta
from tail_lab.research.metrics.downside_capture import downside_capture
from tail_lab.research.metrics.tail_beta import tail_beta
from tail_lab.research.metrics.vol_beta import vol_beta
from tail_lab.research.regimes.timeline import compute_regime_timeline, load_vix_close

#: The five benchmark-regressed raw sensitivity metrics, in stable display
#: order. Vol beta is NOT here: it regresses against VIX changes, not the SPY
#: benchmark, so it needs a different aligned pair -- see ``_fragility``'s
#: separate handling below and ``_ALL_METRIC_NAMES`` for the full screen set.
_METRIC_FUNCS: dict[str, Callable[[pd.Series, pd.Series], float]] = {
    "downside_beta": downside_beta,
    "co_skewness": co_skewness,
    "co_kurtosis": co_kurtosis,
    "tail_beta": tail_beta,
    "downside_capture": downside_capture,
}
#: Every raw metric screened in the bake-off, including vol beta.
_ALL_METRIC_NAMES: tuple[str, ...] = (*_METRIC_FUNCS, "vol_beta")
#: A higher raw value means more fragile (matches ``ranking.py``'s directions):
#: co-skewness and vol beta are fragile-when-*low* (more negative = more
#: crash-prone / more reactive to a fear spike).
_FRAGILE_HIGH: dict[str, bool] = {
    "downside_beta": True,
    "co_skewness": False,
    "co_kurtosis": True,
    "tail_beta": True,
    "downside_capture": True,
    "vol_beta": False,
}
_COMPOSITE = "fragility_score"
#: FDR level for the Benjamini-Hochberg correction across the seven screens'
#: Spearman tests (`docs/END_STATE.md` §4 Q1) -- the conventional per-test
#: 0.05 hurdle this also gates the *uncorrected* `significant_raw` reading,
#: so the two columns are directly comparable at the same nominal level.
_FDR_ALPHA = 0.05
#: Human labels for the cockpit table.
_LABELS: dict[str, str] = {
    "downside_beta": "Downside beta",
    "co_skewness": "Co-skewness",
    "co_kurtosis": "Co-kurtosis",
    "tail_beta": "Tail beta",
    "downside_capture": "Downside capture",
    "vol_beta": "Vol beta",
    _COMPOSITE: "Composite fragility",
}


class MetricScreenEntry(BaseModel):
    """One screen's bake-off result: hold puts on the ``top_k`` names this
    metric flags as most fragile, and see how the basket did."""

    metric: str
    label: str
    top_k_assets: list[str]
    roi_on_premium: float
    annualized_return: float  # geometric annualization of roi_on_premium over `years`
    hit_rate: float
    combined_max_drawdown: float
    verdict: Verdict
    regime_slices: list[RegimeSlice]
    #: In-sample Spearman rank corr between this metric's fragility ordering and
    #: realized put ROI across all scored names; None if <3 usable pairs. A
    #: positive value = more-fragile-by-this-metric names had higher realized
    #: put ROI over the lookback (this metric sorted payoffs well).
    spearman_vs_payoff: float | None
    #: Two-sided p-value for the null hypothesis that `spearman_vs_payoff` is
    #: zero; None exactly when `spearman_vs_payoff` is None.
    spearman_pvalue: float | None
    #: `spearman_pvalue < 0.05`, the hurdle a single test would use in
    #: isolation -- kept alongside `significant_corrected` because the whole
    #: point of the correction is showing how often the uncorrected reading
    #: calls a winner that the FDR-adjusted one does not (`docs/AGENT_TODO.md`).
    significant_raw: bool
    #: Whether this screen survives Benjamini-Hochberg FDR correction across
    #: all screens with a defined p-value in this comparison, at
    #: `MetricScreenComparison.fdr_alpha`. False whenever `spearman_pvalue` is
    #: None (an untested screen cannot be a significant one).
    significant_corrected: bool
    #: Blended ROI minus the buy-puts-on-everyone baseline.
    lift_vs_baseline: float


class MetricScreenComparison(BaseModel):
    """The full bake-off across all seven screens, best blended ROI first.

    In-sample cross-sectional association over the historical lookback (see the
    module docstring): a chooser for which screen to rank on, not a forward
    predictive backtest.
    """

    as_of: dt.date
    moneyness_pct: float
    tenor_weeks: float
    lookback_years: float
    top_k: int
    universe_size: int  # names scored (had data + a completable roll)
    baseline_roi: float  # mean per-name ROI, the "buy puts on everyone" baseline
    #: How many of the seven screens had a defined `spearman_pvalue` and so
    #: entered the Benjamini-Hochberg correction (`docs/END_STATE.md` §4 Q1) --
    #: the number Harvey, Liu & Zhu (2016) says must be reported alongside any
    #: "which metric wins" verdict, not just implied by counting table rows.
    n_comparisons: int
    #: FDR level `significant_corrected` was computed at.
    fdr_alpha: float
    entries: list[MetricScreenEntry]


@dataclass
class _Scored:
    """One universe name after its single backtest + fragility estimate."""

    symbol: str
    metrics: dict[str, float | None]  # the six raw metric values (or None)
    fragility_score: float | None  # cross-sectional composite, filled below
    result: PutBacktestResult


def _spearman(
    values: list[float | None], rois: list[float], *, fragile_high: bool
) -> tuple[float | None, float | None]:
    """In-sample Spearman rank corr (and its two-sided p-value) between a
    metric's fragility ordering and realized put ROI. The metric is turned
    fragile-increasing (flip sign when fragile-when-low), so a positive result
    means more-fragile names had higher ROI; flipping the sign changes neither
    the correlation's magnitude nor its p-value. Both are None if <3 usable
    pairs (or the correlation is undefined, e.g. a constant column) --
    ``scipy.stats.spearmanr`` needs at least 2 points to run at all and returns
    ``nan`` for a degenerate (constant) input rather than raising."""
    pairs = [
        (v if fragile_high else -v, r) for v, r in zip(values, rois, strict=True) if v is not None
    ]
    if len(pairs) < 3:
        return None, None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    result = spearmanr(xs, ys)
    corr, pvalue = float(result.statistic), float(result.pvalue)
    if math.isnan(corr) or math.isnan(pvalue):
        return None, None
    return corr, pvalue


def _run_screen(
    metric: str,
    values: list[float | None],
    *,
    fragile_high: bool,
    scored: list[_Scored],
    timeline: pd.Series,
    notional: float,
    top_k: int,
    baseline_roi: float,
    years: float,
) -> MetricScreenEntry:
    """Rank the scored names by ``metric`` in its fragile direction, hold an
    equal-weight OOM-put basket on the top ``top_k``, and summarize it."""
    # Fragile direction: higher fragile-adjusted value = more fragile.
    ranked = sorted(
        ((i, v if fragile_high else -v) for i, v in enumerate(values) if v is not None),
        key=lambda t: t[1],
        reverse=True,
    )
    picks = [scored[i] for i, _ in ranked[:top_k]]

    # Pool the picks' cycles at equal weight, mirroring run_portfolio: each name
    # gets budget notional/len(picks); its unit cycles scale by per-roll budget
    # s = budget / n_cycles so its total premium equals its budget.
    pooled: list[PutRollCycle] = []
    if picks:
        budget = notional / len(picks)
        for name in picks:
            s = budget / name.result.n_cycles
            pooled.extend(_scaled(c, s) for c in name.result.cycles)

    slices, verdict = regime_breakdown(pooled, timeline)

    # payoff - net - cost == budget (net is net of brokerage); ROI is on that
    # premium budget and net of brokerage (== sum(net)/budget).
    total_premium = sum(c.payoff - c.net - c.cost for c in pooled)
    total_net = sum(c.net for c in pooled)
    roi = total_net / total_premium if total_premium > 0 else 0.0
    hit_rate = sum(1 for c in pooled if c.net > 0.0) / len(pooled) if pooled else 0.0

    # Combined bleed: accumulate realized net at each expiry DATE (summing any
    # cycles that share a date first, so a same-day pair can't fabricate an
    # intra-step trough that never existed at a real timestamp), then the
    # curve's max drawdown. This matches run_portfolio's union-date semantics.
    net_by_date: dict[dt.date, float] = {}
    for c in pooled:
        net_by_date[c.expiry_date] = net_by_date.get(c.expiry_date, 0.0) + c.net
    cum = 0.0
    cum_values: list[float] = []
    for d in sorted(net_by_date):
        cum += net_by_date[d]
        cum_values.append(cum)
    combined_dd = _max_drawdown(cum_values)

    spearman, pvalue = _spearman(
        values, [s.result.roi_on_premium for s in scored], fragile_high=fragile_high
    )
    return MetricScreenEntry(
        metric=metric,
        label=_LABELS[metric],
        top_k_assets=[p.symbol for p in picks],
        roi_on_premium=roi,
        annualized_return=annualized_return(roi, years),
        hit_rate=hit_rate,
        combined_max_drawdown=combined_dd,
        verdict=verdict,
        regime_slices=slices,
        spearman_vs_payoff=spearman,
        spearman_pvalue=pvalue,
        significant_raw=pvalue is not None and pvalue < _FDR_ALPHA,
        # Corrected across all screens once every entry in the comparison
        # exists -- compare_metric_screens patches this in via model_copy.
        significant_corrected=False,
        lift_vs_baseline=roi - baseline_roi,
    )


def compare_metric_screens(
    store: LakeStore,
    *,
    symbols: tuple[str, ...],
    as_of: dt.date,
    moneyness_pct: float,
    tenor_weeks: float,
    years: float,
    top_k: int = 5,
    notional: float = 1000.0,
) -> MetricScreenComparison:
    """Bake off the seven fragility screens over ``symbols`` as of ``as_of``.

    For each metric, hold an equal-weight OOM-put basket on its ``top_k`` most
    fragile names and report the basket's blended result, so you can pick the
    screen that best sorted realized put payoffs over the lookback. **This is an
    in-sample cross-sectional association, not a forward predictive backtest**
    (see the module docstring): the metric and the payoff are measured over the
    same historical window.

    Point-in-time: the regime timeline, the SPY benchmark, and every price path
    are read as of ``as_of``. A name with no OHLCV as-of, or too short a window
    for one roll, is skipped. Raises ``LookupError`` if the VIX regime timeline
    is missing, if the SPY benchmark is absent (the metrics can't be computed
    without it), or if no name could be scored.
    """
    timeline = compute_regime_timeline(store, as_of=as_of)  # LookupError if no VIX
    lookback_days = round(years * 252)
    # timeline above already proved a VIX snapshot exists as of `as_of`, so
    # this cannot raise LookupError here.
    vol_changes = load_vix_close(store, as_of=as_of).pct_change().dropna()

    try:
        bench_prices, _ = load_asof_series(store, BENCHMARK, as_of)
    except LookupError as exc:
        raise LookupError(
            f"no {BENCHMARK.upper()} benchmark known as of {as_of.isoformat()}; "
            "fragility metrics cannot be computed"
        ) from exc
    bench_ret = _returns(bench_prices)

    def _fragility(asset_prices: pd.Series) -> dict[str, float | None]:
        aligned = pd.concat({"a": _returns(asset_prices), "b": bench_ret}, axis=1).dropna()
        window = aligned.iloc[-lookback_days:]
        a, b = window["a"], window["b"]

        def _safe(fn: Callable[[pd.Series, pd.Series], float]) -> float | None:
            try:
                return fn(a, b)
            except ValueError:
                return None

        values: dict[str, float | None] = {name: _safe(fn) for name, fn in _METRIC_FUNCS.items()}

        # Vol beta regresses against VIX changes, not the SPY benchmark, so it
        # aligns and windows its own pair rather than reusing (a, b) above.
        vol_aligned = pd.concat({"a": _returns(asset_prices), "v": vol_changes}, axis=1).dropna()
        vol_window = vol_aligned.iloc[-lookback_days:]
        try:
            values["vol_beta"] = vol_beta(vol_window["a"], vol_window["v"])
        except ValueError:
            values["vol_beta"] = None

        return values

    scored: list[_Scored] = []
    for symbol in symbols:
        try:
            prices, realized_vol_proxy = load_asof_series(store, symbol, as_of)
            # Unit notional: outputs scale linearly, so we backtest once at 1.0
            # and scale into each basket's budget (mirrors run_portfolio).
            result = run_put_roll(
                prices,
                realized_vol_proxy,
                asset=symbol,
                as_of=as_of,
                notional=1.0,
                moneyness_pct=moneyness_pct,
                tenor_weeks=tenor_weeks,
                lookback_years=years,
            )
        except LookupError:
            continue  # no data / too short a window for this name -> skip
        scored.append(_Scored(symbol, _fragility(prices), None, result))

    if not scored:
        raise LookupError(f"no universe name could be scored as of {as_of.isoformat()}")

    # Cross-sectional fragility rank of every metric (all six are ranked so
    # each gets a bake-off row), but the composite averages only the
    # COMPOSITE_METRICS -- identical to how rank_universe builds fragility_score,
    # so the "Composite fragility" row here matches the shipped screen exactly
    # (co-kurtosis excluded; see ranking.COMPOSITE_METRICS).
    per_metric_rank = {
        name: _frac_rank([s.metrics[name] for s in scored], fragile_high=_FRAGILE_HIGH[name])
        for name in _ALL_METRIC_NAMES
    }
    for i, s in enumerate(scored):
        parts = [
            per_metric_rank[name][i] for name in COMPOSITE_METRICS if i in per_metric_rank[name]
        ]
        s.fragility_score = sum(parts) / len(parts) if parts else None

    baseline_roi = sum(s.result.roi_on_premium for s in scored) / len(scored)

    entries: list[MetricScreenEntry] = []
    for name in _ALL_METRIC_NAMES:
        entries.append(
            _run_screen(
                name,
                [s.metrics[name] for s in scored],
                fragile_high=_FRAGILE_HIGH[name],
                scored=scored,
                timeline=timeline,
                notional=notional,
                top_k=top_k,
                baseline_roi=baseline_roi,
                years=years,
            )
        )
    entries.append(
        _run_screen(
            _COMPOSITE,
            [s.fragility_score for s in scored],
            fragile_high=True,  # composite is 0..1 with 1 = most fragile
            scored=scored,
            timeline=timeline,
            notional=notional,
            top_k=top_k,
            baseline_roi=baseline_roi,
            years=years,
        )
    )
    entries.sort(key=lambda e: e.roi_on_premium, reverse=True)
    entries, n_comparisons = _correct_for_multiple_testing(entries)

    return MetricScreenComparison(
        as_of=as_of,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=years,
        top_k=top_k,
        universe_size=len(scored),
        baseline_roi=baseline_roi,
        n_comparisons=n_comparisons,
        fdr_alpha=_FDR_ALPHA,
        entries=entries,
    )


def _correct_for_multiple_testing(
    entries: list[MetricScreenEntry],
) -> tuple[list[MetricScreenEntry], int]:
    """Benjamini-Hochberg FDR correction across every screen with a defined
    p-value (`docs/END_STATE.md` §4 Q1) -- run once over the full set of
    Spearman tests, not per-entry, since the correction is only meaningful
    relative to how many hypotheses were tested together. Returns the entries
    with `significant_corrected` filled in, plus how many had a p-value to
    correct (`MetricScreenComparison.n_comparisons`)."""
    tested = [
        (i, e.spearman_pvalue) for i, e in enumerate(entries) if e.spearman_pvalue is not None
    ]
    if not tested:
        return entries, 0
    indices, pvalues = zip(*tested, strict=True)
    for i, significant in zip(
        indices, benjamini_hochberg(list(pvalues), alpha=_FDR_ALPHA), strict=True
    ):
        entries[i] = entries[i].model_copy(update={"significant_corrected": significant})
    return entries, len(tested)
