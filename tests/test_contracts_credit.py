from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.credit import DATASET, DEFAULT_SERIES_IDS, CreditSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "series_id": ["BAMLH0A0HYM2"],
        "obs_date": pd.to_datetime(["2026-01-02"]),
        "value": [3.5],
        "vintage_date": pd.to_datetime(["2026-01-05"]),
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = CreditSchema.validate(_row())
    assert len(validated) == 1


def test_zero_spread_is_accepted() -> None:
    """The floor is 0.0, not exclusive of it -- a spread can print at zero."""
    validated = CreditSchema.validate(_row(value=[0.0]))
    assert validated["value"].iloc[0] == pytest.approx(0.0)


def test_negative_spread_is_rejected() -> None:
    """An OAS is non-negative by construction (`docs/DATA_CONTRACTS.md` #4) --
    unlike a Treasury yield, a negative print here is a unit/parse error."""
    with pytest.raises((SchemaError, SchemaErrors)):
        CreditSchema.validate(_row(value=[-0.5]), lazy=True)


def test_absurdly_large_spread_is_rejected() -> None:
    """The ceiling exists to catch unit errors (e.g. a spread fed in bps)
    rather than to bound history -- the 2008 HY OAS peak was ~19.9%."""
    with pytest.raises((SchemaError, SchemaErrors)):
        CreditSchema.validate(_row(value=[2000.0]), lazy=True)


def test_null_obs_date_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        CreditSchema.validate(_row(obs_date=[pd.NaT]), lazy=True)


def test_null_vintage_date_is_rejected() -> None:
    """vintage_date is the required point-in-time column -- a row without
    one cannot be resolved as-of a simulation date (docs/adr/0009)."""
    with pytest.raises((SchemaError, SchemaErrors)):
        CreditSchema.validate(_row(vintage_date=[pd.NaT]), lazy=True)


def test_duplicate_series_obs_vintage_triple_is_rejected() -> None:
    df = pd.DataFrame(
        {
            "series_id": ["BAMLH0A0HYM2", "BAMLH0A0HYM2"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [3.5, 3.6],
            "vintage_date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
        }
    )
    with pytest.raises((SchemaError, SchemaErrors)):
        CreditSchema.validate(df, lazy=True)


def test_same_obs_date_two_vintages_is_not_a_duplicate() -> None:
    """A genuinely revised observation carries two rows -- one per vintage --
    and that is exactly what the schema must allow, not reject."""
    df = pd.DataFrame(
        {
            "series_id": ["BAMLH0A0HYM2", "BAMLH0A0HYM2"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [3.5, 3.6],
            "vintage_date": pd.to_datetime(["2026-01-02", "2026-01-06"]),
        }
    )
    validated = CreditSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_same_obs_date_across_two_series_is_not_a_duplicate() -> None:
    """The family shares one dataset, so uniqueness is on
    (series_id, obs_date, vintage_date). If it were on obs_date alone,
    ingesting HY OAS and IG OAS together would quarantine one of them on
    every shared business day."""
    df = pd.DataFrame(
        {
            "series_id": ["BAMLH0A0HYM2", "BAMLC0A0CM"],
            "obs_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "value": [3.5, 1.2],
            "vintage_date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
        }
    )
    validated = CreditSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_unknown_column_is_rejected() -> None:
    """strict=True: an extra column means the source format moved under us,
    which should fail loudly rather than land unvalidated data in bronze."""
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        CreditSchema.validate(df, lazy=True)


def test_default_series_ids_cover_the_documented_set() -> None:
    """docs/DATA_CONTRACTS.md #4: HY OAS and IG OAS."""
    assert {"BAMLH0A0HYM2", "BAMLC0A0CM"} <= set(DEFAULT_SERIES_IDS)


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "credit"
