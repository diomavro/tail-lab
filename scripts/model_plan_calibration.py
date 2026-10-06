"""Check the Book's model-plan pricer against real SPY put mids.

Reproduces ``model_plan.CALIBRATION``: at every monthly third-Friday roll from
2008 on, price the 5% and 10% OTM put the plan would buy -- Black-Scholes at
the VIX plus ``MEASURED_VOL_GAP`` -- and divide by the real mid of the nearest
SPY put expiring within five days of the next roll. Prints the median ratio
by VIX regime beside the alternative it replaced (premium x the median
market/model *price* ratio), so the choice can be re-litigated on data.

Offline and local only: reads the licence-limited lambdaclass ``data-v1``
corpus from ``data/vendor/`` (absent on CI and Fly) and SPX + VIX from the
configured lake, point-in-time as of today. Never served.

    env -u PYTHONPATH .venv/bin/python scripts/model_plan_calibration.py
"""

from __future__ import annotations

import datetime as dt
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from tail_lab.config import get_lake_store
from tail_lab.contracts.cboe_strategy import DATASET as CBOE_STRATEGY_DATASET
from tail_lab.research.backtest.index_replication import roll_schedule, series_from
from tail_lab.research.backtest.model_plan import MEASURED_VOL_GAP
from tail_lab.research.backtest.put_roll import DEFAULT_RATE
from tail_lab.research.option_pricer import BlackScholesPricer

CORPUS = Path("data/vendor/lambdaclass-data-v1")
#: The superseded calibration, kept only to print the comparison.
MEDIAN_PRICE_RATIO = {5.0: 1.42, 10.0: 7.38}
REGIMES = (
    ("calm (VIX < 17)", 0.0, 17.0),
    ("normal", 17.0, 28.0),
    ("stress (VIX >= 28)", 28.0, 999.0),
)


def main() -> int:
    if not (CORPUS / "SPY_options.parquet").exists():
        print(f"no {CORPUS}/SPY_options.parquet -- this check runs only where the corpus is")
        return 1
    store = get_lake_store()
    today = dt.date.today()
    cboe = store.read_bronze_as_of(CBOE_STRATEGY_DATASET, today)
    spx = series_from(cboe[cboe["index_symbol"] == "SPX"], date_col="trade_date", value_col="close")
    vix = series_from(store.read_bronze_as_of("vix", today), date_col="date", value_col="close")
    und = pq.read_table(CORPUS / "SPY_underlying.parquet", columns=["date", "close"]).to_pandas()
    spot = pd.Series(und["close"].to_numpy(), index=pd.to_datetime(und["date"]))

    common = pd.DatetimeIndex(spx.index).intersection(pd.DatetimeIndex(vix.index))
    dates = [
        d
        for d in roll_schedule(common, rolls_per_year=12)
        if d >= pd.Timestamp("2008-01-01") and d in spot.index
    ]
    quotes = (
        ds.dataset(CORPUS / "SPY_options.parquet")
        .to_table(
            filter=ds.field("date").isin([np.datetime64(d, "ns") for d in dates])
            & (ds.field("type") == "put"),
            columns=["date", "expiration", "strike", "bid", "ask"],
        )
        .to_pandas()
    )
    pricer = BlackScholesPricer()
    rows: list[tuple[float, float, float, float, float]] = []
    for day, nxt in pairwise(dates):
        s = float(spot[day])
        sigma = float(vix[day]) / 100.0
        t = (nxt - day).days / 365.25
        today_q = quotes[quotes["date"] == day]
        if today_q.empty:
            continue
        expiry = min(
            today_q["expiration"].unique(), key=lambda e: abs((pd.Timestamp(e) - nxt).days)
        )
        if abs((pd.Timestamp(expiry) - nxt).days) > 5:
            continue
        chain = today_q[today_q["expiration"] == expiry]
        for m, gap in MEASURED_VOL_GAP.items():
            nearest = chain.iloc[(chain["strike"] - s * (1 - m / 100)).abs().argsort()[:1]]
            if nearest.empty or float(nearest["bid"].iloc[0]) <= 0:
                continue
            k = float(nearest["strike"].iloc[0])
            mid = (float(nearest["bid"].iloc[0]) + float(nearest["ask"].iloc[0])) / 2
            base = pricer.price_put(
                spot=s, strike=k, t_years=t, r=DEFAULT_RATE, sigma=sigma, q=0.019
            )
            gapped = pricer.price_put(
                spot=s, strike=k, t_years=t, r=DEFAULT_RATE, sigma=sigma + gap, q=0.019
            )
            rows.append((m, sigma * 100, mid, gapped, base * MEDIAN_PRICE_RATIO[m]))
    frame = pd.DataFrame(rows, columns=["m", "vix", "mid", "gap", "ratio"])
    for m in MEASURED_VOL_GAP:
        sub = frame[frame["m"] == m]
        print(f"{m:g}% OTM, {len(sub)} rolls -- median plan premium / market mid")
        for name, lo, hi in REGIMES:
            r = sub[(sub["vix"] >= lo) & (sub["vix"] < hi)]
            print(
                f"  {name:20s} n={len(r):3d}  vol gap {np.median(r['gap'] / r['mid']):.2f}x"
                f"   median price ratio {np.median(r['ratio'] / r['mid']):.2f}x"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
