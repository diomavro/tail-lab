"""Tests for ``research/backtest/quote_fills.OptionQuotesSource`` -- the
``contracts/option_quotes`` sibling of ``OptionsDxQuoteSource``.

Mirrors ``test_research_backtest_quote_fills.py`` in spirit: every
non-adversarial case pins its numbers by hand in the docstring, and every
guard gets a test that TRIES TO CHEAT it (``docs/STANDARDS.md``). What is
NOT re-tested here: guards 1 (point-in-time filtering mechanics), 2 (the
basis-plausibility arithmetic) and 4 (the snap-tolerance arithmetic) share
their implementation byte-for-byte with ``OptionsDxQuoteSource`` via
``quote_fills._session``/``_resolve_basis``/``_fill``, and that shared
machinery is already exercised by ``test_research_backtest_quote_fills.py``.
This file covers what is genuinely different about this source: SPY-only
construction, the tenor guard unique to a monthly-only panel, and the
richer (open-interest-aware) liquidity test -- plus ``from_store``, which
``OptionsDxQuoteSource`` does not have.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.option_quotes import DATASET as OPTION_QUOTES_DATASET
from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.quote_fills import OptionQuotesSource

SYMBOL = "SPY"
#: 2024-01-05 + 28 calendar days -- the panel's one listed expiry in every
#: fixture below, chosen to sit inside the dataset's monthly DTE band
#: (26-40, median 31 -- contracts/option_quotes.py).
ENTRY = dt.date(2024, 1, 5)
EXPIRY = pd.Timestamp("2024-02-02")


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "underlying": SYMBOL,
        "quote_date": pd.Timestamp(ENTRY),
        "expiration": EXPIRY,
        "strike": 450.0,
        "bid": 5.00,
        "ask": 5.50,
        "volume": 500,
        "open_interest": 50,
        "spot": 500.0,
    }
    base.update(overrides)
    return base


def _panel(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(list(rows) or [_row()])


def _source(*rows: dict[str, object]) -> OptionQuotesSource:
    return OptionQuotesSource(_panel(*rows))


# ---- the happy path, pinned by hand -----------------------------------------


def test_a_clean_4week_10pct_request_returns_the_hand_derived_fill() -> None:
    """entry 2024-01-05, spot 500, moneyness 10%: target strike is
    500 x 0.90 = 450.0 exactly, target expiry is 2024-01-05 + 28 calendar
    days = 2024-02-02 exactly (realized DTE 28, inside the panel's monthly
    26-40 band), and the panel lists precisely that contract at 5.00/5.50
    with open interest 50 (>= MIN_OPEN_INTEREST). If the expiry arithmetic,
    target-strike formula, tenor guard, or ask-selection regress, this stops
    matching numbers a reader can recompute on paper."""
    fill = _source(_row()).fill(entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0)

    assert fill is not None
    assert fill.strike == pytest.approx(450.0)
    assert fill.premium == pytest.approx(5.50)  # the ASK, not the 5.25 mid
    assert fill.expiry == dt.date(2024, 2, 2)
    assert fill.realized_moneyness_pct == pytest.approx(10.0)
    assert fill.realized_dte == 28


# ---- adversarial point-in-time ----------------------------------------------


def test_adversarial_a_later_dated_row_that_matches_better_is_never_used() -> None:
    """The entry-date session lists only a contract expiring 2024-01-08 (DTE
    3 -- before the 4-week target, so ``_select_expiry`` excludes it and the
    request is unserviceable that day). A session three days LATER lists the
    exact 450-strike/2024-02-02 contract the request wants. If point-in-time
    filtering ever loosens from ``==`` to ``<=``, this starts reaching
    forward in time to fill an order and returns a ``Fill`` instead of
    ``None`` -- the docs/adr/0009 failure mode."""
    source = _source(
        _row(quote_date=pd.Timestamp(ENTRY), expiration=pd.Timestamp("2024-01-08")),
        _row(quote_date=pd.Timestamp("2024-01-08"), expiration=EXPIRY),
    )
    fill = source.fill(entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0)
    assert fill is None


# ---- guard 3: monthly-only tenor -- 1w/12w refuse, ~4w fills ----------------


def test_a_1week_request_is_unserviceable_not_stretched() -> None:
    """The only listed expiry is 28 calendar days out (DTE 28). A 1-week
    request targets 7 days out; the realized DTE is 21 days further than
    requested, past ``MAX_TENOR_GAP_DAYS`` (7). Without this guard,
    ``_select_expiry``'s "nearest listed expiry AT OR AFTER target" rule
    would happily accept the 28-day contract and report it as a 1-week
    fill -- wrong, not approximate, since nothing shorter is ever listed on
    this panel."""
    fill = _source(_row()).fill(entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=1.0)
    assert fill is None


def test_a_12week_request_is_also_unserviceable() -> None:
    """A 12-week request targets 84 days out; the only listed expiry (28
    days out) is BEFORE that target, so ``_select_expiry`` itself already
    refuses (guard 3's "AT OR AFTER" rule) -- belt-and-braces with the
    tenor-gap guard above, since a monthly-only panel can fail a too-long
    request either way, and both must land on ``None``."""
    fill = _source(_row()).fill(entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=12.0)
    assert fill is None


def test_a_4week_request_fills() -> None:
    """Sanity companion to the two refusals above: the tenor this panel
    actually lists (~4 weeks) is serviceable -- restated from the pinned
    happy path so the three tenor outcomes read together."""
    fill = _source(_row()).fill(entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0)
    assert fill is not None


# ---- guard 5: the richer liquidity test, including open interest -----------


def test_a_row_failing_ONLY_open_interest_is_refused() -> None:
    """Same strike, same expiry, a tight 5.00/5.20 quote (bid > 0, spread
    ~4% -- comfortably inside MAX_RELATIVE_SPREAD) -- but open interest of 9,
    one below MIN_OPEN_INTEREST (10). ``OptionsDxQuoteSource``'s own
    ``_is_liquid`` has no open-interest column to check and would pass this
    row; ``OptionQuotesSource`` uses ``marks._is_liquid`` (which does check
    it) and must refuse -- the test that proves the richer liquidity check
    is actually wired, not just documented."""
    fill = _source(_row(bid=5.00, ask=5.20, open_interest=9)).fill(
        entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None

    # The same row at the floor value passes -- isolates OI as the cause.
    ok = _source(_row(bid=5.00, ask=5.20, open_interest=10)).fill(
        entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert ok is not None


# ---- guard 2: SPY-only construction -----------------------------------------


def test_a_non_spy_symbol_raises_at_construction() -> None:
    """``option_quotes`` holds SPY only (docs/DATA_CONTRACTS.md). A panel
    carrying another underlying is a configuration bug, not a coverage gap --
    it must be refused loudly at construction, not silently return ``None``
    forever the way a symbol-mismatched ``OptionsDxQuoteSource`` would."""
    with pytest.raises(ValueError, match="SPY-only"):
        OptionQuotesSource(_panel(_row(underlying="QQQ")))


# ---- guard 2: split basis, measured not assumed -----------------------------


def test_an_implausible_split_basis_is_refused() -> None:
    """panel spot 750 / caller spot 500 = 1.5x -- not ~1 (same basis) and not
    ~any clean split factor. Measured on the real overlap this panel has
    with bronze OHLCV the ratio is exactly 1.0000 (SPY has not split in this
    span), but that must not be hardcoded: an implausible ratio, however it
    arose, is refused rather than divided through."""
    fill = _source(_row(spot=750.0)).fill(
        entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is None


# ---- mark() -------------------------------------------------------------


def test_mark_returns_that_sessions_bid_not_the_ask() -> None:
    """The same contract quoted on two sessions -- day 1 at 5.00/5.50, day 2
    (after some decay) at 4.40/4.90. ``mark`` on each day must return THAT
    day's bid, never the ask ``fill`` would have paid."""
    panel = _panel(
        _row(quote_date=pd.Timestamp(ENTRY), bid=5.00, ask=5.50),
        _row(quote_date=pd.Timestamp("2024-01-08"), bid=4.40, ask=4.90),
    )
    source = OptionQuotesSource(panel)

    assert source.mark(entry_date=ENTRY, strike=450.0, expiry=dt.date(2024, 2, 2)) == pytest.approx(
        5.00
    )
    assert source.mark(
        entry_date=dt.date(2024, 1, 8), strike=450.0, expiry=dt.date(2024, 2, 2)
    ) == pytest.approx(4.40)


def test_mark_refuses_a_session_with_no_matching_contract() -> None:
    """No session at all on the requested date, and a session that exists
    but lists nothing near the requested strike, both refuse (``None``)
    rather than guessing."""
    source = _source(_row())
    assert source.mark(entry_date=dt.date(2024, 1, 9), strike=450.0, expiry=EXPIRY.date()) is None
    assert source.mark(entry_date=ENTRY, strike=900.0, expiry=EXPIRY.date()) is None


# ---- from_store ---------------------------------------------------------


def test_from_store_reads_the_asof_bronze_snapshot(tmp_path: Path) -> None:
    """``from_store`` must read through ``read_bronze_as_of``, not a bare
    ``read_bronze``: a snapshot ingested AFTER ``as_of`` must not leak into a
    source built as of an earlier date -- the same point-in-time contract
    every other backtest read in this repo (docs/adr/0009) is held to."""
    store = DeltaLakeStore(tmp_path)
    day1, day2 = dt.date(2024, 1, 1), dt.date(2024, 1, 2)
    store.write_bronze(OPTION_QUOTES_DATASET, day1, _panel(_row()))

    source_day1 = OptionQuotesSource.from_store(store, as_of=day1)
    fill = source_day1.fill(entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0)
    assert fill is not None
    assert fill.strike == pytest.approx(450.0)

    # A later snapshot with a different strike must not change the day1 read.
    store.write_bronze(OPTION_QUOTES_DATASET, day2, _panel(_row(strike=400.0)))
    source_day1_again = OptionQuotesSource.from_store(store, as_of=day1)
    fill_again = source_day1_again.fill(
        entry_date=ENTRY, spot=500.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill_again is not None
    assert fill_again.strike == pytest.approx(450.0)


def test_mark_converts_the_strike_into_the_panel_basis_and_the_bid_back() -> None:
    """The one line in the shared `_mark` that a split makes load-bearing.

    Added because mutation testing found it unprotected: replacing
    `panel_strike = strike * basis` with `panel_strike = strike` survived the
    entire 851-test suite, and no test anywhere called `.mark()` with a basis
    other than 1.0. That is the exact defect the module already fixed once and
    documented at length -- an NVDA-shaped 10x offset made every mark miss, so
    "carry the last real mark forward" degraded to "there was never a first
    mark", and the tape became a flat line at the entry ask.

    It matters more now than it did then: `_mark` is shared, so the untested
    line is the mark path for BOTH sources rather than one.
    """
    # Panel quoted 10x the caller's basis, the shape a pre-split panel has.
    source = _source(_row(spot=5000.0, strike=4500.0, bid=52.0, ask=55.0))

    # The caller holds a strike in ITS basis (450.0) and must still find the
    # 4500.0 contract, getting a bid back in its own basis.
    assert source.mark(entry_date=ENTRY, strike=450.0, expiry=EXPIRY, basis=10.0) == pytest.approx(
        5.2
    )

    # And the same call without the basis must NOT find it: a silent miss here
    # is what produced the flat tape.
    assert source.mark(entry_date=ENTRY, strike=450.0, expiry=EXPIRY, basis=1.0) is None
