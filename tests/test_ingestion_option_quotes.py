"""Tests for the local-file put-smile adapter.

Hermetic by construction: the source is a file, so there is no network to
avoid — but the fixture is a **real** 77 KB slice of the 632 MB vendor
parquet, keeping the vendor's own columns and dtypes, so the extraction SQL is
pinned against the shape it will actually meet.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.option_quotes import DATASET
from tail_lab.ingestion.option_quotes import (
    QUARANTINE_DATASET,
    extract_roll_smile,
    ingest_option_quotes,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

INGEST = dt.date(2026, 8, 22)


def _extract(vendor: Path) -> pd.DataFrame:
    return extract_roll_smile(
        vendor / "SPY_options.parquet", vendor / "SPY_underlying.parquet", underlying="spy"
    )


def test_extraction_keeps_only_puts(lambdaclass_vendor_dir: Path) -> None:
    """The fixture is half calls. A call in a put-smile dataset would price
    every downstream hedge backwards."""
    smile = _extract(lambdaclass_vendor_dir)
    assert not smile.empty
    # Every kept row must be worth less than a same-strike call would be at
    # these deep-OTM strikes; the direct check is that the vendor's `type`
    # column never survives, so assert on the contract's shape instead.
    assert set(smile.columns) == {
        "underlying",
        "quote_date",
        "expiration",
        "strike",
        "bid",
        "ask",
        "volume",
        "open_interest",
        "spot",
    }
    # Puts struck below spot must be cheaper the further OTM they are.
    day = smile[smile["quote_date"] == pd.Timestamp("2020-03-20")].sort_values("strike")
    assert day["bid"].is_monotonic_increasing


def test_extraction_keeps_only_the_expiry_the_roll_buys(lambdaclass_vendor_dir: Path) -> None:
    """The fixture carries decoy expiries a week out and three months out. A
    roll buys the ~one-month contract; the others must not survive."""
    smile = _extract(lambdaclass_vendor_dir)
    kept = set(smile["expiration"].dt.date.unique())

    assert kept == {dt.date(2008, 11, 22), dt.date(2020, 4, 17)}
    tenors = (smile["expiration"] - smile["quote_date"]).dt.days
    assert tenors.between(25, 40).all()


def test_the_pre_2015_saturday_expiration_is_handled(lambdaclass_vendor_dir: Path) -> None:
    """Equity options expired on the Saturday after the third Friday until
    February 2015. A tenor filter tuned to Fridays alone would silently drop
    the entire 2008-2014 history — the half of the data that matters most."""
    smile = _extract(lambdaclass_vendor_dir)
    old = smile[smile["quote_date"] == pd.Timestamp("2008-10-17")]

    assert not old.empty
    assert old["expiration"].dt.dayofweek.unique().tolist() == [5]  # Saturday


def test_extraction_drops_the_vendors_unreliable_derived_columns(
    lambdaclass_vendor_dir: Path,
) -> None:
    """`mark` is $0.01 in 87-91% of 2008-2009 rows and IV sits on a sentinel
    floor (docs/DATA_VERDICTS.md). Dropping them at the schema is the cheapest
    way to make them unavailable rather than merely discouraged."""
    smile = _extract(lambdaclass_vendor_dir)
    for column in ("mark", "implied_volatility", "delta", "gamma", "last"):
        assert column not in smile.columns


def test_extraction_carries_spot_on_every_row(lambdaclass_vendor_dir: Path) -> None:
    """Moneyness is the only question anyone asks of a quote. Recovering it
    should not require a second dataset that may have been re-ingested from a
    different vendor since (docs/DISCOVERIES.md #2)."""
    smile = _extract(lambdaclass_vendor_dir)
    assert smile["spot"].gt(0).all()
    assert smile["spot"].nunique() == 2  # one per roll date
    moneyness = smile["strike"] / smile["spot"]
    assert moneyness.between(0.40, 1.05).all()


def test_extraction_rejects_one_sided_and_crossed_quotes(
    lambdaclass_vendor_dir: Path,
) -> None:
    smile = _extract(lambdaclass_vendor_dir)
    assert smile["bid"].gt(0).all()
    assert (smile["ask"] >= smile["bid"]).all()


def test_ingest_commits_a_bronze_snapshot(tmp_path: Path, lambdaclass_vendor_dir: Path) -> None:
    store = DeltaLakeStore(tmp_path)

    result = ingest_option_quotes(
        store, underlying="spy", vendor_dir=lambdaclass_vendor_dir, ingest_date=INGEST
    )

    assert result.valid_rows > 0
    assert result.quarantined_rows == 0
    assert result.underlying == "SPY"
    bronze = store.read_bronze_as_of(DATASET, INGEST)
    assert len(bronze) == result.valid_rows
    assert bronze["quote_date"].nunique() == 2


def test_bad_rows_are_quarantined_never_dropped(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    good = pd.DataFrame(
        {
            "underlying": ["SPY", "SPY"],
            "quote_date": pd.to_datetime(["2020-03-20", "2020-03-20"]),
            "expiration": pd.to_datetime(["2020-04-17", "2020-04-17"]),
            "strike": [200.0, 210.0],
            "bid": [1.0, 2.0],
            "ask": [1.1, 2.1],
            "volume": [10, 20],
            "open_interest": [100, 200],
            "spot": [229.0, 229.0],
        }
    )
    bad = good.iloc[[0]].copy()
    bad["strike"] = -5.0  # a negative strike is a garbled row, not a price

    result = ingest_option_quotes(
        store,
        vendor_dir=Path("/nonexistent"),
        ingest_date=INGEST,
        frame=pd.concat([good, bad], ignore_index=True),
    )

    assert result.valid_rows == 2
    assert result.quarantined_rows == 1
    assert result.quarantine_path is not None
    assert len(store.read_bronze_as_of(QUARANTINE_DATASET, INGEST)) == 1


def test_an_empty_extraction_refuses_to_commit(tmp_path: Path) -> None:
    """An empty partition would shadow a good one on the next as-of read
    (docs/DISCOVERIES.md #3). It has to fail loudly instead."""
    store = DeltaLakeStore(tmp_path)
    empty = pd.DataFrame(
        columns=[
            "underlying",
            "quote_date",
            "expiration",
            "strike",
            "bid",
            "ask",
            "volume",
            "open_interest",
            "spot",
        ]
    )
    with pytest.raises(ValueError, match="refusing to commit an empty"):
        ingest_option_quotes(store, vendor_dir=Path("/nonexistent"), frame=empty)


def test_a_missing_vendor_download_says_where_to_get_it(tmp_path: Path) -> None:
    """The file is licence-limited and can vanish. When it is absent the error
    has to point at the verification procedure, not just fail."""
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(FileNotFoundError, match="DATA_VERDICTS"):
        ingest_option_quotes(store, vendor_dir=tmp_path / "nope")


def test_validation_passes_a_clean_frame_through_untouched() -> None:
    frame = pd.DataFrame(
        {
            "underlying": ["SPY"],
            "quote_date": pd.to_datetime(["2020-03-20"]),
            "expiration": pd.to_datetime(["2020-04-17"]),
            "strike": [200.0],
            "bid": [1.0],
            "ask": [1.1],
            "volume": [10],
            "open_interest": [100],
            "spot": [229.0],
        }
    )
    valid, quarantined = validate_and_quarantine(frame)
    assert len(valid) == 1
    assert quarantined.empty
