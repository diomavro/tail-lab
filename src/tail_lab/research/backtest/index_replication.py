"""Replicate a published Cboe option-strategy index, and measure the residual.

This is the platform's answer to ``docs/adr/0004``'s admitted limitation:
every Put Lab premium is a **model** price, and until now nothing said how
far a model price sits from what the option actually cost. Cboe's PPUT rule
is public and deterministic — hold the S&P 500, buy a 5% OTM one-month put,
roll at each monthly expiration — and Cboe publishes the resulting index at
OPRA transaction prices. So running that exact rule with our own pricer and
differencing the two NAVs isolates one number:

    residual = (what our model says the strategy earns)
             - (what the strategy actually earned)

**That residual is the model-vs-market pricing gap the S1 thesis rests on**,
measured for free rather than inferred.

Everything here runs on the free Cboe indices already in bronze
(``cboe_strategy`` for the index levels, ``vix`` for the volatility input).
It deliberately does **not** require historical option chains: the one free
chain set that reaches 2008 is licence-gated (``HUMAN_TODO.md``), so tying
the harness to it would make the platform's central measurement hostage to a
decision the platform does not control.

What the replication does *not* match, stated up front — every item below
adds to the measured residual and none of them is a pricing error:

* **Settlement.** SPX monthlies settle against the AM opening SOQ; bronze
  ``cboe_strategy`` carries closes only, so this settles at the close.
  Measured cost of exactly this substitution: correlation 0.9927 → 0.9847 and
  tracking error +0.67%/yr (``docs/DATA_VERDICTS.md``).
* **Roll pricing.** Cboe buys at the 11:30-12:00 ET VWAP; this buys at the
  close.
* **Dividends.** ``SPXT`` (total return) is not served by the CDN (HTTP 403),
  so the equity leg's dividends are reconstructed from a flat assumed yield.
  This is the single biggest lever on the headline drag, which is why
  :func:`run_index_replication` reports a sensitivity across yields instead
  of one number.
* **Rates.** A flat ``rate`` stands in for a term structure that ranged from
  0% to 6.5% over the replication window.
* **Skew.** ``sigma`` is the VIX — a ~30-day ATM-ish implied vol — but the
  put being priced is 5% OTM, where implied vol is *higher*. This one is not
  a nuisance: it is the leading suspect for the residual's sign, and the
  reason ``AGENT_TODO.md`` queues a skew-aware pricer. A negative drag
  (replication beating the real index) is what systematic under-pricing of
  the put looks like.

An exact match is therefore not the goal, and claiming one would be wrong.
The goal is a residual small and stable enough that changes to the *pricer*
move it visibly.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Sequence
from itertools import pairwise

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.contracts.regime import RegimeLabel
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.put_roll import DEFAULT_RATE
from tail_lab.research.option_pricer import BlackScholesPricer, OptionPricer
from tail_lab.research.regimes.timeline import compute_regime_timeline

#: The index whose level the replication trades. Every Cboe strategy index
#: below is written on the S&P 500, so one underlying serves all of them.
UNDERLYING_SYMBOL = "SPX"

#: Assumed continuous dividend yield for the S&P 500 equity leg. The index's
#: realized yield ran ~1.2% to ~3.5% across the replication window; 1.9% is close
#: to its median. This is an *assumption*, not a measurement — see the module
#: docstring — so results carry a sensitivity across neighbouring yields.
DEFAULT_DIVIDEND_YIELD = 0.019

#: Dividend yields the headline run is re-run at, to show how much of the
#: reported drag is really the yield assumption.
SENSITIVITY_YIELDS: tuple[float, ...] = (0.014, 0.019, 0.024)

#: SPX listed strikes step in 5-point increments over the relevant range.
STRIKE_INCREMENT = 5.0

#: Calendar days per year used to convert a roll span into a pricing tenor.
DAYS_PER_YEAR = 365.25


class ReplicationProgram(BaseModel):
    """One published index and the rule that generates it."""

    index_symbol: str
    moneyness_pct: float
    #: ``12`` for a monthly roll, ``4`` for a quarterly one.
    rolls_per_year: int
    description: str


#: The programs this harness can replicate: two S&P 500 put-protection
#: overlays that differ in *both* tenor and strike (monthly 5% OTM vs quarterly
#: 10% OTM). That is deliberate coverage -- a pricing residual should be
#: checked along the two axes the volatility surface actually varies over.
PROGRAMS: dict[str, ReplicationProgram] = {
    "PPUT": ReplicationProgram(
        index_symbol="PPUT",
        moneyness_pct=5.0,
        rolls_per_year=12,
        description="S&P 500 + 5% OTM one-month put, rolled monthly",
    ),
    # Cboe names this one the "S&P 500 Tail Risk Index", which hides that it is
    # the quarterly sibling of PPUT -- and, crucially, that it is struck at
    # **10%** OTM, not 5%. Read the methodology, not the ticker.
    "PPUT3M": ReplicationProgram(
        index_symbol="PPUT3M",
        moneyness_pct=10.0,
        rolls_per_year=4,
        description="S&P 500 + 10% OTM quarterly put (Cboe S&P 500 Tail Risk Index)",
    ),
}


class ReplicationRoll(BaseModel):
    """One roll-to-roll span of the replication, against the real index."""

    roll_date: dt.date
    settle_date: dt.date
    spot: float
    strike: float
    sigma: float
    t_years: float
    premium: float
    premium_pct: float  # premium as a fraction of spot — the bleed per roll
    payoff: float
    replicated_return: float
    actual_return: float
    residual: float  # replicated - actual; positive = our model is too cheap
    regime: RegimeLabel | None = None


class ResidualBucket(BaseModel):
    """Residual aggregated over a slice of rolls (a year, or a regime)."""

    key: str
    n_rolls: int
    years: float
    mean_residual: float  # arithmetic, per roll
    annualized_drag: float  # geometric, replicated CAGR - actual CAGR
    tracking_error: float


class DividendSensitivityPoint(BaseModel):
    """Headline drag recomputed at one alternative dividend-yield assumption."""

    dividend_yield: float
    annualized_drag: float


class IndexReplicationResult(BaseModel):
    """Full replication of one published index over one window."""

    index_symbol: str
    description: str
    start: dt.date
    end: dt.date
    years: float
    n_rolls: int
    rolls_per_year: float
    moneyness_pct: float
    rate: float
    dividend_yield: float
    mean_premium_pct: float
    correlation: float | None
    tracking_error: float  # annualized stdev of the per-roll residual
    annualized_drag: float  # replicated CAGR - actual CAGR
    replicated_cagr: float
    actual_cagr: float
    by_year: list[ResidualBucket]
    by_regime: list[ResidualBucket]
    dividend_sensitivity: list[DividendSensitivityPoint]
    rolls: list[ReplicationRoll]


def third_friday(year: int, month: int) -> dt.date:
    """The third Friday of ``year``-``month`` — the standard US equity-index
    option expiration, and therefore the roll date of every program here."""
    fridays = [
        d
        for d in (dt.date(year, month, day) for day in range(1, 29))
        if d.weekday() == 4  # Monday is 0
    ]
    return fridays[2]


def roll_schedule(
    available: pd.DatetimeIndex,
    *,
    rolls_per_year: int,
) -> list[pd.Timestamp]:
    """Roll dates for a ``rolls_per_year`` cadence, snapped to trading days.

    Each nominal roll is a third Friday (of every month, or of March/June/
    September/December for a quarterly program). A third Friday can itself be
    a market holiday — Good Friday collides with the April expiration
    regularly — so each nominal date is snapped to **the latest available date
    on or before it**, which is what the exchange does. Nominal dates that
    snap onto an already-used date (or that predate the series) are dropped,
    so the returned schedule is strictly increasing.

    Pure: ``available`` is the only source of truth about which dates exist.
    """
    if rolls_per_year not in (4, 12):
        raise ValueError("rolls_per_year must be 12 (monthly) or 4 (quarterly)")
    if len(available) == 0:
        return []

    months = range(1, 13) if rolls_per_year == 12 else (3, 6, 9, 12)
    ordered = available.sort_values()
    first, last = ordered[0], ordered[-1]

    schedule: list[pd.Timestamp] = []
    for year in range(first.year, last.year + 1):
        for month in months:
            nominal = pd.Timestamp(third_friday(year, month))
            if nominal < first or nominal > last:
                continue
            eligible = ordered[ordered <= nominal]
            if len(eligible) == 0:
                continue
            snapped = eligible[-1]
            if schedule and snapped <= schedule[-1]:
                continue
            schedule.append(snapped)
    return schedule


def _round_strike(target: float, increment: float) -> float:
    """Nearest listed strike to ``target``. ``increment <= 0`` means no
    rounding (used by tests that want an exactly-known strike)."""
    if increment <= 0:
        return target
    return float(round(target / increment) * increment)


def _replicate(
    index_level: pd.Series,
    sigma: pd.Series,
    benchmark: pd.Series,
    *,
    schedule: Sequence[pd.Timestamp],
    moneyness_pct: float,
    rate: float,
    dividend_yield: float,
    strike_increment: float,
    pricer: OptionPricer,
) -> list[ReplicationRoll]:
    """The core loop: one :class:`ReplicationRoll` per span of ``schedule``.

    Holds one unit of the index and one put per unit, financed out of the
    portfolio, exactly as the published rule does::

        units      = V_k / (S_k + P_k)
        V_{k+1}    = units * (S_{k+1} * exp(q*T) + max(K - S_{k+1}, 0))

    The ``exp(q*T)`` factor is the dividend reconstruction: bronze carries the
    S&P 500 *price* index, the published program holds the *total-return*
    portfolio, and a continuous yield is the cleanest bridge between them.

    Every date in ``schedule`` must exist in all three series — a missing one
    is a bug in the caller's alignment, not something to skip quietly, because
    a skipped roll would silently break the chained NAV.
    """
    rolls: list[ReplicationRoll] = []
    for start, end in pairwise(schedule):
        for name, series in (("index", index_level), ("sigma", sigma), ("benchmark", benchmark)):
            for when in (start, end):
                if when not in series.index:
                    raise ValueError(f"{name} series has no value at {when.date()}")

        spot = float(index_level.loc[start])
        spot_next = float(index_level.loc[end])
        vol = float(sigma.loc[start])
        t_years = (end - start).days / DAYS_PER_YEAR
        if not (math.isfinite(vol) and vol > 0.0):
            raise ValueError(f"sigma at {start.date()} is not a usable volatility: {vol}")
        if t_years <= 0.0:
            raise ValueError(f"non-positive tenor between {start.date()} and {end.date()}")

        strike = _round_strike(spot * (1.0 - moneyness_pct / 100.0), strike_increment)
        premium = pricer.price_put(
            spot=spot, strike=strike, t_years=t_years, r=rate, sigma=vol, q=dividend_yield
        )
        payoff = max(strike - spot_next, 0.0)
        equity_leg = spot_next * math.exp(dividend_yield * t_years)
        replicated = (equity_leg + payoff) / (spot + premium) - 1.0
        actual = float(benchmark.loc[end]) / float(benchmark.loc[start]) - 1.0

        rolls.append(
            ReplicationRoll(
                roll_date=start.date(),
                settle_date=end.date(),
                spot=spot,
                strike=strike,
                sigma=vol,
                t_years=t_years,
                premium=premium,
                premium_pct=premium / spot,
                payoff=payoff,
                replicated_return=replicated,
                actual_return=actual,
                residual=replicated - actual,
            )
        )
    return rolls


def _cagr(returns: Sequence[float], years: float) -> float:
    """Geometric annual rate that compounds ``returns`` over ``years``."""
    if years <= 0:
        return 0.0
    growth = 1.0
    for r in returns:
        growth *= 1.0 + r
    if growth <= 0.0:
        return -1.0
    return float(growth ** (1.0 / years) - 1.0)


def _group_by(
    rolls: Sequence[ReplicationRoll],
    key: Callable[[ReplicationRoll], str],
) -> list[tuple[str, list[ReplicationRoll]]]:
    """Group rolls by ``key``, preserving first-seen order.

    Ordinary ``groupby`` would need the rolls pre-sorted by key, which would
    scramble the chronological order the buckets are read in. Rolls arrive in
    date order, so first-seen order *is* chronological for years, and stable
    for regimes.
    """
    groups: dict[str, list[ReplicationRoll]] = {}
    for roll in rolls:
        groups.setdefault(key(roll), []).append(roll)
    return list(groups.items())


def _bucket(key: str, rolls: Sequence[ReplicationRoll], *, rolls_per_year: float) -> ResidualBucket:
    """Aggregate one slice of rolls into a reportable residual.

    ``annualized_drag`` is geometric (the difference of the two compounded
    rates over the slice), matching the headline; ``mean_residual`` is the
    plain per-roll arithmetic mean, which is the quantity ``tracking_error``
    is the dispersion of.
    """
    years = sum(r.t_years for r in rolls)
    residuals = np.array([r.residual for r in rolls], dtype=float)
    drag = _cagr([r.replicated_return for r in rolls], years) - _cagr(
        [r.actual_return for r in rolls], years
    )
    te = float(residuals.std(ddof=1)) * math.sqrt(rolls_per_year) if len(residuals) > 1 else 0.0
    return ResidualBucket(
        key=key,
        n_rolls=len(rolls),
        years=years,
        mean_residual=float(residuals.mean()),
        annualized_drag=drag,
        tracking_error=te,
    )


def run_index_replication(
    index_level: pd.Series,
    sigma: pd.Series,
    benchmark: pd.Series,
    *,
    program: ReplicationProgram,
    schedule: Sequence[pd.Timestamp],
    rate: float = DEFAULT_RATE,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    strike_increment: float = STRIKE_INCREMENT,
    pricer: OptionPricer | None = None,
    regimes: pd.Series | None = None,
    sensitivity_yields: Sequence[float] = SENSITIVITY_YIELDS,
) -> IndexReplicationResult:
    """Replicate ``program`` over ``schedule`` and measure the residual.

    Pure: three date-indexed series in (the index level being traded, the
    volatility used to price each put, and the published index to difference
    against), one result out. ``schedule`` must be strictly increasing and
    every date in it must exist in all three series — build it with
    :func:`roll_schedule` over the *intersection* of their indexes.

    ``regimes`` optionally labels each roll with the market regime in force at
    entry, which is what makes the per-regime residual table possible; it is
    read at the roll date only (no look-ahead).

    Raises ``LookupError`` when the schedule is too short to complete a single
    roll — a two-entry schedule is the minimum, since one roll spans two
    dates.
    """
    if len(schedule) < 2:
        raise LookupError("need at least two roll dates to complete one roll")

    pricer = pricer or BlackScholesPricer()
    rolls = _replicate(
        index_level,
        sigma,
        benchmark,
        schedule=schedule,
        moneyness_pct=program.moneyness_pct,
        rate=rate,
        dividend_yield=dividend_yield,
        strike_increment=strike_increment,
        pricer=pricer,
    )

    if regimes is not None:
        for roll in rolls:
            stamp = pd.Timestamp(roll.roll_date)
            eligible = regimes.loc[regimes.index <= stamp]
            if len(eligible):
                roll.regime = eligible.iloc[-1]

    years = sum(r.t_years for r in rolls)
    rolls_per_year = len(rolls) / years if years > 0 else float(program.rolls_per_year)
    replicated = [r.replicated_return for r in rolls]
    actual = [r.actual_return for r in rolls]
    residuals = np.array([r.residual for r in rolls], dtype=float)

    correlation: float | None = None
    if len(rolls) > 1:
        candidate = float(np.corrcoef(replicated, actual)[0, 1])
        correlation = candidate if math.isfinite(candidate) else None

    by_year = [
        _bucket(str(year), group, rolls_per_year=rolls_per_year)
        for year, group in _group_by(rolls, lambda r: str(r.roll_date.year))
    ]
    by_regime = [
        _bucket(label, group, rolls_per_year=rolls_per_year)
        for label, group in _group_by(rolls, lambda r: r.regime or "unlabelled")
    ]

    replicated_cagr = _cagr(replicated, years)
    actual_cagr = _cagr(actual, years)

    sensitivity: list[DividendSensitivityPoint] = []
    for candidate_q in sensitivity_yields:
        alt = _replicate(
            index_level,
            sigma,
            benchmark,
            schedule=schedule,
            moneyness_pct=program.moneyness_pct,
            rate=rate,
            dividend_yield=candidate_q,
            strike_increment=strike_increment,
            pricer=pricer,
        )
        alt_years = sum(r.t_years for r in alt)
        sensitivity.append(
            DividendSensitivityPoint(
                dividend_yield=candidate_q,
                annualized_drag=_cagr([r.replicated_return for r in alt], alt_years)
                - _cagr([r.actual_return for r in alt], alt_years),
            )
        )

    return IndexReplicationResult(
        index_symbol=program.index_symbol,
        description=program.description,
        start=rolls[0].roll_date,
        end=rolls[-1].settle_date,
        years=years,
        n_rolls=len(rolls),
        rolls_per_year=rolls_per_year,
        moneyness_pct=program.moneyness_pct,
        rate=rate,
        dividend_yield=dividend_yield,
        mean_premium_pct=float(np.mean([r.premium_pct for r in rolls])),
        correlation=correlation,
        tracking_error=float(residuals.std(ddof=1)) * math.sqrt(rolls_per_year)
        if len(rolls) > 1
        else 0.0,
        annualized_drag=replicated_cagr - actual_cagr,
        replicated_cagr=replicated_cagr,
        actual_cagr=actual_cagr,
        by_year=by_year,
        by_regime=by_regime,
        dividend_sensitivity=sensitivity,
        rolls=rolls,
    )


def _series_from(bronze: pd.DataFrame, *, date_col: str, value_col: str) -> pd.Series:
    """Date-indexed float series from a bronze frame, sorted and de-duplicated.

    Later rows win on a duplicated date — the same ``keep="last"`` convention
    the rest of ``research/`` uses, so a corrected value in a later row of the
    same snapshot is the one that survives.
    """
    ordered = bronze.sort_values(date_col).drop_duplicates(subset=date_col, keep="last")
    return pd.Series(
        ordered[value_col].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered[date_col]),
    )


def compute_index_replication(
    store: LakeStore,
    *,
    index_symbol: str,
    as_of: dt.date,
    rate: float = DEFAULT_RATE,
    dividend_yield: float = DEFAULT_DIVIDEND_YIELD,
    pricer: OptionPricer | None = None,
) -> IndexReplicationResult:
    """Replicate ``index_symbol`` from the lake, point-in-time as of ``as_of``.

    Reads only snapshots known on or before ``as_of``
    (:meth:`LakeStore.read_bronze_as_of`), so the residual reported for a past
    date is the residual that could have been computed on that date.

    Three series have to agree on their dates before a roll can be priced: the
    S&P 500 level being traded, the VIX pricing it, and the published index
    being differenced against. The schedule is built over their **intersection**
    rather than over any one of them, which is what lets
    :func:`_replicate` treat a missing date as a caller bug instead of
    quietly skipping a roll and breaking the chained NAV.

    Raises ``KeyError`` for an unknown program and ``LookupError`` when the
    lake has no usable snapshot or too little overlap to complete a roll.
    """
    if index_symbol not in PROGRAMS:
        raise KeyError(f"no replication rule for {index_symbol}; known: {sorted(PROGRAMS)}")
    program = PROGRAMS[index_symbol]

    try:
        strategy_bronze = store.read_bronze_as_of(CBOE_STRATEGY_DATASET, as_of)
    except LookupError as exc:
        raise LookupError(f"no Cboe strategy indices known as of {as_of.isoformat()}") from exc

    levels = strategy_bronze[strategy_bronze["index_symbol"] == UNDERLYING_SYMBOL]
    published = strategy_bronze[strategy_bronze["index_symbol"] == index_symbol]
    for name, frame in ((UNDERLYING_SYMBOL, levels), (index_symbol, published)):
        if frame.empty:
            raise LookupError(f"no {name} rows in the snapshot known as of {as_of.isoformat()}")

    index_level = _series_from(levels, date_col="trade_date", value_col="close")
    benchmark = _series_from(published, date_col="trade_date", value_col="close")

    regimes = compute_regime_timeline(store, as_of=as_of)
    # The VIX arrives in points (17.5); Black-Scholes wants a fraction (0.175).
    sigma = _series_from(
        store.read_bronze_as_of("vix", as_of), date_col="date", value_col="close"
    ).div(100.0)

    common = index_level.index.intersection(benchmark.index).intersection(sigma.index)
    schedule = roll_schedule(pd.DatetimeIndex(common), rolls_per_year=program.rolls_per_year)
    if len(schedule) < 2:
        raise LookupError(
            f"{index_symbol}: only {len(schedule)} roll date(s) in the overlap of "
            f"SPX, VIX and {index_symbol} as of {as_of.isoformat()}"
        )

    return run_index_replication(
        index_level,
        sigma,
        benchmark,
        program=program,
        schedule=schedule,
        rate=rate,
        dividend_yield=dividend_yield,
        pricer=pricer,
        regimes=regimes,
    )


def format_replication_report(result: IndexReplicationResult) -> str:
    """Render a replication as the plain-text report ``make residual`` prints.

    Kept here, next to the model it renders, so the headline number and its
    caveats travel together: a residual quoted without its dividend-yield
    sensitivity is a number pretending to be a measurement.
    """
    pct = 100.0
    lines = [
        f"{result.index_symbol}: {result.description}",
        f"  window        {result.start} .. {result.end}   "
        f"{result.years:.1f}y, {result.n_rolls} rolls",
        f"  mean premium  {result.mean_premium_pct * pct:.3f}% of spot per roll",
        f"  correlation   {result.correlation:.4f}"
        if result.correlation is not None
        else "  correlation   n/a",
        f"  tracking err  {result.tracking_error * pct:.2f}%/yr",
        f"  CAGR  model   {result.replicated_cagr * pct:+.2f}%/yr"
        f"   actual {result.actual_cagr * pct:+.2f}%/yr",
        f"  RESIDUAL      {result.annualized_drag * pct:+.2f}%/yr"
        f"   (q={result.dividend_yield:.3f}, r={result.rate:.2f})",
    ]
    if result.dividend_sensitivity:
        points = "  ".join(
            f"q={p.dividend_yield:.3f} -> {p.annualized_drag * pct:+.2f}%/yr"
            for p in result.dividend_sensitivity
        )
        lines.append(f"  dividend sensitivity: {points}")
    for title, buckets in (("by regime", result.by_regime), ("by year", result.by_year)):
        lines.append(f"  {title}:")
        for bucket in sorted(buckets, key=lambda b: b.key):
            lines.append(
                f"    {bucket.key:11s} n={bucket.n_rolls:4d}"
                f"  drag {bucket.annualized_drag * pct:+7.2f}%/yr"
                f"  TE {bucket.tracking_error * pct:6.2f}%"
            )
    return "\n".join(lines)
