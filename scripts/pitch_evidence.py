"""The real-quote evidence behind the Pitch tab: does buying puts pay?

OFFLINE and human-run (``make pitch-evidence``): reads the lake (whatever
``TAIL_LAB_LAKE_BACKEND`` names; the parsed ``optionsdx_quotes_*`` bronze
tables were measured in the Tigris lake on 2026-10-27 -- the raw vendor
archives ``docs/DATA_CONTRACTS.md`` #12 describes are not needed) through the
store's as-of reads
(``docs/adr/0009``), never writes to it, and writes ONE file of aggregate
statistics, ``frontend/src/content/pitch_evidence.json``, that the Pitch tab
renders. No raw quote row is written -- no bid, ask or strike, refusal
reasons included: the optionsDX licence forbids redistribution and
``option_quotes`` is cleared for private research only; aggregates are fine.

Nothing here prices an option with a model. Every purchase goes through the
platform's own guard pipeline,
``research.backtest.quote_fills.OptionsDxQuoteSource``: the exact session only
(point in time), the nearest listed expiry AT OR AFTER the target, a listed
strike within ``MAX_SNAP_MONEYNESS_PP`` (1pp) of the requested distance, bid
> 0 and relative spread <= ``marks.MAX_RELATIVE_SPREAD`` (50%), bought at the
ASK. A sale, and every daily mark of a hedge book, is at the BID of the exact
contract bought (``OptionsDxQuoteSource.mark``), checked only for the session,
an exact strike match and bid > 0 -- the spread cap does not apply on exit. When there is no exit bid it is credited intrinsic
(the American put's exercise value), counted per rule as
``exits_at_intrinsic``. Almost all of those are not refused bids: the panel keeps
only strikes within ``contracts.optionsdx`` MONEYNESS_MIN..MONEYNESS_MAX
(0.60-1.02) of spot, so a put more than 2% in the money at exit has no quote at
all (``exits_beyond_panel``). Intrinsic drops the time value such a put still
has, and that happens on crash exits, so it biases sold-rule returns, winners
and hedge growth AGAINST the put; the same truncation marks a deep put at
intrinsic on the books' daily paths. Commission ``brokerage.COMMISSION_PER_CONTRACT``
($0.65) per contract on a buy and on a sale, none on expiry.

Three questions, all on SPY (2010-2023) and, for the first two, QQQ (2012-2023):

1. **Standalone.** 12 rules (4 depths x 3 structures). Return per dollar of
   premium, with a circular block-bootstrap CI over rolls.
2. **As a hedge.** SPY plus a flat dividend accrual, spending b x wealth per
   roll on the rule's puts; time-average (log) growth against the unhedged
   book over one common window.
3. **Timing.** 35 gates on six signals, chosen on rolls entered 2010-2016 and
   tested on 2017-2023; Benjamini-Hochberg across every combination.
4. **2008-09.** The optionsDX panels start in 2010, so the crisis they miss is
   checked separately on ``option_quotes`` (SPY puts on monthly roll dates
   only, licence unresolved -- research only) through ``OptionQuotesSource``.

Disclosed simplifications (the page prints them): a flat 1.9% dividend yield
on both arms (``tiingo_eod`` is not in the lake), fractional contracts,
American early exercise ignored, expiry settled at the panel's spot on the
last session on or before expiry.

    cd <a checkout whose .env reaches the lake>
    env -u PYTHONPATH .venv/bin/python scripts/pitch_evidence.py --out <path>
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import math
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import tail_lab
from tail_lab.config import Settings, get_lake_store
from tail_lab.contracts.optionsdx import MONEYNESS_MAX
from tail_lab.contracts.regime import classify_vix_series
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest import marks
from tail_lab.research.backtest.brokerage import COMMISSION_PER_CONTRACT
from tail_lab.research.backtest.marks import MAX_RELATIVE_SPREAD, MIN_OPEN_INTEREST
from tail_lab.research.backtest.multiple_testing import benjamini_hochberg
from tail_lab.research.backtest.quote_fills import (
    MAX_SNAP_MONEYNESS_PP,
    OptionQuotesSource,
    OptionsDxQuoteSource,
)
from tail_lab.research.skew import implied_vol_put
from tail_lab.research.surface.implied_alpha import fit_implied_alpha
from tail_lab.research.surface.ladder import Anchor
from tail_lab.transforms.vix import bronze_to_silver, silver_to_gold

#: ``index_replication.DEFAULT_DIVIDEND_YIELD``. ``tiingo_eod`` is absent from
#: the lake (HUMAN_TODO), so both arms of every comparison accrue this.
DIV_YIELD = 0.019
SEED = 20261027
BLOCK = 6  # rolls per bootstrap block (~6 months of monthly rolls)
CI_REPS = 5000
#: Bootstrap draws per p-value. 10,000 puts the floor, 1 / 10,001, under the
#: first Benjamini-Hochberg step over ~400 tests (0.10 / 408 = 0.000245): with
#: 2,000 the floor (0.0005) sat above it and no single test could ever pass.
P_REPS = 10_000
FDR = 0.10
DEPTHS = (5.0, 10.0, 15.0, 20.0)
#: name -> (tenor weeks, hold weeks or None = hold to expiry)
STRUCTS: dict[str, tuple[float, float | None]] = {
    "4w_hold": (4.0, None),
    "8w_sell4w": (8.0, 4.0),
    "12w_sell4w": (12.0, 4.0),
}
#: The page's "declines of 10% or more", and the key ``episodes_10pct``.
EPISODE_DD = 0.10
BUDGETS = (0.0025, 0.005, 0.01)  # of the book, per roll
SPANS = {"SPY": ("2010-01-04", "2023-12-29"), "QQQ": ("2012-01-03", "2023-12-29")}
PERIODS = {
    "2010-2013": ("2010-01-01", "2013-12-31"),
    "2014-2017": ("2014-01-01", "2017-12-31"),
    "2018-2020": ("2018-01-01", "2020-12-31"),
    "2021-2023": ("2021-01-01", "2023-12-31"),
}
IS_END = pd.Timestamp("2016-12-31")
PHASES = 20  # sessions in a 4-week cycle: the every-session study's phases
TIMING_BUDGET = 0.005
MIN_IS_TAKEN = 8
PANEL_COLUMNS = ["underlying", "quote_date", "expiration", "strike", "bid", "ask", "spot", "iv"]
SIGNALS = ("vix_lvl", "vix_z", "put_rich", "put_cost", "alpha")
REGIME_GATES: dict[str, frozenset[str]] = {
    "calm": frozenset({"calm"}),
    "elevated": frozenset({"elevated"}),
    "crisis": frozenset({"crisis"}),
    "not_calm": frozenset({"elevated", "crisis"}),
    "not_crisis": frozenset({"calm", "elevated"}),
}

Json = dict[str, object]


# ---------------------------------------------------------------- roll engine


@dataclass
class Roll:
    entry: pd.Timestamp
    exit: pd.Timestamp
    strike: float
    ask: float
    spot_entry: float
    spot_exit: float
    dte: int
    roi: float  # per unit of premium budget, after commissions
    path: pd.Series = field(repr=False)  # daily value of one unit of budget
    #: A sale with no qualifying exit bid, credited at intrinsic
    #: (strike - spot, what exercising the American put pays) instead.
    exit_at_intrinsic: bool = False
    #: Of those, one whose strike/spot at exit is above the panel's
    #: ``MONEYNESS_MAX``: the dataset holds no quote for it at all, so the
    #: credit omits the put's remaining time value (biased against the put).
    exit_beyond_panel: bool = False


@dataclass
class Market:
    symbol: str
    src: OptionsDxQuoteSource
    sessions: pd.DatetimeIndex
    spot: pd.Series
    chain: pd.DataFrame


def market_from_panel(panel: pd.DataFrame, symbol: str) -> Market:
    spot = panel.groupby("quote_date")["spot"].first().sort_index()
    return Market(
        symbol,
        OptionsDxQuoteSource(panel, symbol=symbol),
        pd.DatetimeIndex(spot.index),
        spot,
        panel,
    )


def first_on_or_after(sessions: pd.DatetimeIndex, d: pd.Timestamp) -> pd.Timestamp | None:
    i = int(sessions.searchsorted(d, side="left"))
    return sessions[i] if i < len(sessions) else None


def last_on_or_before(sessions: pd.DatetimeIndex, d: pd.Timestamp) -> pd.Timestamp | None:
    i = int(sessions.searchsorted(d, side="right")) - 1
    return sessions[i] if i >= 0 else None


def unit_value(contracts: float, per_share: float, *, sold: bool) -> float:
    """What ``contracts`` puts are worth per unit of budget: a sale pays the
    commission, an expiry settlement does not."""
    gross = contracts * 100.0 * per_share
    fee = contracts * COMMISSION_PER_CONTRACT if (sold and per_share > 0) else 0.0
    return max(gross - fee, 0.0)


def _exit_session(
    mkt: Market, t: pd.Timestamp, expiry: pd.Timestamp, hold_weeks: float | None
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(exit session, next entry session), or None when the roll would not
    complete inside the panel."""
    if hold_weeks is None:
        settle = last_on_or_before(mkt.sessions, expiry)
        nxt = first_on_or_after(mkt.sessions, expiry)
        if nxt is None or settle is None or settle <= t:
            return None
        return settle, nxt
    exit_ = first_on_or_after(mkt.sessions, t + pd.Timedelta(days=round(hold_weeks * 7)))
    if exit_ is None or exit_ > expiry:
        return None
    return exit_, exit_


def _path(
    mkt: Market,
    t: pd.Timestamp,
    exit_: pd.Timestamp,
    contracts: float,
    fill_strike: float,
    expiry: dt.date,
    basis: float,
    final: float,
) -> pd.Series:
    """Daily value of one unit of budget: at entry 1 less commission, marked
    at the BID in between (intrinsic when no bid qualifies), ``final`` on exit."""
    days = mkt.sessions[(mkt.sessions >= t) & (mkt.sessions <= exit_)]
    vals = []
    for d in days:
        if d == t:
            vals.append(1.0 - contracts * COMMISSION_PER_CONTRACT)
        elif d == exit_:
            vals.append(final)
        else:
            bid = mkt.src.mark(entry_date=d.date(), strike=fill_strike, expiry=expiry, basis=basis)
            vals.append(
                contracts
                * 100.0
                * (bid if bid is not None else max(fill_strike - float(mkt.spot[d]), 0.0))
            )
    return pd.Series(vals, index=days)


def run_rolls(
    mkt: Market,
    *,
    moneyness: float,
    tenor_weeks: float,
    hold_weeks: float | None,
    start: str,
    end: str,
    daily_paths: bool = True,
) -> tuple[list[Roll], int]:
    """Back-to-back rolls: buy at the ask on session t, exit, buy the next on
    the exit session. A refused fill tries the next session (counted)."""
    t = first_on_or_after(mkt.sessions, pd.Timestamp(start))
    rolls: list[Roll] = []
    skips = 0
    while t is not None and t <= pd.Timestamp(end):
        s0 = float(mkt.spot[t])
        f = mkt.src.fill(
            entry_date=t.date(), spot=s0, moneyness_pct=moneyness, tenor_weeks=tenor_weeks
        )
        if f is None:
            skips += 1
            t = first_on_or_after(mkt.sessions, t + pd.Timedelta(days=1))
            continue
        ex = _exit_session(mkt, t, pd.Timestamp(f.expiry), hold_weeks)
        if ex is None:
            break
        exit_, nxt = ex
        contracts = 1.0 / (100.0 * f.premium + COMMISSION_PER_CONTRACT)
        s1 = float(mkt.spot[exit_])
        v = max(f.strike - s1, 0.0)
        at_intrinsic = False
        if hold_weeks is not None:
            bid = mkt.src.mark(
                entry_date=exit_.date(), strike=f.strike, expiry=f.expiry, basis=f.basis
            )
            # No qualifying bid: credit intrinsic, the exercise value of an
            # American put (zero when out of the money). On the real panel that
            # is mostly a strike beyond MONEYNESS_MAX of spot, which the dataset
            # dropped -- not a refused bid. Counted separately and printed.
            at_intrinsic = bid is None
            v = bid if bid is not None else v
        roi = unit_value(contracts, v, sold=hold_weeks is not None) - 1.0
        path = (
            _path(mkt, t, exit_, contracts, f.strike, f.expiry, f.basis, roi + 1.0)
            if daily_paths
            else pd.Series(dtype=float)
        )
        # The caller's spot here IS the panel's, so the basis is 1 and this is
        # the same strike/spot the ingestion slice was cut on.
        beyond = at_intrinsic and f.strike / s1 > MONEYNESS_MAX
        rolls.append(
            Roll(
                t,
                exit_,
                f.strike,
                f.premium,
                s0,
                s1,
                f.realized_dte,
                roi,
                path,
                at_intrinsic,
                beyond,
            )
        )
        t = nxt
    return rolls, skips


def rolls_frame(rolls: Sequence[Roll]) -> pd.DataFrame:
    keys = ("entry", "exit", "strike", "ask", "spot_entry", "spot_exit", "dte", "roi")
    return pd.DataFrame([{k: getattr(r, k) for k in keys} for r in rolls])


# ------------------------------------------------------------------ books


def _equity(
    mkt: Market, a: pd.Timestamp, b: pd.Timestamp, w: float, out: dict[pd.Timestamp, float]
) -> float:
    days = mkt.sessions[(mkt.sessions >= a) & (mkt.sessions <= b)]
    sp = mkt.spot.loc[days].to_numpy()
    yrs = (days - days[0]).days.to_numpy() / 365.25
    vals = w * (sp / sp[0]) * np.exp(DIV_YIELD * yrs)
    out.update(zip(days, vals, strict=True))
    return float(vals[-1])


def unhedged(mkt: Market, a: pd.Timestamp, b: pd.Timestamp) -> pd.Series:
    out: dict[pd.Timestamp, float] = {}
    _equity(mkt, a, b, 1.0, out)
    return pd.Series(out).sort_index()


def book(
    mkt: Market, rolls: Sequence[Roll], *, budget: float, skip: int | None = None
) -> pd.Series:
    """Daily wealth of a book that at each roll entry spends ``budget`` x
    wealth on that roll's puts and holds the rest in the underlying (price
    plus the flat dividend accrual); at each exit everything is rebalanced.
    Between rolls (a refused fill) it is fully invested, never dropped.
    Roll number ``skip``, if given, is left unhedged: the same book without
    that one trade."""
    w = 1.0
    out: dict[pd.Timestamp, float] = {}
    prev: pd.Timestamp | None = None
    for i, r in enumerate(rolls):
        if prev is not None and r.entry > prev:
            w = _equity(mkt, prev, r.entry, w, out)
        b = 0.0 if i == skip else budget
        days = r.path.index
        sp = mkt.spot.loc[days].to_numpy()
        yrs = (days - days[0]).days.to_numpy() / 365.25
        vals = (1.0 - b) * w * (sp / sp[0]) * np.exp(DIV_YIELD * yrs) + b * w * r.path.to_numpy()
        out.update(zip(days, vals, strict=True))
        w = float(vals[-1])
        prev = r.exit
    return pd.Series(out).sort_index()


def peak_put(rolls: Sequence[Roll], peak: str) -> Json | None:
    """The roll open on a drawdown's peak day, if any: its put's value then
    and at its exit, per unit of premium (1 = what it cost). A peak far above
    1 that settles far below it is a paper mark given back, not premium bled
    through a falling market."""
    d = pd.Timestamp(peak)
    for r in rolls:
        if r.entry < d < r.exit:
            return {
                "entry": str(r.entry.date()),
                "exit": str(r.exit.date()),
                "mark_multiple": float(r.path[d]),
                "exit_multiple": r.roi + 1.0,
            }
    return None


def ann_log_growth(w: pd.Series) -> float:
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    return float(np.log(w.iloc[-1] / w.iloc[0]) / yrs)


def max_drawdown(w: pd.Series) -> float:
    return float((w / w.cummax() - 1.0).min())


def drawdown_span(w: pd.Series) -> tuple[str, str]:
    """(peak date, trough date) of the worst drawdown: says WHICH decline the
    number measures -- a market fall, or a hedge's own gain given back."""
    dd = w / w.cummax() - 1.0
    trough = dd.idxmin()
    return str(w[:trough].idxmax().date()), str(trough.date())


# ------------------------------------------------------------- statistics


def block_bootstrap_idx(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap indices: blocks of ``block`` consecutive rolls,
    so a crash that spans two rolls stays together."""
    starts = rng.integers(0, n, size=math.ceil(n / block))
    return ((starts[:, None] + np.arange(block)[None, :]).ravel() % n)[:n]


def ci95(draws: np.ndarray) -> list[float]:
    """The 2.5th and 97.5th percentiles of a bootstrap distribution."""
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def boot_mean_ci(x: np.ndarray, rng: np.random.Generator, reps: int = CI_REPS) -> list[float]:
    return ci95(np.array([x[block_bootstrap_idx(len(x), BLOCK, rng)].mean() for _ in range(reps)]))


def bootstrap_p(draws: np.ndarray) -> float:
    """One-sided p for "the effect is <= 0": the share of resamples at or
    below zero, with the (b + 1) / (R + 1) correction so a p is never 0 --
    a bootstrap of R draws cannot resolve anything finer than 1 / (R + 1),
    and a p of exactly 0 would pass any Benjamini-Hochberg line it meets."""
    return float((np.sum(draws <= 0) + 1) / (len(draws) + 1))


def longest_losing_streak(roi: np.ndarray) -> int:
    best = run = 0
    for v in roi:
        run = run + 1 if v <= 0 else 0
        best = max(best, run)
    return best


def drawdown_episodes(spot: pd.Series, thresh: float) -> list[Json]:
    """Distinct peak-to-trough declines of at least ``thresh``; a new episode
    starts only after a full recovery to the prior peak."""
    eps: list[Json] = []
    peak_v, peak_d = float(spot.iloc[0]), spot.index[0]
    trough_v, trough_d = peak_v, peak_d
    for d, v in spot.items():
        if v >= peak_v:
            if trough_v / peak_v - 1 <= -thresh:
                eps.append(
                    {
                        "peak": str(peak_d.date()),
                        "trough": str(trough_d.date()),
                        "dd": trough_v / peak_v - 1,
                    }
                )
            peak_v, peak_d, trough_v, trough_d = float(v), d, float(v), d
        elif v < trough_v:
            trough_v, trough_d = float(v), d
    if trough_v / peak_v - 1 <= -thresh:
        eps.append(
            {
                "peak": str(peak_d.date()),
                "trough": str(trough_d.date()),
                "dd": trough_v / peak_v - 1,
                "unrecovered": True,
            }
        )
    return eps


# ----------------------------------------------------------- 1. standalone


def standalone_stats(df: pd.DataFrame, rng: np.random.Generator) -> Json:
    r = df["roi"].to_numpy()
    pnl = r + 1.0
    by_period: Json = {}
    for p, (a, b) in PERIODS.items():
        sub = df[(df.entry >= a) & (df.entry <= b)]
        if len(sub):
            by_period[p] = {
                "n": len(sub),
                "mean_roi": float(sub.roi.mean()),
                "hits": int((sub.roi > 0).sum()),
            }
    return {
        "n_rolls": len(r),
        "mean_roi": float(r.mean()),
        "mean_roi_ci95": boot_mean_ci(r, rng),
        "hits": int((r > 0).sum()),
        "best_roi": float(r.max()),
        "best_entry": str(df.loc[df["roi"].idxmax(), "entry"].date()),
        "share_of_payoff_from_best_roll": float(pnl.max() / pnl.sum()) if pnl.sum() > 0 else None,
        "longest_losing_streak": longest_losing_streak(r),
        "median_ask_pct_spot": float((df["ask"] / df["spot_entry"]).median() * 100),
        "median_dte": float(df["dte"].median()),
        "first_entry": str(df.entry.iloc[0].date()),
        "last_exit": str(df.exit.iloc[-1].date()),
        "by_period": by_period,
    }


def phase_means(rois: pd.Series, period: int) -> list[float]:
    """Mean return of each of the ``period`` ways to phase a cycle: phase k
    keeps entries k, k + period, k + 2 x period, ... (NaN = refused, dropped)."""
    return [float(rois.iloc[k::period].dropna().mean()) for k in range(period)]


def dx_refusal_guard(session: pd.DataFrame | None, entry: pd.Timestamp, m: float) -> str:
    """Which ``OptionsDxQuoteSource.fill`` guard refused this session's
    ``m``% OOM 4-week put, checked in the pipeline's own order: no quotes,
    no expiry 28+ days out, no listed strike within ``MAX_SNAP_MONEYNESS_PP``
    of the depth (deep strikes go unlisted in calm years), no bid, or a
    spread over the cap. A count, never a quote, leaves the lake."""
    if session is None or session.empty:
        return "no_quotes"
    exp = session["expiration"][session["expiration"] >= entry + pd.Timedelta(days=28)]
    if exp.empty:
        return "no_expiry"
    at = session[session["expiration"] == exp.min()]
    spot = float(at["spot"].iloc[0])
    row = at.iloc[int((at["strike"] - spot * (1.0 - m / 100.0)).abs().to_numpy().argmin())]
    if abs((1.0 - float(row["strike"]) / spot) * 100.0 - m) > MAX_SNAP_MONEYNESS_PP:
        return "strike"
    bid, ask = float(row["bid"]), float(row["ask"])
    if bid <= 0.0 or ask <= 0.0:
        return "no_bid"
    if (ask - bid) / ((ask + bid) / 2) > MAX_RELATIVE_SPREAD:
        return "spread"
    return "other"


def every_day_entries(mkt: Market, m: float, span: tuple[str, str]) -> Json:
    """The roll calendar is arbitrary: buy a 4-week put on EVERY session and
    hold it to expiry; phase k keeps every 20th entry from session k. With a
    handful of tail events in the sample, the phase can decide the sign.
    Each refused session is counted under the guard that refused it, and the
    strike-guard refusals by year: refusal is not random, so the mean is
    over a sample the guards selected."""
    rois: list[float] = []
    by_guard: dict[str, int] = {}
    strike_by_year: dict[str, int] = {}
    rows_of = mkt.chain.groupby("quote_date").indices
    for t in mkt.sessions[(mkt.sessions >= span[0]) & (mkt.sessions <= span[1])]:
        f = mkt.src.fill(
            entry_date=t.date(), spot=float(mkt.spot[t]), moneyness_pct=m, tenor_weeks=4.0
        )
        settle = last_on_or_before(mkt.sessions, pd.Timestamp(f.expiry)) if f else None
        if f is None or settle is None or settle <= t or pd.Timestamp(f.expiry) > mkt.sessions[-1]:
            rois.append(float("nan"))
            pos = rows_of.get(t)
            guard = (
                dx_refusal_guard(None if pos is None else mkt.chain.iloc[pos], t, m)
                if f is None
                else "expiry_beyond_panel"
            )
            by_guard[guard] = by_guard.get(guard, 0) + 1
            if guard == "strike":
                strike_by_year[str(t.year)] = strike_by_year.get(str(t.year), 0) + 1
            continue
        c = 1.0 / (100.0 * f.premium + COMMISSION_PER_CONTRACT)
        rois.append(c * 100.0 * max(f.strike - float(mkt.spot[settle]), 0.0) - 1.0)
    g = pd.Series(rois)
    means = phase_means(g, PHASES)
    ok = g.dropna()
    return {
        "entry_days": len(ok),
        "refused_days": int(g.isna().sum()),
        "refused_by_guard": dict(sorted(by_guard.items())),
        "strike_refused_by_year": strike_by_year,
        "mean_roi": float(ok.mean()),
        "hit_rate": float((ok > 0).mean()),
        "phases": PHASES,
        "phases_positive": sum(x > 0 for x in means),
        "phase_min": min(means),
        "phase_max": max(means),
    }


# ------------------------------------------------------------- 2. hedge


def hedge(mkt: Market, rolls_by_rule: dict[str, list[Roll]]) -> Json:
    a = max(r[0].entry for r in rolls_by_rule.values())
    b = min(r[-1].exit for r in rolls_by_rule.values())
    w0 = unhedged(mkt, a, b)
    g0 = ann_log_growth(w0)
    rows: list[Json] = []
    n_beat = n_cut_dd = 0
    dd0 = max_drawdown(w0)

    def growth(rolls: list[Roll], budget: float, skip: int | None = None) -> pd.Series:
        w = book(mkt, rolls, budget=budget, skip=skip)[a:b]
        return w / w.iloc[0]

    for rule, rolls in rolls_by_rule.items():
        yrs = (rolls[-1].exit - rolls[0].entry).days / 365.25
        best = max(range(len(rolls)), key=lambda i: rolls[i].roi)
        for budget in BUDGETS:
            w = growth(rolls, budget)
            g = ann_log_growth(w)
            row: Json = {
                "rule": rule,
                "budget": budget,
                "premium_pct_per_year": budget * len(rolls) / yrs * 100,
                "growth": g,
                "edge": g - g0,
                "max_dd": max_drawdown(w),
                "max_dd_span": drawdown_span(w),
                "max_dd_peak_put": peak_put(rolls, drawdown_span(w)[0]),
            }
            if g > g0:
                n_beat += 1
                # Same window, same daily book, the best roll left unhedged:
                # one quantity, one value (the table's edge is this edge).
                row["drop_best"] = {
                    "edge": g - g0,
                    "edge_without_best": ann_log_growth(growth(rolls, budget, best)) - g0,
                    "best_entry": str(rolls[best].entry.date()),
                }
            n_cut_dd += max_drawdown(w) > dd0
            rows.append(row)
    return {
        "window": [str(a.date()), str(b.date())],
        "unhedged_growth": g0,
        "unhedged_max_dd": dd0,
        "unhedged_max_dd_span": drawdown_span(w0),
        "configs": rows,
        "n_beat": n_beat,
        "n_cut_dd": n_cut_dd,
    }


def cboe_benchmark(cboe: pd.DataFrame, span: Sequence[str]) -> Json:
    """Cboe's indices over ``span`` -- the hedge study's own window."""
    piv = cboe.pivot(index="trade_date", columns="index_symbol", values="close").sort_index()[
        span[0] : span[1]
    ]
    out: Json = {"span": [str(piv.index[0].date()), str(piv.index[-1].date())]}
    for sym in ("SPX", "PPUT", "PPUT3M", "VXTH"):
        s = piv[sym].dropna()
        out[sym] = {"growth": ann_log_growth(s), "max_dd": max_drawdown(s)}
    return out


# ------------------------------------------------------------- 3. timing


@dataclass
class TimingInputs:
    vix: pd.Series
    vix_z: pd.Series
    regime: pd.Series
    rate: pd.DataFrame  # DGS3MO vintages: obs_date, vintage_date, value (decimal)
    alpha_cache: dict[pd.Timestamp, float] = field(default_factory=dict)


def lagged(series: pd.Series, t: pd.Timestamp) -> object:
    """The last value strictly BEFORE ``t``: known at the entry close."""
    s = series[series.index < t]
    return s.iloc[-1] if len(s) else None


def timing_inputs(vix_bronze: pd.DataFrame, rates: pd.DataFrame) -> TimingInputs:
    gold = silver_to_gold(bronze_to_silver(vix_bronze)).set_index("date")
    labels = pd.Series(classify_vix_series(gold["close"].tolist()), index=gold.index)
    return TimingInputs(gold["close"], gold["z_score"], labels, rate_vintages(rates))


def rate_vintages(rates: pd.DataFrame) -> pd.DataFrame:
    """DGS3MO's vintages as decimals, sorted so the last row of any prefix
    by vintage is the newest observation in its newest print."""
    r = rates.loc[rates.series_id == "DGS3MO", ["obs_date", "vintage_date", "value"]]
    r = r.assign(value=r["value"] / 100.0)
    return r.sort_values(["obs_date", "vintage_date"]).reset_index(drop=True)


def rate_known_before(vintages: pd.DataFrame, t: pd.Timestamp) -> object:
    """The bill rate as published strictly BEFORE ``t``: the newest
    observation among vintages dated before ``t``, in its newest such print.
    Filtering on the observation date instead would read a value FRED had
    not yet published (first vintages trail their observation by 0-6 days)."""
    known = vintages[vintages.vintage_date < t]
    return known["value"].iloc[-1] if len(known) else None


def _alpha_anchor(mkt: Market, t: pd.Timestamp) -> tuple[Anchor, pd.DataFrame] | None:
    day = mkt.chain[mkt.chain.quote_date == t]
    if day.empty:
        return None
    spot = float(day["spot"].iloc[0])
    exps = sorted(e for e in day["expiration"].unique() if (pd.Timestamp(e) - t).days >= 28)
    if not exps:
        return None
    q = day[day.expiration == exps[0]].drop_duplicates("strike")
    below = q[q.strike < spot]
    if below.empty:
        return None
    row = below.iloc[int((below.strike - spot * 0.95).abs().argsort().iloc[0])]
    if not (0 < row.bid <= row.ask):  # a crossed or one-sided quote is no anchor
        return None
    anchor = Anchor(
        mkt.symbol,
        t.date(),
        pd.Timestamp(exps[0]).date(),
        spot,
        float(row.strike),
        (float(row.bid) + float(row.ask)) / 2,
        float(row.bid),
        float(row.ask),
    )
    return anchor, q[["strike", "bid", "ask", "quote_date", "expiration", "iv"]]


def implied_alpha_on(mkt: Market, t: pd.Timestamp, cache: dict[pd.Timestamp, float]) -> float:
    """The Surface's implied tail index at a 5%-OTM anchor on the entry
    session's first expiry >= 28 days out; NaN when the fit refuses."""
    if t not in cache:
        cache[t] = float("nan")
        got = _alpha_anchor(mkt, t)
        if got is not None:
            try:
                fit = fit_implied_alpha(*got)
            except ValueError:  # the anchor broke the fit's caller contract -> no reading
                fit = None
            if fit is not None and fit.alpha is not None:
                cache[t] = float(fit.alpha)
    return cache[t]


def _as_float(x: object) -> float:
    return float(x) if isinstance(x, int | float) else float("nan")


def signals(mkt: Market, df: pd.DataFrame, ti: TimingInputs) -> pd.DataFrame:
    """Every signal is known at the entry close or earlier: VIX, its stretch
    and the regime from the day BEFORE entry; the bill rate as published
    before the entry day; the chain signals from the entry session's own
    close, the same close the put is bought at."""
    s = pd.DataFrame(index=df.index)
    s["vix_lvl"] = [_as_float(lagged(ti.vix, t)) for t in df.entry]
    s["vix_z"] = [_as_float(lagged(ti.vix_z, t)) for t in df.entry]
    s["regime"] = [lagged(ti.regime, t) for t in df.entry]
    rich = []
    for r, v in zip(df.itertuples(), s["vix_lvl"], strict=True):
        rate = _as_float(rate_known_before(ti.rate, r.entry))
        iv = implied_vol_put(
            r.ask,
            spot=r.spot_entry,
            strike=r.strike,
            t_years=r.dte / 365.25,
            r=0.0 if math.isnan(rate) else rate,
            q=DIV_YIELD,
        )
        rich.append(iv / (v / 100.0) if iv is not None and v > 0 else float("nan"))
    s["put_rich"] = rich
    s["put_cost"] = (df.ask / df.spot_entry * 100).to_numpy()
    s["alpha"] = [implied_alpha_on(mkt, t, ti.alpha_cache) for t in df.entry]
    return s


Gate = tuple[str, str, float | frozenset[str]]


def gates(sig_is: pd.DataFrame) -> dict[str, Gate]:
    """35 gates: each continuous signal <= or >= its IN-SAMPLE 33/50/67th
    percentile (30), and five regime sets."""
    g: dict[str, Gate] = {}
    for col in SIGNALS:
        for q in (1 / 3, 1 / 2, 2 / 3):
            thr = float(np.nanquantile(sig_is[col].to_numpy(dtype=float), q))
            g[f"{col}<=p{round(q * 100)}"] = (col, "le", thr)
            g[f"{col}>=p{round(q * 100)}"] = (col, "ge", thr)
    for name, ok in REGIME_GATES.items():
        g[f"regime={name}"] = ("regime", "in", ok)
    return g


def apply_gate(sig: pd.DataFrame, gate: Gate) -> np.ndarray:
    col, op, thr = gate
    if isinstance(thr, frozenset):
        return sig[col].isin(thr).to_numpy()
    v = sig[col].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        return (v <= thr) if op == "le" else (v >= thr)  # NaN -> not taken


def roi_diff(roi: np.ndarray, take: np.ndarray) -> float:
    """Mean return on the rolls the gate takes minus the mean on all rolls:
    does the gate pick better rolls? (Total P&L would reward any gate that
    simply buys less of a losing trade.)"""
    return float(roi[take].mean() - roi.mean()) if take.sum() else float("nan")


def boot_roi_diff(
    roi: np.ndarray, take: np.ndarray, rng: np.random.Generator
) -> tuple[list[float], float]:
    ds = []
    for _ in range(P_REPS):
        i = block_bootstrap_idx(len(roi), BLOCK, rng)
        if take[i].sum():
            ds.append(roi[i][take[i]].mean() - roi[i].mean())
    d = np.array(ds)
    return ci95(d), bootstrap_p(d)


def boot_dg(terms: np.ndarray, yrs: float, rng: np.random.Generator) -> tuple[list[float], float]:
    """CI and p for the annualised growth difference: per-roll terms
    resampled in blocks of ``BLOCK`` rolls, like every other interval here."""
    bs = np.array(
        [terms[block_bootstrap_idx(len(terms), BLOCK, rng)].sum() / yrs for _ in range(P_REPS)]
    )
    return ci95(bs), bootstrap_p(bs)


def hedge_diff_terms(eq: np.ndarray, roi: np.ndarray, take: np.ndarray) -> np.ndarray:
    """Per-roll log growth of the gated book minus a SPEND-MATCHED ungated
    book (same rule, budget x the share of rolls taken)."""

    def lr(b: np.ndarray) -> np.ndarray:
        return np.log((1 - b) * eq + b * (1 + roi))

    return lr(TIMING_BUDGET * take) - lr(np.full(len(roi), TIMING_BUDGET * take.mean()))


@dataclass
class Combo:
    rule: str
    gate: str
    roi: np.ndarray
    eq: np.ndarray
    take: np.ndarray
    is_: np.ndarray
    yrs_is: float
    yrs_oos: float
    entries: tuple[str, ...]  # entry date of each roll, aligned with roi

    def stats(self, oos: bool) -> Json:
        m = ~self.is_ if oos else self.is_
        roi, tk, eq = self.roi[m], self.take[m], self.eq[m]
        yrs = self.yrs_oos if oos else self.yrs_is
        dg = (
            float(hedge_diff_terms(eq, roi, tk).sum() / yrs)
            if 0 < tk.sum() < len(tk)
            else float("nan")
        )
        return {
            "n": int(m.sum()),
            "taken": int(tk.sum()),
            "roi_diff": roi_diff(roi, tk),
            "hits_taken": int((roi[tk] > 0).sum()),
            "hits_all": int((roi > 0).sum()),
            "dg": dg,
            "mean_roi_taken": float(roi[tk].mean()) if tk.sum() else float("nan"),
            "mean_roi_all": float(roi.mean()),
        }


def rule_combos(rule: str, df: pd.DataFrame, sig: pd.DataFrame, eq: np.ndarray) -> list[Combo]:
    """One Combo per gate. Thresholds come from the IN-SAMPLE signals only;
    the gate is then applied to every roll, out-of-sample ones included."""
    is_ = (df.entry <= IS_END).to_numpy()
    yrs_is = (df.exit[is_].max() - df.entry[is_].min()).days / 365.25
    yrs_oos = (df.exit[~is_].max() - df.entry[~is_].min()).days / 365.25
    return [
        Combo(
            rule,
            gname,
            df.roi.to_numpy(),
            eq,
            apply_gate(sig, gate),
            is_,
            yrs_is,
            yrs_oos,
            tuple(str(d.date()) for d in df.entry),
        )
        for gname, gate in gates(sig[is_]).items()
    ]


def timing_combos(
    mkt: Market, ti: TimingInputs, span: tuple[str, str]
) -> tuple[list[Combo], dict[str, float]]:
    """Every rule x gate, and per rule the share of entries with an implied
    alpha reading (the fit refuses on thin chains)."""
    combos: list[Combo] = []
    coverage: dict[str, float] = {}
    for sname, (ten, hold) in STRUCTS.items():
        for m in DEPTHS:
            rolls, _ = run_rolls(
                mkt,
                moneyness=m,
                tenor_weeks=ten,
                hold_weeks=hold,
                start=span[0],
                end=span[1],
                daily_paths=False,
            )
            df = rolls_frame(rolls)
            eq = underlying_gross(df)
            sig = signals(mkt, df, ti)
            combos += rule_combos(f"{int(m)}pct_{sname}", df, sig, eq)
            coverage[f"{int(m)}pct_{sname}"] = float(sig.alpha.notna().mean())
            print(
                f"timing {int(m)}pct_{sname}: alpha coverage {sig.alpha.notna().mean():.2f}",
                file=sys.stderr,
            )
    return combos, coverage


def underlying_gross(df: pd.DataFrame) -> np.ndarray:
    """Each roll's unhedged arm: the underlying's gross return from entry to
    exit, with the flat dividend accrual."""
    return (
        df.spot_exit / df.spot_entry * np.exp(DIV_YIELD * (df.exit - df.entry).dt.days / 365.25)
    ).to_numpy()


def _select(rows: list[tuple[Combo, Json, Json]], key: str) -> list[tuple[Combo, Json, Json]]:
    ok = [
        r
        for r in rows
        if int(str(r[1]["taken"])) >= MIN_IS_TAKEN
        and not math.isnan(_as_float(r[1][key]))
        and not math.isnan(_as_float(r[2][key]))
    ]
    return sorted(ok, key=lambda r: -_as_float(r[1][key]))


def _spearman(ok: list[tuple[Combo, Json, Json]], key: str) -> float:
    a = pd.Series([_as_float(r[1][key]) for r in ok]).rank()
    b = pd.Series([_as_float(r[2][key]) for r in ok]).rank()
    return float(a.corr(b))


def timing(combos: list[Combo], rng: np.random.Generator) -> Json:
    rows = [(c, c.stats(oos=False), c.stats(oos=True)) for c in combos]
    out: Json = {
        "combinations": len(rows),
        "rules": len({c.rule for c in combos}),
        "gates": len({c.gate for c in combos}),
        "is_period": ["2010-01-04", str(IS_END.date())],
        "oos_period": ["2017-01-01", "2023-12-29"],
        "min_is_taken": MIN_IS_TAKEN,
        "budget": TIMING_BUDGET,
    }
    for key in ("roi_diff", "dg"):
        ok = _select(rows, key)
        c, is_s, oos_s = ok[0]
        o = ~c.is_
        if key == "roi_diff":
            ci, p = boot_roi_diff(c.roi[o], c.take[o], rng)
        else:
            ci, p = boot_dg(hedge_diff_terms(c.eq[o], c.roi[o], c.take[o]), c.yrs_oos, rng)
        out[key] = {
            "eligible": len(ok),
            "rule": c.rule,
            "gate": c.gate,
            "is": is_s,
            "oos": oos_s,
            "oos_ci95": ci,
            "oos_p": p,
            **sign_persistence(ok, key),
            "spearman_is_oos": _spearman(ok, key),
            "oos_mean_taken_without_best": _mean_without_best(c.roi[o][c.take[o]]),
        }
    out["bh"] = benjamini_hochberg_oos(_select(rows, "roi_diff"), rng)
    return out


def sign_persistence(ok: list[tuple[Combo, Json, Json]], key: str) -> Json:
    """Of the combinations that improved in sample, how many also improved
    out of sample. NOT the share positive out of sample over all of them:
    most gates have a near-complement in the set (<= and >= one threshold;
    calm and not-calm; crisis and not-crisis), and where the pair splits the
    rolls the two return differences have opposite signs, so that share sits
    near one half whatever the data. Not every pair splits them: apply_gate
    drops a NaN reading (alpha, put_rich) from BOTH sides of a threshold, so
    both can beat, or both trail, the all-roll mean, and "elevated" has no
    complement. Persistence is about one half with no signal and rises with
    one."""
    pos = [r for r in ok if _as_float(r[1][key]) > 0]
    observed = sum(_as_float(r[2][key]) > 0 for r in pos)
    return {
        "is_positive": len(pos),
        "is_positive_oos_positive": observed,
        **_persistence_null([r[0] for r in pos], key, observed),
    }


def _oos_effect(c: Combo, key: str, shift: int) -> float:
    """A combination's out-of-sample effect with its rolls' outcomes rotated
    ``shift`` places against its filter's (unmoved) choices."""
    o = ~c.is_
    roi, eq, take = np.roll(c.roi[o], shift), np.roll(c.eq[o], shift), c.take[o]
    return (
        roi_diff(roi, take) if key == "roi_diff" else float(hedge_diff_terms(eq, roi, take).sum())
    )


def _persistence_null(pos: list[Combo], key: str, observed: int) -> Json:
    """How often persistence reaches ``observed`` by chance. The combinations
    share rolls, so their out-of-sample signs move together and the count is
    far noisier than independent coins. The null rotates every rule's
    out-of-sample outcomes by the same number of rolls against the filters'
    choices: that keeps the outcomes' own clustering and the combinations'
    dependence, and breaks only the link from signal to outcome. Every
    rotation at least one bootstrap block from zero (mod n) is used, so no
    random draw is involved: outcomes cluster, so a rotation by a few rolls
    either way leaves the alignment nearly intact and is no null.

    That window was chosen after the first run had printed the null over
    every rotation 1..n-1, so both are returned (``null_all_*``) and the page
    prints them side by side."""
    if not pos:
        return {
            "null_rotations": 0,
            "null_at_least_observed": 0,
            "null_all_rotations": 0,
            "null_all_at_least_observed": 0,
        }
    n = min(int((~c.is_).sum()) for c in pos)
    reached = {k: sum(_oos_effect(c, key, k) > 0 for c in pos) >= observed for k in range(1, n)}
    window = range(BLOCK, n - BLOCK + 1)
    return {
        "null_rotations": len(window),
        "null_at_least_observed": int(sum(reached[k] for k in window)),
        "null_all_rotations": len(reached),
        "null_all_at_least_observed": int(sum(reached.values())),
    }


def _mean_without_best(roi: np.ndarray) -> float | None:
    """The mean return of the rolls a gate took, with its single best roll
    removed: how much of a filter's edge is one trade."""
    return float(np.delete(roi, int(np.argmax(roi))).mean()) if len(roi) > 1 else None


def is_sell_rule(rule: str) -> bool:
    """A rule sold after a holding period, not held to expiry."""
    return STRUCTS[rule.split("pct_")[1]][1] is not None


def oracle_take(roi: np.ndarray) -> np.ndarray:
    """A perfect-foresight filter: the best third of rolls by realised return.
    Its p is the smallest any real filter on these rolls could hope for."""
    k = math.ceil(len(roi) / 3)
    take = np.zeros(len(roi), dtype=bool)
    take[np.argsort(-roi, kind="stable")[:k]] = True
    return take


def benjamini_hochberg_oos(ok: list[tuple[Combo, Json, Json]], rng: np.random.Generator) -> Json:
    """Every eligible combination's OUT-OF-SAMPLE return difference, tested
    together at a 10% false-discovery rate; then the same over the sold rules
    alone, and a perfect-foresight filter per rule -- the power check: a rule
    whose oracle cannot reach significance contributes tests that could not
    have passed."""
    ps = [boot_roi_diff(c.roi[~c.is_], c.take[~c.is_], rng)[1] for c, _, _ in ok]
    sig = benjamini_hochberg(ps, alpha=FDR)
    survivors = [
        {"rule": c.rule, "gate": c.gate, "oos": o, "oos_p": p}
        for (c, _, o), p, s in zip(ok, ps, sig, strict=True)
        if s
    ]
    sell = [p for (c, _, _), p in zip(ok, ps, strict=True) if is_sell_rule(c.rule)]
    order = sorted(range(len(ps)), key=lambda i: ps[i])[:8]
    rules = {c.rule: c for c, _, _ in ok}
    oracle: Json = {}
    for name, c in rules.items():
        roi = c.roi[~c.is_]
        oracle[name] = {
            "oos_winners": int((roi > 0).sum()),
            "p": boot_roi_diff(roi, oracle_take(roi), rng)[1],
        }
    return {
        "tested": len(ps),
        "fdr": FDR,
        "survivors": survivors,
        "min_p": min(ps),
        "p_below_05": sum(p < 0.05 for p in ps),
        "p_floor": 1 / (P_REPS + 1),
        "sell_rules": {
            "tested": len(sell),
            "survivors": int(sum(benjamini_hochberg(sell, alpha=FDR))),
            "min_p": min(sell),
        },
        "smallest_p": [
            {
                "rule": ok[i][0].rule,
                "gate": ok[i][0].gate,
                "is": ok[i][1],
                "is_skipped": [
                    d
                    for d, s, k in zip(ok[i][0].entries, ok[i][0].is_, ok[i][0].take, strict=True)
                    if s and not k
                ],
                "oos": ok[i][2],
                "oos_p": ps[i],
            }
            for i in order
        ],
        "oracle": oracle,
        "smallest_p_overlap": overlap([ok[i][0] for i in order]),
    }


def overlap(combos: Sequence[Combo]) -> Json:
    """How alike a set of tests are: whether they share their out-of-sample
    entry dates, which of those dates their filters skip, and the pairwise
    correlation of their out-of-sample roll returns. Tests on the same dates
    skipping the same dates are one result seen several times."""
    oos = [tuple(np.array(c.entries)[~c.is_]) for c in combos]
    skipped = [tuple(np.array(c.entries)[~c.is_ & ~c.take]) for c in combos]
    same_entries = len(set(oos)) == 1
    corr: list[float] = []
    if same_entries:
        rois = [c.roi[~c.is_] for c in combos]
        corr = [
            float(np.corrcoef(rois[i], rois[j])[0, 1])
            for i in range(len(rois))
            for j in range(i + 1, len(rois))
        ]
    return {
        "tests": len(combos),
        "same_oos_entries": same_entries,
        "oos_entries": len(oos[0]) if oos else 0,
        "same_skipped": len(set(skipped)) == 1,
        "skipped": sorted({d for s in skipped for d in s}),
        "roi_corr": [min(corr), float(np.median(corr))] if corr else None,
    }


# ------------------------------------------------------------- 4. 2008-09

CRISIS_SPAN = ("2008-01-01", "2009-12-31")


def crisis_rolls(
    q: pd.DataFrame, spx: pd.Series, moneyness: float, dates: Sequence[pd.Timestamp]
) -> pd.DataFrame:
    """One put per monthly roll date, bought at the ask through
    ``OptionQuotesSource`` (its own guards, open interest included) and held
    to expiry. The request is the NEAREST listed expiry's own tenor: "buy the
    next monthly put" (two 2009 dates also list a quarterly; it is not used).
    The panel has no expiry-day spot, so SPY at expiry = SPY at entry x the
    SPX move (one month of SPY-vs-SPX tracking is the error). A refused roll
    carries the guard that refused it."""
    src = OptionQuotesSource(q)
    rows: list[Json] = []
    for t in dates:
        s0 = float(q.loc[q.quote_date == t, "spot"].iloc[0])
        listed = pd.Timestamp(q.loc[q.quote_date == t, "expiration"].min())
        f = src.fill(
            entry_date=t.date(), spot=s0, moneyness_pct=moneyness, tenor_weeks=(listed - t).days / 7
        )
        if f is None:
            day = q[(q.quote_date == t) & (q.expiration == listed)]
            rows.append({"entry": t, "filled": False, "reason": refusal_reason(day, moneyness)})
            continue
        exp = pd.Timestamp(f.expiry)
        s1 = s0 * float(spx[:exp].iloc[-1]) / float(spx[:t].iloc[-1])
        c = 1.0 / (100.0 * f.premium + COMMISSION_PER_CONTRACT)
        rows.append(
            {
                "entry": t,
                "filled": True,
                "expiry": exp,
                "roi": c * 100.0 * max(f.strike - s1, 0.0) - 1.0,
            }
        )
    return pd.DataFrame(rows)


def refusal_reason(at_expiry: pd.DataFrame, moneyness: float) -> str:
    """Which of ``OptionQuotesSource``'s strike and liquidity guards refused
    this session's put -- so a page can say WHY a roll is missing rather
    than leave a reader to think the market refused it. Same thresholds as
    the guards (``MAX_SNAP_MONEYNESS_PP``, ``MIN_OPEN_INTEREST``,
    ``MAX_RELATIVE_SPREAD``), checked on the strike they would pick."""
    spot = float(at_expiry["spot"].iloc[0])
    target = spot * (1.0 - moneyness / 100.0)
    row = at_expiry.iloc[int((at_expiry["strike"] - target).abs().to_numpy().argmin())]
    got = (1.0 - float(row["strike"]) / spot) * 100.0
    bid, ask = float(row["bid"]), float(row["ask"])
    if abs(got - moneyness) > MAX_SNAP_MONEYNESS_PP:
        return f"no strike within {MAX_SNAP_MONEYNESS_PP:g}pp (nearest {got:.1f}% OOM)"
    # Only the guard and a derived ratio: no bid, ask or strike leaves the lake
    # (option_quotes is cleared for private research use only).
    if bid <= 0.0 or ask <= 0.0:
        return "no bid"
    if int(row["open_interest"]) < MIN_OPEN_INTEREST:
        return f"open interest under {MIN_OPEN_INTEREST}"
    spread = (ask - bid) / ((ask + bid) / 2)
    if spread > MAX_RELATIVE_SPREAD:
        return f"spread {spread:.0%} of mid, over the {MAX_RELATIVE_SPREAD:.0%} cap"
    return "refused by another guard"


def crisis_equity(spx: pd.Series, dates: Sequence[pd.Timestamp], end: pd.Timestamp) -> np.ndarray:
    """The index's gross return (with the flat dividend accrual) from each roll
    date to the next, the last to ``end``: the unhedged arm, interval by
    interval. A refused roll is simply unhedged; it is never dropped."""
    out = []
    for t, t2 in zip(dates, [*dates[1:], end], strict=True):
        a, z = float(spx[:t].iloc[-1]), float(spx[:t2].iloc[-1])
        out.append(z / a * math.exp(DIV_YIELD * (t2 - t).days / 365.25))
    return np.array(out)


def crisis_books(df: pd.DataFrame, eq: np.ndarray) -> Json:
    """Per budget: terminal wealth and the worst drawdown on roll dates (no
    daily marks exist). A book that ends above the unhedged one is rerun with
    its best roll left unhedged: how much of the win rests on one month."""
    roi = df["roi"].fillna(-1.0).to_numpy()
    filled = df["filled"].to_numpy(dtype=bool)
    best = int(np.argmax(np.where(filled, roi, -np.inf)))

    def path(b: float, skip: int | None = None) -> np.ndarray:
        bb = np.where(filled, b, 0.0)
        if skip is not None:
            bb[skip] = 0.0
        return np.cumprod((1 - bb) * eq + bb * (1 + roi))

    def mdd(w: np.ndarray) -> float:
        return float((w / np.maximum.accumulate(np.r_[1.0, w])[1:] - 1).min())

    w0 = path(0.0)
    books: list[Json] = []
    for b in BUDGETS:
        w = path(b)
        row: Json = {"budget": b, "terminal": float(w[-1]), "max_dd": mdd(w)}
        if w[-1] > w0[-1]:
            row["terminal_without_best"] = float(path(b, skip=best)[-1])
        books.append(row)
    ok = df[df["filled"]]
    return {
        "unhedged": {"terminal": float(w0[-1]), "max_dd": mdd(w0)},
        "books": books,
        "n_beat": sum("terminal_without_best" in r for r in books),
        "rolls": len(df),
        "filled": len(ok),
        "mean_roi": float(ok.roi.mean()),
        "hits": int((ok.roi > 0).sum()),
        "best_roi": float(ok.roi.max()),
        "best_entry": str(df.entry[best].date()),
        "refused": [
            {"entry": str(r.entry.date()), "reason": str(r.reason)}
            for r in df[~df["filled"]].itertuples()
        ],
    }


def crisis_2008(q: pd.DataFrame, cboe: pd.DataFrame) -> Json:
    spx = cboe[cboe.index_symbol == "SPX"].set_index("trade_date")["close"].sort_index()
    spx.index = pd.DatetimeIndex(pd.to_datetime(spx.index))
    q = q.assign(quote_date=pd.to_datetime(q.quote_date), expiration=pd.to_datetime(q.expiration))
    days = pd.DatetimeIndex(q.quote_date.unique()).sort_values()
    dates = list(days[(days >= CRISIS_SPAN[0]) & (days <= CRISIS_SPAN[1])])
    win = q[q.quote_date.isin(dates)]
    nearest_dte = (
        win.groupby("quote_date").expiration.min() - pd.Series(dates, index=dates)
    ).dt.days
    n_exp = win.groupby("quote_date").expiration.nunique()
    listed = win.groupby("quote_date").expiration.min()
    out: Json = {
        "roll_dates": len(dates),
        "first": str(dates[0].date()),
        "nearest_dte": [int(nearest_dte.min()), int(nearest_dte.max())],
        "dates_with_two_expiries": [str(d.date()) for d in n_exp[n_exp > 1].index],
        "months_without_roll_date": sorted(
            set(pd.period_range(*CRISIS_SPAN, freq="M").astype(str))
            - {d.strftime("%Y-%m") for d in dates}
        ),
        "unhedged_spans": unhedged_spans(dates, listed),
    }
    beat = 0
    for m in DEPTHS:
        res = _crisis_depth(q, spx, m, dates)
        beat += int(str(res["n_beat"]))
        out[f"{int(m)}pct"] = res
    out["n_beat"] = beat
    out["configs"] = len(DEPTHS) * len(BUDGETS)
    out["spread_guard_off"] = crisis_without_spread_guard(q, spx, dates)
    return out


def unhedged_spans(dates: Sequence[pd.Timestamp], listed: pd.Series) -> list[Json]:
    """Where a missing roll date leaves the book with no put: the roll before
    the gap is held to ITS expiry, and nothing is held from then until the
    next roll date. A missing month is therefore not itself unhedged."""
    return [
        {"entry": str(t.date()), "expiry": str(listed[t].date()), "next_entry": str(t2.date())}
        for t, t2 in itertools.pairwise(dates)
        if t2 > listed[t]
    ]


def _crisis_depth(q: pd.DataFrame, spx: pd.Series, m: float, dates: list[pd.Timestamp]) -> Json:
    df = crisis_rolls(q, spx, m, dates)
    last = df[df["filled"]].iloc[-1]
    end = pd.Timestamp(last["expiry"]) if last["entry"] == dates[-1] else dates[-1]
    res = crisis_books(df, crisis_equity(spx, dates, end))
    res["last"] = str(end.date())
    return res


def crisis_without_spread_guard(q: pd.DataFrame, spx: pd.Series, dates: list[pd.Timestamp]) -> Json:
    """The 2008-09 check again with the 50%-of-mid spread cap lifted, so a
    wide quote is bought at its ask: how much of the result the cap decides.
    The other guards (strike snap, bid > 0, open interest) stay."""
    with patch.object(marks, "MAX_RELATIVE_SPREAD", math.inf):
        per = {f"{int(m)}pct": _crisis_depth(q, spx, m, dates) for m in DEPTHS}
    return {
        "n_beat": sum(int(str(r["n_beat"])) for r in per.values()),
        "depths": {
            k: {
                "filled": r["filled"],
                "rolls": r["rolls"],
                "n_beat": r["n_beat"],
                "best_entry": r["best_entry"],
                "unhedged": r["unhedged"],
                "books": r["books"],
            }
            for k, r in per.items()
        },
    }


# ------------------------------------------------------------- data + main


def exchange_calendar(cboe: pd.DataFrame) -> pd.DatetimeIndex:
    """NYSE sessions: the dates Cboe printed an SPX close. Not VIX's dates --
    since 2022-05-30 the VIX file carries rows on exchange holidays."""
    spx = cboe.loc[cboe.index_symbol == "SPX", "trade_date"]
    return pd.DatetimeIndex(pd.to_datetime(spx)).unique().sort_values()


def data_quality(panel_spot: pd.Series, calendar: pd.DatetimeIndex) -> Json:
    """Sessions against the exchange calendar. The engine treats every panel
    date as a session, so a row dated on a closed day is used like any other;
    ``closed_day_then_next_missing`` counts closed-day rows whose next
    exchange session is absent from the panel -- the signature of a vendor
    file dated one session early, not of an extra session."""
    lo, hi = panel_spot.index[0], panel_spot.index[-1]
    trading = calendar[(calendar >= lo) & (calendar <= hi)]
    closed = [d for d in panel_spot.index if d not in calendar]
    nxt = [first_on_or_after(trading, d) for d in closed]
    return {
        "closed_day_then_next_missing": sum(
            n is not None and n not in panel_spot.index for n in nxt
        ),
        "sessions": len(panel_spot),
        "first": str(lo.date()),
        "last": str(hi.date()),
        "missing_trading_days": int((~trading.isin(panel_spot.index)).sum()),
        "sessions_on_closed_days": [str(d.date()) for d in panel_spot.index if d not in calendar],
    }


def standalone_and_hedge(
    mkt: Market,
    span: tuple[str, str],
    rng: np.random.Generator,
    closed_days: Sequence[pd.Timestamp] = (),
) -> Json:
    rules: Json = {}
    rolls_by_rule: dict[str, list[Roll]] = {}
    for sname, (ten, hold) in STRUCTS.items():
        for m in DEPTHS:
            key = f"{int(m)}pct_{sname}"
            rolls, skips = run_rolls(
                mkt, moneyness=m, tenor_weeks=ten, hold_weeks=hold, start=span[0], end=span[1]
            )
            st = standalone_stats(rolls_frame(rolls), rng)
            st["fill_skips"] = skips
            st.update(intrinsic_exits(rolls))
            rules[key] = st
            rolls_by_rule[key] = rolls
            print(
                f"{mkt.symbol} {key}: n={st['n_rolls']} mean={st['mean_roi']:+.3f}", file=sys.stderr
            )
    closed = set(closed_days)
    return {
        "standalone": rules,
        "rolls_on_closed_days": touching_closed_days(rolls_by_rule, closed),
        "hedge": hedge(mkt, rolls_by_rule),
        "every_day": {f"{int(m)}pct_4w_hold": every_day_entries(mkt, m, span) for m in DEPTHS},
        "episodes_10pct": drawdown_episodes(mkt.spot[span[0] : span[1]], EPISODE_DD),
    }


def intrinsic_exits(rolls: Sequence[Roll]) -> Json:
    """Sold rolls credited intrinsic for want of a qualifying exit bid; of
    those the ones in the money (an out-of-the-money one is credited 0 and
    loses exactly its premium); of those the ones whose strike is beyond the
    panel's slice, so no quote exists; and whether the rule's best roll is one."""
    best = max(rolls, key=lambda r: r.roi, default=None)
    return {
        "exits_at_intrinsic": sum(r.exit_at_intrinsic for r in rolls),
        "exits_at_intrinsic_itm": sum(r.exit_at_intrinsic and r.roi > -1.0 for r in rolls),
        "exits_beyond_panel": sum(r.exit_beyond_panel for r in rolls),
        "best_roll_beyond_panel": best is not None and best.exit_beyond_panel,
    }


def touching_closed_days(rolls_by_rule: dict[str, list[Roll]], closed: set[pd.Timestamp]) -> Json:
    """Rolls (over every rule) entered or exited on a session the exchange
    was shut: how much of the study the misdated rows reach."""
    rolls = [r for rs in rolls_by_rule.values() for r in rs]
    return {
        "rolls": len(rolls),
        "touching": sum(r.entry in closed or r.exit in closed for r in rolls),
    }


def check_imports_from(root: Path, imported: Path) -> None:
    """Refuse to run when ``tail_lab`` is imported from another checkout's
    ``src/`` (a worktree using the main checkout's venv does exactly that):
    the recorded version would then name code that did not run."""
    if not imported.resolve().is_relative_to((root / "src").resolve()):
        raise RuntimeError(
            f"tail_lab is imported from {imported}, not {root / 'src'}: "
            f"set PYTHONPATH={root / 'src'}"
        )


def code_version(root: Path) -> Json:
    """The commit that produced the file, the git TREE hashes of src/ and
    scripts/ (a squash merge keeps a tree, not a branch commit), and whether
    the code that ran differed from them (README constitution, rule 4)."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {
        "sha": git("rev-parse", "HEAD"),
        "tree": {d: git("rev-parse", f"HEAD:{d}") for d in ("src", "scripts")},
        "dirty": bool(git("status", "--porcelain", "--", "scripts", "src")),
    }


ROOT = Path(__file__).resolve().parents[1]


def run(store: LakeStore, as_of: dt.date) -> Json:
    check_imports_from(ROOT, Path(tail_lab.__file__))
    rng = np.random.default_rng(SEED)
    snaps: Json = {}

    def read(ds: str) -> pd.DataFrame:
        # The id is taken next to the read, not after the long runs below: the
        # main checkout's timers ingest vix and cboe_strategy daily, so a
        # partition landing mid-run would otherwise be cited but never read.
        snaps[ds] = store.bronze_snapshot_id(ds, as_of)
        return store.read_bronze_as_of(ds, as_of)

    vix_bronze = read("vix")
    rates = read("rates")
    cboe = read("cboe_strategy")
    calendar = exchange_calendar(cboe)
    out: Json = {
        "as_of": as_of.isoformat(),
        "code": code_version(ROOT),
        "dividend_yield_assumed": DIV_YIELD,
        "commission_per_contract": COMMISSION_PER_CONTRACT,
        "block_rolls": BLOCK,
        "snapshots": {},
        "panels": {},
    }
    panels: Json = {}
    spy: Market | None = None
    for sym, span in SPANS.items():
        ds = f"optionsdx_quotes_{sym.lower()}"
        snaps[ds] = store.bronze_snapshot_id(ds, as_of)
        panel = store.read_bronze_columns_as_of(ds, as_of, PANEL_COLUMNS)
        mkt = market_from_panel(panel, sym)
        panels[sym] = {**data_quality(mkt.spot, calendar), "rows": len(panel)}
        closed = [d for d in mkt.sessions if d not in calendar]
        out[sym] = standalone_and_hedge(mkt, span, rng, closed)
        if sym == "SPY":
            spy = mkt
    out["panels"] = panels
    if spy is None:
        raise LookupError("the SPY panel did not load")
    hedge_window = spy_hedge_window(out)
    out["cboe"] = cboe_benchmark(cboe, hedge_window)
    out["crisis_2008"] = crisis_2008(read("option_quotes"), cboe)
    out["snapshots"] = snaps
    combos, coverage = timing_combos(spy, timing_inputs(vix_bronze, rates), SPANS["SPY"])
    tm = timing(combos, rng)
    tm["alpha_coverage"] = [min(coverage.values()), max(coverage.values())]
    out["timing"] = tm
    return out


def spy_hedge_window(out: Json) -> list[str]:
    spy = out["SPY"]
    assert isinstance(spy, dict)
    window = spy["hedge"]["window"]
    assert isinstance(window, list)
    return [str(d) for d in window]


def finite(x: object) -> object:
    """NaN and inf become null: JSON has no NaN, and Vite's import rejects it."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [finite(v) for v in x]
    return x


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    ap.add_argument("--out", type=Path, default=Path("frontend/src/content/pitch_evidence.json"))
    args = ap.parse_args(argv)
    result = run(get_lake_store(Settings()), args.as_of)
    args.out.write_text(json.dumps(finite(result), indent=1, default=str, allow_nan=False) + "\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
