"""``research/vrp.py``: implied vol minus the vol the option's life realised.

Every number is pinned against a closed form. The price paths alternate daily
log returns of exactly ``+STEP`` and ``-STEP``, so the zero-mean realised vol
of ANY window on them is ``STEP * sqrt(252)`` -- independent of where the
window starts or how long it is -- and the premium is ``iv`` minus that.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.vrp import (
    MIN_RETURNS,
    compute_vrp,
    measure_vrp,
    realised_vol,
    select_puts,
)

STEP = 0.01
RV = STEP * math.sqrt(252)  # 0.158745...
DAYS = pd.bdate_range("2020-01-01", periods=120)


def _zigzag(dates: pd.DatetimeIndex, step: float = STEP, start: float = 100.0) -> pd.Series:
    signs = np.where(np.arange(len(dates)) % 2 == 0, step, -step)
    signs[0] = 0.0
    return pd.Series(start * np.exp(np.cumsum(signs)), index=dates)


def _quote(
    quote_date: pd.Timestamp,
    dte: int,
    strike: float = 100.0,
    iv: float | None = 0.25,
    spot: float = 100.0,
) -> dict[str, object]:
    return {
        "quote_date": quote_date,
        "expiration": quote_date + pd.Timedelta(days=dte),
        "strike": strike,
        "spot": spot,
        "iv": iv,
    }


# ---- realised vol ----------------------------------------------------------


def test_realised_vol_of_a_zigzag_is_step_times_root_252() -> None:
    closes = _zigzag(DAYS)
    rv, n = realised_vol(closes, DAYS[3], DAYS[24])
    assert n == 21
    assert rv == pytest.approx(RV, rel=1e-12)


def test_realised_vol_is_zero_mean_not_a_standard_deviation() -> None:
    """A steady +1%/day climb has zero standard deviation but realises
    0.01 x sqrt(252): an option holder is paid on movement, drift included."""
    closes = pd.Series(100.0 * np.exp(STEP * np.arange(30)), index=DAYS[:30])
    rv, n = realised_vol(closes, DAYS[0], DAYS[29])
    assert n == 29
    assert rv == pytest.approx(RV, rel=1e-12)


def test_realised_vol_anchors_on_the_last_close_on_or_before_each_end() -> None:
    """A weekend start and a Saturday expiry both resolve to the Friday
    before -- the close an option on that date actually lives from/to."""
    closes = _zigzag(DAYS)
    saturday = pd.Timestamp("2020-01-11")  # DAYS[7] is Friday 2020-01-10
    _rv, n = realised_vol(closes, saturday, pd.Timestamp("2020-01-25"))  # Fri 01-24 = DAYS[17]
    assert n == 10


def test_realised_vol_needs_a_close_before_the_start() -> None:
    rv, n = realised_vol(_zigzag(DAYS), pd.Timestamp("2019-12-01"), DAYS[10])
    assert n == 0
    assert math.isnan(rv)


# ---- which put ---------------------------------------------------------------


def test_select_puts_takes_the_expiry_nearest_the_target_then_the_nearest_strike() -> None:
    day = DAYS[0]
    quotes = pd.DataFrame(
        [
            _quote(day, 21, strike=100.0, iv=0.11),
            _quote(day, 28, strike=90.0, iv=0.12),
            _quote(day, 28, strike=99.0, iv=0.13),  # nearest 30 days, nearest spot
            _quote(day, 28, strike=102.0, iv=0.14),
            _quote(day, 35, strike=100.0, iv=0.15),
        ]
    )
    picked = select_puts(quotes, moneyness=1.0, target_days=30)
    assert len(picked) == 1
    assert picked.loc[0, "iv"] == 0.13


def test_select_puts_breaks_ties_shorter_expiry_and_lower_strike() -> None:
    day = DAYS[0]
    quotes = pd.DataFrame(
        [
            _quote(day, 35, strike=100.0, iv=0.31),
            _quote(day, 25, strike=101.0, iv=0.32),
            _quote(day, 25, strike=99.0, iv=0.33),  # 25 vs 35 tie on 30; 99 vs 101 tie on 100
        ]
    )
    assert select_puts(quotes, moneyness=1.0, target_days=30).loc[0, "iv"] == 0.33


def test_select_puts_targets_moneyness_against_that_days_spot() -> None:
    day = DAYS[0]
    quotes = pd.DataFrame(
        [
            _quote(day, 30, strike=190.0, iv=0.21, spot=200.0),  # 0.95 x 200
            _quote(day, 30, strike=200.0, iv=0.22, spot=200.0),
        ]
    )
    assert select_puts(quotes, moneyness=0.95, target_days=30).loc[0, "iv"] == 0.21


def test_select_puts_skips_a_blank_or_zero_vendor_iv() -> None:
    """A blank ``P_IV`` is a failed solve (CLAUDE.md, optionsDX): the nearer
    strike without one must lose to a farther strike with one."""
    day = DAYS[0]
    quotes = pd.DataFrame(
        [
            _quote(day, 30, strike=100.0, iv=None),
            _quote(day, 30, strike=100.5, iv=0.0),
            _quote(day, 30, strike=103.0, iv=0.27),
        ]
    )
    assert select_puts(quotes, moneyness=1.0, target_days=30).loc[0, "iv"] == 0.27


def test_select_puts_ignores_a_put_expiring_on_its_quote_date() -> None:
    day = DAYS[0]
    quotes = pd.DataFrame([_quote(day, 0, iv=0.9), _quote(day, 60, iv=0.2)])
    assert select_puts(quotes, moneyness=1.0, target_days=1).loc[0, "iv"] == 0.2


# ---- the premium -------------------------------------------------------------


def test_vrp_is_implied_minus_realised_on_a_known_path() -> None:
    quotes = pd.DataFrame([_quote(DAYS[0], 28, iv=0.25), _quote(DAYS[5], 28, iv=0.10)])
    summary = measure_vrp(quotes, _zigzag(DAYS), underlying="spy", as_of=dt.date(2020, 6, 1))

    assert summary.underlying == "SPY"
    assert summary.n_observations == 2
    first, second = summary.observations
    assert first.realised_vol == pytest.approx(RV, rel=1e-12)
    assert first.n_returns == 20  # 28 calendar days from a Wednesday = 20 sessions
    assert first.vrp == pytest.approx(0.25 - RV, rel=1e-12)
    assert second.vrp == pytest.approx(0.10 - RV, rel=1e-12)
    assert summary.mean_vrp == pytest.approx((0.25 + 0.10) / 2 - RV, rel=1e-12)
    assert summary.median_vrp == pytest.approx(summary.mean_vrp, rel=1e-12)
    assert summary.share_positive == 0.5
    assert summary.mean_implied_vol == pytest.approx(0.175)
    assert summary.mean_realised_vol == pytest.approx(RV, rel=1e-12)
    assert (summary.start, summary.end) == (DAYS[0].date(), DAYS[5].date())


def test_a_doubled_step_doubles_the_realised_leg() -> None:
    """Independent of the code's own constant: 2% daily moves realise twice
    what 1% moves do."""
    quotes = pd.DataFrame([_quote(DAYS[0], 28, iv=0.5)])
    summary = measure_vrp(
        quotes, _zigzag(DAYS, step=0.02), underlying="spy", as_of=dt.date(2020, 6, 1)
    )
    assert summary.observations[0].realised_vol == pytest.approx(2 * RV, rel=1e-12)


def test_vrp_never_reads_a_close_after_as_of() -> None:
    """Adversarial (``docs/adr/0009``): the closes run past ``as_of`` into a
    crash of 20% daily moves. An option whose life ended by ``as_of`` must be
    measured on the calm path alone, and one still alive at ``as_of`` must not
    appear at all -- even though the crash closes that would complete its
    window are sitting in the series."""
    as_of = DAYS[40]
    closes = _zigzag(DAYS)
    crash = _zigzag(DAYS[40:], step=0.20, start=float(closes.iloc[40]))
    leaked = pd.concat([closes.iloc[:41], crash.iloc[1:]])

    finished = _quote(DAYS[0], 28, iv=0.30)  # expires well before as_of
    alive = _quote(DAYS[30], 28, iv=0.30)  # expires after as_of
    summary = measure_vrp(
        pd.DataFrame([finished, alive]), leaked, underlying="spy", as_of=as_of.date()
    )

    assert [o.quote_date for o in summary.observations] == [DAYS[0].date()]
    assert summary.observations[0].realised_vol == pytest.approx(RV, rel=1e-12)


def test_vrp_ignores_quotes_dated_after_as_of() -> None:
    quotes = pd.DataFrame([_quote(DAYS[0], 28, iv=0.30), _quote(DAYS[50], 7, iv=0.99)])
    summary = measure_vrp(quotes, _zigzag(DAYS), underlying="spy", as_of=DAYS[45].date())
    assert summary.n_observations == 1


def test_an_expiry_past_the_last_known_close_is_not_yet_measurable() -> None:
    """``as_of`` is past the expiry but the price series stops short of it:
    the window is not closed, so there is no realised leg to report."""
    quotes = pd.DataFrame([_quote(DAYS[0], 28, iv=0.30), _quote(DAYS[15], 28, iv=0.30)])
    closes = _zigzag(DAYS[:30])
    summary = measure_vrp(quotes, closes, underlying="spy", as_of=dt.date(2020, 6, 1))
    assert [o.quote_date for o in summary.observations] == [DAYS[0].date()]


def test_a_window_shorter_than_min_returns_is_dropped() -> None:
    short = _quote(DAYS[0], 5, iv=0.3)  # Wed -> Mon: 3 sessions
    long_enough = _quote(DAYS[10], 9, iv=0.3)  # Wed -> Fri next week: 7 sessions
    summary = measure_vrp(
        pd.DataFrame([short, long_enough]),
        _zigzag(DAYS),
        underlying="spy",
        as_of=dt.date(2020, 6, 1),
        target_days=5,
    )
    assert [o.n_returns for o in summary.observations] == [7]
    assert MIN_RETURNS > 3


def test_no_closed_window_is_a_lookup_error() -> None:
    quotes = pd.DataFrame([_quote(DAYS[100], 28)])
    with pytest.raises(LookupError, match="SPY"):
        measure_vrp(quotes, _zigzag(DAYS), underlying="spy", as_of=DAYS[105].date())


def test_a_month_with_no_quote_is_reported_missing() -> None:
    dates = pd.bdate_range("2020-01-01", "2020-05-29")
    jan, mar = pd.Timestamp("2020-01-06"), pd.Timestamp("2020-03-02")
    quotes = pd.DataFrame([_quote(jan, 28), _quote(mar, 28)])
    summary = measure_vrp(quotes, _zigzag(dates), underlying="spy", as_of=dt.date(2020, 6, 1))
    assert summary.missing_months == ["202002"]


@settings(max_examples=40, deadline=None)
@given(
    ivs=st.lists(st.floats(0.05, 1.5), min_size=1, max_size=12),
    seed=st.integers(0, 2**32 - 1),
)
def test_the_summary_does_not_depend_on_row_order(ivs: list[float], seed: int) -> None:
    quotes = pd.DataFrame([_quote(DAYS[i * 3], 28, iv=iv) for i, iv in enumerate(ivs)])
    shuffled = quotes.sample(frac=1.0, random_state=seed % 2**32)
    closes = _zigzag(DAYS)
    a = measure_vrp(quotes, closes, underlying="spy", as_of=dt.date(2020, 9, 1))
    b = measure_vrp(shuffled, closes, underlying="spy", as_of=dt.date(2020, 9, 1))
    assert a == b
    assert a.mean_vrp == pytest.approx(float(np.mean(ivs)) - RV, rel=1e-9, abs=1e-12)


# ---- the lake ---------------------------------------------------------------


def test_compute_vrp_reads_the_panel_and_ohlcv_and_keeps_their_overlap(tmp_path: Path) -> None:
    """The panel starts before the OHLCV does (as SPY's does: 2010 vs 2016):
    a quote with no price history under it is outside the overlap and is not
    measured, rather than measured against nothing."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2020, 7, 1)
    prices = DAYS[10:]
    closes = _zigzag(prices)
    store.write_bronze(
        "ohlcv_spy",
        ingest,
        pd.DataFrame(
            {
                "symbol": "SPY",
                "trade_date": prices,
                "open": closes.to_numpy(),
                "high": closes.to_numpy(),
                "low": closes.to_numpy(),
                "close": closes.to_numpy(),
                "volume": np.full(len(prices), 1_000, dtype=int),
                "adj_close": closes.to_numpy(),
            }
        ),
    )
    panel = pd.DataFrame(
        [_quote(DAYS[0], 28, iv=0.40), _quote(DAYS[12], 28, iv=0.20, strike=100.0)]
    )
    panel["underlying"] = "SPY"
    panel["bid"], panel["ask"], panel["volume"] = 1.0, 1.1, 10
    store.write_bronze("optionsdx_quotes_spy", ingest, panel)

    summary = compute_vrp(store, underlying="SPY", as_of=ingest)
    assert [o.quote_date for o in summary.observations] == [DAYS[12].date()]
    assert summary.mean_vrp == pytest.approx(0.20 - RV, rel=1e-12)


def test_compute_vrp_before_any_snapshot_is_a_lookup_error(tmp_path: Path) -> None:
    with pytest.raises(LookupError):
        compute_vrp(DeltaLakeStore(tmp_path), underlying="spy", as_of=dt.date(2020, 7, 1))
