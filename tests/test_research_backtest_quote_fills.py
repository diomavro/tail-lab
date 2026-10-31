from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.contracts.ohlcv import dataset_id
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.portfolio import PortfolioLeg, run_portfolio
from tail_lab.research.backtest.put_roll import (
    load_asof_series,
    load_ohlcv_series,
    price_snapshot_ids,
)
from tail_lab.research.backtest.quote_fills import (
    MAX_SNAP_MONEYNESS_PP,
    SEAM_SESSIONS,
    OptionsDxQuoteSource,
    build_session_index,
    index_nbytes,
    with_panel_prehistory,
)
from tail_lab.research.backtest.ranking import snapshot_ids_for
from tail_lab.research.quote_coverage import compute_quote_coverage

SYMBOL = "SPY"


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "underlying": SYMBOL,
        "quote_date": pd.Timestamp("2024-01-05"),
        "expiration": pd.Timestamp("2024-02-02"),
        "strike": 90.0,
        "bid": 1.20,
        "ask": 1.30,
        "volume": 500,
        "spot": 100.0,
        "iv": 0.28,
        "delta": -0.32,
        "vega": 0.15,
        "theta": -12.0,
    }
    base.update(overrides)
    return base


def _panel(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(list(rows) or [_row()])


def _source(*rows: dict[str, object], symbol: str = SYMBOL) -> OptionsDxQuoteSource:
    return OptionsDxQuoteSource(_panel(*rows), symbol=symbol)


# ---- the happy path, pinned by hand -----------------------------------------


def test_a_clean_4week_10pct_request_returns_the_hand_derived_fill() -> None:
    """entry 2024-01-05, spot 100, basis 1: target expiry is 2024-01-05 + 28
    calendar days = 2024-02-02 exactly, target strike is 100 x 0.90 = 90.0
    exactly, and the panel lists precisely that contract at 1.20/1.30. If the
    expiry-arithmetic, target-strike formula, or ask-selection regress, this
    stops matching numbers a reader can recompute on paper."""
    source = _source(_row())
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )

    assert fill is not None
    assert fill.strike == pytest.approx(90.0)
    assert fill.premium == pytest.approx(1.30)  # the ASK, not the 1.25 mid
    assert fill.expiry == dt.date(2024, 2, 2)
    assert fill.realized_moneyness_pct == pytest.approx(10.0)
    assert fill.realized_dte == 28


# ---- the guards, which are the point ----------------------------------------


def test_adversarial_a_future_dated_row_that_matches_better_is_never_used() -> None:
    """The panel holds a row dated 2024-01-08 (three days after entry) whose
    strike/expiry match the request exactly -- and a row dated on the entry
    date whose only listed expiry is too early to serve it. If quote_date
    filtering regresses to `<=` or is dropped, this starts reaching forward in
    time to fill an order and returns a Fill instead of None -- the
    docs/adr/0009 failure mode, in the one module built to price the deepest,
    least-model-trustworthy legs."""
    source = _source(
        _row(quote_date=pd.Timestamp("2024-01-05"), expiration=pd.Timestamp("2024-01-12")),
        _row(quote_date=pd.Timestamp("2024-01-08"), expiration=pd.Timestamp("2024-02-02")),
    )
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


def test_a_quote_from_an_EARLIER_session_is_not_substituted_either() -> None:
    """The entry-date session lists nothing usable; a session three days EARLIER
    lists a perfect match. It must still refuse.

    Added because mutation-testing the guard showed the existing adversarial
    test does not cover this: relaxing the filter from ``==`` to ``<=`` passed
    all nine tests. That relaxation is not look-ahead -- it reaches backwards,
    not forwards -- which is exactly why it slips past a suite that only asks
    "did we see the future". It is still wrong: it prices a position entered
    today with a premium nobody was quoting today, and on the deep OOM legs
    this module exists for, a three-day-old ask can be several multiples of
    the live one. The quote must be the one from THAT session or there is no
    fill.
    """
    source = _source(
        _row(quote_date=pd.Timestamp("2024-01-02"), expiration=pd.Timestamp("2024-02-02")),
        _row(quote_date=pd.Timestamp("2024-01-05"), expiration=pd.Timestamp("2024-01-12")),
    )
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


def test_no_row_at_all_for_the_entry_date_is_refused_not_substituted() -> None:
    """The panel only quotes this symbol on 2024-01-08 -- nothing on the
    2024-01-05 entry date. This is guard 1's base case, distinct from the
    adversarial test above (there, an entry-date row exists but the wrong
    expiry; here, none exists at all): if the point-in-time filter is ever
    dropped rather than merely loosened, this is the case that would start
    silently reaching into a day that has no rows to filter down to."""
    source = _source(_row(quote_date=pd.Timestamp("2024-01-08")))
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


def test_split_basis_resolves_strike_in_panel_basis_and_converts_back() -> None:
    """The real NVDA 2023-06-01 numbers: caller (split-adjusted OHLCV) spot
    39.77, panel (as-traded) spot 397.74 -- a ~10.001x basis from the 2024
    10:1 split. A target strike resolved in the CALLER's basis (39.77 x 0.80
    = 31.82) would look for a listed strike near 32 on a panel actually
    quoting strikes near 320 and either find nothing or snap to noise; a
    target resolved against the wrong side entirely (e.g. multiplying instead
    of dividing) lands near a nonsensical 240+ strike. The correct answer is a
    strike ~20% below 39.77, translated back through the measured basis."""
    source = _source(
        _row(
            underlying="NVDA",
            quote_date=pd.Timestamp("2023-06-01"),
            expiration=pd.Timestamp("2023-06-29"),
            strike=340.0,
            bid=3.00,
            ask=3.20,
            spot=397.74,
        ),
        _row(
            underlying="NVDA",
            quote_date=pd.Timestamp("2023-06-01"),
            expiration=pd.Timestamp("2023-06-29"),
            strike=318.0,
            bid=2.10,
            ask=2.20,
            spot=397.74,
        ),
        _row(
            underlying="NVDA",
            quote_date=pd.Timestamp("2023-06-01"),
            expiration=pd.Timestamp("2023-06-29"),
            strike=300.0,
            bid=1.50,
            ask=1.65,
            spot=397.74,
        ),
        symbol="NVDA",
    )
    fill = source.fill(
        entry_date=dt.date(2023, 6, 1), spot=39.77, moneyness_pct=20.0, tenor_weeks=4.0
    )

    assert fill is not None
    basis = 397.74 / 39.77
    assert fill.strike == pytest.approx(318.0 / basis, rel=1e-6)
    assert fill.strike == pytest.approx(39.77 * 0.80, rel=0.02)  # ~20% below 39.77
    assert fill.strike < 100.0  # nowhere near the 240+ a basis-inversion bug would produce
    assert fill.premium == pytest.approx(2.20 / basis, rel=1e-6)


def test_split_basis_refusal_on_a_ratio_that_is_neither_clean_nor_one() -> None:
    """panel spot 150 / caller spot 100 = 1.5x -- not ~1 (same basis) and not
    ~any of {2,3,4,5,7,10,20} (a clean split). A ratio like this is a data
    problem (wrong symbol joined, stale spot, a non-integer corporate action),
    and dividing through it anyway would produce a plausible-looking, silently
    wrong strike and premium -- worse than refusing."""
    source = _source(_row(spot=150.0))
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


def test_snap_tolerance_refuses_a_3pp_miss() -> None:
    """Requested 10% OTM; the only listed strike at this expiry is 87, i.e.
    13% OTM -- a 3pp miss, 3x MAX_SNAP_MONEYNESS_PP. Filling this would size
    and report a materially different strategy under the '10% OTM' label."""
    source = _source(_row(strike=87.0))
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None

    # A miss just inside the boundary is still filled -- the guard is > , not >=.
    boundary_strike = 100.0 - (10.0 + MAX_SNAP_MONEYNESS_PP) + 1e-6  # ~0.999999pp miss
    ok = _source(_row(strike=boundary_strike)).fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert ok is not None


def test_a_zero_bid_contract_is_not_a_price() -> None:
    """Same strike, same expiry, a tight-looking 0.20 ask -- but no bid.
    Nobody is offering to take the position back, so this is not a fill
    however cheap the ask looks."""
    source = _source(_row(bid=0.0, ask=0.20))
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


def test_a_spread_two_thirds_of_mid_is_not_a_price() -> None:
    """0.02/0.04: mid 0.03, spread (0.04-0.02)/0.03 = 0.67 -- above
    MAX_RELATIVE_SPREAD (0.50, ported unchanged from marks). Quoting a mid
    here and sizing from it would invent a fill nobody offered."""
    source = _source(_row(bid=0.02, ask=0.04))
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


def test_no_listed_expiry_at_or_after_the_target_is_refused() -> None:
    """The only listed expiry (2024-01-19) is before the 4-week target
    (2024-02-02). Snapping DOWN to it would buy a shorter, cheaper, different
    position than the one requested -- the one rounding direction that
    quietly flatters a result compared against it, so it is refused rather
    than substituted."""
    source = _source(_row(expiration=pd.Timestamp("2024-01-19")))
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


# ---- index_nbytes: the cache's byte-budget input ----------------------------


def test_index_nbytes_overstates_rather_than_understates_the_real_cost() -> None:
    """A byte budget fed an UNDERCOUNT does not evict when it should.

    `memory_usage(deep=True)` accounts for values and not for pandas' per-frame
    overhead. With 3,500 session frames that is ~30%: measured on the real SPY
    panel, an index reporting 125 MB cost ~174 MB of process RSS. Three such
    sources then sat at 94% of a 400 MB budget while the process peaked at
    1195 MB on a 1024 MB machine -- the cache believing itself comfortably in
    budget is precisely how it overshoots.

    An earlier version of this test asserted the opposite property: that
    `index_nbytes` deduplicates sessions aliasing one parent block. Measured,
    they do not alias -- `panel[mask]` is a boolean-index copy -- so the dedup
    changed the answer by 0.3% and the test was pinning a rationale that was
    not true.
    """
    rows = 4_000
    panel = pd.DataFrame(
        {
            "underlying": ["SPY"] * rows,
            "quote_date": pd.to_datetime([f"2024-01-{i % 4 + 1:02d}" for i in range(rows)]),
            "expiration": pd.to_datetime(["2024-02-02"] * rows),
            "strike": np.arange(rows, dtype=float),
            "bid": np.arange(rows, dtype=float),
            "ask": np.arange(rows, dtype=float),
            "spot": np.full(rows, 100.0),
        }
    )
    sessions = build_session_index(panel, "SPY")
    raw = sum(int(f.memory_usage(deep=True).sum()) for f in sessions.values())

    assert index_nbytes(sessions) > raw, "the estimate must not undercount"
    assert index_nbytes({}) == 0


def test_index_nbytes_of_an_empty_index_is_zero() -> None:
    assert index_nbytes({}) == 0


# ---- the panel's prehistory in front of OHLCV --------------------------------

INGEST = dt.date(2026, 1, 5)
#: 30 panel-only sessions, then 40 shared with OHLCV (its first is the seam).
PANEL_DAYS = pd.bdate_range("2016-08-01", periods=70)
OHLCV_DAYS = PANEL_DAYS[30:]


def _ohlcv(close: float = 200.0) -> pd.Series:
    return pd.Series(close, index=OHLCV_DAYS, name="SPY", dtype=float)


def _write_panel(
    store: DeltaLakeStore,
    spot: list[float] | np.ndarray,
    *,
    symbol: str = "spy",
    ingest: dt.date = INGEST,
    days: pd.DatetimeIndex = PANEL_DAYS,
) -> None:
    """One put per session, its ``spot`` as given -- all the splice reads."""
    rows = [
        _row(
            underlying=symbol.upper(),
            quote_date=day,
            expiration=day + pd.Timedelta(days=28),
            spot=float(s),
            strike=float(s) * 0.9,
        )
        for day, s in zip(days, spot, strict=True)
    ]
    store.write_bronze(f"optionsdx_quotes_{symbol}", ingest, pd.DataFrame(rows))


def _write_ohlcv(store: DeltaLakeStore, close: float = 200.0) -> None:
    store.write_bronze(
        dataset_id("spy"),
        INGEST,
        pd.DataFrame(
            {
                "symbol": "SPY",
                "trade_date": OHLCV_DAYS,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000_000,
                "adj_close": close,
            }
        ),
    )


def test_the_panel_sessions_before_ohlcv_are_spliced_in_front_on_ohlcv_basis(
    tmp_path: Path,
) -> None:
    """Panel spot 1950 before the seam, then 2000 alongside OHLCV's 200 -- a
    clean 10x basis (the NVDA shape). The 30 earlier sessions must arrive as
    1950 / 10 = 195.0, ahead of OHLCV's own rows, which are untouched."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [1950.0] * 30 + [2000.0] * 40)
    prices = _ohlcv()

    spliced = with_panel_prehistory(store, prices, symbol="SPY", as_of=INGEST)

    assert spliced.index[0] == PANEL_DAYS[0]
    assert len(spliced) == 70
    assert spliced.index.is_monotonic_increasing
    assert spliced.iloc[:30].tolist() == pytest.approx([195.0] * 30)
    pd.testing.assert_series_equal(spliced.iloc[30:], prices, check_freq=False)
    assert spliced.name == "SPY"


@pytest.mark.parametrize(
    "seam_spot",
    [
        # 1.5x, flat through the seam: neither the same basis nor a clean
        # split apart, and no step for the split guard to catch instead.
        [300.0] * 40,
        # Same basis on the first session, 2% off on one inside the seam.
        [200.0] * 5 + [204.0] + [200.0] * 34,
        # ...and on the seam's LAST session, so the window cannot quietly shrink.
        [200.0] * (SEAM_SESSIONS - 1) + [204.0] + [200.0] * (40 - SEAM_SESSIONS),
    ],
)
def test_a_seam_that_does_not_measure_one_clean_basis_leaves_ohlcv_alone(
    tmp_path: Path, seam_spot: list[float]
) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [seam_spot[0]] * 30 + seam_spot)
    prices = _ohlcv()

    assert with_panel_prehistory(store, prices, symbol="spy", as_of=INGEST) is prices


def test_a_seam_the_panel_does_not_fully_cover_leaves_ohlcv_alone(tmp_path: Path) -> None:
    """One OHLCV session inside the seam has no panel row: the basis would
    rest on 19 sessions, not the 20 a reader is told, so it is refused."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70)
    prices = _ohlcv()
    prices.index = prices.index.insert(5, pd.Timestamp("2016-09-17")).delete(6)  # a Saturday
    prices = prices.sort_index()

    assert with_panel_prehistory(store, prices, symbol="spy", as_of=INGEST) is prices


def test_a_split_after_the_seam_does_not_cost_the_prehistory(tmp_path: Path) -> None:
    """NVDA's shape: a 4:1 split inside the OHLCV overlap, which split-adjusted
    OHLCV already absorbs. Only steps up to the seam are read as splits."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 50 + [50.0] * 20)

    spliced = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert len(spliced) == 70


def test_ohlcv_shorter_than_the_seam_is_never_spliced_onto(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70)
    prices = _ohlcv().iloc[: SEAM_SESSIONS - 1]

    assert with_panel_prehistory(store, prices, symbol="spy", as_of=INGEST) is prices


def test_a_session_is_priced_at_the_median_spot_its_rows_quote(tmp_path: Path) -> None:
    """The real panel has hundreds of rows a session. The prehistory's first
    session quotes 0 (dropped), 190, 200 and 260: its price is 200."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70)
    first = PANEL_DAYS[0]
    store.write_bronze(
        "optionsdx_quotes_spy",
        INGEST + dt.timedelta(days=1),
        pd.DataFrame(
            [
                _row(quote_date=first, spot=s, strike=100.0 + i)
                for i, s in enumerate([0.0, 190.0, 260.0])
            ]
            + [_row(quote_date=d, spot=200.0) for d in PANEL_DAYS]
        ),
    )

    spliced = with_panel_prehistory(
        store, _ohlcv(), symbol="spy", as_of=INGEST + dt.timedelta(days=1)
    )

    assert spliced.iloc[0] == pytest.approx(200.0)


def test_a_hole_in_the_panel_starts_the_prehistory_after_it(tmp_path: Path) -> None:
    """Ten sessions in 2010, nothing until 2016: a roll entered in 2010 must
    not be valued against a 2016 price, so only the run touching the seam
    is kept."""
    store = DeltaLakeStore(tmp_path)
    days = pd.bdate_range("2010-01-04", periods=10).append(PANEL_DAYS[10:])
    _write_panel(store, [200.0] * 70, days=days)

    spliced = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert spliced.index[0] == PANEL_DAYS[10]


@pytest.mark.parametrize(
    ("prehistory", "first"),
    [
        # A reverse split (1:2, 100 -> 200): the guard looks both ways.
        ([100.0] * 10 + [200.0] * 20, 10),
        # A 3:2 split (300 -> 200) is caught too, not only whole-number ones.
        ([300.0] * 10 + [200.0] * 20, 10),
        # Two splits: only the sessions after the LAST one share the seam's basis.
        ([800.0] * 5 + [400.0] * 5 + [200.0] * 20, 10),
    ],
)
def test_the_prehistory_starts_after_the_last_step_either_way(
    tmp_path: Path, prehistory: list[float], first: int
) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, prehistory + [200.0] * 40)

    spliced = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert spliced.index[0] == PANEL_DAYS[first]
    assert spliced.iloc[: 30 - first].tolist() == pytest.approx([200.0] * (30 - first))


def test_the_memo_never_serves_a_later_snapshot_to_an_earlier_as_of(tmp_path: Path) -> None:
    """One store, two panel snapshots, the later one read first: the earlier
    as_of must still see its own snapshot's prices."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70)
    _write_panel(store, [190.0] * 30 + [200.0] * 40, ingest=INGEST + dt.timedelta(days=1))

    later = with_panel_prehistory(
        store, _ohlcv(), symbol="spy", as_of=INGEST + dt.timedelta(days=1)
    )
    earlier = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert later.iloc[0] == pytest.approx(190.0)
    assert earlier.iloc[0] == pytest.approx(200.0)


def test_a_disagreement_past_the_seam_window_does_not_refuse(tmp_path: Path) -> None:
    """Only the first SEAM_SESSIONS shared sessions are compared."""
    store = DeltaLakeStore(tmp_path)
    seam = [200.0] * SEAM_SESSIONS + [260.0] * (40 - SEAM_SESSIONS)
    _write_panel(store, [190.0] * 30 + seam)

    spliced = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert len(spliced) == 70


def test_a_split_inside_the_prehistory_keeps_only_the_sessions_after_it(tmp_path: Path) -> None:
    """A 2:1 step at session 10 (400 -> 200) is a split the seam, measured
    after it, cannot correct: the ten sessions before it are dropped."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [400.0] * 10 + [200.0] * 20 + [200.0] * 40)

    spliced = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert spliced.index[0] == PANEL_DAYS[10]
    assert spliced.iloc[:20].tolist() == pytest.approx([200.0] * 20)


def test_a_split_on_the_seam_itself_leaves_ohlcv_alone(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [400.0] * 30 + [200.0] * 40)
    prices = _ohlcv()

    assert with_panel_prehistory(store, prices, symbol="spy", as_of=INGEST) is prices


def test_a_crash_day_below_the_split_jump_is_kept(tmp_path: Path) -> None:
    """A 25% one-session fall is a market move, not a split."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 10 + [150.0] * 20 + [200.0] * 40)

    spliced = with_panel_prehistory(store, _ohlcv(), symbol="spy", as_of=INGEST)

    assert spliced.index[0] == PANEL_DAYS[0]


def test_a_panel_ingested_after_as_of_is_not_read(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70, ingest=INGEST + dt.timedelta(days=1))
    prices = _ohlcv()

    assert with_panel_prehistory(store, prices, symbol="spy", as_of=INGEST) is prices


@pytest.mark.parametrize("symbol", ["hyg", "qqq"])
def test_a_name_without_a_panel_in_the_lake_is_left_alone(tmp_path: Path, symbol: str) -> None:
    """``hyg`` is not an optionsDX name; ``qqq`` is, but this lake holds SPY's."""
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70)
    prices = _ohlcv()

    assert with_panel_prehistory(store, prices, symbol=symbol, as_of=INGEST) is prices


def test_a_panel_with_nothing_before_ohlcv_adds_nothing(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_panel(store, [200.0] * 70)
    prices = pd.Series(200.0, index=PANEL_DAYS, name="SPY")

    assert with_panel_prehistory(store, prices, symbol="spy", as_of=INGEST) is prices


def test_every_backtest_path_and_the_marks_span_read_the_spliced_prices(tmp_path: Path) -> None:
    """Through the chokepoint: the price path starts at the panel's first
    session, and the realised-vol proxy is warm at OHLCV's first day instead
    of NaN for its first 20 sessions."""
    store = DeltaLakeStore(tmp_path)
    _write_ohlcv(store)
    _write_panel(store, [200.0] * 70)

    prices, vol = load_asof_series(store, "spy", INGEST)
    coverage = compute_quote_coverage(store, symbol="spy", as_of=INGEST)

    assert prices.index[0] == PANEL_DAYS[0]
    assert vol.loc[OHLCV_DAYS[0]] == pytest.approx(0.0)
    assert coverage.price_span == (PANEL_DAYS[0].date(), OHLCV_DAYS[-1].date())


def test_a_spliced_path_cites_the_panel_it_read(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_ohlcv(store)
    _write_panel(store, [200.0] * 70)
    store.write_bronze("vix", INGEST, pd.DataFrame({"date": OHLCV_DAYS, "close": 15.0}))
    ohlcv = store.bronze_snapshot_id(dataset_id("spy"), INGEST)
    panel = store.bronze_snapshot_id("optionsdx_quotes_spy", INGEST)

    assert price_snapshot_ids(store, "SPY", INGEST) == [ohlcv, panel]
    assert snapshot_ids_for(store, INGEST, ["spy"])[1:3] == [ohlcv, panel]


def test_a_portfolio_cites_the_panel_its_legs_read(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_ohlcv(store)
    _write_panel(store, [200.0] * 70)
    store.write_bronze("vix", INGEST, pd.DataFrame({"date": PANEL_DAYS, "close": 15.0}))
    leg = PortfolioLeg(asset="spy", moneyness_pct=5.0, tenor_weeks=1.0, weight=1.0)

    result = run_portfolio(store, legs=[leg], as_of=INGEST, notional=1000.0, years=0.2)

    assert store.bronze_snapshot_id("optionsdx_quotes_spy", INGEST) in result.snapshot_ids


def test_the_ohlcv_reader_never_splices(tmp_path: Path) -> None:
    """The Surface and the strike preview read through it, and must never
    touch the panel (their own docstrings)."""
    store = DeltaLakeStore(tmp_path)
    _write_ohlcv(store)
    _write_panel(store, [200.0] * 70)

    prices, _ = load_ohlcv_series(store, "spy", INGEST)

    assert prices.index[0] == OHLCV_DAYS[0]


def test_a_name_without_a_panel_cites_its_ohlcv_alone(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    _write_ohlcv(store)

    assert price_snapshot_ids(store, "spy", INGEST) == [
        store.bronze_snapshot_id(dataset_id("spy"), INGEST)
    ]
