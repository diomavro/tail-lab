"""The roll schedule — the platform's recommendations as placeable order intent.

This is the one artefact that crosses the wall in ``docs/adr/0007``. Research
lives on this side of it; execution (by hand, or by a separate process that
holds the brokerage credential and authors no code) lives on the other. Nothing
here talks to a broker, holds a credential, or places an order — it states what
the screen concluded, in units an executor can act on, and is explicit about
every place the backtest was silent.

Three deliberate refusals, each of which would be easy to paper over and wrong:

**It sizes in premium BUDGET, not contracts.** Every return this platform
reports divides by ``n_cycles x notional`` (concepts.ts ``roi_on_premium``), and
the model's premium is emphatically not the market's — measured median
market/model ratio runs 1.42x at 5% OTM to 21,663x at 20%
(``docs/MODEL_RESIDUAL.md``). An executor sizes from the real quote it can see;
the budget is the thing that must be held constant for the result to be
comparable to the backtest.

**Its strikes and expiries are targets, not contracts.** ``run_put_roll``
strikes at ``spot x (1 - moneyness/100)`` — an exact real number no chain
lists — and counts *trading* days rather than resolving a listed expiry. The
executor snaps both, and the schedule says so rather than emitting a date that
looks like a contract and is not one.

**It carries the model's own premium estimate** so that the gap between it and
the fill is measurable. That gap is the entire open question in
``docs/adr/0018``: it is what bounds the strategy search to 10% OOM today, and
paper fills are the cheapest way to keep measuring it forward.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from tail_lab.contracts.hypothesis import Verdict
from tail_lab.research.backtest.put_roll import (
    REALIZED_VOL_CAP,
    REALIZED_VOL_FLOOR,
    TRADING_DAYS_PER_WEEK,
)
from tail_lab.research.backtest.ranking import RankedAsset
from tail_lab.research.backtest.sizing import SizingMode
from tail_lab.research.option_pricer import BlackScholesPricer, OptionPricer

#: A flat stand-in rate, matching what the backtest prices with. The schedule
#: quotes the model premium only so the fill can be compared against it, so an
#: exact discount leg is not the point; ``docs/MODEL_RESIDUAL.md`` lists the
#: flat rate among the residual's known contributors.
SCHEDULE_RATE = 0.04

#: Standard US equity option multiplier. Contracts cover 100 shares.
CONTRACT_MULTIPLIER = 100

#: The smallest premium a US equity option can quote: $0.01 per share, i.e.
#: $1.00 per contract.
#:
#: Below this the model's number is not a price, it is a rounding artefact —
#: and because sizing divides a fixed budget by it, the implied position
#: explodes. Measured on the live screen: XLF at 6% OOM over 2 weeks priced at
#: $0.0010/share, implying 9,874 contracts for a $1,000 budget. None of those
#: could be bought.
#:
#: Note where this bites: 6% OOM is comfortably inside
#: ``sweep.MODEL_PRICED_MAX_MONEYNESS_PCT``. That bound (docs/adr/0018) is a
#: function of moneyness alone, calibrated on ~30-day SPY quotes, and a
#: two-week tenor collapses the premium far harder than the same strike at a
#: month. The bound wants to be a function of moneyness AND tenor — of how many
#: standard deviations the strike sits away, not raw percent. Until it is
#: measured rather than guessed, this floor is the factual half: whatever the
#: model says, an option below a penny cannot be traded.
MIN_TRADEABLE_PREMIUM = 0.01

BASIS = (
    "Model-priced and in sample: these parameters were chosen by looking at the same "
    "window they are scored on, and every premium behind them is a Black-Scholes price "
    "at a flat volatility, not a quote. This is a screen, not advice, and not a "
    "prediction. tail-lab never places a trade (docs/adr/0007)."
)

EXECUTION_NOTES = (
    "Strike is a target: the backtest strikes at spot x (1 - moneyness/100), which no "
    "chain lists. Snap to the nearest listed strike and record what you actually used.",
    "Expiry is a target: the backtest counts trading days "
    f"({TRADING_DAYS_PER_WEEK} per week) and never resolves a listed expiry. Snap to "
    "the nearest listed expiry at or after the target date.",
    "Size from the real quote, not from model_contracts: hold the premium budget "
    "constant so the result stays comparable to the backtest, and let the contract "
    "count fall out of what the market actually charges.",
    "Record the fill premium against model_premium. That difference is the skew gap "
    "docs/adr/0018 bounds the strategy search on, and a fill is a free measurement "
    "of it.",
    "A leg flagged model_premium_below_min_tick carries no contract count on purpose: "
    f"the model priced it under ${MIN_TRADEABLE_PREMIUM:.2f} per share, which is below "
    "the smallest increment a US equity option quotes in, so no position size follows "
    "from it. Size it from the real quote or skip it.",
)


class RollLeg(BaseModel):
    """One position to open, and what the screen expected of it.

    ``expected_*`` are the backtest's figures at *this* leg's strike and tenor —
    the same cell, never blended with the screened one — so a later comparison
    of realized against expected is like for like.
    """

    rank: int
    asset: str
    name: str
    action: Literal["BUY_PUT"] = "BUY_PUT"
    #: The position, in the units the screen reasons in.
    moneyness_pct: float
    tenor_weeks: float
    #: ...and in the units an order needs. Both are targets — see EXECUTION_NOTES.
    spot: float
    target_strike: float
    target_expiry: dt.date
    #: Cash to spend on premium per roll. The invariant, not the contract count.
    premium_budget: float
    #: What this platform's model thinks one share of that put costs today, and
    #: the contract count that would imply. ``None`` when no volatility estimate
    #: was available — an absent number rather than a guessed one.
    model_premium: float | None = None
    model_contracts: int | None = None
    #: True when ``model_premium`` fell below the minimum tradeable increment.
    #: ``model_contracts`` is then deliberately absent -- see
    #: MIN_TRADEABLE_PREMIUM.
    model_premium_below_min_tick: bool = False
    expected_annualized: float | None = None
    expected_roi_on_premium: float | None = None
    expected_hit_rate: float | None = None
    expected_verdict: Verdict | None = None


class RollSchedule(BaseModel):
    """A dated set of positions to open, and the cadence to re-open them on.

    ``schedule_id`` is a deterministic digest of the content: an executor
    dedupes on it, so re-fetching an unchanged schedule must not place the same
    orders twice, and a changed one must not be mistaken for one already placed.
    """

    schedule_id: str
    as_of: dt.date
    #: The screen that produced it, so a schedule can be reproduced from its own
    #: contents.
    screen_moneyness_pct: float
    screen_tenor_weeks: float
    lookback_years: float
    universe_size: int
    top_k: int
    premium_budget_per_leg: float
    legs: list[RollLeg]
    basis: str = BASIS
    execution_notes: list[str] = list(EXECUTION_NOTES)


def _digest(as_of: dt.date, notional: float, legs: Sequence[RollLeg]) -> str:
    payload = json.dumps(
        {
            "as_of": as_of.isoformat(),
            "notional": notional,
            "legs": [
                [leg.asset, leg.moneyness_pct, leg.tenor_weeks, round(leg.target_strike, 4)]
                for leg in legs
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_roll_schedule(
    ranked: Sequence[RankedAsset],
    *,
    as_of: dt.date,
    notional: float,
    top_k: int,
    sigma_by_asset: Mapping[str, float],
    screen_moneyness_pct: float = 0.0,
    screen_tenor_weeks: float = 0.0,
    lookback_years: float = 0.0,
    pricer: OptionPricer | None = None,
    sizing_mode: SizingMode | None = None,
) -> RollSchedule:
    """Assemble the top ``top_k`` strategies into placeable order intent.

    Pure: takes the ranking already computed and a volatility estimate per
    asset, and reads nothing. ``sigma_by_asset`` may omit a name, in which case
    that leg carries no model premium rather than a fabricated one.

    ``sizing_mode``, when given, resolves the per-leg premium budget and
    ``notional`` is ignored; it resolves with ``n_legs`` equal to the actual
    number of legs the schedule ends up with (``min(top_k, len(scorable))``),
    not ``top_k`` itself, since a thin universe can produce fewer scorable
    legs than asked for. The resolution happens here, before any ``RollLeg``
    is built, precisely because this module's own docstring requires every
    number an executor reads to be a concrete cash figure, never a live
    formula -- see ``research/backtest/sizing.py``. Leaving it ``None`` (the
    default) uses ``notional`` for every leg exactly as before.
    """
    pricer = pricer or BlackScholesPricer()

    scorable = [
        r
        for r in ranked
        if r.best_annualized is not None
        and r.best_moneyness_pct is not None
        and r.best_tenor_weeks is not None
    ]
    scorable.sort(key=lambda r: -(r.best_annualized or 0.0))
    n_legs = min(top_k, len(scorable))
    budget = (
        sizing_mode.resolve(n_legs=n_legs) if sizing_mode is not None and n_legs > 0 else notional
    )

    legs: list[RollLeg] = []
    for i, r in enumerate(scorable[:top_k]):
        moneyness = float(r.best_moneyness_pct or 0.0)
        tenor = float(r.best_tenor_weeks or 0.0)
        strike = r.spot * (1.0 - moneyness / 100.0)

        model_premium: float | None = None
        model_contracts: int | None = None
        below_tick = False
        sigma = sigma_by_asset.get(r.asset)
        if sigma is not None and sigma > 0 and strike > 0 and tenor > 0:
            # Clamped exactly as run_put_roll clamps it, so the number quoted
            # here is the number the backtest would have priced with.
            clamped = float(min(max(sigma, REALIZED_VOL_FLOOR), REALIZED_VOL_CAP))
            t_years = max(round(tenor * TRADING_DAYS_PER_WEEK), 1) / 252.0
            model_premium = pricer.price_put(
                spot=r.spot, strike=strike, t_years=t_years, r=SCHEDULE_RATE, sigma=clamped
            )
            below_tick = model_premium < MIN_TRADEABLE_PREMIUM
            if model_premium > 0 and not below_tick:
                # Same floor as the backtest: at least one contract, so the fill
                # can land either side of the budget.
                model_contracts = max(1, int(budget // (model_premium * CONTRACT_MULTIPLIER)))

        legs.append(
            RollLeg(
                rank=i + 1,
                asset=r.asset,
                name=r.name,
                moneyness_pct=moneyness,
                tenor_weeks=tenor,
                spot=r.spot,
                target_strike=strike,
                # Calendar weeks forward. The backtest's own horizon is in
                # trading days, which is why this is a target -- see
                # EXECUTION_NOTES.
                target_expiry=as_of + dt.timedelta(weeks=int(tenor))
                if float(tenor).is_integer()
                else as_of + dt.timedelta(days=round(tenor * 7)),
                premium_budget=budget,
                model_premium=model_premium,
                model_contracts=model_contracts,
                model_premium_below_min_tick=below_tick,
                expected_annualized=r.best_annualized,
                expected_roi_on_premium=r.best_roi_on_premium,
                expected_hit_rate=r.best_hit_rate,
                expected_verdict=r.best_verdict,
            )
        )

    return RollSchedule(
        schedule_id=_digest(as_of, budget, legs),
        as_of=as_of,
        screen_moneyness_pct=screen_moneyness_pct,
        screen_tenor_weeks=screen_tenor_weeks,
        lookback_years=lookback_years,
        universe_size=len(ranked),
        top_k=top_k,
        premium_budget_per_leg=budget,
        legs=legs,
    )
