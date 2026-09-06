from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.event_calendar import DATASET, EVENT_TYPES, SOURCE_IDS, EventSchema


def _row(**overrides: object) -> pd.DataFrame:
    base = {
        "event_id": ["fomc_2026-01-28"],
        "event_type": ["FOMC"],
        "event_date": pd.to_datetime(["2026-01-28"]),
        "announced_at": pd.to_datetime(["2026-01-01"]),
        "symbol": [None],
        "description": ["FOMC meeting"],
        "source_id": ["fed_calendar"],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(base)


def test_valid_row_passes() -> None:
    validated = EventSchema.validate(_row())
    assert len(validated) == 1


def test_valid_earnings_row_with_symbol_passes() -> None:
    validated = EventSchema.validate(
        _row(
            event_id=["earnings_aapl_2026-01-28"],
            event_type=["EARNINGS"],
            symbol=["AAPL"],
            description=["AAPL Q1 earnings"],
            source_id=["manual"],
        )
    )
    assert validated["symbol"].iloc[0] == "AAPL"


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(_row(event_type=["SURPRISE_RATE_CUT"]), lazy=True)


def test_unknown_source_id_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(_row(source_id=["twitter_rumor"]), lazy=True)


def test_earnings_row_without_a_symbol_is_rejected() -> None:
    """The invariant `EVENT_TYPES` used to only document -- an EARNINGS row
    is not a legitimate event without the symbol it's about."""
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(
            _row(event_id=["earnings_x_2026-01-28"], event_type=["EARNINGS"], symbol=[None]),
            lazy=True,
        )


def test_null_event_date_is_rejected() -> None:
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(_row(event_date=[pd.NaT]), lazy=True)


def test_null_announced_at_is_rejected() -> None:
    """announced_at is the field lake/asof.py must filter on -- a null here
    means an event with no recoverable point-in-time boundary, which is not
    a valid row to write into a path a backtest reads (docs/adr/0009)."""
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(_row(announced_at=[pd.NaT]), lazy=True)


def test_duplicate_event_id_is_rejected() -> None:
    df = pd.concat([_row(), _row()], ignore_index=True)
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(df, lazy=True)


def test_empty_description_is_rejected() -> None:
    """A blank description is a garbled row, not a legitimate observation --
    every event on the cockpit's proximity flags must say what it is."""
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(_row(description=[""]), lazy=True)


def test_unknown_column_is_rejected() -> None:
    """strict=True: an extra column means the source format moved under us,
    which should fail loudly rather than land unvalidated data in bronze."""
    df = _row()
    df["surprise"] = [1.0]
    with pytest.raises((SchemaError, SchemaErrors)):
        EventSchema.validate(df, lazy=True)


def test_event_types_and_source_ids_match_the_data_contract() -> None:
    """docs/DATA_CONTRACTS.md #5 names these exact values -- drifting either
    list here silently changes what a consumer can rely on."""
    assert set(EVENT_TYPES) == {"FOMC", "CPI", "EARNINGS", "MANUAL"}
    assert set(SOURCE_IDS) == {"fed_calendar", "bls_calendar", "manual", "nasdaq_earnings"}


def test_dataset_id_is_stable() -> None:
    """Bronze is immutable and read back by name; renaming this silently
    orphans every existing partition."""
    assert DATASET == "event_calendar"
