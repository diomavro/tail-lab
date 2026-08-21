from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tail_lab.transforms.options_expiry import classify_cadence


def test_weekly_spaced_dates_classify_weekly_with_exact_avg_gap() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-08", "2026-01-15", "2026-01-22"])

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of)

    assert cadence == "weekly"
    assert avg_gap_days == pytest.approx(7.0)


def test_monthly_spaced_dates_classify_monthly_with_exact_avg_gap() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-31", "2026-03-02", "2026-04-01"])

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of)

    assert cadence == "monthly"
    assert avg_gap_days == pytest.approx(30.0)


def test_far_dated_expirations_are_excluded_from_the_near_term_window() -> None:
    as_of = dt.date(2026, 1, 1)
    # Two weekly expirations plus one far LEAPS date well outside the window.
    dates = pd.to_datetime(["2026-01-08", "2026-01-15", "2026-06-01"])

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of, near_term_days=30)

    assert cadence == "weekly"
    assert avg_gap_days == pytest.approx(7.0)


def test_expiration_exactly_at_as_of_is_included() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-01", "2026-01-08"])

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of)

    assert cadence == "weekly"
    assert avg_gap_days == pytest.approx(7.0)


def test_lapsed_expiration_before_as_of_is_excluded() -> None:
    as_of = dt.date(2026, 1, 15)
    # One already-lapsed date (a stale snapshot) plus two genuinely upcoming ones.
    dates = pd.to_datetime(["2025-12-01", "2026-01-22", "2026-01-29"])

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of)

    assert cadence == "weekly"
    assert avg_gap_days == pytest.approx(7.0)


def test_duplicate_expirations_do_not_double_count() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-08", "2026-01-08", "2026-01-15"])

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of)

    assert cadence == "weekly"
    assert avg_gap_days == pytest.approx(7.0)


def test_fewer_than_two_near_term_expirations_raises() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-08"])

    with pytest.raises(ValueError, match="need at least 2"):
        classify_cadence(dates, as_of=as_of)


def test_no_near_term_expirations_raises() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2027-01-08", "2027-02-08"])

    with pytest.raises(ValueError, match="need at least 2"):
        classify_cadence(dates, as_of=as_of, near_term_days=90)


def test_avg_gap_exactly_at_weekly_threshold_classifies_weekly() -> None:
    """The boundary is inclusive: an avg gap exactly equal to
    ``weekly_threshold_days`` is "weekly", not "monthly"."""
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-11", "2026-01-21"])  # 10-day gap

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of, weekly_threshold_days=10.0)

    assert cadence == "weekly"
    assert avg_gap_days == pytest.approx(10.0)


def test_avg_gap_just_above_weekly_threshold_classifies_monthly() -> None:
    as_of = dt.date(2026, 1, 1)
    dates = pd.to_datetime(["2026-01-12", "2026-01-23"])  # 11-day gap

    cadence, avg_gap_days = classify_cadence(dates, as_of=as_of, weekly_threshold_days=10.0)

    assert cadence == "monthly"
    assert avg_gap_days == pytest.approx(11.0)
