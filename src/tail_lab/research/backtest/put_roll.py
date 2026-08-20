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

import numpy as np
import pandas as pd
from pydantic import BaseModel

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import LakeStore
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


class PutRollCycle(BaseModel):
    """One entry-to-expiry roll of the strategy."""

    entry_date: dt.date
    expiry_date: dt.date
    spot: float
    strike: float
    sigma: float
    premium: float  # model price of one put at entry
    contracts: float  # notional / premium
    payoff: float  # contracts * max(strike - spot_at_expiry, 0)
    net: float  # payoff - notional (premium budget spent)


class EquityPoint(BaseModel):
    date: dt.date
    cum_pnl: float


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
    net_pnl: float
    roi_on_premium: float
    hit_rate: float
    biggest_payoff_mult: float
    worst_bleed_streak: int
    equity_curve: list[EquityPoint]
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
    cum = 0.0
    equity: list[EquityPoint] = []
    total_payoff = 0.0
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

        spot_at_expiry = px[i + tenor_days]
        payoff = contracts * max(strike - spot_at_expiry, 0.0)
        net = payoff - notional

        if not equity:
            equity.append(EquityPoint(date=dates[i], cum_pnl=0.0))
        cum += net
        total_payoff += payoff
        if payoff > notional:
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
                payoff=float(payoff),
                net=float(net),
            )
        )
        equity.append(EquityPoint(date=dates[i + tenor_days], cum_pnl=float(cum)))
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

    total_premium = len(cycles) * notional
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
        net_pnl=total_payoff - total_premium,
        roi_on_premium=(total_payoff - total_premium) / total_premium,
        hit_rate=wins / len(cycles),
        biggest_payoff_mult=biggest_mult,
        worst_bleed_streak=worst_streak,
        equity_curve=equity,
        price_path=price_path,
        cycles=cycles,
    )


def _adj_close_series(bronze: pd.DataFrame) -> pd.Series:
    """Adjusted-close series indexed by trade date, sorted and de-duplicated."""
    ordered = bronze.sort_values("trade_date").drop_duplicates(subset="trade_date", keep="last")
    return pd.Series(
        ordered["adj_close"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(ordered["trade_date"]),
        name=str(ordered["symbol"].iloc[0]) if len(ordered) else "",
    )


def load_asof_series(store: LakeStore, asset: str, as_of: dt.date) -> tuple[pd.Series, pd.Series]:
    """Point-in-time ``(prices, iv_proxy)`` for ``asset`` as of ``as_of``.

    Reads only the bronze OHLCV snapshot known on or before ``as_of`` (one
    lake read), returning the adjusted-close path and its trailing-realized-
    vol IV proxy. Factored out so a parameter sweep can read the path once and
    roll many strategies over it, rather than re-reading per cell. Raises
    ``LookupError`` if no snapshot exists as of that date.
    """
    try:
        bronze = store.read_bronze_as_of(dataset_id(asset), as_of)
    except LookupError as exc:
        raise LookupError(f"no OHLCV for {asset} known as of {as_of.isoformat()}") from exc
    if bronze.empty:
        raise LookupError(f"no OHLCV for {asset} known as of {as_of.isoformat()}")
    prices = _adj_close_series(bronze)
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
) -> PutBacktestResult:
    """Point-in-time Put Lab backtest for ``asset`` as of ``as_of``.

    Reads the as-of price path and its IV proxy and rolls the strategy. Raises
    ``LookupError`` if no snapshot exists as of that date or the window is too
    short for a single roll.
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
    )
