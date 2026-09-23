"""Generate the option-quote test fixtures, with invented numbers.

These fixtures used to be real slices of vendor files -- a lambdaclass
``data-v1`` cut and eleven lines of an optionsDX month. Both are commercial
option data redistributed under research-only terms, so checking them into a
repo that might ever be public makes this the second republisher of someone
else's paid data (`HUMAN_TODO.md`, the 2026-08-22 licence call).

Nothing in the test suite needs the values to be REAL -- only correctly
SHAPED. So this builds the same structure with made-up numbers, preserving
every quirk the tests actually assert on:

* two roll dates, one pre-2015 with its **Saturday** expiration and one
  mid-COVID, so the weekday assertion still means something;
* decoy expiries too near and too far, because an extractor fixture with
  nothing to reject would prove nothing;
* both calls and puts, so "keeps only puts" is a real filter;
* strikes inside and outside the moneyness band, and a crossed/one-sided
  quote, so the rejection paths are exercised;
* the vendor's own column names and dtypes, including the sentinel-filled
  mark/IV/greek columns the contract drops.

Run: env -u PYTHONPATH .venv/bin/python scripts/make_test_fixtures.py
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

#: (quote_date, spot, target_expiry, decoy_expiries). The 2008 target is a
#: Saturday on purpose: US equity options expired Saturday until 2015.
ROLLS = [
    (dt.date(2008, 10, 17), 93.21, dt.date(2008, 11, 22), [dt.date(2008, 12, 20)]),
    (
        dt.date(2020, 3, 20),
        228.80,
        dt.date(2020, 4, 17),
        [dt.date(2020, 3, 27), dt.date(2020, 6, 19)],
    ),
]


def _rows(quote_date: dt.date, spot: float, expiry: dt.date, *, decoy: bool) -> list[dict]:
    out: list[dict] = []
    # Strikes from 0.30 to 1.15 of spot: deliberately wider than the 0.40-1.05
    # band the contract keeps, so the moneyness filter has something to drop.
    for i, frac in enumerate([0.30, 0.45, 0.60, 0.75, 0.90, 1.00, 1.05, 1.15]):
        strike = round(spot * frac, 0)
        for right in ("call", "put"):
            # Puts get monotonically increasing bids in strike -- the tests
            # assert that, and it is true of any sane put smile.
            bid = (
                round(0.05 + (frac**3) * 12.0, 2)
                if right == "put"
                else round(max(0.05, (spot - strike)) + 0.5, 2)
            )
            out.append(
                {
                    "contract_id": f"SPY{expiry:%y%m%d}{right[0].upper()}{int(strike * 1000):08d}",
                    "symbol": "SPY",
                    "expiration": pd.Timestamp(expiry),
                    "strike": float(strike),
                    "type": right,
                    "last": bid,
                    # Sentinel-filled on the pre-2011 slice, as the real
                    # vendor file is; the contract drops these columns.
                    "mark": 0.0 if quote_date.year < 2011 else round(bid + 0.03, 2),
                    "bid": bid,
                    "bid_size": 10,
                    "ask": round(bid + 0.06, 2),
                    "ask_size": 12,
                    "volume": 100 + i,
                    "open_interest": 500 + i,
                    "date": pd.Timestamp(quote_date),
                    "implied_volatility": 0.0 if quote_date.year < 2011 else 0.25,
                    "delta": 0.0 if quote_date.year < 2011 else -0.2,
                    "gamma": 0.0,
                    "theta": 0.0,
                    "vega": 0.0,
                    "rho": 0.0,
                    "in_the_money": strike > spot if right == "put" else strike < spot,
                }
            )
    if not decoy:
        # One one-sided quote and one crossed quote on the TARGET expiry, so
        # the rejection paths are exercised rather than assumed.
        base = out[0].copy()
        base.update({"strike": float(round(spot * 0.5)), "type": "put", "bid": 0.0, "ask": 0.0})
        out.append(base)
        crossed = out[0].copy()
        crossed.update({"strike": float(round(spot * 0.55)), "type": "put", "bid": 9.0, "ask": 1.0})
        out.append(crossed)
    return out


#: The vendor's own header, reproduced verbatim. A file FORMAT is not the
#: vendor's data -- the parser exists to read exactly this layout, and
#: `test_a_changed_vendor_header_raises_rather_than_guessing` depends on it
#: being right. What is licence-bearing is the quotes underneath, and those
#: are invented below.
OPTIONSDX_HEADER = (
    "[QUOTE_UNIXTIME], [QUOTE_READTIME], [QUOTE_DATE], [QUOTE_TIME_HOURS], [UNDERLYING_LAST], "
    "[EXPIRE_DATE], [EXPIRE_UNIX], [DTE], [C_DELTA], [C_GAMMA], [C_VEGA], [C_THETA], [C_RHO], "
    "[C_IV], [C_VOLUME], [C_LAST], [C_SIZE], [C_BID], [C_ASK], [STRIKE], [P_BID], [P_ASK], "
    "[P_SIZE], [P_LAST], [P_DELTA], [P_GAMMA], [P_VEGA], [P_THETA], [P_RHO], [P_IV], "
    "[P_VOLUME], [STRIKE_DISTANCE], [STRIKE_DISTANCE_PCT]"
)


def _optionsdx_rows() -> list[str]:
    """One quote day, invented numbers, every branch the parser has.

    Carries strikes inside and outside the 0.60-1.02 moneyness band, a tenor
    past MAX_DTE_DAYS, a zero-ask row that must be dropped, a zero-BID row
    that must be KEPT (a far-OTM put with no resting offer is a real market
    state), and a blank ``P_IV`` -- optionsDX leaves greeks blank on illiquid
    rows, and coercing those to 0.0 would put a real-looking number where
    there is no observation.
    """
    spot = 182.90
    quote_date, quote_unix = "2014-01-03", 1388782800
    rows: list[str] = []
    # (moneyness, dte, p_bid, p_ask, blank_iv)
    spec = [
        (0.50, 30, 0.01, 0.05, False),  # below the band -> dropped
        (0.65, 30, 0.08, 0.12, False),
        (0.80, 30, 0.41, 0.47, False),
        (0.90, 30, 1.15, 1.22, False),
        (0.95, 30, 2.30, 2.44, True),  # illiquid: blank P_IV
        (1.00, 30, 4.85, 5.02, False),
        (1.02, 30, 5.90, 6.11, False),
        (1.10, 30, 9.40, 9.80, False),  # above the band -> dropped
        (0.90, 150, 6.20, 6.60, False),  # past MAX_DTE_DAYS -> dropped
        (0.85, 30, 0.00, 0.00, False),  # no ask at all -> dropped
        (0.75, 30, 0.00, 0.06, False),  # zero BID but a real ask -> KEPT
    ]
    for moneyness, dte, p_bid, p_ask, blank_iv in spec:
        strike = round(spot * moneyness, 0)
        expire_unix = quote_unix + dte * 86400
        expire = dt.date.fromtimestamp(expire_unix).isoformat()
        p_iv = "" if blank_iv else "0.184320"
        dist = round(abs(strike - spot), 6)
        rows.append(
            ", ".join(
                [
                    str(quote_unix),
                    f"{quote_date} 16:00",
                    quote_date,
                    "16.000000",
                    f"{spot:.6f}",
                    expire,
                    str(expire_unix),
                    f"{dte:.6f}",
                    "0.612340",
                    "0.004320",
                    "0.110390",
                    "-0.024970",
                    "0.040560",
                    "0.164530",
                    "120",
                    "1.200000",
                    "32 x 1",
                    f"{spot - strike + 1:.6f}",
                    f"{spot - strike + 1.4:.6f}",
                    f"{strike:.6f}",
                    f"{p_bid:.6f}",
                    f"{p_ask:.6f}",
                    "10 x 12",
                    f"{p_bid:.6f}",
                    "-0.184800",
                    "0.004210",
                    "0.100760",
                    "-0.015250",
                    "-0.020000",
                    p_iv,
                    "84",
                    f"{dist:.6f}",
                    f"{dist / spot:.6f}",
                ]
            )
        )
    return rows


def main() -> None:
    rows: list[dict] = []
    under: list[dict] = []
    for idx, (qd, spot, target, decoys) in enumerate(ROLLS):
        rows += _rows(qd, spot, target, decoy=False)
        for d in decoys:
            rows += _rows(qd, spot, d, decoy=True)
        under.append(
            {
                "id": 2255 + idx,
                "symbol": "SPY",
                "date": qd.isoformat(),
                "open": spot - 1.2,
                "high": spot + 2.0,
                "low": spot - 2.5,
                "close": spot,
                "adjusted_close": round(spot * 0.73, 6),
                "volume": 476_649_000,
                "dividend_amount": 0.0,
                "split_coefficient": 1.0,
                "created_at": "2025-12-15 15:56:25",
            }
        )

    opts = pd.DataFrame(rows)
    for col, typ in (
        ("bid_size", "int32"),
        ("ask_size", "int32"),
        ("volume", "int32"),
        ("open_interest", "int32"),
    ):
        opts[col] = opts[col].astype(typ)
    for col in ("implied_volatility", "delta", "gamma", "theta", "vega", "rho"):
        opts[col] = opts[col].astype("float32")

    out = FIXTURES / "lambdaclass"
    out.mkdir(parents=True, exist_ok=True)
    opts.to_parquet(out / "SPY_options.parquet", index=False)
    pd.DataFrame(under).to_parquet(out / "SPY_underlying.parquet", index=False)
    print(f"wrote {len(opts)} option rows, {len(under)} underlying rows -> {out}")

    sample = FIXTURES / "optionsdx_spy_sample.txt"
    sample.write_text("\n".join([OPTIONSDX_HEADER, *_optionsdx_rows()]) + "\n")
    print(f"wrote {len(_optionsdx_rows())} optionsDX rows -> {sample}")


if __name__ == "__main__":
    main()
