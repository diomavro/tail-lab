"""Hypothesis property tests for the options-expiry cadence classification
(STANDARDS.md "Property tests for numeric code"). Complements the pinned
known-answer cases in ``test_transforms_options_expiry.py`` with invariants
that hold for *any* valid near-term expiration list, not just the hand-picked
ones.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from tail_lab.transforms.options_expiry import NEAR_TERM_DAYS, classify_cadence

_AS_OF = dt.date(2026, 1, 1)

_offsets = st.lists(
    st.integers(min_value=0, max_value=NEAR_TERM_DAYS),
    min_size=2,
    max_size=15,
    unique=True,
)


def _dates_from_offsets(offsets: list[int]) -> pd.Series:
    return pd.Series(pd.to_datetime([_AS_OF + dt.timedelta(days=o) for o in offsets]))


@given(offsets=_offsets)
def test_avg_gap_days_always_positive(offsets: list[int]) -> None:
    """At least two *distinct* near-term dates always yield a strictly
    positive average gap -- a gap between two different calendar dates can
    never be zero or negative."""
    dates = _dates_from_offsets(offsets)
    _, avg_gap_days = classify_cadence(dates, as_of=_AS_OF)
    assert avg_gap_days > 0


@given(offsets=_offsets)
def test_cadence_is_always_weekly_or_monthly(offsets: list[int]) -> None:
    dates = _dates_from_offsets(offsets)
    cadence, _ = classify_cadence(dates, as_of=_AS_OF)
    assert cadence in ("weekly", "monthly")


@given(offsets=_offsets)
def test_result_is_order_independent(offsets: list[int]) -> None:
    """The same set of dates, presented in reverse order, must classify
    identically -- the function sorts internally, so input order carries no
    information a real bronze read (arbitrary row order) could smuggle in."""
    dates = _dates_from_offsets(offsets)
    reversed_dates = dates.iloc[::-1].reset_index(drop=True)

    assert classify_cadence(dates, as_of=_AS_OF) == classify_cadence(reversed_dates, as_of=_AS_OF)


@given(offsets=_offsets)
def test_duplicating_an_existing_date_does_not_change_the_result(offsets: list[int]) -> None:
    """A duplicated listing (e.g. a chain snapshot re-listing the same
    expiration twice) must not shift the average gap -- duplicates are
    dropped before the gap is computed."""
    dates = _dates_from_offsets(offsets)
    with_duplicate = pd.concat([dates, dates.iloc[[0]]], ignore_index=True)

    assert classify_cadence(dates, as_of=_AS_OF) == classify_cadence(with_duplicate, as_of=_AS_OF)
