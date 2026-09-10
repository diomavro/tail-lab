from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tail_lab.research.backtest.quote_fills import (
    MAX_SNAP_MONEYNESS_PP,
    OptionsDxQuoteSource,
    index_nbytes,
)

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


def test_index_nbytes_dedupes_two_sessions_that_alias_the_same_parent_block() -> None:
    """`frame[keep]` under pandas copy-on-write is a lazy reference: two
    session frames sliced from the same parent panel can share the exact same
    underlying block, so summing each frame's own `nbytes` counts that shared
    memory once per session instead of once. `index_nbytes` must dedupe by
    the block's `base` identity and report the true, single-counted figure --
    the specific failure mode its docstring describes (131.5 MB summed vs.
    193 MB actually resident)."""
    parent = pd.DataFrame({"strike": [90.0, 95.0, 100.0, 105.0]})
    session_a = parent[["strike"]]
    session_b = parent[["strike"]]

    # Precondition: the two frames really do alias one block, or this test
    # would not be exercising the dedup path at all.
    base_a = session_a._mgr.blocks[0].values.base
    base_b = session_b._mgr.blocks[0].values.base
    assert base_a is not None
    assert base_a is base_b

    naive_sum = sum(
        block.values.nbytes for frame in (session_a, session_b) for block in frame._mgr.blocks
    )
    sessions = {
        pd.Timestamp("2024-01-05"): session_a,
        pd.Timestamp("2024-01-08"): session_b,
    }

    actual = index_nbytes(sessions)
    assert actual == parent["strike"].to_numpy().nbytes
    assert actual == naive_sum // 2


def test_index_nbytes_of_an_empty_index_is_zero() -> None:
    assert index_nbytes({}) == 0
