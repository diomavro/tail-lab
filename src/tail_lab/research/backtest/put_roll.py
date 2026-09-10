"""The Put Lab roll engine — model-priced OOM-put backtest.

``run_put_roll`` is pure: it takes an underlying price series and an IV-proxy
series (both indexed by trade date) and rolls a fixed-budget out-of-the-money
put strategy through them, pricing every premium with an injected
:class:`OptionPricer`. ``compute_put_backtest`` is the orchestration wrapper
that reads point-in-time OHLCV from the lake, builds those two series, and
calls the pure engine.

Every premium is a **model price**, not a real historical quote
(``docs/adr/0004``); the underlying path is real. Point-in-time safety: the
IV proxy at an entry date uses only trailing prices (no future leakage into
the premium), and ``compute_put_backtest`` reads only the bronze snapshot
known on or before ``as_of`` (``LakeStore.read_bronze_as_of``).

**Market-priced rolls.** Passing ``basis=PricingBasis(quotes=...)`` switches
every leg in the run from the model above to a real listed contract fetched
through :mod:`tail_lab.research.backtest.quote_fills` — see that module's
docstring for why the model cannot be trusted at the depths this exists for.
There is no per-leg fallback: a cycle that cannot fill a real quote is
SKIPPED and counted (``PutBacktestResult.n_cycles_skipped``), never priced by
the model, because a silent model price inside a run labelled
``priced_from == "market"`` is the one failure mode this path exists to rule
out.
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.brokerage import COMMISSION_PER_CONTRACT, roll_cost
from tail_lab.research.backtest.growth import time_average_growth
from tail_lab.research.backtest.sizing import SizingMode, WealthFraction

# quote_fills -> marks -> roll_schedule -> put_roll (roll_schedule imports IV_CAP/
# IV_FLOOR/TRADING_DAYS_PER_WEEK from here), so a top-level import of QuoteSource
# would be circular. `from __future__ import annotations` (above) already makes
# every annotation in this file a lazy string, so a TYPE_CHECKING-only import is
# enough for mypy and never runs at import time -- QuoteSource is only ever used
# here as a type, never constructed or introspected.
if TYPE_CHECKING:
    from tail_lab.research.backtest.quote_fills import QuoteSource
from tail_lab.research.option_pricer import BlackScholesPricer, OptionPricer

#: Trailing window (trading days) for the realized-vol IV proxy.
IV_WINDOW = 20
#: Floor/cap on the annualized IV proxy — a 20-day realized vol can collapse
#: toward zero in a dead-calm window (making puts look free) or spike absurdly
#: in a crash; both are artifacts of a short estimator, not tradable IVs.
IV_FLOOR = 0.06
IV_CAP = 2.0
#: Default continuously-compounded risk-free rate. A real term structure
#: (FRED, ``docs/DATA_CONTRACTS.md`` #3) is a later upgrade; a flat rate is a
#: fine approximation for ranking soon-expiry OOM-put outcomes.
DEFAULT_RATE = 0.04
#: Trading days per calendar week used to convert a tenor in weeks to a hold.
TRADING_DAYS_PER_WEEK = 5
#: Premium is floored at this fraction of spot before sizing, so a
#: vanishingly cheap far-OOM/short-dated put can't imply infinite contracts.
#: Model path only — see ``PricingBasis``: a real quoted ask is a real price
#: and is never floored (quote_fills's own liquidity guard already refuses
#: the pennies this floor was accidentally protecting against).
PREMIUM_FLOOR_FRAC = 1e-4
#: Don't annualize the running return-on-premium until at least this much of the
#: window has elapsed. Annualizing a two-week ROI raises ``(1+roi)`` to the 26th
#: power, which explodes a small early loss/gain into a nonsense yearly rate;
#: starting the "annualized so far" curve at a quarter-year keeps every point a
#: meaningful horizon rather than a numeric artifact (README convex-hedge lens).
MIN_YEARS_FOR_ANNUALIZED = 0.25


@dataclass(frozen=True)
class PricingBasis:
    """How premiums are produced for one ``run_put_roll`` call.

    ``pricer`` is the existing model path (``None`` behaves exactly as
    passing nothing at all: :class:`BlackScholesPricer` is used). ``quotes``,
    when given, switches the WHOLE run to real listed contracts via
    :mod:`tail_lab.research.backtest.quote_fills` — there is no mixing the
    two within a single run, and no per-leg fallback from ``quotes`` back to
    ``pricer`` (see the module docstring's "Market-priced rolls" paragraph).
    This replaces the old ``pricer: OptionPricer | None`` parameter
    one-for-one so ``run_put_roll``'s keyword-only argument count does not
    grow past the ``max-args = 13`` ratchet in ``pyproject.toml``.
    """

    pricer: OptionPricer | None = None
    quotes: QuoteSource | None = None


class PutRollCycle(BaseModel):
    """One entry-to-expiry roll of the strategy."""

    entry_date: dt.date
    expiry_date: dt.date
    spot: float
    strike: float
    #: IV proxy (clamped realized-vol) at entry. Used to price the premium on
    #: the model path; on the market path (``PricingBasis.quotes`` set) the
    #: premium comes from a real quote instead, and this is kept only as the
    #: vol-regime context for that entry, not an input to the price.
    sigma: float
    premium: float  # model price of one put at entry (the real ask, on the market path)
    contracts: float  # notional / premium
    cost: float  # entry brokerage (commission + bid-ask half-spread) for this roll
    #: Quote-panel basis this leg was resolved under (1.0 on the model path).
    #: The mark-to-market lookup needs it to find the same contract again; see
    #: quote_fills.Fill.basis for what happens without it.
    quote_basis: float = 1.0
    payoff: float  # contracts * max(strike - spot_at_expiry, 0)
    net: float  # payoff - notional - cost (premium budget + brokerage spent)


class EquityPoint(BaseModel):
    date: dt.date
    cum_pnl: float


class AnnualizedPoint(BaseModel):
    """One point of the "annualized return so far" curve — at ``date`` (a roll's
    expiry), ``annualized`` is the geometric yearly rate the strategy had earned
    on premium from the first entry up to that date (net of brokerage)."""

    date: dt.date
    annualized: float


class PricePoint(BaseModel):
    date: dt.date
    price: float


class PutBacktestResult(BaseModel):
    """Full Put Lab backtest output for one parameter set."""

    asset: str
    as_of: dt.date
    spot: float  # latest underlying price, for showing the strike in real $
    notional: float
    moneyness_pct: float
    tenor_weeks: float
    lookback_years: float
    rate: float
    n_cycles: int
    total_premium: float
    total_payoff: float
    total_brokerage: float  # sum of every roll's entry brokerage cost
    net_pnl: float
    roi_on_premium: float
    annualized_return: float  # geometric annualization of roi_on_premium over lookback_years
    #: Time-average (geometric) growth rate of `wealth` held in the benchmark
    #: with this run's hedge P&L layered on top (`docs/END_STATE.md` §4 Q8) --
    #: labels the difference `roi_on_premium`/`annualized_return` cannot:
    #: those describe the put in isolation, this describes what compounding
    #: does to the *combined* portfolio, which is what an investor actually
    #: experiences. Only populated when `compute_put_backtest` was given
    #: `sizing_mode=WealthFraction(...)` (a flat `notional` has no `wealth`
    #: figure to compound against); `None` otherwise, or when growth is
    #: undefined -- see `research.backtest.growth.time_average_growth`.
    time_average_growth: float | None = None
    hit_rate: float
    biggest_payoff_mult: float
    worst_bleed_streak: int
    sharpe_ratio: float | None  # annualized Sharpe of the per-roll returns (None if < 2 rolls)
    #: Return the strategy had annualized from the first entry up to each roll's
    #: expiry — lets the tape show at which horizons the hedge beat the market.
    annualized_so_far: list[AnnualizedPoint]
    #: S&P 500 buy-and-hold annualized over the same window — the hurdle the tape
    #: draws the annualized-so-far line against. Populated by the API route (the
    #: pure engine has no benchmark), so it defaults to ``None``.
    benchmark_annualized: float | None = None
    #: "model" (default) or "market" — whether every premium in this run came
    #: from BlackScholesPricer or from a real listed ask via quote_fills. See
    #: PricingBasis.
    priced_from: Literal["model", "market"] = "model"
    #: How much of the REQUESTED window the run actually traded, 0..1.
    #:
    #: This used to be fills / attempts, which is not a coverage and could not
    #: be read as one: a refusal advances one DAY while a fill advances one
    #: CYCLE, so the ratio mixed units and moved with the tenor rather than
    #: with data availability. Measured on the same SPY window and the same
    #: quotes, only the tenor changing: 1w 0.098, 2w 0.052, 4w 0.027, 12w
    #: 0.009 -- a 10x swing reporting 2.7% where the honest window coverage
    #: was ~34%. A single scalar whose whole job is to qualify a "market"
    #: label must not be off by 13x in the alarming direction while the label
    #: is wrong in the reassuring one.
    #:
    #: Defined the same way `accuracy.QuoteCoverage` defines it -- span
    #: traded over span requested -- so the two numbers in this codebase
    #: called "coverage" mean one thing.
    quote_coverage_pct: float = 0.0
    #: Fills over attempts. Kept because it says something different and
    #: useful (how often a quotable contract existed at all) but it is NOT a
    #: coverage; see above.
    fill_rate: float = 0.0
    #: Cycles skipped because quote_fills.QuoteSource.fill(...) returned None
    #: (no guard satisfied) or the realized expiry ran past the end of the
    #: price series. Always 0 on the model path.
    n_cycles_skipped: int = 0
    #: First entry date / last expiry date actually traded — may fall short
    #: of the nominal [as_of - lookback_years, as_of] window when cycles were
    #: skipped. annualized_return is computed over this span, not the nominal
    #: window, whenever n_cycles_skipped > 0 (see run_put_roll's callsite).
    traded_start: dt.date | None = None
    traded_end: dt.date | None = None
    equity_curve: list[EquityPoint]  # realized, one point per expiry (+ a seed)
    mtm_curve: list[EquityPoint]  # daily mark-to-model cum P&L, aligned with price_path
    price_path: list[PricePoint]  # underlying over the traded window (for the tape)
    cycles: list[PutRollCycle]


def trailing_realized_vol(prices: pd.Series, *, window: int = IV_WINDOW) -> pd.Series:
    """Annualized trailing realized volatility of ``prices`` — the IV proxy.

    Sample std (``ddof=1``) of daily log returns over the trailing ``window``,
    annualized by ``sqrt(252)``. The value at date *t* uses only returns up to
    and including *t* (``rolling`` is backward-looking), so pricing a premium
    with it never peeks at the future. Rows with fewer than ``window`` returns
    are ``NaN`` (an entry there is skipped, not priced on a stub estimate).
    """
    ratio = (prices / prices.shift(1)).to_numpy(dtype=float)
    log_returns = pd.Series(np.log(ratio), index=prices.index)
    rolling_std = log_returns.rolling(window=window, min_periods=window).std()
    return pd.Series(rolling_std * float(np.sqrt(252.0)), index=prices.index)


def annualized_return(total_roi: float, years: float) -> float:
    """Geometric annualization of a total return-on-premium over ``years``.

    ``(1 + total_roi) ** (1/years) - 1``, i.e. the constant yearly rate that
    compounds to the total. ``total_roi`` can't be below -1 (you can't lose more
    than the premium); at exactly -1 the annualized rate is -1 (-100%/yr).
    """
    if years <= 0:
        return total_roi
    base = 1.0 + total_roi
    if base <= 0.0:
        return -1.0
    return float(base ** (1.0 / years) - 1.0)


def annualized_so_far_curve(
    settled: Sequence[tuple[dt.date, float]],
    *,
    first_entry_date: dt.date,
    notional: float,
) -> list[AnnualizedPoint]:
    """Running "annualized return on premium so far", one point per settled roll.

    ``settled`` is ``(expiry_date, net)`` per roll in expiry order (``net`` is
    already net of brokerage). At each roll *k* (1-based), using only the rolls
    settled by its expiry::

        roi_so_far     = sum(net of first k rolls) / (k * notional)
        years_elapsed  = (expiry_date - first_entry_date).days / 365.25
        annualized     = annualized_return(roi_so_far, years_elapsed)

    Points with ``years_elapsed < MIN_YEARS_FOR_ANNUALIZED`` are skipped, not
    clamped: annualizing a sub-quarter ROI compounds a tiny early swing into a
    nonsense yearly rate, so the curve simply starts once a quarter-year of
    horizon exists. The final point's ``roi_so_far`` equals the result's
    ``roi_on_premium`` exactly (same numerator and denominator), so the last
    ``annualized`` matches ``annualized_return`` up to the difference between the
    actual elapsed years here and the nominal ``lookback_years`` used there.
    """
    points: list[AnnualizedPoint] = []
    cum_net = 0.0
    for k, (expiry_date, net) in enumerate(settled, start=1):
        cum_net += net
        years_elapsed = (expiry_date - first_entry_date).days / 365.25
        if years_elapsed < MIN_YEARS_FOR_ANNUALIZED:
            continue
        roi_so_far = cum_net / (k * notional)
        points.append(
            AnnualizedPoint(
                date=expiry_date,
                annualized=annualized_return(roi_so_far, years_elapsed),
            )
        )
    return points


#: Below this fraction of |mean return|, the per-roll spread is treated as
#: degenerate and no Sharpe is reported. 1% of the mean: wide enough to catch
#: "every roll lost the whole premium", narrow enough that a genuinely
#: low-variance strategy still gets a number.
_DEGENERATE_SD_FRAC = 0.01


def annualized_sharpe(
    returns: Sequence[float],
    *,
    rolls_per_year: float,
    rate: float = DEFAULT_RATE,
) -> float | None:
    """Annualized Sharpe ratio of a strategy's per-roll returns.

    Each ``r_i`` is one roll's return on the premium risked, ``net_i / notional``
    (net of brokerage). With ``rf_per_roll = rate / rolls_per_year`` the
    per-roll risk-free carry (so the annualized excess is over the ``rate`` = 4%
    risk-free), the Sharpe is::

        (mean(r) - rf_per_roll) / std(r, ddof=1) * sqrt(rolls_per_year)

    Returns ``None`` when there are fewer than two rolls or the returns have zero
    dispersion (an undefined ratio). Sharpe is only a *rough* lens for a convex
    tail hedge: its returns are lumpy and fat-tailed (many small premium bleeds,
    rare large payoffs), which violates the mean/variance normality Sharpe
    assumes — it is reported for completeness, not as a headline.
    """
    n = len(returns)
    if n < 2 or rolls_per_year <= 0.0:
        return None
    arr = np.asarray(returns, dtype=float)
    sd = float(arr.std(ddof=1))
    # Degenerate dispersion, not just EXACTLY zero. On the market path every
    # roll can be a near-identical total loss -- measured on real SPY 10% / 4
    # weeks: 30 rolls, all with zero payoff, mean return -1.0085, sd 5.3e-3 --
    # and dividing by that sd reported a Sharpe of -468. A Sharpe of -468 is not
    # a number to show anyone; it is a division artefact of a strategy that did
    # the same thing every time. Scaled to the mean so the test is unit-free.
    if sd <= _DEGENERATE_SD_FRAC * max(abs(float(arr.mean())), 1e-12):
        return None
    rf_per_roll = rate / rolls_per_year
    excess = float(arr.mean()) - rf_per_roll
    return float(excess / sd * np.sqrt(rolls_per_year))


@dataclass
class _CycleAccumulation:
    """Mutable bookkeeping shared by ``_roll_model_cycles`` and
    ``_roll_market_cycles`` — one instance per ``run_put_roll`` call, built up
    cycle by cycle and read back into local variables once the loop is done.
    Existing purely so the two roll loops can be functions of their own
    (``docs/adr/0023``'s max-complexity/max-statements ratchet: merging both
    loops into ``run_put_roll`` itself pushed it to complexity 21 and 135
    statements against limits of 11 and 75) without each one returning an
    eleven-tuple.
    """

    cycles: list[PutRollCycle] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)  # (entry_idx, expiry_idx)
    equity: list[EquityPoint] = field(default_factory=list)
    total_payoff: float = 0.0
    total_brokerage: float = 0.0
    wins: int = 0
    biggest_mult: float = 0.0
    worst_streak: int = 0
    #: Running state, not part of what the caller reads back -- the current
    #: cumulative P&L and losing-streak length as of the last recorded cycle.
    cum: float = 0.0
    streak: int = 0
    first_traded_idx: int | None = None
    last_expiry_idx: int = 0
    n_attempted: int = 0
    n_cycles_skipped: int = 0


@dataclass(frozen=True)
class _CycleFacts:
    """The economics of one completed roll.

    Bundled rather than passed as nine keyword arguments: `_record_cycle`
    reached 14 parameters against `pyproject.toml`'s `max-args = 13`, a ratchet
    that "may only ever go down" (docs/adr/0023 — reshape the change, never
    raise the number). These nine always travel together and are exactly the
    fields `PutRollCycle` records, so the bundle is the shape the data already
    had.
    """

    spot: float
    strike: float
    sigma: float
    premium: float
    contracts: float
    cost: float
    payoff: float
    net: float
    #: Quote-panel basis (1.0 on the model path); see PutRollCycle.quote_basis.
    quote_basis: float = 1.0


def _record_cycle(
    acc: _CycleAccumulation,
    *,
    entry_idx: int,
    expiry_idx: int,
    dates: list[dt.date],
    facts: _CycleFacts,
    notional: float,
) -> None:
    """Append one settled cycle to ``acc`` and update its running bookkeeping.

    Shared by both roll loops so the equity-curve/win-streak/biggest-payoff
    tallying — which has nothing to do with whether the premium came from the
    model or a real quote — is written once rather than twice in step.
    """
    if not acc.equity:
        acc.equity.append(EquityPoint(date=dates[entry_idx], cum_pnl=0.0))
    acc.cum += facts.net
    acc.total_payoff += facts.payoff
    acc.total_brokerage += facts.cost
    if facts.net > 0:
        acc.wins += 1
    acc.biggest_mult = max(acc.biggest_mult, facts.payoff / notional)
    if facts.net < 0:
        acc.streak += 1
        acc.worst_streak = max(acc.worst_streak, acc.streak)
    else:
        acc.streak = 0
    acc.cycles.append(
        PutRollCycle(
            entry_date=dates[entry_idx],
            expiry_date=dates[expiry_idx],
            spot=float(facts.spot),
            strike=float(facts.strike),
            sigma=facts.sigma,
            premium=float(facts.premium),
            contracts=float(facts.contracts),
            cost=float(facts.cost),
            payoff=float(facts.payoff),
            net=float(facts.net),
            quote_basis=facts.quote_basis,
        )
    )
    acc.equity.append(EquityPoint(date=dates[expiry_idx], cum_pnl=float(acc.cum)))
    acc.spans.append((entry_idx, expiry_idx))
    if acc.first_traded_idx is None:
        acc.first_traded_idx = entry_idx
    acc.last_expiry_idx = expiry_idx


def _roll_model_cycles(
    *,
    px: np.ndarray,
    iv: np.ndarray,
    dates: list[dt.date],
    first_entry: int,
    n: int,
    tenor_days: int,
    pricer: OptionPricer,
    rate: float,
    notional: float,
    moneyness_pct: float,
    tenor_weeks: float,
    commission_per_contract: float,
    spread_scale: float,
) -> _CycleAccumulation:
    """The model-priced roll loop — behaviour unchanged from before
    ``PricingBasis`` existed, only moved out of ``run_put_roll`` (see
    ``_CycleAccumulation``'s docstring for why)."""
    t_years = tenor_days / 252.0
    acc = _CycleAccumulation(last_expiry_idx=first_entry)
    i = first_entry
    while i + tenor_days < n:
        sigma = iv[i]
        if not np.isfinite(sigma):  # not enough trailing history yet — step forward
            i += 1
            continue
        sigma = float(min(max(sigma, IV_FLOOR), IV_CAP))
        spot = px[i]
        strike = spot * (1.0 - moneyness_pct / 100.0)
        premium = pricer.price_put(spot=spot, strike=strike, t_years=t_years, r=rate, sigma=sigma)
        premium = max(premium, spot * PREMIUM_FLOOR_FRAC)
        contracts = notional / premium
        cost = roll_cost(
            contracts,
            notional,
            tenor_weeks,
            moneyness_pct,
            commission_per_contract=commission_per_contract,
            spread_scale=spread_scale,
        )
        spot_at_expiry = px[i + tenor_days]
        payoff = contracts * max(strike - spot_at_expiry, 0.0)
        net = payoff - notional - cost
        _record_cycle(
            acc,
            entry_idx=i,
            expiry_idx=i + tenor_days,
            dates=dates,
            facts=_CycleFacts(
                spot=spot,
                strike=strike,
                sigma=sigma,
                premium=premium,
                contracts=contracts,
                cost=cost,
                payoff=payoff,
                net=net,
            ),
            notional=notional,
        )
        i += tenor_days
    return acc


def _roll_market_cycles(
    *,
    px: np.ndarray,
    iv: np.ndarray,
    dates: list[dt.date],
    first_entry: int,
    n: int,
    quotes: QuoteSource,
    notional: float,
    moneyness_pct: float,
    tenor_weeks: float,
    commission_per_contract: float,
) -> _CycleAccumulation:
    """The market-priced roll loop — see the "Market-priced rolls" paragraph
    in the module docstring for what it does and why there is no fallback to
    the model. Moved out of ``run_put_roll`` for the same ratchet reason as
    ``_roll_model_cycles``."""
    acc = _CycleAccumulation(last_expiry_idx=first_entry)
    i = first_entry
    while i < n:
        sigma_raw = iv[i]
        if not np.isfinite(sigma_raw):  # not enough trailing history yet — step forward
            i += 1
            continue
        sigma = float(min(max(sigma_raw, IV_FLOOR), IV_CAP))
        spot = px[i]
        entry_date = dates[i]

        acc.n_attempted += 1
        fill = quotes.fill(
            entry_date=entry_date,
            spot=float(spot),
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
        )
        if fill is None:
            # No guard in quote_fills was satisfied for this session -- SKIP
            # and COUNT, never fall back to the model.
            acc.n_cycles_skipped += 1
            i += 1
            continue

        expiry_idx = bisect.bisect_left(dates, fill.expiry, i)
        if expiry_idx >= n or expiry_idx <= i:
            # Two ways a fill cannot become a settled cycle, and BOTH must
            # advance `i` or the loop cannot terminate.
            #
            # `expiry_idx >= n`: the listed expiry runs past the end of the
            # price series, so there is no settlement price. A skip, not a
            # truncated payoff.
            #
            # `expiry_idx <= i`: the listed expiry is on or before the entry
            # session, so `bisect_left(..., lo=i)` returns `i` itself and
            # `i = expiry_idx` would leave the index exactly where it was --
            # an unbounded loop appending a cycle every pass, measured leaking
            # 90 MB/s and exhausting a 1 GB machine in ~10 seconds.
            #
            # That is reachable, not theoretical: `marks._select_expiry` takes
            # the nearest expiry AT OR AFTER `entry + round(tenor_weeks * 7)`
            # days, so any `tenor_weeks <= 1/14` rounds the target to zero days
            # and a 0DTE listing on that session satisfies it. The real SPY
            # panel has 100,956 rows on 1,509 sessions where `expiration ==
            # quote_date`, and `/api/putlab/backtest` accepts `tenor_weeks`
            # down to just above 0.
            acc.n_cycles_skipped += 1
            i += 1
            continue

        strike = fill.strike  # the REALIZED strike, not spot*(1-moneyness/100)
        premium = fill.premium  # the real ask -- no PREMIUM_FLOOR_FRAC (guard 6)
        contracts = notional / premium
        cost = roll_cost(
            contracts,
            notional,
            tenor_weeks,
            moneyness_pct,
            commission_per_contract=commission_per_contract,
            spread_scale=0.0,  # the ask already prices the spread -- see run_put_roll's docstring
        )
        spot_at_expiry = px[expiry_idx]
        payoff = contracts * max(strike - spot_at_expiry, 0.0)
        net = payoff - notional - cost
        _record_cycle(
            acc,
            entry_idx=i,
            expiry_idx=expiry_idx,
            dates=dates,
            facts=_CycleFacts(
                spot=spot,
                strike=strike,
                sigma=sigma,
                premium=premium,
                contracts=contracts,
                cost=cost,
                payoff=payoff,
                net=net,
                quote_basis=fill.basis,
            ),
            notional=notional,
        )
        i = expiry_idx
    return acc


def run_put_roll(
    prices: pd.Series,
    iv_proxy: pd.Series,
    *,
    asset: str,
    as_of: dt.date,
    notional: float,
    moneyness_pct: float,
    tenor_weeks: float,
    lookback_years: float,
    rate: float = DEFAULT_RATE,
    basis: PricingBasis | None = None,
    commission_per_contract: float = COMMISSION_PER_CONTRACT,
    spread_scale: float = 1.0,
    include_curves: bool = True,
) -> PutBacktestResult:
    """Roll a fixed-``notional`` OOM-put strategy through ``prices``.

    At each entry the strategy spends ``notional`` on puts struck
    ``moneyness_pct`` percent below spot, expiring ``tenor_weeks`` weeks out,
    priced at ``iv_proxy`` for that date; at expiry it collects the intrinsic
    payoff, then re-enters (non-overlapping rolls). ``prices`` and ``iv_proxy``
    must share the same date index.

    Only the trailing ``lookback_years`` of the series is traded (the head is
    still needed so the first tradable entry already has a valid IV proxy).
    Raises ``ValueError`` on misaligned series or non-positive inputs, and
    ``LookupError`` if the window is too short to complete even one roll.

    ``basis`` selects how every premium in the run is produced (default
    ``None`` == ``PricingBasis()`` == the model path, exactly as before).
    Passing ``PricingBasis(quotes=...)`` switches to real listed contracts:
    each cycle's strike, expiry AND premium come from ``quotes.fill(...)``
    rather than Black-Scholes, and a cycle whose request cannot fill (see
    ``quote_fills``'s guards) is SKIPPED and counted in
    ``PutBacktestResult.n_cycles_skipped`` rather than priced by the model —
    there is no fallback between the two paths within one run. On the market
    path ``spread_scale`` is ignored (always treated as ``0.0``): the premium
    is already the real historical ask, so charging the model's bid-ask
    half-spread on top of it double-counts a cost already paid (measured
    2.4-5pp of ROI per roll) — ``commission_per_contract`` still applies,
    since a real trade still pays a real commission. When any cycle was
    skipped, ``annualized_return`` is computed over the ACTUAL traded span
    (first entry to last expiry) rather than the nominal ``lookback_years``:
    annualizing a truncated run over its nominal window is a measured
    flatterer — a truncated SPY run reported -15.0%/yr over the nominal
    window where the honest figure, over the traded span, is -26.2%/yr.

    Every roll's ``net`` is **net of realistic retail brokerage** (see
    :mod:`tail_lab.research.backtest.brokerage`): a ``$0.65``/contract
    commission plus a tenor/moneyness-dependent bid-ask half-spread, paid at
    entry (the half-spread is model-path only — see above).
    ``commission_per_contract`` and ``spread_scale`` are exposed only so a
    test can run the identical model strategy cost-free (both ``0``) to
    isolate the cost drag; production callers use the defaults.

    ``include_curves=False`` skips the two **per-day** outputs — the
    mark-to-model curve and the price path — and returns them empty. Every
    scalar, the cycle list, and the per-roll curves are unchanged, so a caller
    that only scores a run gets an identical verdict. This is not a
    micro-optimization: the mark-to-model curve is O(days x cycles) with a
    pricer (or quote) call per day, and it dominates the cost of a run by an
    order of magnitude. A 45-cell sweep computes it 45 times and displays it
    zero times (:mod:`tail_lab.research.backtest.sweep`), and the universe
    ranking would compute it ~1,600 times. Charts pass ``True``; scoring
    passes ``False``.
    """
    if not prices.index.equals(iv_proxy.index):
        raise ValueError("prices and iv_proxy must share the same date index")
    if notional <= 0 or tenor_weeks <= 0 or not 0 < moneyness_pct < 100:
        raise ValueError("notional>0, tenor_weeks>0, and 0<moneyness_pct<100 required")

    basis = basis or PricingBasis()
    pricer = basis.pricer or BlackScholesPricer()
    quotes = basis.quotes
    tenor_days = max(round(tenor_weeks * TRADING_DAYS_PER_WEEK), 1)
    n = len(prices)
    lookback_days = round(lookback_years * 252)
    first_entry = max(n - lookback_days, IV_WINDOW)

    px = prices.to_numpy(dtype=float)
    iv = iv_proxy.to_numpy(dtype=float)
    dates = [d.date() if isinstance(d, pd.Timestamp) else d for d in prices.index]

    if quotes is not None:
        acc = _roll_market_cycles(
            px=px,
            iv=iv,
            dates=dates,
            first_entry=first_entry,
            n=n,
            quotes=quotes,
            notional=notional,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            commission_per_contract=commission_per_contract,
        )
    else:
        acc = _roll_model_cycles(
            px=px,
            iv=iv,
            dates=dates,
            first_entry=first_entry,
            n=n,
            tenor_days=tenor_days,
            pricer=pricer,
            rate=rate,
            notional=notional,
            moneyness_pct=moneyness_pct,
            tenor_weeks=tenor_weeks,
            commission_per_contract=commission_per_contract,
            spread_scale=spread_scale,
        )

    cycles = acc.cycles
    spans = acc.spans
    equity = acc.equity
    total_payoff = acc.total_payoff
    total_brokerage = acc.total_brokerage
    wins = acc.wins
    biggest_mult = acc.biggest_mult
    worst_streak = acc.worst_streak
    first_traded_idx = acc.first_traded_idx
    last_expiry_idx = acc.last_expiry_idx
    n_attempted = acc.n_attempted
    n_cycles_skipped = acc.n_cycles_skipped

    if not cycles or first_traded_idx is None:
        raise LookupError(
            f"not enough price history as of {as_of.isoformat()} to complete "
            f"one {tenor_weeks:g}-week roll for {asset}"
        )

    # The underlying over exactly the traded window (first entry -> last expiry),
    # aligned with equity_curve, for the strategy tape. The head before
    # first_traded_idx only exists for IV warm-up and isn't shown.
    price_path: list[PricePoint] = []
    mtm_curve: list[EquityPoint] = []
    if include_curves:
        price_path = [
            PricePoint(date=dates[k], price=float(px[k]))
            for k in range(first_traded_idx, last_expiry_idx + 1)
        ]
        mtm_curve = _mark_to_market_curve(
            cycles,
            spans,
            px=px,
            iv=iv,
            dates=dates,
            pricer=pricer,
            rate=rate,
            notional=notional,
            first_traded_idx=first_traded_idx,
            last_expiry_idx=last_expiry_idx,
            quotes=quotes,
        )

    total_premium = len(cycles) * notional
    # Net of brokerage everywhere: roi_on_premium == sum(net)/total_premium ==
    # (total_payoff - total_premium - total_brokerage)/total_premium.
    net_pnl = total_payoff - total_premium - total_brokerage
    roi_on_premium = net_pnl / total_premium
    # Annualized Sharpe over the per-roll returns (net/notional). rolls_per_year
    # paces the annualization by how often the strategy actually rolled -- which
    # is what the traded span measures, and what `lookback_years` does not. This
    # divided by the REQUESTED window until 2026-09-09; on a run trading 2.33
    # years against `years=5` that overstated the pacing by 2.1x and reported a
    # Sharpe of -468.
    traded_start = cycles[0].entry_date
    traded_end = cycles[-1].expiry_date
    traded_years = (traded_end - traded_start).days / 365.25
    rolls_per_year = len(cycles) / traded_years if traded_years > 0 else 0.0
    sharpe = annualized_sharpe(
        [c.net / notional for c in cycles], rolls_per_year=rolls_per_year, rate=rate
    )
    annualized_so_far = annualized_so_far_curve(
        [(c.expiry_date, c.net) for c in cycles],
        first_entry_date=cycles[0].entry_date,
        notional=notional,
    )

    # ALWAYS pace by what was actually traded, never by what was requested.
    #
    # This used to be conditional on `n_cycles_skipped > 0`, which tests the
    # wrong thing: the commonest truncation is not a skipped cycle, it is the
    # price series simply being shorter than the window asked for. Bronze OHLCV
    # is a rolling ~5-year Tiingo window while `/api/putlab/backtest` accepts
    # `years` up to 20, so that case is not exotic -- it is what any long
    # lookback does today. Measured on real SPY 5% / 12 weeks, where all three
    # runs trade the SAME 20 cycles over the SAME 4.79 years:
    #
    #     years=5   reported  -9.72%/yr
    #     years=10  reported  -4.98%/yr
    #     years=20  reported  -2.52%/yr     honest, all three: -10.13%/yr
    #
    # The bleed is understated fourfold at the top of the allowed range, and
    # this is a bleed strategy: understating it is understating the whole cost
    # of the hedge. `ranking.MIN_WINDOW_COVERAGE` guards the same bug class for
    # cross-name ranking and its comment names the mechanism exactly.
    #
    # Pacing by the traded span also moves the fully-covered case slightly
    # (-9.72% -> -10.13% above), because the first entry lands after the window
    # opens and the last expiry before it closes. That is the correct number:
    # the strategy was on risk for 4.79 years, not 5.
    final_annualized = annualized_return(roi_on_premium, traded_years)

    return PutBacktestResult(
        asset=asset,
        as_of=as_of,
        spot=float(px[-1]),
        notional=notional,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=lookback_years,
        rate=rate,
        n_cycles=len(cycles),
        total_premium=total_premium,
        total_payoff=total_payoff,
        total_brokerage=total_brokerage,
        net_pnl=net_pnl,
        roi_on_premium=roi_on_premium,
        annualized_return=final_annualized,
        hit_rate=wins / len(cycles),
        biggest_payoff_mult=biggest_mult,
        worst_bleed_streak=worst_streak,
        sharpe_ratio=sharpe,
        annualized_so_far=annualized_so_far,
        equity_curve=equity,
        mtm_curve=mtm_curve,
        price_path=price_path,
        cycles=cycles,
        priced_from="market" if quotes is not None else "model",
        quote_coverage_pct=(min(1.0, traded_years / lookback_years) if lookback_years > 0 else 0.0),
        fill_rate=(len(cycles) / n_attempted) if n_attempted > 0 else 0.0,
        n_cycles_skipped=n_cycles_skipped,
        traded_start=traded_start,
        traded_end=traded_end,
    )


def _mark_to_market_curve(
    cycles: list[PutRollCycle],
    spans: list[tuple[int, int]],
    *,
    px: np.ndarray,
    iv: np.ndarray,
    dates: list[dt.date],
    pricer: OptionPricer,
    rate: float,
    notional: float,
    first_traded_idx: int,
    last_expiry_idx: int,
    quotes: QuoteSource | None = None,
) -> list[EquityPoint]:
    """Daily mark-to-market cumulative P&L over ``first_traded_idx..last_expiry_idx``.

    One point per trading day, on the same dates as ``price_path``, so the
    frontend can plot it on the price axis. At day ``k`` the value is::

        mtm_k = realized_completed + unrealized_open

    where ``realized_completed`` sums the (net-of-brokerage) ``net`` of every
    cycle that has expired by ``k`` and is *not* the currently-open roll.

    **Model path** (``quotes is None``, unchanged from before this existed):
    ``unrealized_open = contracts * BS_raw(spot[k], strike, (expiry-k)/252, iv[k]) -
    notional - cost`` marks the open put with **raw** Black-Scholes (less its
    entry brokerage ``cost``, so the curve steps down by the cost at entry) —
    no ``PREMIUM_FLOOR_FRAC`` (flooring the mark would overstate a decayed OOM
    put and break the expiry identity below). ``sigma`` is clamped with
    ``IV_FLOOR``/``IV_CAP`` exactly as entry pricing does, so a dead-calm
    window (realized vol 0, which would make the pricer reject ``sigma<=0``)
    still marks cleanly.

    **Market path** (``quotes`` given): the open leg is a real listed
    contract, so it is marked from the SAME panel at the **bid** (you sell to
    close, not buy — see ``quote_fills.OptionsDxQuoteSource.mark``), not
    re-priced by the model. If a day's bid is unavailable, the last known
    real mark for that open cycle is carried forward rather than substituted
    with a model price — marking a market entry with the model here would
    show every roll losing ~100% of its premium on day 1, since the model has
    already underflowed to ~0 at the depths quote_fills exists for (see its
    module docstring). The very first day a mark is needed for a given cycle
    and no bid is available yet falls back to the cycle's own entry premium
    (the ask actually paid), never to the model.

    Rolls re-enter on the expiry date, so at a shared expiry index the
    newly-entered roll is treated as the open one (unrealized ~= 0) and the
    just-expired roll counts as realized. At ``k == expiry_idx`` the mark is
    always the exact intrinsic ``max(strike-spot,0)`` (both paths); since
    ``contracts * premium == notional``, ``unrealized = contracts*intrinsic -
    notional - cost = payoff - notional - cost = net``, so ``mtm_curve`` meets
    the realized ``equity_curve`` at every expiry date. Point-in-time safe:
    every input at ``k`` is known at ``k`` (``iv`` is backward-looking, and a
    quote lookup is filtered to the session dated ``k`` itself).
    """
    curve: list[EquityPoint] = []
    open_mark: float | None = None
    open_mark_pos: int | None = None
    for k in range(first_traded_idx, last_expiry_idx + 1):
        # The open roll is the (newest, at a shared boundary) cycle bracketing k.
        open_pos: int | None = None
        for c, (entry_idx, expiry_idx) in enumerate(spans):
            if entry_idx <= k <= expiry_idx:
                open_pos = c
        realized = sum(
            cycles[c].net
            for c, (_, expiry_idx) in enumerate(spans)
            if expiry_idx <= k and c != open_pos
        )
        unrealized = 0.0
        if open_pos is not None:
            cyc = cycles[open_pos]
            _, expiry_idx = spans[open_pos]
            if quotes is not None:
                if open_mark_pos != open_pos:  # a new roll opened -- forget the old mark
                    open_mark = None
                    open_mark_pos = open_pos
                if k == expiry_idx:
                    mark = max(cyc.strike - float(px[k]), 0.0)  # intrinsic (guards T=0)
                else:
                    bid = quotes.mark(
                        entry_date=dates[k],
                        strike=cyc.strike,
                        expiry=cyc.expiry_date,
                        basis=cyc.quote_basis,
                    )
                    if bid is not None:
                        mark = bid
                        open_mark = bid
                    elif open_mark is not None:
                        mark = open_mark  # carry the last known REAL mark forward
                    else:
                        mark = cyc.premium  # no mark seen yet -- the entry ask, not the model
            else:
                t_years = (expiry_idx - k) / 252.0
                if t_years <= 0.0:
                    mark = max(cyc.strike - float(px[k]), 0.0)  # intrinsic at expiry (guards T=0)
                else:
                    sigma = float(min(max(iv[k], IV_FLOOR), IV_CAP))
                    mark = pricer.price_put(
                        spot=float(px[k]), strike=cyc.strike, t_years=t_years, r=rate, sigma=sigma
                    )
            # Net of the open roll's entry brokerage, so the curve steps down by
            # the cost at entry and still converges to the net realized value at
            # expiry (contracts*intrinsic - notional - cost == net).
            unrealized = cyc.contracts * mark - notional - cyc.cost
        curve.append(EquityPoint(date=dates[k], cum_pnl=float(realized + unrealized)))
    return curve


def _close_series(bronze: pd.DataFrame) -> pd.Series:
    """Raw closing-price series indexed by trade date, sorted and de-duplicated.

    **Raw ``close``, deliberately — not ``adj_close``.** An option is written
    on the price that actually printed. A 5% OTM put struck on 2021-08-23 was
    struck off SPY's real close of 447.26, giving a 424.90 strike; the
    dividend-adjusted series says 418.02 that day, which would set the strike
    at 397.12 — a **6.5% error**, so the "5% OTM" backtest would in truth be
    running an ~11% OTM put. The error grows the further back the window
    reaches, because back-adjustment subtracts every dividend paid since.

    The cost of this choice is negligible and was measured (2026-08-21, real
    SPY bronze): using raw closes moves the trailing realized-vol proxy by
    0.3% of its mean and downside beta by ~0.4% relative — the small
    ex-dividend gaps that dividend adjustment exists to smooth. Trading a
    0.3% volatility artefact for a 6.5% strike error is not a close call.

    Total-return questions ("what would holding this have earned") still want
    ``adj_close`` and should read it explicitly rather than through here.
    See `docs/DISCOVERIES.md`.
    """
    ordered = bronze.sort_values("trade_date").drop_duplicates(subset="trade_date", keep="last")
    return pd.Series(
        ordered["close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["trade_date"]),
        name=str(ordered["symbol"].iloc[0]) if len(ordered) else "",
    )


def load_asof_series(store: LakeStore, asset: str, as_of: dt.date) -> tuple[pd.Series, pd.Series]:
    """Point-in-time ``(prices, iv_proxy)`` for ``asset`` as of ``as_of``.

    Reads only the bronze OHLCV snapshot known on or before ``as_of`` (one
    lake read), returning the **raw closing-price** path (see
    :func:`_close_series` for why raw rather than adjusted) and its
    trailing-realized-vol IV proxy. Factored out so a parameter sweep can read
    the path once and roll many strategies over it, rather than re-reading per
    cell. Raises ``LookupError`` if no snapshot exists as of that date.

    This is the single chokepoint every backtest path reads through —
    ``put_roll``, ``portfolio``, ``ranking``, ``metric_screen`` and the API
    routes — so the price basis is decided here, once, for all of them.
    """
    try:
        bronze = store.read_bronze_as_of(dataset_id(asset), as_of)
    except LookupError as exc:
        raise LookupError(f"no OHLCV for {asset} known as of {as_of.isoformat()}") from exc
    if bronze.empty:
        raise LookupError(f"no OHLCV for {asset} known as of {as_of.isoformat()}")
    prices = _close_series(bronze)
    return prices, trailing_realized_vol(prices)


def compute_put_backtest(
    store: LakeStore,
    *,
    asset: str,
    as_of: dt.date,
    notional: float,
    moneyness_pct: float,
    tenor_weeks: float,
    lookback_years: float,
    rate: float = DEFAULT_RATE,
    basis: PricingBasis | None = None,
    commission_per_contract: float = COMMISSION_PER_CONTRACT,
    spread_scale: float = 1.0,
    sizing_mode: SizingMode | None = None,
) -> PutBacktestResult:
    """Point-in-time Put Lab backtest for ``asset`` as of ``as_of``.

    Reads the as-of price path and its IV proxy and rolls the strategy. Raises
    ``LookupError`` if no snapshot exists as of that date or the window is too
    short for a single roll. ``commission_per_contract``/``spread_scale`` pass
    through to :func:`run_put_roll` (production uses the realistic defaults),
    and so does ``basis`` — see its dataclass and ``run_put_roll``'s docstring
    for the model-vs-market behavior it selects.

    ``sizing_mode``, when given, resolves the premium budget and ``notional``
    is ignored; a single asset is one leg, so it resolves with ``n_legs=1``.
    Leaving it ``None`` (the default) uses ``notional`` exactly as before --
    see ``research/backtest/sizing.py``. When ``sizing_mode`` is specifically a
    ``WealthFraction``, the result's ``time_average_growth`` is also populated
    (``research/backtest/growth.py``) -- a flat ``notional``/``FixedPremium``
    run has no ``wealth`` figure to compound the hedge against, so it stays
    ``None``.
    """
    budget = sizing_mode.resolve(n_legs=1) if sizing_mode is not None else notional
    prices, iv_proxy = load_asof_series(store, asset, as_of)
    result = run_put_roll(
        prices,
        iv_proxy,
        asset=asset,
        as_of=as_of,
        notional=budget,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=lookback_years,
        rate=rate,
        basis=basis,
        commission_per_contract=commission_per_contract,
        spread_scale=spread_scale,
    )
    if isinstance(sizing_mode, WealthFraction):
        growth = time_average_growth(
            [(p.date, p.price) for p in result.price_path],
            [(e.date, e.cum_pnl) for e in result.mtm_curve],
            wealth=sizing_mode.wealth,
        )
        result = result.model_copy(update={"time_average_growth": growth})
    return result
