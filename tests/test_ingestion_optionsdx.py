from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.optionsdx import (
    DATASET,
    MAX_DTE_DAYS,
    MONEYNESS_MIN,
    month_coverage,
)
from tail_lab.ingestion.optionsdx import (
    ingest_optionsdx,
    parse_optionsdx_month,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

FIXTURE = Path(__file__).parent / "fixtures" / "optionsdx_spy_sample.txt"


@pytest.fixture
def sample() -> str:
    """Ten real rows lifted from spy_eod_201401.txt, chosen so each filter has
    something to reject: in-band, far-OTM, long-dated, no-ask, blank greeks."""
    return FIXTURE.read_text()


def test_parses_the_vendors_real_header(sample: str) -> None:
    """The header ships as `[QUOTE_DATE], [UNDERLYING_LAST], ...` — brackets,
    spaces and all. Parsing it is the one thing that breaks silently if the
    vendor reformats, so it is pinned against the real file rather than a
    hand-typed approximation."""
    frame, unparsable = parse_optionsdx_month("spy", sample)
    assert unparsable == 0
    assert list(frame.columns) == [
        "underlying",
        "quote_date",
        "expiration",
        "strike",
        "bid",
        "ask",
        "volume",
        "spot",
        "iv",
        "delta",
        "vega",
        "theta",
    ]
    assert (frame["underlying"] == "SPY").all()


def test_it_keeps_only_the_put_wing_it_says_it_keeps(sample: str) -> None:
    frame, _ = parse_optionsdx_month("spy", sample)
    assert not frame.empty
    moneyness = frame["strike"] / frame["spot"]
    assert (moneyness >= MONEYNESS_MIN).all()
    dte = (frame["expiration"] - frame["quote_date"]).dt.days
    assert (dte <= MAX_DTE_DAYS).all()
    assert (dte >= 0).all()


def test_a_row_with_no_ask_is_dropped_but_a_zero_bid_is_kept(sample: str) -> None:
    """A zero bid is a real market state for a far-OTM put — nobody is bidding.
    No ask means there was no quote to record at all."""
    frame, _ = parse_optionsdx_month("spy", sample)
    assert (frame["ask"] > 0).all()
    assert (frame["bid"] >= 0).all()


def test_a_blank_greek_is_absence_not_zero(sample: str) -> None:
    """optionsDX leaves greeks blank on illiquid rows. Coercing those to 0.0
    would put a real-looking number where there is no observation — the error
    docs/DATA_VERDICTS.md caught in the lambdaclass file."""
    frame, _ = parse_optionsdx_month("spy", sample)
    blanks = frame["iv"].isna()
    assert blanks.any(), "fixture should carry at least one blank-greek row"
    assert not (frame.loc[blanks, "iv"] == 0.0).any()


def test_a_changed_vendor_header_raises_rather_than_guessing() -> None:
    """The format is the one thing that can change under us. Failing loudly
    beats parsing some other column as a strike."""
    with pytest.raises(ValueError, match="missing column"):
        parse_optionsdx_month("spy", "FOO,BAR\n1,2\n")


def test_short_or_empty_input_is_an_empty_frame_not_a_crash() -> None:
    frame, unparsable = parse_optionsdx_month("spy", "")
    assert frame.empty and unparsable == 0


def test_truncated_rows_are_counted_not_silently_dropped(sample: str) -> None:
    """A vendor format change should show up as a NUMBER, not as quietly
    missing data."""
    lines = sample.splitlines()
    damaged = "\n".join([lines[0], "1,2,3", *lines[1:]]) + "\n"
    _frame, unparsable = parse_optionsdx_month("spy", damaged)
    assert unparsable == 1


# ---- coverage, which is the point of this dataset's contract ---------------


def test_month_coverage_finds_the_holes() -> None:
    """SPY in this download has 63 of 168 months. A backtest handed only the
    months that exist would roll straight across the holes and draw an equity
    curve for a period it has no quotes for."""
    dates = [dt.date(2020, 1, 5), dt.date(2020, 1, 20), dt.date(2020, 4, 3)]
    present, missing = month_coverage(dates)
    assert present == ["202001", "202004"]
    assert missing == ["202002", "202003"]


def test_month_coverage_of_a_continuous_run_reports_no_holes() -> None:
    dates = [dt.date(2021, m, 15) for m in range(1, 13)]
    present, missing = month_coverage(dates)
    assert len(present) == 12 and missing == []


def test_month_coverage_of_nothing_is_empty_not_an_error() -> None:
    assert month_coverage([]) == ([], [])


# ---- orchestration ---------------------------------------------------------


def test_ingest_commits_a_per_symbol_partition_and_reports_coverage(
    tmp_path: Path, sample: str
) -> None:
    """One partition PER SYMBOL, not one for the download: the symbols have
    wildly different coverage (VIX 168/168, SPY 63/168) and a shared partition
    would make a complete panel and a 40%-complete one indistinguishable at
    read time."""
    store = DeltaLakeStore(tmp_path)
    result = ingest_optionsdx(
        store, "spy", ingest_date=dt.date(2026, 9, 3), months=[("spy_eod_201401.txt", sample)]
    )

    assert result.symbol == "SPY"
    assert result.valid_rows > 0
    assert result.months_present == 1
    assert f"{DATASET}_spy" in result.bronze_path
    stored = store.read_bronze_as_of(f"{DATASET}_spy", dt.date(2026, 9, 3))
    assert len(stored) == result.valid_rows


def test_ingest_of_an_absent_symbol_says_so_rather_than_writing_nothing(
    tmp_path: Path,
) -> None:
    """The corpus is a hand-download that is absent on CI and in production. A
    missing symbol is a setup problem and must not look like an empty market."""
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(FileNotFoundError, match="no optionsDX archives"):
        ingest_optionsdx(store, "aapl", vendor_dir=tmp_path / "nope")


def test_validate_quarantines_a_bad_row_without_losing_the_good_ones() -> None:
    good = {
        "underlying": "SPY",
        "quote_date": pd.Timestamp("2014-01-03"),
        "expiration": pd.Timestamp("2014-02-21"),
        "strike": 170.0,
        "bid": 1.0,
        "ask": 1.1,
        "volume": 10,
        "spot": 182.9,
        "iv": 0.2,
        "delta": -0.2,
        "vega": 0.1,
        "theta": -5.0,
    }
    bad = {**good, "strike": 175.0, "ask": 0.0}  # no ask is not a quote
    valid, quarantined = validate_and_quarantine(pd.DataFrame([good, bad]))
    assert valid["strike"].tolist() == [170.0]
    assert quarantined["strike"].tolist() == [175.0]


def test_a_blank_iv_voids_the_whole_greek_block() -> None:
    """The vendor does not blank the rest of the block when its solver fails --
    it fills it with garbage. Real row, VIX 2010-01-22: spot 27.70, strike 18,
    IV blank, and yet delta pinned to exactly -1.0 (true value is near zero for
    a put that far out), gamma and theta 0.0, vega -41.4.

    Taking those fields at face value would have put a -1.0 delta on a
    near-worthless put into the lake, and it would have PASSED the schema,
    because -1.0 is a legal delta. Same rule as Cboe's zero-fill in
    ingestion/option_chain.py, reached independently from a second vendor."""
    header = ",".join(
        f"[{c}]"
        for c in (
            "QUOTE_DATE",
            "UNDERLYING_LAST",
            "EXPIRE_DATE",
            "DTE",
            "STRIKE",
            "P_BID",
            "P_ASK",
            "P_VOLUME",
            "P_IV",
            "P_DELTA",
            "P_VEGA",
            "P_THETA",
        )
    )
    unsolved = "2010-01-22,27.70,2010-02-16,25.0,18.0,0.15,0.19,3,,-1.000000,-41.400050,0.0"
    solved = "2010-01-22,27.70,2010-02-16,25.0,26.0,1.10,1.20,9,0.85,-0.42,0.031,-4.2"
    frame, _ = parse_optionsdx_month("vix", f"{header}\n{unsolved}\n{solved}\n")

    assert len(frame) == 2
    bad = frame[frame["strike"] == 18.0].iloc[0]
    assert pd.isna(bad["iv"]) and pd.isna(bad["delta"])
    assert pd.isna(bad["vega"]) and pd.isna(bad["theta"])
    # ...and the QUOTE on that row is still trusted and kept: the solver failed,
    # the market did not.
    assert bad["ask"] == 0.19

    good = frame[frame["strike"] == 26.0].iloc[0]
    assert good["delta"] == pytest.approx(-0.42)
    assert good["vega"] == pytest.approx(0.031)


def test_coverage_counts_a_month_that_was_parsed_even_if_nothing_survived(
    tmp_path: Path, sample: str
) -> None:
    """A month rejected wholesale must read as present-and-empty, never as
    absent: "downloaded and unusable" and "never downloaded" are opposite
    problems and the second one is invisible."""
    store = DeltaLakeStore(tmp_path)
    result = ingest_optionsdx(
        store, "spy", ingest_date=dt.date(2026, 9, 3), months=[("m.txt", sample)]
    )
    assert result.months_present == 1


def test_a_redownloaded_archive_does_not_delete_a_year(tmp_path: Path) -> None:
    """The failure this guards, observed on the first real VIX run: a browser
    re-download had landed as `vix_eod_2010-0pjoap (2).7z` beside the original.
    The glob read both, every row collided on the schema's unique key, pandera
    quarantined BOTH copies — and the ingest reported "168 months present, 0
    missing" while the stored data silently began in 2011.

    A duplicate FILE must never cost a year of DATA."""
    from tail_lab.ingestion.optionsdx import _dedupe_archives

    paths = [
        tmp_path / "vix_eod_2010-0pjoap.7z",
        tmp_path / "vix_eod_2010-0pjoap (2).7z",
        tmp_path / "vix_eod_2011-q0gy68 (1).7z",
        tmp_path / "vix_eod_2012-04ebjs.7z",
    ]
    kept = [p.name for p in _dedupe_archives(paths)]
    assert kept == [
        "vix_eod_2010-0pjoap.7z",  # the unsuffixed original wins the collision
        # ...but a LONE re-download is the only copy of that year, so it is kept
        # as it is on disk. Canonicalising the NAME must not drop the FILE.
        "vix_eod_2011-q0gy68 (1).7z",
        "vix_eod_2012-04ebjs.7z",
    ]


def test_overlapping_archives_are_deduped_not_quarantined(tmp_path: Path, sample: str) -> None:
    """Different problem, same symptom. The corpus mixes year files with
    quarter files (`tsla_eod_2022q2_3`), so two archives can legitimately cover
    one month. A repeated row is repeated data, not bad data, and must not land
    in quarantine where it would read as a source defect."""
    store = DeltaLakeStore(tmp_path)
    result = ingest_optionsdx(
        store,
        "spy",
        ingest_date=dt.date(2026, 9, 3),
        months=[("a.txt", sample), ("b.txt", sample)],  # same month, twice
    )
    assert result.duplicate_rows > 0
    assert result.quarantined_rows == 0
    stored = store.read_bronze_as_of(f"{DATASET}_spy", dt.date(2026, 9, 3))
    assert len(stored) == result.valid_rows
    assert not stored.duplicated(subset=["underlying", "quote_date", "expiration", "strike"]).any()
