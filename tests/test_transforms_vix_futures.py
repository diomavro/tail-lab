from __future__ import annotations

import pandas as pd
import pytest

from tail_lab.transforms.vix_futures import bronze_to_silver, silver_to_gold


def _bronze_row(trade_date: str, contract_expiry: str, settle: float) -> dict[str, object]:
    """A fully schema-shaped VX futures row -- ``bronze_to_silver`` validates
    against every column, unlike ``silver_to_gold`` which only reads three."""
    return {
        "contract_expiry": pd.Timestamp(contract_expiry),
        "trade_date": pd.Timestamp(trade_date),
        "open": settle,
        "high": settle,
        "low": settle,
        "close": settle,
        "settle": settle,
        "volume": 1000,
        "open_interest": 5000,
    }


def test_bronze_to_silver_sorts_and_dedupes() -> None:
    df = pd.DataFrame(
        [
            _bronze_row("2026-01-05", "2026-01-22", 18.0),
            _bronze_row("2026-01-02", "2026-01-22", 17.0),
            _bronze_row("2026-01-05", "2026-01-22", 99.0),  # duplicate key: last wins
        ]
    )
    silver = bronze_to_silver(df)
    assert silver["trade_date"].tolist() == list(pd.to_datetime(["2026-01-02", "2026-01-05"]))
    assert silver["settle"].tolist() == [17.0, 99.0]


def _curve(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Build a minimal silver-shaped frame from ``(trade_date, contract_expiry,
    settle)`` tuples -- ``silver_to_gold`` only reads these three columns."""
    trade_dates, expiries, settles = zip(*rows, strict=True)
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(trade_dates),
            "contract_expiry": pd.to_datetime(expiries),
            "settle": settles,
        }
    )


def test_silver_to_gold_hand_computed_interpolation() -> None:
    """One trade_date, two contracts bracketing the 30-day target: 20 days
    out at 18.0, 50 days out at 22.0. Weight on the far leg is
    (30-20)/(50-20) = 1/3, so cm_settle = 18.0 + 1/3 * (22.0-18.0) = 19.(3)."""
    df = _curve(
        [
            ("2026-01-02", "2026-01-22", 18.0),  # 20 days to expiry
            ("2026-01-02", "2026-02-21", 22.0),  # 50 days to expiry
        ]
    )
    gold = silver_to_gold(df, target_days=30)

    assert len(gold) == 1
    row = gold.iloc[0]
    assert row["cm_settle"] == pytest.approx(18.0 + (1 / 3) * 4.0)
    assert row["front_settle"] == pytest.approx(18.0)
    assert row["front_days_to_expiry"] == 20


def test_silver_to_gold_ignores_non_bracketing_contracts() -> None:
    """A third, nearer contract (10 days out) exists but does not bracket
    the target with anything closer than the 25/55-day pair, so it must not
    change the interpolation -- only front_settle/front_days_to_expiry read
    it."""
    df = _curve(
        [
            ("2026-01-02", "2026-01-12", 15.0),  # 10 days -- front, not used to interpolate
            ("2026-01-02", "2026-01-27", 19.0),  # 25 days
            ("2026-01-02", "2026-02-26", 25.0),  # 55 days
        ]
    )
    gold = silver_to_gold(df, target_days=30)

    row = gold.iloc[0]
    expected_weight_far = (30 - 25) / (55 - 25)
    assert row["cm_settle"] == pytest.approx(19.0 + expected_weight_far * (25.0 - 19.0))
    assert row["front_settle"] == pytest.approx(15.0)
    assert row["front_days_to_expiry"] == 10


def test_silver_to_gold_exact_match_needs_no_division() -> None:
    """target_days lands exactly on a listed contract's days-to-expiry --
    cm_settle is that contract's settle, not a 0/0 interpolation."""
    df = _curve(
        [
            ("2026-01-02", "2026-02-01", 20.0),  # exactly 30 days out
            ("2026-01-02", "2026-03-03", 24.0),  # 60 days out
        ]
    )
    gold = silver_to_gold(df, target_days=30)

    assert gold.iloc[0]["cm_settle"] == pytest.approx(20.0)


def test_silver_to_gold_drops_dates_that_cannot_be_bracketed() -> None:
    """Every listed contract on this trade_date is nearer than the 30-day
    target -- extrapolating past what's listed would be a guess, so the
    date is dropped rather than reported."""
    df = _curve(
        [
            ("2026-01-02", "2026-01-07", 14.0),  # 5 days
            ("2026-01-02", "2026-01-17", 16.0),  # 15 days
        ]
    )
    gold = silver_to_gold(df, target_days=30)

    assert gold.empty
    assert list(gold.columns) == [
        "trade_date",
        "cm_settle",
        "front_settle",
        "front_days_to_expiry",
    ]


def test_silver_to_gold_handles_multiple_trade_dates_independently() -> None:
    df = _curve(
        [
            ("2026-01-02", "2026-01-22", 18.0),
            ("2026-01-02", "2026-02-21", 22.0),
            ("2026-01-05", "2026-01-19", 17.0),  # 14 days -- unbracketable
        ]
    )
    gold = silver_to_gold(df, target_days=30)

    assert len(gold) == 1
    assert gold.iloc[0]["trade_date"] == pd.Timestamp("2026-01-02")
