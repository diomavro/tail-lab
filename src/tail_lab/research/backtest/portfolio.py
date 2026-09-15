"""Portfolio-of-puts backtest (``docs/END_STATE.md`` §1) — combine a mix of
OOM-put legs into one model-priced hedge and see the blended result.

Design decisions from the v3 plan review:

- **Weight = share of total capital over the window**, not per-roll budget. A
  1-week leg rolls ~4x as often as a 4-week leg, so equal per-roll budgets
  would deploy ~4x the capital into the short-tenor leg. Instead each leg's
  *total* premium over the window is ``weight x notional`` (weights normalized
  to sum to 1); its per-roll budget is that divided by its roll count. Because
  ``run_put_roll``'s outputs scale linearly with the per-roll notional, we run
  each leg once at unit notional and scale.
- **Combined bleed = max drawdown** of the summed cumulative-PnL curve on the
  union of event dates (forward-filled), not a cross-leg "streak" (undefined
  across interleaved expiries). We also report the sum of the legs' individual
  drawdowns, so diversification (combined DD < Σ individual DD) is visible.
- **Combined regime verdict = pool every leg's (scaled) cycles into one
  ``regime_breakdown``** — capital-weighted by construction (a larger-weight
  leg contributes larger per-cycle premium), reusing tested code unchanged.
- Realized-at-settlement (each leg realizes its net at its own expiries), same
  model as the single-name engine; a continuous daily mark-to-model is a
  separate future increment.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from pydantic import BaseModel

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.contracts.options_calendar import cadence_for
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import (
    EquityPoint,
    PutBacktestResult,
    PutRollCycle,
    annualized_return,
    load_asof_series,
    run_put_roll,
)
from tail_lab.research.backtest.regime_verdict import regime_breakdown
from tail_lab.research.backtest.sizing import SizingMode
from tail_lab.research.regimes.timeline import VIX_DATASET, compute_regime_timeline


class PortfolioLeg(BaseModel):
    asset: str
    moneyness_pct: float
    tenor_weeks: float
    weight: float  # relative; normalized to sum to 1 across legs


class LegResult(BaseModel):
    asset: str
    name: str
    weight: float  # normalized share of capital
    total_premium: float
    net_pnl: float
    roi_on_premium: float
    annualized_return: float  # geometric annualization of roi_on_premium over `years`
    verdict: Verdict
    n_cycles: int


class PortfolioResult(BaseModel):
    as_of: dt.date
    notional: float
    lookback_years: float
    #: What the basket was actually on risk for: earliest leg entry to latest
    #: leg expiry. `annualized_return` is paced by THIS, not by
    #: `lookback_years`, because a window the data could not fill is not a
    #: window the strategy traded. Reported rather than kept internal so a
    #: reader can see the two differ (README, "accuracy is surfaced").
    traded_start: dt.date | None = None
    traded_end: dt.date | None = None
    total_premium: float
    total_payoff: float
    net_pnl: float
    roi_on_premium: float
    annualized_return: float  # geometric annualization of roi_on_premium over lookback_years
    combined_max_drawdown: float
    sum_individual_max_drawdown: float
    verdict: Verdict
    legs: list[LegResult]
    equity_curve: list[EquityPoint]
    snapshot_ids: list[str]


def _traded_span(
    units: Sequence[PutBacktestResult],
) -> tuple[dt.date | None, dt.date | None]:
    """Earliest leg entry and latest leg expiry across the basket."""
    spans = [(u.traded_start, u.traded_end) for u in units if u.traded_start and u.traded_end]
    if not spans:
        return None, None
    return min(s for s, _ in spans), max(e for _, e in spans)


def _traded_years(units: Sequence[PutBacktestResult], fallback: float) -> float:
    """Span from the earliest leg entry to the latest leg expiry, in years.

    The union rather than an average: the basket was on risk from the moment
    its first leg opened until its last one settled, and that is the period the
    combined return has to be divided by. Falls back to the requested window
    only when no leg traded at all, where there is nothing else to use.
    """
    start, end = _traded_span(units)
    if start is None or end is None:
        return fallback
    return (end - start).days / 365.25


def _max_drawdown(cum: list[float]) -> float:
    """Largest peak-to-trough drop of a cumulative-PnL series (<= 0)."""
    peak = 0.0
    worst = 0.0
    for v in cum:
        peak = max(peak, v)
        worst = min(worst, v - peak)
    return worst


def _scaled(cycle: PutRollCycle, s: float) -> PutRollCycle:
    """Scale one unit-notional cycle to a per-roll budget of ``s``: contracts,
    cost, payoff, and net scale linearly; premium (per-put model price) is
    unchanged but unused downstream (regime_breakdown recovers premium as
    payoff - net - cost)."""
    return cycle.model_copy(
        update={
            "contracts": cycle.contracts * s,
            "cost": cycle.cost * s,
            "payoff": cycle.payoff * s,
            "net": cycle.net * s,
        }
    )


def run_portfolio(
    store: LakeStore,
    *,
    legs: list[PortfolioLeg],
    as_of: dt.date,
    notional: float,
    years: float,
    sizing_mode: SizingMode | None = None,
) -> PortfolioResult:
    """Backtest a weighted mix of OOM-put legs as of ``as_of``.

    Raises ``ValueError`` if there are no legs or the weights sum to zero, and
    ``LookupError`` if the VIX regime timeline is missing or no leg can be
    scored (every leg lacks data / too short a window).

    ``sizing_mode``, when given, resolves the *total* capital pool and
    ``notional`` is ignored; it resolves with ``n_legs=1`` because this
    function's own weight-based split (``leg.weight / total_weight``) is what
    divides that pool across legs -- resolving per-leg here would divide it
    twice. Leaving it ``None`` (the default) uses ``notional`` exactly as
    before -- see ``research/backtest/sizing.py``.
    """
    if not legs:
        raise ValueError("a portfolio needs at least one leg")
    total_weight = sum(leg.weight for leg in legs)
    if total_weight <= 0:
        raise ValueError("leg weights must sum to a positive number")
    budget = sizing_mode.resolve(n_legs=1) if sizing_mode is not None else notional

    timeline = compute_regime_timeline(store, as_of=as_of)

    leg_results: list[LegResult] = []
    units: list[PutBacktestResult] = []
    pooled_cycles: list[PutRollCycle] = []
    per_leg_cum: list[list[tuple[dt.date, float]]] = []
    snapshot_ids: list[str] = [store.bronze_snapshot_id(VIX_DATASET, as_of)]

    for leg in legs:
        share = leg.weight / total_weight
        try:
            prices, realized_vol_proxy = load_asof_series(store, leg.asset, as_of)
            unit = run_put_roll(
                prices,
                realized_vol_proxy,
                asset=leg.asset,
                as_of=as_of,
                notional=1.0,
                moneyness_pct=leg.moneyness_pct,
                tenor_weeks=leg.tenor_weeks,
                lookback_years=years,
            )
        except LookupError:
            continue  # skip a leg with no data / too short a window

        leg_budget = share * budget
        s = leg_budget / unit.n_cycles  # per-roll budget
        scaled = [_scaled(c, s) for c in unit.cycles]
        pooled_cycles.extend(scaled)

        leg_net = sum(c.net for c in scaled)  # net of brokerage
        _, leg_verdict = regime_breakdown(scaled, timeline)

        cum = 0.0
        curve: list[tuple[dt.date, float]] = []
        for c in sorted(scaled, key=lambda x: x.expiry_date):
            cum += c.net
            curve.append((c.expiry_date, cum))
        per_leg_cum.append(curve)

        snap = store.bronze_snapshot_id(f"ohlcv_{leg.asset.lower()}", as_of)
        if snap not in snapshot_ids:
            snapshot_ids.append(snap)

        units.append(unit)
        leg_results.append(
            LegResult(
                asset=leg.asset,
                name=cadence_for(leg.asset).name,
                weight=share,
                total_premium=leg_budget,
                net_pnl=leg_net,
                roi_on_premium=leg_net / leg_budget,
                # unit was rolled at the same `years` and roi_on_premium is
                # notional-scale-invariant, so unit's annualized figure already
                # matches this leg's (leg_net/leg_budget == unit.roi_on_premium).
                annualized_return=unit.annualized_return,
                verdict=leg_verdict,
                n_cycles=unit.n_cycles,
            )
        )

    if not leg_results:
        raise LookupError(f"no portfolio leg could be scored as of {as_of.isoformat()}")

    # Combined cumulative PnL on the union of expiry dates: forward-fill each
    # leg's realized cum (0 before its first expiry) and sum.
    union_dates = sorted({d for curve in per_leg_cum for d, _ in curve})
    combined_curve: list[EquityPoint] = []
    combined_cum_values: list[float] = []
    for d in union_dates:
        total = 0.0
        for curve in per_leg_cum:
            realized = [c for dd, c in curve if dd <= d]
            total += realized[-1] if realized else 0.0
        combined_curve.append(EquityPoint(date=d, cum_pnl=total))
        combined_cum_values.append(total)

    total_premium = sum(r.total_premium for r in leg_results)
    total_payoff = sum(c.payoff for c in pooled_cycles)
    net_pnl = sum(c.net for c in pooled_cycles)  # net of brokerage
    combined_roi = net_pnl / total_premium
    _, combined_verdict = regime_breakdown(pooled_cycles, timeline)
    sum_individual_dd = sum(_max_drawdown([c for _, c in curve]) for curve in per_leg_cum)

    return PortfolioResult(
        as_of=as_of,
        notional=budget,
        lookback_years=years,
        traded_start=_traded_span(units)[0],
        traded_end=_traded_span(units)[1],
        total_premium=total_premium,
        total_payoff=total_payoff,
        net_pnl=net_pnl,
        roi_on_premium=combined_roi,
        # Paced by what the basket actually traded, not by the window asked
        # for -- matching `run_put_roll`, which stopped pacing by the nominal
        # window on 2026-09-09. Leaving this on `years` while the legs moved
        # would make the combined figure disagree with its own components: the
        # legs take `unit.annualized_return` directly, so they are already on
        # the traded span. The basket's span is the union of its legs'.
        annualized_return=annualized_return(combined_roi, _traded_years(units, years)),
        combined_max_drawdown=_max_drawdown(combined_cum_values),
        sum_individual_max_drawdown=sum_individual_dd,
        verdict=combined_verdict,
        legs=leg_results,
        equity_curve=combined_curve,
        snapshot_ids=snapshot_ids,
    )
