from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from tail_lab.contracts.option_chain import (
    MAX_TENOR_DAYS,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    OptionChainSnapshotSchema,
)
from tail_lab.contracts.option_quotes import OptionQuoteSchema


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "underlying": "SPY",
        "quote_date": pd.Timestamp("2026-08-26"),
        "expiration": pd.Timestamp("2026-09-25"),
        "strike": 700.0,
        "bid": 1.20,
        "ask": 1.30,
        "volume": 42,
        "open_interest": 811,
        "spot": 767.12,
        "iv": 0.2415,
        "delta": -0.19,
        "theo": 1.25,
    }
    row.update(overrides)
    return row


def _frame(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(list(rows) or [_row()])


def test_a_well_formed_snapshot_row_validates() -> None:
    assert len(OptionChainSnapshotSchema.validate(_frame(), lazy=True)) == 1


def test_the_greek_columns_are_nullable() -> None:
    """Cboe publishes no IV for a contract it cannot mark. That absence must
    be storable as absence — the alternative is a 0.0 that reads as a
    measurement (see the module docstring, and docs/DATA_VERDICTS.md)."""
    frame = _frame(_row(iv=None, delta=None, theo=None))
    validated = OptionChainSnapshotSchema.validate(frame, lazy=True)
    assert validated["iv"].isna().all()


def test_a_zero_bid_is_accepted_but_a_zero_ask_is_not() -> None:
    """Nobody bidding for a far-OTM put is a real market state worth
    recording; no offer at all means there was no quote to record."""
    OptionChainSnapshotSchema.validate(_frame(_row(bid=0.0)), lazy=True)
    with pytest.raises(SchemaErrors):
        OptionChainSnapshotSchema.validate(_frame(_row(ask=0.0)), lazy=True)


def test_a_positive_delta_is_rejected() -> None:
    """This dataset is the put wing. A positive delta means a call leaked
    through the slice, which is a parser bug, not a strange quote."""
    with pytest.raises(SchemaErrors):
        OptionChainSnapshotSchema.validate(_frame(_row(delta=0.3)), lazy=True)


def test_duplicate_contracts_are_rejected() -> None:
    """(underlying, quote_date, expiration, strike) is the key. A duplicate
    means the same contract was swept twice — bronze is immutable, so that
    has to fail on the way in rather than be deduplicated later."""
    with pytest.raises(SchemaErrors):
        OptionChainSnapshotSchema.validate(_frame(_row(), _row()), lazy=True)


def test_the_shared_columns_match_the_historical_vendor_contract() -> None:
    """The whole point of the schema choice (module docstring): the vendor
    back-history and this forward collection must union on their shared
    columns without a translation layer. If someone adds a column to one and
    not the other, or renames one, this is where it should hurt."""
    historical = set(OptionQuoteSchema.to_schema().columns)
    forward = set(OptionChainSnapshotSchema.to_schema().columns)
    assert historical <= forward
    assert forward - historical == {"iv", "delta", "theo"}


def test_the_slice_bands_bracket_spot() -> None:
    """A sanity rail on the constants themselves: the band has to contain
    at-the-money or the dataset would hold no reference point for skew."""
    assert MONEYNESS_MIN < 1.0 < MONEYNESS_MAX
    assert 0 < MAX_TENOR_DAYS <= 365
