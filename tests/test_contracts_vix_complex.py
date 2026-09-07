from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.vix_complex import (
    DATASET,
    SERIES_BOUNDS,
    SERIES_NAMES,
    VixComplexSchema,
    empty_vix_complex_frame,
)


def _row(**overrides: object) -> pd.DataFrame:
    base: dict[str, object] = {
        "series": ["VIX3M"],
        "trade_date": pd.to_datetime(["2026-01-02"]),
        "open": [18.0],
        "high": [18.5],
        "low": [17.8],
        "close": [18.2],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = VixComplexSchema.validate(_row(), lazy=True)
    assert len(validated) == 1


def test_null_open_high_low_is_allowed() -> None:
    """VVIX/SKEW never carry OHLC on the wire -- only close is mandatory."""
    df = _row(open=[float("nan")], high=[float("nan")], low=[float("nan")])
    validated = VixComplexSchema.validate(df, lazy=True)
    assert validated["open"].isna().all()


def test_null_close_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        VixComplexSchema.validate(_row(close=[float("nan")]), lazy=True)


def test_unknown_series_is_rejected() -> None:
    """Spot VIX itself deliberately stays out of this dataset -- see the
    module docstring. A row claiming to be "VIX" here is a bug, not data."""
    with pytest.raises((SchemaError, SchemaErrors)):
        VixComplexSchema.validate(_row(series=["VIX"]), lazy=True)


def test_close_bound_is_per_series_not_shared() -> None:
    """A SKEW print of 220 is unremarkable (its band runs to 250); the same
    number as a VIX3M print (band ends at 200) is an obvious unit error.
    One shared range could not honour both."""
    skew_row = _row(series=["SKEW"], close=[220.0])
    validated = VixComplexSchema.validate(skew_row, lazy=True)
    assert validated["close"].iloc[0] == pytest.approx(220.0)

    vix3m_row = _row(series=["VIX3M"], close=[220.0])
    with pytest.raises((SchemaError, SchemaErrors)):
        VixComplexSchema.validate(vix3m_row, lazy=True)


def test_duplicate_series_date_pair_is_rejected() -> None:
    df = pd.DataFrame(
        {
            "series": ["VIX3M", "VIX3M"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "open": [18.0, 18.1],
            "high": [18.5, 18.6],
            "low": [17.8, 17.9],
            "close": [18.2, 18.3],
        }
    )
    with pytest.raises((SchemaError, SchemaErrors)):
        VixComplexSchema.validate(df, lazy=True)


def test_unknown_column_is_rejected() -> None:
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        VixComplexSchema.validate(df, lazy=True)


def test_empty_frame_matches_schema_columns() -> None:
    empty = empty_vix_complex_frame()
    validated = VixComplexSchema.validate(empty, lazy=True)
    assert validated.empty
    assert list(validated.columns) == ["series", "trade_date", "open", "high", "low", "close"]


def test_series_names_match_bounds_keys() -> None:
    assert set(SERIES_NAMES) == set(SERIES_BOUNDS)


def test_spot_vix_is_excluded_from_this_family() -> None:
    """`contracts/vix.py` owns spot VIX; this dataset covers the rest of the
    complex only (`docs/DATA_CONTRACTS.md` #2)."""
    assert "VIX" not in SERIES_NAMES


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "vix_complex"
