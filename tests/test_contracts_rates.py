from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.rates import DATASET, DEFAULT_SERIES_IDS, RatesSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "series_id": ["DGS10"],
        "obs_date": pd.to_datetime(["2026-01-02"]),
        "value": [4.33],
        "vintage_date": pd.to_datetime(["2026-01-05"]),
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = RatesSchema.validate(_row())
    assert len(validated) == 1


def test_negative_rate_within_bounds_is_accepted() -> None:
    """Short-term repo/SOFR stress has produced genuine negative prints --
    the floor exists to catch unit errors, not to forbid a real negative
    rate."""
    validated = RatesSchema.validate(_row(value=[-0.05]))
    assert validated["value"].iloc[0] == pytest.approx(-0.05)


def test_absurdly_negative_rate_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        RatesSchema.validate(_row(value=[-50.0]), lazy=True)


def test_absurdly_large_rate_is_rejected() -> None:
    """The ceiling exists to catch unit errors (e.g. a rate fed in bps)
    rather than to bound history -- the early-1980s fed funds peak was
    ~20%."""
    with pytest.raises((SchemaError, SchemaErrors)):
        RatesSchema.validate(_row(value=[2000.0]), lazy=True)


def test_null_obs_date_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        RatesSchema.validate(_row(obs_date=[pd.NaT]), lazy=True)


def test_null_vintage_date_is_rejected() -> None:
    """vintage_date is the required point-in-time column -- a row without
    one cannot be resolved as-of a simulation date (docs/adr/0009)."""
    with pytest.raises((SchemaError, SchemaErrors)):
        RatesSchema.validate(_row(vintage_date=[pd.NaT]), lazy=True)


def test_duplicate_series_obs_vintage_triple_is_rejected() -> None:
    df = pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [4.33, 4.35],
            "vintage_date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
        }
    )
    with pytest.raises((SchemaError, SchemaErrors)):
        RatesSchema.validate(df, lazy=True)


def test_same_obs_date_two_vintages_is_not_a_duplicate() -> None:
    """A genuinely revised observation carries two rows -- one per vintage --
    and that is exactly what the schema must allow, not reject."""
    df = pd.DataFrame(
        {
            "series_id": ["DGS10", "DGS10"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [4.33, 4.35],
            "vintage_date": pd.to_datetime(["2026-01-02", "2026-01-06"]),
        }
    )
    validated = RatesSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_same_obs_date_across_two_series_is_not_a_duplicate() -> None:
    """The family shares one dataset, so uniqueness is on
    (series_id, obs_date, vintage_date). If it were on obs_date alone,
    ingesting DGS10 and SOFR together would quarantine one of them on
    every shared business day."""
    df = pd.DataFrame(
        {
            "series_id": ["DGS10", "SOFR"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [4.33, 4.31],
            "vintage_date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
        }
    )
    validated = RatesSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_unknown_column_is_rejected() -> None:
    """strict=True: an extra column means the source format moved under us,
    which should fail loudly rather than land unvalidated data in bronze."""
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        RatesSchema.validate(df, lazy=True)


def test_default_series_ids_cover_the_documented_set() -> None:
    """docs/DATA_CONTRACTS.md #3: Treasury curve, SOFR, fed funds."""
    assert {"DGS1MO", "DGS10", "DGS30", "SOFR", "DFF"} <= set(DEFAULT_SERIES_IDS)


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "rates"
