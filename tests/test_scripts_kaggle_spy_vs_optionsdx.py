"""scripts/kaggle_spy_vs_optionsdx.py is the Kaggle corpus's first job: say
where two vendors disagree about the same 2023 put wing. A comparison that
counted out-of-band Kaggle strikes as "missing from optionsDX", or matched
quotes across different dates, would manufacture disagreement; one that
crashed when a corpus is absent would be useless on every machine but one."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.optionsdx import DATASET as ODX_DATASET
from tail_lab.ingestion.kaggle_spy import ingest_kaggle_spy_year, read_year
from tail_lab.lake.store import DeltaLakeStore

_SPEC = importlib.util.spec_from_file_location(
    "kaggle_spy_vs_optionsdx",
    Path(__file__).resolve().parents[1] / "scripts" / "kaggle_spy_vs_optionsdx.py",
)
assert _SPEC and _SPEC.loader
script = importlib.util.module_from_spec(_SPEC)
# A dataclass resolves its module via sys.modules, so register before exec.
sys.modules[_SPEC.name] = script
_SPEC.loader.exec_module(script)

FIXTURE = Path(__file__).parent / "fixtures" / "kaggle_spy_sample.json"
#: Roughly SPY's close on the fixture's two dates; every fixture strike sits
#: inside optionsDX's 0.60-1.02 band against these.
SPOT = {pd.Timestamp("2023-01-03"): 380.0, pd.Timestamp("2023-12-29"): 475.31}


@pytest.fixture
def kaggle(tmp_path: Path) -> pd.DataFrame:
    ingest_kaggle_spy_year(2023, out_dir=tmp_path, source=io.StringIO(FIXTURE.read_text()))
    return read_year(2023, out_dir=tmp_path)


def _optionsdx_from(kaggle: pd.DataFrame) -> pd.DataFrame:
    """An optionsDX panel that agrees with Kaggle exactly, to perturb."""
    odx = kaggle[["quote_date", "expiration", "strike", "bid", "ask"]].copy()
    odx["spot"] = odx["quote_date"].map(SPOT)
    return odx.reset_index(drop=True)


def test_identical_vendors_agree_perfectly(kaggle: pd.DataFrame) -> None:
    c = script.compare(kaggle, _optionsdx_from(kaggle))
    assert c is not None
    assert (c.dates_compared, c.keys_both, c.keys_only_kaggle, c.keys_only_optionsdx) == (
        2,
        22,
        0,
        0,
    )
    assert c.median_jaccard == 1.0
    assert c.median_abs_bid_diff == 0.0
    assert c.share_outside_tolerance == 0.0


def test_disagreement_is_counted_on_the_right_side(kaggle: pd.DataFrame) -> None:
    odx = _optionsdx_from(kaggle)
    day1 = odx["quote_date"] == pd.Timestamp("2023-01-03")
    dropped = odx.index[day1][0]
    odx = odx.drop(index=dropped)  # a key only Kaggle has
    extra = odx.iloc[[-1]].assign(strike=479.5)  # a key only optionsDX has
    odx = pd.concat([odx, extra], ignore_index=True)
    odx.loc[odx.index[0], "bid"] += 0.10  # one quote disagrees by 10 cents

    c = script.compare(kaggle, odx)
    assert c is not None
    assert (c.keys_both, c.keys_only_kaggle, c.keys_only_optionsdx) == (21, 1, 1)
    # Day 1: 8 shared of 9 keys; day 2: 13 of 14.
    assert c.worst_dates[0] == ("2023-01-03", pytest.approx(8 / 9))
    assert c.share_outside_tolerance == pytest.approx(1 / 21)


def test_kaggle_rows_outside_optionsdxs_band_are_not_disagreement(
    kaggle: pd.DataFrame,
) -> None:
    """optionsDX is stored pre-sliced, so a far-OTM Kaggle strike is absent
    from it by construction. Counting it as "only Kaggle" would invent a
    vendor gap out of our own slice."""
    far = kaggle.iloc[[0]].assign(strike=100.0, contract_id="far")
    long_dated = kaggle.iloc[[0]].assign(expiration=pd.Timestamp("2026-12-18"), contract_id="long")
    c = script.compare(pd.concat([kaggle, far, long_dated]), _optionsdx_from(kaggle))
    assert c is not None
    assert c.keys_only_kaggle == 0


def _quote_diff(kaggle: pd.DataFrame, side: str, cents: int) -> float:
    odx = _optionsdx_from(kaggle)
    # 0.30 + 0.02 is not 0.32 in float; that is the case the check must survive.
    odx.loc[odx.index[0], side] = round(odx.loc[odx.index[0], side] + cents / 100, 2)
    c = script.compare(kaggle, odx)
    assert c is not None
    return c.share_outside_tolerance


@pytest.mark.parametrize("side", ["bid", "ask"])
def test_tolerance_is_two_whole_cents_on_either_side(kaggle: pd.DataFrame, side: str) -> None:
    """Exactly two cents is inside (float subtraction made it outside); three
    is outside -- on the ask as much as the bid."""
    assert _quote_diff(kaggle, side, 2) == 0.0
    assert _quote_diff(kaggle, side, -2) == 0.0
    assert _quote_diff(kaggle, side, 3) == pytest.approx(1 / 22)


def _at_dte(kaggle: pd.DataFrame, days: int, cid: str) -> pd.DataFrame:
    row = kaggle.iloc[[0]].copy()
    row["expiration"] = row["quote_date"] + pd.Timedelta(days=days)
    row["contract_id"] = cid
    return row


@pytest.mark.parametrize("days", [1, 119])
def test_a_contract_just_inside_the_dte_band_is_matched(kaggle: pd.DataFrame, days: int) -> None:
    """Present in both corpora on the first and last compared day: a match,
    not a gap. The days are literals, so narrowing the band fails here."""
    edge = _at_dte(kaggle, days, "edge")
    both = pd.concat([kaggle, edge], ignore_index=True)
    c = script.compare(both, _optionsdx_from(both))
    assert c is not None
    assert (c.keys_both, c.keys_only_kaggle, c.keys_only_optionsdx) == (23, 0, 0)


@pytest.mark.parametrize("days", [0, 120, 121])
def test_the_band_edges_are_excluded_on_both_sides(kaggle: pd.DataFrame, days: int) -> None:
    """optionsDX's vendor DTE disagrees with the calendar AT its 0- and 120-day
    edges, which on real 2023 made 755 one-sided keys out of nothing. Neither
    side's edge contract may count, whichever corpus holds it."""
    edge = _at_dte(kaggle, days, f"dte{days}")
    c = script.compare(pd.concat([kaggle, edge], ignore_index=True), _optionsdx_from(kaggle))
    assert c is not None and c.keys_only_kaggle == 0
    odx = pd.concat([_optionsdx_from(kaggle), _optionsdx_from(edge)], ignore_index=True)
    c = script.compare(kaggle, odx)
    assert c is not None and c.keys_only_optionsdx == 0


@pytest.mark.parametrize("moneyness", [0.595, 1.025])
def test_a_strike_just_outside_the_moneyness_band_is_not_a_gap(
    kaggle: pd.DataFrame, moneyness: float
) -> None:
    row = kaggle.iloc[[0]].copy()
    row["strike"] = round(SPOT[row["quote_date"].iloc[0]] * moneyness, 2)
    c = script.compare(pd.concat([kaggle, row], ignore_index=True), _optionsdx_from(kaggle))
    assert c is not None and c.keys_only_kaggle == 0


def test_no_shared_dates_is_none_not_a_report(kaggle: pd.DataFrame) -> None:
    odx = _optionsdx_from(kaggle).assign(quote_date=pd.Timestamp("2022-06-01"))
    assert script.compare(kaggle, odx) is None


def test_main_degrades_when_kaggle_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert script.main([]) == 0
    assert "SKIPPED -- Kaggle corpus absent" in capsys.readouterr().out


def test_main_degrades_when_optionsdx_is_absent(
    tmp_path: Path,
    kaggle: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(script, "read_year", lambda _year: kaggle)
    monkeypatch.setattr(script, "get_lake_store", lambda: DeltaLakeStore(tmp_path / "lake"))
    assert script.main([]) == 0
    assert f"SKIPPED -- no {ODX_DATASET}_spy snapshot" in capsys.readouterr().out


def test_main_reports_on_real_stores(
    tmp_path: Path,
    kaggle: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = DeltaLakeStore(tmp_path / "lake")
    odx = _optionsdx_from(kaggle)
    store.write_bronze(f"{ODX_DATASET}_spy", dt.date(2026, 9, 3), odx)
    monkeypatch.setattr(script, "read_year", lambda _year: kaggle)
    monkeypatch.setattr(script, "get_lake_store", lambda: store)
    assert script.main(["--as-of", "2026-09-04"]) == 0
    out = capsys.readouterr().out
    assert "dates compared 2" in out
    assert "both 22, only Kaggle 0, only optionsDX 0" in out

    # A year optionsDX does not cover overlaps nothing: said, not crashed.
    assert script.main(["--as-of", "2026-09-04", "--year", "2024"]) == 0
    assert "share no quote dates in 2024" in capsys.readouterr().out


def test_cents_round_rather_than_truncate() -> None:
    """1.15 - 1.12 is 2.9999999999999805 cents in floating point: rounding
    makes it 3 (outside the 2-cent tolerance); truncation would make it 2."""
    assert script._cents(pd.Series([1.15 - 1.12])).tolist() == [3]
