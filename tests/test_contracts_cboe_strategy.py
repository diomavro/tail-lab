from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.cboe_strategy import (
    DATASET,
    DEFAULT_TICKERS,
    STRATEGY_INDEX_CATALOGUE,
    CboeStrategySchema,
)


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "index_symbol": ["PPUT"],
        "trade_date": pd.to_datetime(["2026-01-02"]),
        "close": [101.5],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = CboeStrategySchema.validate(_row())
    assert len(validated) == 1


def test_non_positive_level_is_rejected() -> None:
    """An index level is a NAV rebased to 100 at inception -- zero or negative
    is always a garbled row, never a real observation."""
    with pytest.raises((SchemaError, SchemaErrors)):
        CboeStrategySchema.validate(_row(close=[0.0]), lazy=True)


def test_absurdly_large_level_is_rejected() -> None:
    """The ceiling exists to catch unit errors and feed garbage, not to bound
    compounding: PPUT reached only ~2,200 after 40 years."""
    with pytest.raises((SchemaError, SchemaErrors)):
        CboeStrategySchema.validate(_row(close=[1e9]), lazy=True)


def test_null_date_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        CboeStrategySchema.validate(_row(trade_date=[pd.NaT]), lazy=True)


def test_duplicate_symbol_date_pair_is_rejected() -> None:
    df = pd.DataFrame(
        {
            "index_symbol": ["PPUT", "PPUT"],
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-02"]),
            "close": [101.5, 102.0],
        }
    )
    with pytest.raises((SchemaError, SchemaErrors)):
        CboeStrategySchema.validate(df, lazy=True)


def test_unknown_column_is_rejected() -> None:
    """strict=True: an extra column means the source format moved under us,
    which should fail loudly rather than land unvalidated data in bronze."""
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        CboeStrategySchema.validate(df, lazy=True)


def test_every_default_ticker_is_in_the_catalogue() -> None:
    """The default ingest set must not drift out of the documented catalogue --
    a ticker with no label is one nobody can explain the provenance of."""
    assert set(DEFAULT_TICKERS) <= set(STRATEGY_INDEX_CATALOGUE)


def test_default_tickers_cover_the_tail_hedge_benchmarks() -> None:
    """These four are why the dataset exists (docs/DATA_SOURCING.md §9.1):
    the real-quote put-buying programs the model-priced backtester is scored
    against, plus SPX as the unhedged baseline."""
    assert {"PPUT", "PPUT3M", "VXTH", "SPX"} <= set(DEFAULT_TICKERS)


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "cboe_strategy"
