"""The shape checks every ingestion parser now leans on. Their contract is ONE
named failure, ``ValueError``: ``sources.first_available`` catches exactly that
(plus ``requests`` faults) to treat a junk payload as a failed source, so any
other exception escaping here would crash an ingest instead of falling over."""

from __future__ import annotations

import datetime as dt

import pytest

from tail_lab.ingestion.json_payload import (
    first_object,
    json_list,
    json_object,
    to_float,
    to_int,
    utc_from_epoch,
)


def test_well_formed_values_pass_through_unchanged() -> None:
    obj = {"a": 1}
    items = [obj]
    assert json_object(obj, "x") is obj
    assert json_list(items, "x") is items
    assert first_object(items, "x") is obj
    assert to_float("1,5".replace(",", "."), "x") == 1.5
    assert to_float(3, "x") == 3.0
    assert to_int(7.9, "x") == 7  # int() truncates, exactly as the untyped parsers did
    assert to_int("12", "x") == 12
    assert utc_from_epoch(0, "x") == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


@pytest.mark.parametrize("value", [None, [], "s", 1])
def test_a_non_object_is_a_named_value_error(value: object) -> None:
    with pytest.raises(ValueError, match="chart is not a JSON object"):
        json_object(value, "chart")


@pytest.mark.parametrize("value", [None, {}, "s", 1])
def test_a_non_array_is_a_named_value_error(value: object) -> None:
    with pytest.raises(ValueError, match="timestamp is not a JSON array"):
        json_list(value, "timestamp")


def test_first_object_refuses_an_empty_array_and_a_non_object_head() -> None:
    """``result[0]`` on ``[]`` was an IndexError and on ``[None]`` a TypeError --
    neither of which the narrowed source chain catches."""
    with pytest.raises(ValueError, match="empty array"):
        first_object([], "chart.result")
    with pytest.raises(ValueError, match=r"chart.result\[0\] is not a JSON object"):
        first_object([None], "chart.result")


@pytest.mark.parametrize("value", [None, [], {}])
def test_a_structured_or_null_cell_is_not_a_number(value: object) -> None:
    """``float(None)`` / ``int([])`` raise TypeError; here they are ValueError."""
    with pytest.raises(ValueError, match="is not a number"):
        to_float(value, "close")
    with pytest.raises(ValueError, match="is not a number"):
        to_int(value, "volume")


def test_an_unparseable_string_is_still_float_s_own_value_error() -> None:
    with pytest.raises(ValueError):
        to_float("abc", "close")


@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_an_infinite_count_is_a_value_error_not_an_overflow(value: float) -> None:
    """``json.loads`` accepts ``Infinity`` and ``int(inf)`` raises
    OverflowError, which would escape every narrowed caller."""
    with pytest.raises(ValueError, match="not a finite number"):
        to_int(value, "volume")


@pytest.mark.parametrize(
    "seconds",
    [
        pytest.param(10**20, id="OverflowError"),
        pytest.param(10**17, id="OSError"),
        pytest.param(10**12 * 400, id="ValueError"),
    ],
)
def test_an_absurd_epoch_is_a_value_error(seconds: int) -> None:
    """``fromtimestamp`` raises OverflowError, OSError or ValueError depending
    on how absurd the value is (each id is the class it raised when measured
    on Linux/CPython 3.12, 2026-09-26) -- all three must come out as one."""
    with pytest.raises(ValueError, match="not a usable epoch timestamp"):
        utc_from_epoch(seconds, "timestamp")


def test_an_integer_too_large_for_a_float_is_the_named_failure() -> None:
    """``float(10**400)`` raises OverflowError, which escaped
    ``first_available``'s (RequestException, ValueError) tuple; a bad cell must
    fail the same way whatever makes it bad."""
    with pytest.raises(ValueError, match="too large"):
        to_float(10**400, "close")


@pytest.mark.parametrize("value", [2**63 - 1, -(2**63)])
def test_the_int64_extremes_are_still_numbers(value: int) -> None:
    assert to_int(value, "volume") == value


@pytest.mark.parametrize("value", [2**63, -(2**63) - 1])
def test_one_past_int64_is_the_named_failure(value: int) -> None:
    """pandas and the lake hold int64; one past it raised OverflowError there,
    outside every caller's ValueError catch."""
    with pytest.raises(ValueError, match="int64"):
        to_int(value, "volume")
