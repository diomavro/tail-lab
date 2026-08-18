from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from tail_lab.contracts.ohlcv import OhlcvSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "symbol": ["AAPL"],
        "trade_date": pd.to_datetime(["2026-01-02"]),
        "open": [100.0],
        "high": [105.0],
        "low": [99.0],
        "close": [102.0],
        "volume": [1_000_000],
        "adj_close": [102.0],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_valid_frame_passes() -> None:
    validated = OhlcvSchema.validate(_row(), lazy=True)
    assert len(validated) == 1


def test_negative_open_fails() -> None:
    with pytest.raises(SchemaErrors):
        OhlcvSchema.validate(_row(open=[-1.0]), lazy=True)


def test_zero_volume_passes() -> None:
    # A legitimately quiet trading day (e.g. an illiquid name) has zero
    # volume, not negative — this should pass, unlike a negative price.
    validated = OhlcvSchema.validate(_row(volume=[0]), lazy=True)
    assert len(validated) == 1


def test_negative_volume_fails() -> None:
    with pytest.raises(SchemaErrors):
        OhlcvSchema.validate(_row(volume=[-5]), lazy=True)


def test_high_below_close_fails() -> None:
    # high must bracket the day's open/close — a garbled feed row.
    with pytest.raises(SchemaErrors):
        OhlcvSchema.validate(_row(high=[101.0], close=[102.0]), lazy=True)


def test_low_above_open_fails() -> None:
    with pytest.raises(SchemaErrors):
        OhlcvSchema.validate(_row(low=[100.5], open=[100.0]), lazy=True)


def test_duplicate_symbol_trade_date_fails() -> None:
    df = pd.concat([_row(), _row()], ignore_index=True)
    with pytest.raises(SchemaErrors):
        OhlcvSchema.validate(df, lazy=True)


def test_same_trade_date_different_symbol_passes() -> None:
    df = pd.concat([_row(), _row(symbol=["MSFT"])], ignore_index=True)
    validated = OhlcvSchema.validate(df, lazy=True)
    assert len(validated) == 2
