from __future__ import annotations

import datetime as dt

from tail_lab.contracts.calendar import third_friday


def test_matches_known_expirations() -> None:
    """Real, checkable dates rather than a recomputation of the same rule."""
    assert third_friday(2010, 5) == dt.date(2010, 5, 21)
    assert third_friday(2020, 3) == dt.date(2020, 3, 20)
    assert third_friday(2026, 8) == dt.date(2026, 8, 21)


def test_when_the_first_of_the_month_is_itself_a_friday() -> None:
    """The off-by-one trap: with a Friday on the 1st the third Friday is the
    15th, not the 22nd."""
    assert dt.date(2021, 1, 1).weekday() == 4
    assert third_friday(2021, 1) == dt.date(2021, 1, 15)


def test_february_is_not_a_special_case() -> None:
    """Scanning days 1-28 covers every month, since the third Friday can never
    fall later than the 21st — including a leap February."""
    assert third_friday(2024, 2) == dt.date(2024, 2, 16)
    assert third_friday(2023, 2) == dt.date(2023, 2, 17)


def test_it_is_always_a_friday_in_the_requested_month() -> None:
    for year in (2019, 2024, 2026):
        for month in range(1, 13):
            d = third_friday(year, month)
            assert d.weekday() == 4
            assert (d.year, d.month) == (year, month)
            assert 15 <= d.day <= 21


def test_both_layers_use_this_one_implementation() -> None:
    """The finding this module closes (design review, PR #73): the rule was
    spelled twice, once in research/ and once in ingestion/, because the layer
    contract forbids ingestion from importing research and duplication was the
    only thing the layering allowed. If either grows its own copy again, the
    identity check here fails."""
    from tail_lab.ingestion import vix_futures
    from tail_lab.research.backtest import index_replication

    assert index_replication.third_friday is third_friday
    assert vix_futures.third_friday is third_friday
