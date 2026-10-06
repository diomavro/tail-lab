"""The Book's model-priced plan: spend $X on puts every month (``docs/adr/0027`` §3).

Cboe's real-quote programs cannot express "spend X on puts" -- they publish an
index level, not the premium paid -- so this is the one place the Book prices
puts itself. It is opt-in, and every result carries the measured size of the
model's error, because a model that flatters puts is exactly the wrong error
to trade on. This is the *contribution-funded* accounting (``docs/adr/0027``
§2): the puts are paid for out of each month's new cash, not out of the book.

**The pricer** is Black-Scholes on the S&P 500, rolled monthly on the
third-Friday schedule (the ``index_replication`` schedule), at the VIX **plus
the measured skew gap** for the strike depth: ``docs/MODEL_RESIDUAL.md``
found the market's implied vol at 5% OTM 2.2 vol points above the model's,
and 7.2 at 10%. Only those two depths are offered. Adding the gap, rather
than multiplying premiums by the median market/model *price* ratio, is what
keeps it honest across regimes: checked against 214 real monthly SPY put
mids 2008-2025 (``scripts/model_plan_calibration.py``), the ratio approach
paid 4.8x the market at 10% OTM when the VIX was above 28 (30.6% of spot in
October 2008 against a 3.9% mid) and 0.23x in calm months; the vol gap pays
0.80-1.13x across every regime. It still **underprices calm-market puts by
~16-20%**, which flatters the puts, and the page says so.

``put_roll`` is deliberately NOT used: its realized-vol proxy floors at 6% and
priced a calm-market 5% OTM SPY put at ~$0.03 against a ~$1 market.

**The plan.** ``E0`` buys the S&P 500 (total return at an assumed dividend
yield) at the first roll. At every roll the investor pays in ``X`` and buys
``X`` of puts ``moneyness_pct`` below spot; at the next roll the payoff is
reinvested in the S&P 500. The comparator receives the same cash on the same
dates. No brokerage is modelled. ``E0`` must be positive: with no starting
book the puts arm is a standalone put, which the Workspace covers.

**Granularity.** Wealth and drawdown are measured at monthly roll dates, where
every expired put has paid out and the new one is worth what it just cost;
open puts are not marked between rolls. Monthly sampling misses intra-month
troughs (up to ~7 points shallower than daily in 2008), and the page says so.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.contribution_plan import (
    PlanArm,
    PlanWindow,
    RollingSummary,
    summarize,
    xirr,
)
from tail_lab.research.backtest.hedge_overlay import (
    OverlayDataMissing,
    outcome,
    total_return_levels,
)
from tail_lab.research.backtest.index_replication import (
    DAYS_PER_YEAR,
    DEFAULT_DIVIDEND_YIELD,
    STRIKE_INCREMENT,
    UNDERLYING_SYMBOL,
    roll_schedule,
    series_from,
)
from tail_lab.research.backtest.put_roll import DEFAULT_RATE
from tail_lab.research.option_pricer import BlackScholesPricer

#: The measured implied-vol gap the market charges over the VIX, by strike
#: depth (``docs/MODEL_RESIDUAL.md``: model IV 19.8% vs market 22.0% at 5% OTM,
#: 27.0% at 10%). The only depths offered.
MEASURED_VOL_GAP: dict[float, float] = {5.0: 0.022, 10.0: 0.072}


@dataclass(frozen=True)
class Calibration:
    """How the calibrated premium compared with real SPY put mids, by VIX
    regime: median of plan premium / market mid over 214 monthly rolls,
    2008-2025 (``scripts/model_plan_calibration.py``)."""

    calm: float  # VIX < 17
    normal: float  # 17 <= VIX < 28
    stress: float  # VIX >= 28


CALIBRATION: dict[float, Calibration] = {
    5.0: Calibration(calm=0.84, normal=0.99, stress=1.06),
    10.0: Calibration(calm=0.80, normal=1.04, stress=1.13),
}


class ModelAccuracy(BaseModel):
    """The model's measured error, shown wherever a model-plan number is."""

    moneyness_pct: float
    vol_gap: float
    calm_ratio: float
    stress_ratio: float
    statement: str


@dataclass(frozen=True)
class PutLeg:
    """One monthly put, per unit of the index."""

    entry: dt.date
    expiry: dt.date
    model_premium: float  # Black-Scholes at the VIX
    premium: float  # Black-Scholes at the VIX + the measured skew gap: what is paid
    payoff: float


def accuracy_for(moneyness_pct: float) -> ModelAccuracy:
    """The measured error for an offered depth; ``ValueError`` otherwise."""
    if moneyness_pct not in MEASURED_VOL_GAP:
        raise ValueError(f"model plan offers only {sorted(MEASURED_VOL_GAP)}% OTM")
    gap = MEASURED_VOL_GAP[moneyness_pct]
    cal = CALIBRATION[moneyness_pct]
    return ModelAccuracy(
        moneyness_pct=moneyness_pct,
        vol_gap=gap,
        calm_ratio=cal.calm,
        stress_ratio=cal.stress,
        statement=(
            f"Model-priced: Black-Scholes at the VIX plus the measured skew gap "
            f"(+{gap * 100:.1f} vol points at {moneyness_pct:g}% OTM, docs/MODEL_RESIDUAL.md), at a "
            f"flat 4% rate. Checked against 214 real monthly SPY put mids (2008-2025), it pays a "
            f"median {cal.calm:.2f}x the market in calm months (VIX below 17) -- most so in the "
            "zero-rate years, and less than a buyer pays at the ask -- which flatters the puts, "
            f"and {cal.stress:.2f}x in stress (VIX 28 and up). Drawdowns are sampled at monthly "
            "rolls, so they read shallower than daily ones. An estimate, not a quote."
        ),
    )


def price_legs(
    spx: pd.Series,
    vix: pd.Series,
    *,
    moneyness_pct: float,
    dividend_yield: float,
    rate: float = DEFAULT_RATE,
) -> list[PutLeg]:
    """Monthly puts on the S&P 500 priced at the VIX plus the measured skew gap
    for the depth; third-Friday rolls.

    The schedule is built over the dates both series share; a VIX close in
    points is converted to a fraction. Strikes snap to the listed increment.
    """
    common = pd.DatetimeIndex(spx.index).intersection(pd.DatetimeIndex(vix.index))
    schedule = roll_schedule(common, rolls_per_year=12)
    pricer = BlackScholesPricer()
    gap = accuracy_for(moneyness_pct).vol_gap
    legs: list[PutLeg] = []
    for start, end in pairwise(schedule):
        spot = float(spx.loc[start])
        sigma = float(vix.loc[start]) / 100.0
        t_years = (end - start).days / DAYS_PER_YEAR
        strike = round(spot * (1.0 - moneyness_pct / 100.0) / STRIKE_INCREMENT) * STRIKE_INCREMENT
        model_premium = pricer.price_put(
            spot=spot, strike=strike, t_years=t_years, r=rate, sigma=sigma, q=dividend_yield
        )
        premium = pricer.price_put(
            spot=spot, strike=strike, t_years=t_years, r=rate, sigma=sigma + gap, q=dividend_yield
        )
        legs.append(
            PutLeg(
                entry=start.date(),
                expiry=end.date(),
                model_premium=model_premium,
                premium=premium,
                payoff=max(strike - float(spx.loc[end]), 0.0),
            )
        )
    return legs


def _level(levels: pd.Series, day: dt.date) -> float:
    return float(levels.loc[pd.Timestamp(day)])


def run_model_window(
    legs: Sequence[PutLeg],
    spx_tr: pd.Series,
    *,
    first: int,
    count: int,
    e0: float,
    monthly: float,
    put_share: float = 1.0,
) -> PlanWindow:
    """Both arms over ``count`` consecutive monthly legs from ``legs[first]``;
    the window ends at the last leg's expiry. ``put_share`` of each month's
    ``X`` buys puts and the rest buys the index."""
    if count < 1 or first < 0 or first + count > len(legs):
        raise ValueError("window outside the roll schedule")
    if e0 < 0 or monthly < 0 or e0 + monthly <= 0:
        raise ValueError("a plan needs a non-negative E0 and X, not both zero")
    if not 0.0 < put_share <= 1.0:
        raise ValueError("put_share must be in (0, 1]")
    window = list(legs[first : first + count])
    start, end = window[0].entry, window[-1].expiry
    level_end = _level(spx_tr, end)

    paid = np.full(count, monthly)
    paid[0] += e0
    years = np.array([(end - leg.entry).days / DAYS_PER_YEAR for leg in window])
    on_puts = monthly * put_share
    units_bought = [on_puts / leg.premium if leg.premium > 0 else 0.0 for leg in window]

    c_units = sum(p / _level(spx_tr, leg.entry) for p, leg in zip(paid, window, strict=True))
    h_units = (
        e0 / _level(spx_tr, start)
        + sum((monthly - on_puts) / _level(spx_tr, leg.entry) for leg in window)
        + sum(
            n * leg.payoff / _level(spx_tr, leg.expiry)
            for n, leg in zip(units_bought, window, strict=True)
        )
    )
    h_terminal = float(h_units * level_end)
    c_terminal = float(c_units * level_end)
    h_dd, c_dd = _roll_date_drawdowns(
        window, spx_tr, e0=e0, monthly=monthly, on_puts=on_puts, units=units_bought
    )
    h_irr = xirr(paid, years, h_terminal)
    c_irr = xirr(paid, years, c_terminal)
    gap = h_irr - c_irr
    contributed = float(paid.sum())
    return PlanWindow(
        start=start,
        end=end,
        years=float(years[0]),
        hedged=PlanArm(
            label=f"S&P 500 + {put_share:.0%} of each month on puts (model)",
            contributed=contributed,
            terminal_wealth=h_terminal,
            irr=h_irr,
            max_drawdown=h_dd,
        ),
        comparator=PlanArm(
            label="S&P 500 only",
            contributed=contributed,
            terminal_wealth=c_terminal,
            irr=c_irr,
            max_drawdown=c_dd,
        ),
        irr_gap=gap,
        outcome=outcome(gap),
    )


def _roll_date_drawdowns(
    legs: Sequence[PutLeg],
    spx_tr: pd.Series,
    *,
    e0: float,
    monthly: float,
    on_puts: float,
    units: Sequence[float],
) -> tuple[float, float]:
    """Time-weighted drawdown of each arm, sampled at each roll date.

    Legs are back to back (each expiry is the next entry), so at roll ``i``
    the put bought at roll ``i-1`` has just paid out into the index and a new
    one is bought at cost ``X``. Each period's return is value before today's
    cash over value after the last cash, so a contribution never reads as a
    gain.
    """
    samples = [leg.entry for leg in legs] + [legs[-1].expiry]
    h_units = c_units = 0.0
    h_after = c_after = 0.0
    h_index = [1.0]
    c_index = [1.0]
    for i, day in enumerate(samples):
        level = _level(spx_tr, day)
        if i > 0:
            h_units += units[i - 1] * legs[i - 1].payoff / level
            h_index.append(h_index[-1] * h_units * level / h_after)
            c_index.append(c_index[-1] * c_units * level / c_after)
        if i < len(legs):
            cash = monthly + (e0 if i == 0 else 0.0)
            h_units += (cash - on_puts) / level
            c_units += cash / level
            h_after = h_units * level + on_puts
            c_after = c_units * level
    return _max_drawdown(h_index), _max_drawdown(c_index)


def _max_drawdown(index: Sequence[float]) -> float:
    path = np.asarray(index, dtype=float)
    return float((path / np.maximum.accumulate(path) - 1.0).min())


def model_rolling(
    legs: Sequence[PutLeg],
    spx_tr: pd.Series,
    *,
    horizon_years: int,
    e0: float,
    monthly: float,
    put_share: float = 1.0,
) -> RollingSummary:
    """The plan begun at every monthly roll that leaves a full horizon."""
    count = 12 * horizon_years
    gaps: list[float] = []
    begun: list[dt.date] = []
    for first in range(max(len(legs) - count + 1, 0)):
        w = run_model_window(
            legs, spx_tr, first=first, count=count, e0=e0, monthly=monthly, put_share=put_share
        )
        gaps.append(w.irr_gap)
        begun.append(w.start)
    return summarize(gaps, begun, horizon_years)


class ModelPlanResult(BaseModel):
    """The contribution-funded plan, as of one date, with its error bar."""

    as_of: dt.date
    accounting: str
    moneyness_pct: float
    e0: float
    monthly: float
    put_share: float
    horizon_years: int
    dividend_yield: float
    accuracy: ModelAccuracy
    window: PlanWindow | None
    rolling: RollingSummary | None
    refusal: str | None


ACCOUNTING = (
    "contribution-funded: a share of each month's X buys puts, the rest the S&P 500; "
    "payoffs reinvested in the S&P 500"
)


def run_model_plan(
    spx: pd.Series,
    vix: pd.Series,
    *,
    as_of: dt.date,
    moneyness_pct: float,
    e0: float,
    monthly: float,
    horizon_years: int,
    put_share: float = 1.0,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
) -> ModelPlanResult:
    """Price the legs (cut at ``as_of``), then the most recent full window and
    the rolling verdict. A too-short history is a refusal, not a crash."""
    accuracy = accuracy_for(moneyness_pct)
    if monthly <= 0:
        raise ValueError("the model plan spends X on puts every month; X must be positive")
    if e0 <= 0:
        raise ValueError(
            "the model plan needs a starting book; with none the puts arm is a standalone put "
            "(the Workspace covers that)"
        )
    cutoff = pd.Timestamp(as_of)
    spx = spx[spx.index <= cutoff]
    vix = vix[vix.index <= cutoff]
    legs = price_legs(spx, vix, moneyness_pct=moneyness_pct, dividend_yield=dividend_yield)
    spx_tr = total_return_levels(spx, dividend_yield=dividend_yield)
    base = {
        "as_of": as_of,
        "accounting": ACCOUNTING,
        "moneyness_pct": moneyness_pct,
        "e0": e0,
        "monthly": monthly,
        "put_share": put_share,
        "horizon_years": horizon_years,
        "dividend_yield": dividend_yield,
        "accuracy": accuracy,
    }
    count = 12 * horizon_years
    if len(legs) < count:
        return ModelPlanResult(
            **base,
            window=None,
            rolling=None,
            refusal=f"history is shorter than one {horizon_years}-year plan",
        )
    window = run_model_window(
        legs,
        spx_tr,
        first=len(legs) - count,
        count=count,
        e0=e0,
        monthly=monthly,
        put_share=put_share,
    )
    rolling = model_rolling(
        legs, spx_tr, horizon_years=horizon_years, e0=e0, monthly=monthly, put_share=put_share
    )
    return ModelPlanResult(**base, window=window, rolling=rolling, refusal=None)


def compute_model_plan(
    store: LakeStore,
    *,
    as_of: dt.date,
    moneyness_pct: float,
    e0: float,
    monthly: float,
    horizon_years: int,
    put_share: float = 1.0,
) -> ModelPlanResult:
    """Read SPX (Cboe) and the VIX point-in-time and run the model plan.

    Raises :class:`OverlayDataMissing` when either is absent; a store bug
    (``KeyError``) propagates.
    """
    reads: dict[str, pd.DataFrame] = {}
    for dataset in (CBOE_STRATEGY_DATASET, "vix"):
        try:
            reads[dataset] = store.read_bronze_as_of(dataset, as_of)
        except KeyError:
            raise
        except LookupError as exc:
            raise OverlayDataMissing(f"no {dataset} known as of {as_of.isoformat()}") from exc
    cboe = reads[CBOE_STRATEGY_DATASET]
    rows = cboe[cboe["index_symbol"] == UNDERLYING_SYMBOL]
    if rows.empty:
        raise OverlayDataMissing(f"no {UNDERLYING_SYMBOL} rows in the snapshot known as of {as_of}")
    spx = series_from(rows, date_col="trade_date", value_col="close")
    vix = series_from(reads["vix"], date_col="date", value_col="close")
    return run_model_plan(
        spx,
        vix,
        as_of=as_of,
        moneyness_pct=moneyness_pct,
        e0=e0,
        monthly=monthly,
        horizon_years=horizon_years,
        put_share=put_share,
    )
