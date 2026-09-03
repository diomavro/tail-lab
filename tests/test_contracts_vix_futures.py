from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.vix_futures import DATASET, VxFuturesSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "contract_expiry": pd.to_datetime(["2020-03-18"]),
        "trade_date": pd.to_datetime(["2020-03-17"]),
        "open": [72.5],
        "high": [79.05],
        "low": [64.9],
        "close": [70.55],
        "settle": [68.825],
        "volume": [72592],
        "open_interest": [65191],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = VxFuturesSchema.validate(_row())
    assert len(validated) == 1


def test_null_ohlc_is_allowed() -> None:
    """A no-trade day zero-fills OHLC upstream and the adapter maps that to
    null before validation -- null must pass, not quarantine, a real
    contract's quiet day."""
    validated = VxFuturesSchema.validate(_row(open=[None], high=[None], low=[None], close=[None]))
    assert validated["open"].isna().all()


def test_null_settle_is_rejected() -> None:
    """Unlike OHLC, settle is never zero-filled by the source -- a missing
    settle is a genuinely bad row, not a quiet day."""
    with pytest.raises((SchemaError, SchemaErrors)):
        VxFuturesSchema.validate(_row(settle=[None]), lazy=True)


def test_non_positive_settle_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        VxFuturesSchema.validate(_row(settle=[0.0]), lazy=True)


def test_absurdly_large_settle_is_rejected() -> None:
    """VIX itself has never printed above ~90 -- the ceiling exists to catch
    unit errors, not to bound how high it could someday spike."""
    with pytest.raises((SchemaError, SchemaErrors)):
        VxFuturesSchema.validate(_row(settle=[1e6]), lazy=True)


def test_negative_open_interest_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        VxFuturesSchema.validate(_row(open_interest=[-1]), lazy=True)


def test_duplicate_expiry_trade_date_pair_is_rejected() -> None:
    df = pd.DataFrame(
        {
            "contract_expiry": pd.to_datetime(["2020-03-18", "2020-03-18"]),
            "trade_date": pd.to_datetime(["2020-03-17", "2020-03-17"]),
            "open": [72.5, 72.5],
            "high": [79.05, 79.05],
            "low": [64.9, 64.9],
            "close": [70.55, 70.55],
            "settle": [68.825, 68.825],
            "volume": [72592, 72592],
            "open_interest": [65191, 65191],
        }
    )
    with pytest.raises((SchemaError, SchemaErrors)):
        VxFuturesSchema.validate(df, lazy=True)


def test_same_trade_date_across_two_contracts_is_not_a_duplicate() -> None:
    """Two contracts trade on the same day every day -- uniqueness must be on
    (contract_expiry, trade_date), not trade_date alone, or the term
    structure this dataset exists to build could never be written."""
    df = pd.DataFrame(
        {
            "contract_expiry": pd.to_datetime(["2020-03-18", "2020-04-15"]),
            "trade_date": pd.to_datetime(["2020-03-17", "2020-03-17"]),
            "open": [72.5, 55.0],
            "high": [79.05, 60.0],
            "low": [64.9, 50.0],
            "close": [70.55, 57.0],
            "settle": [68.825, 56.5],
            "volume": [72592, 41000],
            "open_interest": [65191, 30000],
        }
    )
    validated = VxFuturesSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_unknown_column_is_rejected() -> None:
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        VxFuturesSchema.validate(df, lazy=True)


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "vix_futures"
