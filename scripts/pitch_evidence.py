"""The real-quote evidence behind the Pitch tab: does buying puts pay?

OFFLINE and human-run (``make pitch-evidence``): reads the lake (whatever
``TAIL_LAB_LAKE_BACKEND`` names -- the optionsDX panels live in the production
bucket, ``docs/DATA_CONTRACTS.md`` #12) through the store's as-of reads
(``docs/adr/0009``), never writes to it, and writes ONE file of aggregate
statistics, ``frontend/src/content/pitch_evidence.json``, that the Pitch tab
renders. No raw quote row is written: the optionsDX licence forbids
redistribution, aggregates are fine.

Nothing here prices an option with a model. Every fill and every mark goes
through the platform's own guard pipeline,
``research.backtest.quote_fills.OptionsDxQuoteSource``: the exact session only
(point in time), the nearest listed expiry AT OR AFTER the target, a listed
strike within ``MAX_SNAP_MONEYNESS_PP`` (1pp) of the requested distance, bid
> 0 and relative spread <= ``marks.MAX_RELATIVE_SPREAD`` (50%), bought at the
ASK; a sale is at the BID. Commission ``brokerage.COMMISSION_PER_CONTRACT``
($0.65) per contract on a buy and on a sale, none on expiry.

Three questions, all on SPY (2010-2023) and, for the first two, QQQ (2012-2023):

1. **Standalone.** 12 rules (4 depths x 3 structures). Return per dollar of
   premium, with a circular block-bootstrap CI over rolls.
2. **As a hedge.** SPY plus a flat dividend accrual, spending b x wealth per
   roll on the rule's puts; time-average (log) growth against the unhedged
   book over one common window.
3. **Timing.** 35 gates on six signals, chosen on rolls entered 2010-2016 and
   tested on 2017-2023; Benjamini-Hochberg across every combination.

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
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from tail_lab.config import Settings, get_lake_store
from tail_lab.contracts.regime import classify_vix_series
from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.brokerage import COMMISSION_PER_CONTRACT
from tail_lab.research.backtest.multiple_testing import benjamini_hochberg
from tail_lab.research.backtest.quote_fills import OptionsDxQuoteSource
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
BUDGETS = (0.0025, 0.005, 0.01)  # of the book, per roll
SPANS = {"SPY": ("2010-01-04", "2023-12-29"), "QQQ": ("2012-01-03", "2023-12-29")}
PERIODS = {
    "2010-2013": ("2010-01-01", "2013-12-31"),
    "2014-2017": ("2014-01-01", "2017-12-31"),
    "2018-2020": ("2018-01-01", "2020-12-31"),
    "2021-2023": ("2021-01-01", "2023-12-31"),
}
IS_END = pd.Timestamp("2016-12-31")
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
        if hold_weeks is not None:
            bid = mkt.src.mark(
                entry_date=exit_.date(), strike=f.strike, expiry=f.expiry, basis=f.basis
            )
            v = bid if bid is not None else v
        roi = unit_value(contracts, v, sold=hold_weeks is not None) - 1.0
        path = (
            _path(mkt, t, exit_, contracts, f.strike, f.expiry, f.basis, roi + 1.0)
            if daily_paths
            else pd.Series(dtype=float)
        )
        rolls.append(Roll(t, exit_, f.strike, f.premium, s0, s1, f.realized_dte, roi, path))
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


def book(mkt: Market, rolls: Sequence[Roll], *, budget: float) -> pd.Series:
    """Daily wealth of a book that at each roll entry spends ``budget`` x
    wealth on that roll's puts and holds the rest in the underlying (price
    plus the flat dividend accrual); at each exit everything is rebalanced.
    Between rolls (a refused fill) it is fully invested, never dropped."""
    w = 1.0
    out: dict[pd.Timestamp, float] = {}
    prev: pd.Timestamp | None = None
    for r in rolls:
        if prev is not None and r.entry > prev:
            w = _equity(mkt, prev, r.entry, w, out)
        days = r.path.index
        sp = mkt.spot.loc[days].to_numpy()
        yrs = (days - days[0]).days.to_numpy() / 365.25
        vals = (1.0 - budget) * w * (sp / sp[0]) * np.exp(
            DIV_YIELD * yrs
        ) + budget * w * r.path.to_numpy()
        out.update(zip(days, vals, strict=True))
        w = float(vals[-1])
        prev = r.exit
    return pd.Series(out).sort_index()


def ann_log_growth(w: pd.Series) -> float:
    yrs = (w.index[-1] - w.index[0]).days / 365.25
    return float(np.log(w.iloc[-1] / w.iloc[0]) / yrs)


def max_drawdown(w: pd.Series) -> float:
    return float((w / w.cummax() - 1.0).min())


# ------------------------------------------------------------- statistics


def block_bootstrap_idx(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap indices: blocks of ``block`` consecutive rolls,
    so a crash that spans two rolls stays together."""
    starts = rng.integers(0, n, size=math.ceil(n / block))
    return ((starts[:, None] + np.arange(block)[None, :]).ravel() % n)[:n]


def boot_mean_ci(x: np.ndarray, rng: np.random.Generator, reps: int = CI_REPS) -> list[float]:
    ms = np.array([x[block_bootstrap_idx(len(x), BLOCK, rng)].mean() for _ in range(reps)])
    return [float(np.quantile(ms, 0.025)), float(np.quantile(ms, 0.975))]


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


def every_day_entries(mkt: Market, m: float, span: tuple[str, str]) -> Json:
    """The roll calendar is arbitrary: buy a 4-week put on EVERY session and
    hold it to expiry; phase k keeps every 20th entry from session k. With a
    handful of tail events in the sample, the phase can decide the sign."""
    rois: list[float] = []
    for t in mkt.sessions[(mkt.sessions >= span[0]) & (mkt.sessions <= span[1])]:
        f = mkt.src.fill(
            entry_date=t.date(), spot=float(mkt.spot[t]), moneyness_pct=m, tenor_weeks=4.0
        )
        settle = last_on_or_before(mkt.sessions, pd.Timestamp(f.expiry)) if f else None
        if f is None or settle is None or settle <= t or pd.Timestamp(f.expiry) > mkt.sessions[-1]:
            rois.append(float("nan"))
            continue
        c = 1.0 / (100.0 * f.premium + COMMISSION_PER_CONTRACT)
        rois.append(c * 100.0 * max(f.strike - float(mkt.spot[settle]), 0.0) - 1.0)
    g = pd.Series(rois)
    means = [float(g.iloc[k::20].dropna().mean()) for k in range(20)]
    ok = g.dropna()
    return {
        "entry_days": len(ok),
        "refused_days": int(g.isna().sum()),
        "mean_roi": float(ok.mean()),
        "hit_rate": float((ok > 0).mean()),
        "phases": 20,
        "phases_positive": sum(x > 0 for x in means),
        "phase_min": min(means),
        "phase_max": max(means),
    }


# ------------------------------------------------------------- 2. hedge


def drop_best_roll_growth(rolls: Sequence[Roll], budget: float) -> Json:
    """Hedged-minus-unhedged log growth per year, computed roll by roll (the
    equity-only gaps are identical in both books and cancel), with and without
    the single best roll -- how much of a "beats unhedged" rests on one trade."""
    df = rolls_frame(rolls)
    eq = (
        df.spot_exit / df.spot_entry * np.exp(DIV_YIELD * (df.exit - df.entry).dt.days / 365.25)
    ).to_numpy()
    lr = np.log((1 - budget) * eq + budget * (1 + df.roi.to_numpy())) - np.log(eq)
    yrs = (df.exit.iloc[-1] - df.entry.iloc[0]).days / 365.25
    best = int(df.roi.idxmax())
    return {
        "edge": float(lr.sum() / yrs),
        "edge_without_best": float(np.delete(lr, best).sum() / yrs),
        "best_entry": str(df.entry[best].date()),
    }


def hedge(mkt: Market, rolls_by_rule: dict[str, list[Roll]]) -> Json:
    a = max(r[0].entry for r in rolls_by_rule.values())
    b = min(r[-1].exit for r in rolls_by_rule.values())
    w0 = unhedged(mkt, a, b)
    g0 = ann_log_growth(w0)
    rows: list[Json] = []
    n_beat = n_cut_dd = 0
    dd0 = max_drawdown(w0)
    for rule, rolls in rolls_by_rule.items():
        yrs = (rolls[-1].exit - rolls[0].entry).days / 365.25
        for budget in BUDGETS:
            w = book(mkt, rolls, budget=budget)[a:b]
            w = w / w.iloc[0]
            g = ann_log_growth(w)
            row: Json = {
                "rule": rule,
                "budget": budget,
                "premium_pct_per_year": budget * len(rolls) / yrs * 100,
                "growth": g,
                "edge": g - g0,
                "max_dd": max_drawdown(w),
            }
            if g > g0:
                n_beat += 1
                row["drop_best"] = drop_best_roll_growth(rolls, budget)
            n_cut_dd += max_drawdown(w) > dd0
            rows.append(row)
    return {
        "window": [str(a.date()), str(b.date())],
        "unhedged_growth": g0,
        "unhedged_max_dd": dd0,
        "configs": rows,
        "n_beat": n_beat,
        "n_cut_dd": n_cut_dd,
    }


def cboe_benchmark(cboe: pd.DataFrame, span: tuple[str, str]) -> Json:
    piv = cboe.pivot(index="trade_date", columns="index_symbol", values="close").sort_index()[
        span[0] : span[1]
    ]
    out: Json = {}
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
    rate: pd.Series
    alpha_cache: dict[pd.Timestamp, float] = field(default_factory=dict)


def lagged(series: pd.Series, t: pd.Timestamp) -> object:
    """The last value strictly BEFORE ``t``: known at the entry close."""
    s = series[series.index < t]
    return s.iloc[-1] if len(s) else None


def timing_inputs(vix_bronze: pd.DataFrame, rates: pd.DataFrame) -> TimingInputs:
    gold = silver_to_gold(bronze_to_silver(vix_bronze)).set_index("date")
    labels = pd.Series(classify_vix_series(gold["close"].tolist()), index=gold.index)
    r = rates[rates.series_id == "DGS3MO"].sort_values(["obs_date", "vintage_date"])
    # The FIRST vintage of each observation is what was known at the time.
    rate = r.drop_duplicates("obs_date", keep="first").set_index("obs_date")["value"] / 100.0
    return TimingInputs(gold["close"], gold["z_score"], labels, rate)


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
    and the regime from the day BEFORE entry; the chain signals from the entry
    session's own close, the same close the put is bought at."""
    s = pd.DataFrame(index=df.index)
    s["vix_lvl"] = [_as_float(lagged(ti.vix, t)) for t in df.entry]
    s["vix_z"] = [_as_float(lagged(ti.vix_z, t)) for t in df.entry]
    s["regime"] = [lagged(ti.regime, t) for t in df.entry]
    rich = []
    for r, v in zip(df.itertuples(), s["vix_lvl"], strict=True):
        rate = _as_float(lagged(ti.rate, r.entry))
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
    return [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))], bootstrap_p(d)


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


def timing_combos(mkt: Market, ti: TimingInputs, span: tuple[str, str]) -> list[Combo]:
    combos: list[Combo] = []
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
            eq = (
                df.spot_exit
                / df.spot_entry
                * np.exp(DIV_YIELD * (df.exit - df.entry).dt.days / 365.25)
            ).to_numpy()
            sig = signals(mkt, df, ti)
            is_ = (df.entry <= IS_END).to_numpy()
            yrs_is = (df.exit[is_].max() - df.entry[is_].min()).days / 365.25
            yrs_oos = (df.exit[~is_].max() - df.entry[~is_].min()).days / 365.25
            for gname, gate in gates(sig[is_]).items():
                combos.append(
                    Combo(
                        f"{int(m)}pct_{sname}",
                        gname,
                        df.roi.to_numpy(),
                        eq,
                        apply_gate(sig, gate),
                        is_,
                        yrs_is,
                        yrs_oos,
                    )
                )
            print(
                f"timing {int(m)}pct_{sname}: alpha coverage {sig.alpha.notna().mean():.2f}",
                file=sys.stderr,
            )
    return combos


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
    }
    for key in ("roi_diff", "dg"):
        ok = _select(rows, key)
        c, is_s, oos_s = ok[0]
        o = ~c.is_
        if key == "roi_diff":
            ci, p = boot_roi_diff(c.roi[o], c.take[o], rng)
        else:
            terms = hedge_diff_terms(c.eq[o], c.roi[o], c.take[o])
            bs = np.array(
                [
                    terms[block_bootstrap_idx(len(terms), BLOCK, rng)].sum() / c.yrs_oos
                    for _ in range(P_REPS)
                ]
            )
            ci, p = [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))], bootstrap_p(bs)
        out[key] = {
            "eligible": len(ok),
            "rule": c.rule,
            "gate": c.gate,
            "is": is_s,
            "oos": oos_s,
            "oos_ci95": ci,
            "oos_p": p,
            "share_positive_oos": float(np.mean([_as_float(r[2][key]) > 0 for r in ok])),
            "spearman_is_oos": _spearman(ok, key),
            "oos_mean_taken_without_best": _mean_without_best(c.roi[o][c.take[o]]),
        }
    out["bh"] = benjamini_hochberg_oos(_select(rows, "roi_diff"), rng)
    return out


def _mean_without_best(roi: np.ndarray) -> float | None:
    """The mean return of the rolls a gate took, with its single best roll
    removed: how much of a filter's edge is one trade."""
    return float(np.delete(roi, int(np.argmax(roi))).mean()) if len(roi) > 1 else None


def benjamini_hochberg_oos(ok: list[tuple[Combo, Json, Json]], rng: np.random.Generator) -> Json:
    """Every eligible combination's OUT-OF-SAMPLE return difference, tested
    together at a 10% false-discovery rate."""
    ps = [boot_roi_diff(c.roi[~c.is_], c.take[~c.is_], rng)[1] for c, _, _ in ok]
    sig = benjamini_hochberg(ps, alpha=FDR)
    survivors = [
        {"rule": c.rule, "gate": c.gate, "oos": o, "oos_p": p}
        for (c, _, o), p, s in zip(ok, ps, sig, strict=True)
        if s
    ]
    return {
        "tested": len(ps),
        "fdr": FDR,
        "survivors": survivors,
        "min_p": min(ps),
        "p_below_05": sum(p < 0.05 for p in ps),
        "p_floor": 1 / (P_REPS + 1),
    }


# ------------------------------------------------------------- data + main


def data_quality(panel_spot: pd.Series, vix_dates: pd.DatetimeIndex) -> Json:
    lo, hi = panel_spot.index[0], panel_spot.index[-1]
    trading = vix_dates[(vix_dates >= lo) & (vix_dates <= hi)]
    return {
        "sessions": len(panel_spot),
        "first": str(lo.date()),
        "last": str(hi.date()),
        "missing_trading_days": int((~trading.isin(panel_spot.index)).sum()),
        "sessions_on_closed_days": [str(d.date()) for d in panel_spot.index if d not in vix_dates],
    }


def standalone_and_hedge(mkt: Market, span: tuple[str, str], rng: np.random.Generator) -> Json:
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
            rules[key] = st
            rolls_by_rule[key] = rolls
            print(
                f"{mkt.symbol} {key}: n={st['n_rolls']} mean={st['mean_roi']:+.3f}", file=sys.stderr
            )
    return {
        "standalone": rules,
        "hedge": hedge(mkt, rolls_by_rule),
        "every_day": {f"{int(m)}pct_4w_hold": every_day_entries(mkt, m, span) for m in DEPTHS},
        "episodes_10pct": drawdown_episodes(mkt.spot[span[0] : span[1]], 0.10),
    }


def run(store: LakeStore, as_of: dt.date) -> Json:
    rng = np.random.default_rng(SEED)
    vix_bronze = store.read_bronze_as_of("vix", as_of)
    vix_dates = pd.DatetimeIndex(pd.to_datetime(vix_bronze["date"]))
    out: Json = {
        "as_of": as_of.isoformat(),
        "dividend_yield_assumed": DIV_YIELD,
        "commission_per_contract": COMMISSION_PER_CONTRACT,
        "block_rolls": BLOCK,
        "snapshots": {},
        "panels": {},
    }
    snaps: Json = {}
    panels: Json = {}
    spy: Market | None = None
    for sym, span in SPANS.items():
        ds = f"optionsdx_quotes_{sym.lower()}"
        snaps[ds] = store.bronze_snapshot_id(ds, as_of)
        panel = store.read_bronze_columns_as_of(ds, as_of, PANEL_COLUMNS)
        mkt = market_from_panel(panel, sym)
        panels[sym] = {**data_quality(mkt.spot, vix_dates), "rows": len(panel)}
        out[sym] = standalone_and_hedge(mkt, span, rng)
        if sym == "SPY":
            spy = mkt
    rates = store.read_bronze_as_of("rates", as_of)
    cboe = store.read_bronze_as_of("cboe_strategy", as_of)
    for ds in ("vix", "rates", "cboe_strategy"):
        snaps[ds] = store.bronze_snapshot_id(ds, as_of)
    out["snapshots"], out["panels"] = snaps, panels
    out["cboe"] = cboe_benchmark(cboe, SPANS["SPY"])
    if spy is None:
        raise LookupError("the SPY panel did not load")
    out["timing"] = timing(timing_combos(spy, timing_inputs(vix_bronze, rates), SPANS["SPY"]), rng)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--as-of", type=dt.date.fromisoformat, default=dt.date.today())
    ap.add_argument("--out", type=Path, default=Path("frontend/src/content/pitch_evidence.json"))
    args = ap.parse_args(argv)
    result = run(get_lake_store(Settings()), args.as_of)
    args.out.write_text(json.dumps(result, indent=1, default=str) + "\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
