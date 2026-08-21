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
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.brokerage import COMMISSION_PER_CONTRACT, roll_cost
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
PREMIUM_FLOOR_FRAC = 1e-4
#: Don't annualize the running return-on-premium until at least this much of the
#: window has elapsed. Annualizing a two-week ROI raises ``(1+roi)`` to the 26th
#: power, which explodes a small early loss/gain into a nonsense yearly rate;
#: starting the "annualized so far" curve at a quarter-year keeps every point a
#: meaningful horizon rather than a numeric artifact (README convex-hedge lens).
MIN_YEARS_FOR_ANNUALIZED = 0.25


class PutRollCycle(BaseModel):
    """One entry-to-expiry roll of the strategy."""

    entry_date: dt.date
    expiry_date: dt.date
    spot: float
    strike: float
    sigma: float  # IV proxy (clamped realized-vol) used to price the entry premium
    premium: float  # model price of one put at entry
    contracts: float  # notional / premium
    cost: float  # entry brokerage (commission + bid-ask half-spread) for this roll
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
    if sd == 0.0:
        return None
    rf_per_roll = rate / rolls_per_year
    excess = float(arr.mean()) - rf_per_roll
    return float(excess / sd * np.sqrt(rolls_per_year))


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
    pricer: OptionPricer | None = None,
    commission_per_contract: float = COMMISSION_PER_CONTRACT,
    spread_scale: float = 1.0,
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

    Every roll's ``net`` is **net of realistic retail brokerage** (see
    :mod:`tail_lab.research.backtest.brokerage`): a ``$0.65``/contract
    commission plus a tenor/moneyness-dependent bid-ask half-spread, paid at
    entry. ``commission_per_contract`` and ``spread_scale`` are exposed only so
    a test can run the identical strategy cost-free (both ``0``) to isolate the
    cost drag; production callers use the defaults.
    """
    if not prices.index.equals(iv_proxy.index):
        raise ValueError("prices and iv_proxy must share the same date index")
    if notional <= 0 or tenor_weeks <= 0 or not 0 < moneyness_pct < 100:
        raise ValueError("notional>0, tenor_weeks>0, and 0<moneyness_pct<100 required")

    pricer = pricer or BlackScholesPricer()
    tenor_days = max(round(tenor_weeks * TRADING_DAYS_PER_WEEK), 1)
    t_years = tenor_days / 252.0
    n = len(prices)
    lookback_days = round(lookback_years * 252)
    first_entry = max(n - lookback_days, IV_WINDOW)

    px = prices.to_numpy(dtype=float)
    iv = iv_proxy.to_numpy(dtype=float)
    dates = [d.date() if isinstance(d, pd.Timestamp) else d for d in prices.index]

    cycles: list[PutRollCycle] = []
    spans: list[tuple[int, int]] = []  # (entry_idx, expiry_idx) per cycle, for the MTM marks
    cum = 0.0
    equity: list[EquityPoint] = []
    total_payoff = 0.0
    total_brokerage = 0.0
    wins = 0
    biggest_mult = 0.0
    worst_streak = 0
    streak = 0

    first_traded_idx: int | None = None
    last_expiry_idx = first_entry

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

        if not equity:
            equity.append(EquityPoint(date=dates[i], cum_pnl=0.0))
        cum += net
        total_payoff += payoff
        total_brokerage += cost
        if net > 0:
            wins += 1
        biggest_mult = max(biggest_mult, payoff / notional)
        if net < 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

        cycles.append(
            PutRollCycle(
                entry_date=dates[i],
                expiry_date=dates[i + tenor_days],
                spot=float(spot),
                strike=float(strike),
                sigma=sigma,
                premium=float(premium),
                contracts=float(contracts),
                cost=float(cost),
                payoff=float(payoff),
                net=float(net),
            )
        )
        equity.append(EquityPoint(date=dates[i + tenor_days], cum_pnl=float(cum)))
        spans.append((i, i + tenor_days))
        if first_traded_idx is None:
            first_traded_idx = i
        last_expiry_idx = i + tenor_days
        i += tenor_days

    if not cycles or first_traded_idx is None:
        raise LookupError(
            f"not enough price history as of {as_of.isoformat()} to complete "
            f"one {tenor_weeks:g}-week roll for {asset}"
        )

    # The underlying over exactly the traded window (first entry -> last expiry),
    # aligned with equity_curve, for the strategy tape. The head before
    # first_traded_idx only exists for IV warm-up and isn't shown.
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
    )

    total_premium = len(cycles) * notional
    # Net of brokerage everywhere: roi_on_premium == sum(net)/total_premium ==
    # (total_payoff - total_premium - total_brokerage)/total_premium.
    net_pnl = total_payoff - total_premium - total_brokerage
    roi_on_premium = net_pnl / total_premium
    # Annualized Sharpe over the per-roll returns (net/notional). rolls_per_year
    # paces the annualization by how often the strategy actually rolled.
    rolls_per_year = len(cycles) / lookback_years if lookback_years > 0 else 0.0
    sharpe = annualized_sharpe(
        [c.net / notional for c in cycles], rolls_per_year=rolls_per_year, rate=rate
    )
    annualized_so_far = annualized_so_far_curve(
        [(c.expiry_date, c.net) for c in cycles],
        first_entry_date=cycles[0].entry_date,
        notional=notional,
    )
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
        annualized_return=annualized_return(roi_on_premium, lookback_years),
        hit_rate=wins / len(cycles),
        biggest_payoff_mult=biggest_mult,
        worst_bleed_streak=worst_streak,
        sharpe_ratio=sharpe,
        annualized_so_far=annualized_so_far,
        equity_curve=equity,
        mtm_curve=mtm_curve,
        price_path=price_path,
        cycles=cycles,
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
) -> list[EquityPoint]:
    """Daily mark-to-market cumulative P&L over ``first_traded_idx..last_expiry_idx``.

    One point per trading day, on the same dates as ``price_path``, so the
    frontend can plot it on the price axis. At day ``k`` the value is::

        mtm_k = realized_completed + unrealized_open

    where ``realized_completed`` sums the (net-of-brokerage) ``net`` of every
    cycle that has expired by ``k`` and is *not* the currently-open roll, and
    ``unrealized_open = contracts * BS_raw(spot[k], strike, (expiry-k)/252, iv[k]) - notional - cost``
    marks the open put with **raw** Black-Scholes (less its entry brokerage
    ``cost``, so the curve steps down by the cost at entry) — no
    ``PREMIUM_FLOOR_FRAC``
    (flooring the mark would overstate a decayed OOM put and break the expiry
    identity below). ``sigma`` is clamped with ``IV_FLOOR``/``IV_CAP`` exactly
    as entry pricing does, so a dead-calm window (realized vol 0, which would
    make the pricer reject ``sigma<=0``) still marks cleanly.

    Rolls re-enter on the expiry date, so at a shared expiry index the
    newly-entered roll is treated as the open one (unrealized ~= 0) and the
    just-expired roll counts as realized. At ``k == expiry_idx`` the raw BS value
    with ``t_years=0`` is the intrinsic ``max(strike-spot,0)``; since
    ``contracts * premium == notional``, ``unrealized = contracts*intrinsic -
    notional - cost = payoff - notional - cost = net``, so ``mtm_curve`` meets the realized
    ``equity_curve`` at every expiry date. Point-in-time safe: every input at
    ``k`` is known at ``k`` (``iv`` is backward-looking).
    """
    curve: list[EquityPoint] = []
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
    pricer: OptionPricer | None = None,
    commission_per_contract: float = COMMISSION_PER_CONTRACT,
    spread_scale: float = 1.0,
) -> PutBacktestResult:
    """Point-in-time Put Lab backtest for ``asset`` as of ``as_of``.

    Reads the as-of price path and its IV proxy and rolls the strategy. Raises
    ``LookupError`` if no snapshot exists as of that date or the window is too
    short for a single roll. ``commission_per_contract``/``spread_scale`` pass
    through to :func:`run_put_roll` (production uses the realistic defaults).
    """
    prices, iv_proxy = load_asof_series(store, asset, as_of)
    return run_put_roll(
        prices,
        iv_proxy,
        asset=asset,
        as_of=as_of,
        notional=notional,
        moneyness_pct=moneyness_pct,
        tenor_weeks=tenor_weeks,
        lookback_years=lookback_years,
        rate=rate,
        pricer=pricer,
        commission_per_contract=commission_per_contract,
        spread_scale=spread_scale,
    )
