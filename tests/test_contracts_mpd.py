from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.mpd import DATASET, MpdSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "market": ["sp12m"],
        "obs_date": pd.to_datetime(["2026-01-02"]),
        "maturity_months": [12.0],
        "mu": [0.03],
        "sd": [0.18],
        "skew": [-1.1],
        "kurt": [2.5],
        "p10": [-0.19],
        "p50": [0.05],
        "p90": [0.22],
        "prob_large_decline": [0.1],
        "prob_large_increase": [0.14],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = MpdSchema.validate(_row())
    assert len(validated) == 1


def test_null_maturity_months_is_accepted() -> None:
    """The source really does leave this blank for a subset of rows -- not a
    parse failure, so the schema must allow it."""
    validated = MpdSchema.validate(_row(maturity_months=[None]), lazy=True)
    assert pd.isna(validated["maturity_months"].iloc[0])


def test_negative_sd_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        MpdSchema.validate(_row(sd=[-0.1]), lazy=True)


def test_probability_above_one_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        MpdSchema.validate(_row(prob_large_decline=[1.5]), lazy=True)


def test_null_market_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        MpdSchema.validate(_row(market=[None]), lazy=True)


def test_null_obs_date_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        MpdSchema.validate(_row(obs_date=[pd.NaT]), lazy=True)


def test_duplicate_market_obs_date_pair_is_rejected() -> None:
    df = pd.concat([_row(), _row()], ignore_index=True)
    with pytest.raises((SchemaError, SchemaErrors)):
        MpdSchema.validate(df, lazy=True)


def test_same_obs_date_across_two_markets_is_not_a_duplicate() -> None:
    df = pd.concat([_row(), _row(market=["bac"], maturity_months=[3.0])], ignore_index=True)
    validated = MpdSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_unknown_column_is_rejected() -> None:
    """strict=True: an extra column means the source format moved under us,
    which should fail loudly rather than land unvalidated data in bronze."""
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        MpdSchema.validate(df, lazy=True)


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "mpd"
