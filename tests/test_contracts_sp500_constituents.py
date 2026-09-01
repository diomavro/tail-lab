from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.sp500_constituents import DATASET, Sp500ConstituentsSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "obs_date": pd.to_datetime(["2026-01-02"]),
        "tickers": ["AAPL,MSFT,SPY"],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = Sp500ConstituentsSchema.validate(_row())
    assert len(validated) == 1


def test_empty_tickers_field_is_rejected() -> None:
    """A blank membership list is always a garbled row -- the index is never
    actually empty."""
    with pytest.raises((SchemaError, SchemaErrors)):
        Sp500ConstituentsSchema.validate(_row(tickers=[""]), lazy=True)


def test_null_date_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        Sp500ConstituentsSchema.validate(_row(obs_date=[pd.NaT]), lazy=True)


def test_null_tickers_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        Sp500ConstituentsSchema.validate(_row(tickers=[None]), lazy=True)


def test_duplicate_obs_date_is_rejected() -> None:
    df = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "tickers": ["AAPL,MSFT", "AAPL,MSFT,SPY"],
        }
    )
    with pytest.raises((SchemaError, SchemaErrors)):
        Sp500ConstituentsSchema.validate(df, lazy=True)


def test_unknown_column_is_rejected() -> None:
    """strict=True: an extra column means the source format moved under us,
    which should fail loudly rather than land unvalidated data in bronze."""
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        Sp500ConstituentsSchema.validate(df, lazy=True)


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "sp500_constituents"
