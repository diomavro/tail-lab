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

from pydantic import BaseModel

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.contracts.options_calendar import cadence_for
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import (
    EquityPoint,
    PutRollCycle,
    load_asof_series,
    run_put_roll,
)
from tail_lab.research.backtest.regime_verdict import regime_breakdown
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
    verdict: Verdict
    n_cycles: int


class PortfolioResult(BaseModel):
    as_of: dt.date
    notional: float
    lookback_years: float
    total_premium: float
    total_payoff: float
    net_pnl: float
    roi_on_premium: float
    combined_max_drawdown: float
    sum_individual_max_drawdown: float
    verdict: Verdict
    legs: list[LegResult]
    equity_curve: list[EquityPoint]
    snapshot_ids: list[str]


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
    payoff, and net scale linearly; premium (per-put model price) is unchanged
    but unused downstream (regime_breakdown recovers premium as payoff-net)."""
    return cycle.model_copy(
        update={
            "contracts": cycle.contracts * s,
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
) -> PortfolioResult:
    """Backtest a weighted mix of OOM-put legs as of ``as_of``.

    Raises ``ValueError`` if there are no legs or the weights sum to zero, and
    ``LookupError`` if the VIX regime timeline is missing or no leg can be
    scored (every leg lacks data / too short a window).
    """
    if not legs:
        raise ValueError("a portfolio needs at least one leg")
    total_weight = sum(leg.weight for leg in legs)
    if total_weight <= 0:
        raise ValueError("leg weights must sum to a positive number")

    timeline = compute_regime_timeline(store, as_of=as_of)

    leg_results: list[LegResult] = []
    pooled_cycles: list[PutRollCycle] = []
    per_leg_cum: list[list[tuple[dt.date, float]]] = []
    snapshot_ids: list[str] = [store.bronze_snapshot_id(VIX_DATASET, as_of)]

    for leg in legs:
        share = leg.weight / total_weight
        try:
            prices, iv_proxy = load_asof_series(store, leg.asset, as_of)
            unit = run_put_roll(
                prices,
                iv_proxy,
                asset=leg.asset,
                as_of=as_of,
                notional=1.0,
                moneyness_pct=leg.moneyness_pct,
                tenor_weeks=leg.tenor_weeks,
                lookback_years=years,
            )
        except LookupError:
            continue  # skip a leg with no data / too short a window

        leg_budget = share * notional
        s = leg_budget / unit.n_cycles  # per-roll budget
        scaled = [_scaled(c, s) for c in unit.cycles]
        pooled_cycles.extend(scaled)

        leg_payoff = sum(c.payoff for c in scaled)
        leg_net = leg_payoff - leg_budget
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

        leg_results.append(
            LegResult(
                asset=leg.asset,
                name=cadence_for(leg.asset).name,
                weight=share,
                total_premium=leg_budget,
                net_pnl=leg_net,
                roi_on_premium=leg_net / leg_budget,
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
    net_pnl = total_payoff - total_premium
    _, combined_verdict = regime_breakdown(pooled_cycles, timeline)
    sum_individual_dd = sum(_max_drawdown([c for _, c in curve]) for curve in per_leg_cum)

    return PortfolioResult(
        as_of=as_of,
        notional=notional,
        lookback_years=years,
        total_premium=total_premium,
        total_payoff=total_payoff,
        net_pnl=net_pnl,
        roi_on_premium=net_pnl / total_premium,
        combined_max_drawdown=_max_drawdown(combined_cum_values),
        sum_individual_max_drawdown=sum_individual_dd,
        verdict=combined_verdict,
        legs=leg_results,
        equity_curve=combined_curve,
        snapshot_ids=snapshot_ids,
    )
