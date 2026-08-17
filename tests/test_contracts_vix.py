from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from tail_lab.contracts.vix import VixSchema


def test_valid_frame_passes() -> None:
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-05"]), "close": [15.0, 16.0]})
    validated = VixSchema.validate(df, lazy=True)
    assert len(validated) == 2


def test_negative_close_fails() -> None:
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "close": [-5.0]})
    with pytest.raises(SchemaErrors):
        VixSchema.validate(df, lazy=True)


def test_out_of_range_close_fails() -> None:
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "close": [500.0]})
    with pytest.raises(SchemaErrors):
        VixSchema.validate(df, lazy=True)


def test_null_close_fails() -> None:
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "close": [None]})
    with pytest.raises(SchemaErrors):
        VixSchema.validate(df, lazy=True)


def test_duplicate_date_fails() -> None:
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-02"]), "close": [15.0, 16.0]})
    with pytest.raises(SchemaErrors):
        VixSchema.validate(df, lazy=True)
