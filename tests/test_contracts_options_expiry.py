from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from tail_lab.contracts.options_expiry import OptionsExpirySchema, dataset_id


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "symbol": ["SPY"],
        "expiration_date": pd.to_datetime(["2026-09-04"]),
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_valid_frame_passes() -> None:
    validated = OptionsExpirySchema.validate(_row(), lazy=True)
    assert len(validated) == 1


def test_null_symbol_fails() -> None:
    with pytest.raises(SchemaErrors):
        OptionsExpirySchema.validate(_row(symbol=[None]), lazy=True)


def test_duplicate_symbol_expiration_fails() -> None:
    df = pd.concat([_row(), _row()], ignore_index=True)
    with pytest.raises(SchemaErrors):
        OptionsExpirySchema.validate(df, lazy=True)


def test_same_expiration_different_symbol_passes() -> None:
    df = pd.concat([_row(), _row(symbol=["QQQ"])], ignore_index=True)
    validated = OptionsExpirySchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_dataset_id_is_per_symbol_and_lowercased() -> None:
    assert dataset_id("SPY") == "options_expiry_spy"
    assert dataset_id("spy") == "options_expiry_spy"
