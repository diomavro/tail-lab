"""The Put Lab's live regime breakdown (``docs/adr/0015``): split one
backtest's rolls by the market regime each was *entered* in, and classify the
rule — did it pay off across regimes (``confirmed``), in only one
(``regime_only``), or none (``failed``)?

This is the visible half of the memory layer: it needs no stored history, so
the dashboard can show a real ``regime_only`` verdict for whatever parameters
you're looking at right now. The persistent memory (``memory/store.py``)
records these same outcomes over time; both classify with the one shared
:func:`~tail_lab.contracts.hypothesis.verdict_from_passing_regimes`, so the
live view and the stored view can never disagree on the line.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.hypothesis import RuleSpec, Verdict, verdict_from_passing_regimes
from tail_lab.contracts.regime import REGIME_LABELS, RegimeLabel
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import PutRollCycle, compute_put_backtest
from tail_lab.research.regimes.timeline import compute_regime_timeline, regime_on_or_before


class RegimeSlice(BaseModel):
    """One regime's slice of a single backtest."""

    regime: RegimeLabel
    n_cycles: int
    roi_on_premium: float
    paid_off: bool
    hit_rate: float
    biggest_payoff_mult: float


class RegimeVerdict(BaseModel):
    """A rule's live standing, broken down by the regime each roll entered in."""

    asset: str
    as_of: dt.date
    rule_hash: str
    verdict: Verdict
    slices: list[RegimeSlice]


def regime_breakdown(
    cycles: list[PutRollCycle], timeline: pd.Series
) -> tuple[list[RegimeSlice], Verdict]:
    """Group ``cycles`` by the regime in force at each entry date and compute
    per-regime ROI on premium, then classify the rule.

    A roll is attributed to the regime it was *bought into* (its entry date):
    that is the information available when the position is opened. Premium paid
    per roll is recovered as ``payoff - net`` (the fixed notional budget). A
    cycle whose entry predates the regime timeline is skipped (not
    mislabeled). Verdict is ``untested`` only when no cycle could be labeled.
    """
    buckets: dict[RegimeLabel, list[PutRollCycle]] = defaultdict(list)
    for cycle in cycles:
        try:
            regime = regime_on_or_before(timeline, cycle.entry_date)
        except LookupError:
            continue
        buckets[regime].append(cycle)

    slices: list[RegimeSlice] = []
    passing: set[RegimeLabel] = set()
    for regime in REGIME_LABELS:  # stable, stress-ordered output
        group = buckets.get(regime)
        if not group:
            continue
        total_payoff = sum(c.payoff for c in group)
        total_premium = sum(c.payoff - c.net for c in group)  # payoff - net == notional
        roi = (total_payoff - total_premium) / total_premium if total_premium > 0 else 0.0
        paid = roi > 0.0
        if paid:
            passing.add(regime)
        # Per-cycle premium is (payoff - net) == the notional budget; a cycle
        # "hits" when its payoff clears that budget.
        wins = sum(1 for c in group if c.net > 0.0)
        mults = [c.payoff / (c.payoff - c.net) for c in group if (c.payoff - c.net) > 0.0]
        slices.append(
            RegimeSlice(
                regime=regime,
                n_cycles=len(group),
                roi_on_premium=roi,
                paid_off=paid,
                hit_rate=wins / len(group),
                biggest_payoff_mult=max(mults, default=0.0),
            )
        )

    verdict: Verdict = "untested" if not slices else verdict_from_passing_regimes(passing)
    return slices, verdict


def compute_regime_verdict(
    store: LakeStore,
    *,
    asset: str,
    as_of: dt.date,
    notional: float,
    moneyness_pct: float,
    tenor_weeks: float,
    years: float,
) -> RegimeVerdict:
    """Run the backtest, label its rolls by VIX regime as of ``as_of``, and
    return the per-regime breakdown + verdict. Raises ``LookupError`` if the
    asset OHLCV or the VIX regime timeline is missing as of that date."""
    result = compute_put_backtest(
        store,
        asset=asset,
        as_of=as_of,
        notional=notional,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=years,
    )
    timeline = compute_regime_timeline(store, as_of=as_of)
    slices, verdict = regime_breakdown(result.cycles, timeline)
    spec = RuleSpec(
        asset=asset,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=round(years),
    )
    return RegimeVerdict(
        asset=asset, as_of=as_of, rule_hash=spec.rule_hash(), verdict=verdict, slices=slices
    )
