from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from pandera.errors import SchemaError, SchemaErrors

from tail_lab.contracts.credit import (
    DATASET,
    DEFAULT_SERIES_IDS,
    CreditSchema,
    resolve_credit_history,
)


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
    rather than to bound history -- the 2008 HY OAS peak was above 20%."""
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


# ---- resolve_credit_history -------------------------------------------------


def _hist(*rows: tuple[str, float, str, str]) -> pd.DataFrame:
    """``(obs_date, value, vintage_date, ingest_date)`` rows as the store's
    history read returns them (``ingest_date`` already a ``date``)."""
    return pd.DataFrame(
        {
            "obs_date": pd.to_datetime([r[0] for r in rows]),
            "value": [r[1] for r in rows],
            "vintage_date": pd.to_datetime([r[2] for r in rows]),
            "ingest_date": [dt.date.fromisoformat(r[3]) for r in rows],
        }
    )


def test_newest_live_partition_wins_and_vintage_only_breaks_ties_within_one() -> None:
    """WHY: a later partition is a later look at the world. An EARLIER
    partition can carry a LATER vintage, and sorting on vintage first would
    let it beat the newer run."""
    live = _hist(
        ("2026-03-02", 9.0, "2026-06-01", "2026-03-05"),  # old partition, inflated vintage
        ("2026-03-02", 4.0, "2026-03-02", "2026-03-06"),  # newest partition: must win
        ("2026-03-03", 5.0, "2026-03-03", "2026-03-06"),
        ("2026-03-03", 5.5, "2026-03-04", "2026-03-06"),  # same partition, later vintage
    )
    out = resolve_credit_history(live, _hist())
    assert out.loc["2026-03-02"] == 4.0
    assert out.loc["2026-03-03"] == 5.5


def test_resolution_does_not_depend_on_input_row_order() -> None:
    """WHY: a Delta scan returns files in no promised order; newest-wins must
    come from an explicit sort, not from whichever row happens to be last."""
    live = _hist(
        ("2026-03-02", 9.0, "2026-06-01", "2026-03-05"),
        ("2026-03-02", 4.0, "2026-03-02", "2026-03-06"),
        ("2026-03-03", 5.0, "2026-03-03", "2026-03-06"),
        ("2026-03-03", 5.5, "2026-03-04", "2026-03-06"),
    )
    newest_first = resolve_credit_history(live.iloc[::-1].reset_index(drop=True), _hist())
    oldest_first = resolve_credit_history(live, _hist())
    shuffled = resolve_credit_history(live.sample(frac=1.0, random_state=3), _hist())
    pd.testing.assert_series_equal(newest_first, oldest_first)
    pd.testing.assert_series_equal(shuffled, oldest_first)
    assert oldest_first.loc["2026-03-02"] == 4.0


def test_archive_is_authoritative_up_to_its_own_end_and_live_only_after() -> None:
    """WHY: a wide or wrong live window must not rewrite 1996 -> archive_end,
    and archive_end is read from the data (here 2024-06-03, not 2025-11-03)."""
    archive = _hist(
        ("1997-01-02", 3.0, "2025-11-04", "2026-10-31"),
        ("2024-06-03", 4.0, "2025-11-04", "2026-10-31"),  # archive_end itself
    )
    live = _hist(
        ("1997-01-02", 7.0, "1997-01-02", "2026-10-30"),
        ("2024-06-03", 8.0, "2024-06-03", "2026-10-30"),
        ("2024-06-04", 4.5, "2024-06-04", "2026-10-30"),
        ("2024-06-05", 4.6, "2024-06-05", "2026-10-30"),
    )
    out = resolve_credit_history(live, archive)
    expected = pd.to_datetime(["1997-01-02", "2024-06-03", "2024-06-04", "2024-06-05"])
    assert list(out.index) == list(expected)
    assert list(out) == [3.0, 4.0, 4.5, 4.6]
    assert out.name == "hy_oas"


def test_without_archive_rows_live_serves_everything() -> None:
    live = _hist(("1997-01-02", 7.0, "1997-01-02", "2026-10-30"))
    assert list(resolve_credit_history(live, _hist())) == [7.0]


def test_archive_newest_partition_wins_among_archive_partitions() -> None:
    """WHY: the archive is reduced with the same rule as live, so a corrected
    re-ingest (after the first is ignored) replaces, not mixes."""
    archive = _hist(
        ("2020-01-02", 3.0, "2025-11-04", "2026-10-31"),
        ("2020-01-02", 3.2, "2025-11-04", "2026-11-05"),
    )
    assert list(resolve_credit_history(_hist(), archive)) == [3.2]
