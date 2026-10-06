"""The Kaggle SPY corpus is an offline cross-check (``docs/DATA_CONTRACTS.md``
#13). These tests pin the three things that would make it lie: a streamed read
that loses or double-counts records at chunk boundaries, a vendor zero-fill
stored as a real greek, and the corpus leaking into anything served.

The fixture is real Alpha Vantage / Kaggle bytes (2023-01-03 and 2023-12-29,
lifted by byte-range read from ``spy_options_data_23.json``) in the vendor's
exact nesting -- one array per trading day -- with these planted defects:

- 3 calls (2 on day 1, 1 on day 2) -> not kept
- put 372 (2023-01-03): IV and every greek zero-filled ``"0.00000"``
- put 373 (2023-01-03): IV blank ``""``
- put 374 (2023-01-03): ask ``"0.00"`` -> not a quote
- put 375 (2023-01-03): appears twice -> one duplicate
- put 466 (2023-12-29): strike ``"N/A"`` -> unparsable
- put 467 (2023-12-29): delta ``+0.5`` on a put -> impossible
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from tail_lab.contracts.kaggle_spy import AV_COLUMNS, GREEK_COLUMNS, year_file_name
from tail_lab.ingestion.kaggle_spy import (
    IngestResult,
    _iv_distinct_ratio,
    ingest_kaggle_spy_year,
    iter_record_batches,
    parquet_path,
    read_year,
    validate_and_quarantine,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "kaggle_spy_sample.json"


@pytest.fixture
def sample() -> str:
    return FIXTURE.read_text()


def _set(text: str, contract_id: str, key: str, value: str) -> str:
    """Change one field of one fixture record, leaving the rest byte-identical."""
    pattern = re.compile(r'\{"contractID": "' + contract_id + r'"[^{}]*\}')
    record = pattern.search(text)
    assert record is not None, contract_id
    new = re.sub(rf'"{key}": "[^"]*"', f'"{key}": "{value}"', record.group())
    return text.replace(record.group(), new, 1)


_LOSSES = (
    "not_spy_put_rows",
    "unparsable_rows",
    "no_ask_rows",
    "duplicate_rows",
    "quarantined_rows",
)


def _reconciles(r: IngestResult) -> bool:
    return r.valid_rows + sum(getattr(r, f) for f in _LOSSES) == r.records_read


def _ingest(text: str, out_dir: Path, chunk_chars: int = 1 << 20) -> IngestResult:
    return ingest_kaggle_spy_year(
        2023, out_dir=out_dir, source=io.StringIO(text), chunk_chars=chunk_chars
    )


def test_fixture_is_the_vendors_exact_shape(sample: str) -> None:
    """Guards the fixture itself: if it drifted from the real nesting or keys,
    every other test here would be testing an imaginary format."""
    assert sample.startswith('[[{"contractID": ')
    assert sample.endswith('"}]]')
    assert sample.count("}], [{") == 1  # two trading days
    first = re.search(r"\{[^{}]*\}", sample)
    assert first is not None
    assert tuple(re.findall(r'"(\w+)": ', first.group())) == AV_COLUMNS


def test_ingest_accounts_for_every_record(sample: str, tmp_path: Path) -> None:
    """28 records in; each one is kept or lands in exactly one named loss."""
    r = _ingest(sample, tmp_path)
    assert r.records_read == 28
    assert r.not_spy_put_rows == 3
    assert r.unparsable_rows == 1
    assert r.no_ask_rows == 1
    assert r.duplicate_rows == 1
    assert r.quarantined_rows == 0
    assert r.valid_rows == 22
    assert _reconciles(r)
    assert (r.trading_days, r.first_quote, r.last_quote) == (2, "2023-01-03", "2023-12-29")


def _as_date_object(sample: str) -> str:
    """The same records in the OTHER outer shape the corpus uses: 2014-2018
    and 2025 are ``{"<date>": [records], ...}`` with holidays as ``[]``."""
    days = json.loads(sample)
    return json.dumps({"2023-01-01": [], **{d[0]["date"]: d for d in days}})


def test_both_outer_shapes_ingest_identically(sample: str, tmp_path: Path) -> None:
    """Confirmed by byte-range reads: 2019-2024 open ``[[{``, 2014-2018 and
    2025 open ``{"2014-01-01": [], ``. An outer brace miscounted as a broken
    record would put a phantom loss in every one of those six years."""
    wrapped = _as_date_object(sample)
    assert wrapped.startswith('{"2023-01-01": [], "2023-01-03": [{"contractID": ')
    a = _ingest(sample, tmp_path / "a")
    for chunk in (5, 1 << 20):
        b = _ingest(wrapped, tmp_path / "b", chunk_chars=chunk)
        assert (b.records_read, b.valid_rows, b.unparsable_rows) == (
            a.records_read,
            a.valid_rows,
            a.unparsable_rows,
        )
    pd.testing.assert_frame_equal(
        read_year(2023, out_dir=tmp_path / "a"), read_year(2023, out_dir=tmp_path / "b")
    )


@pytest.mark.parametrize("chunk_chars", [1, 7, 333, 1000, 10**7])
def test_chunk_size_never_changes_the_answer(sample: str, tmp_path: Path, chunk_chars: int) -> None:
    """The year is streamed in fixed chunks, so records straddle boundaries.
    A boundary that dropped or duplicated a record would show up as a
    different row set at some chunk size -- including 1 char at a time."""
    r = _ingest(sample, tmp_path, chunk_chars=chunk_chars)
    assert (r.records_read, r.valid_rows, r.unparsable_rows) == (28, 22, 1)
    whole = read_year(2023, out_dir=tmp_path)
    reference = _ingest(sample, tmp_path / "ref")
    pd.testing.assert_frame_equal(
        whole, read_year(2023, out_dir=Path(reference.parquet_path).parent)
    )


def test_zero_filled_blank_and_impossible_greeks_are_voided_whole(
    sample: str, tmp_path: Path
) -> None:
    """The house rule: a zero-filled IV is "could not compute", never a 0%
    vol; a blank is absence; a +0.5 put delta is garbage. Each voids the WHOLE
    block, and the quote on the row is kept."""
    r = _ingest(sample, tmp_path)
    df = read_year(2023, out_dir=tmp_path).set_index("contract_id")
    for cid in ("SPY230106P00372000", "SPY230106P00373000", "SPY240119P00467000"):
        assert df.loc[cid, list(GREEK_COLUMNS)].isna().all(), cid
        assert df.loc[cid, "ask"] > 0, cid
    assert not (df["iv"] == 0).any()
    assert r.voided_greek_rows == 3
    # Every row NOT planted keeps its vendor block: the rule is not a blanket.
    assert df["iv"].notna().sum() == r.valid_rows - 3


_OK_PUT = "SPY240119P00470000"


@pytest.mark.parametrize("key", ["gamma", "vega", "theta", "rho"])
def test_a_block_blank_in_any_part_is_voided_whole(sample: str, tmp_path: Path, key: str) -> None:
    """Not only a blank IV: a block missing any one greek is not a block."""
    r = _ingest(_set(sample, _OK_PUT, key, ""), tmp_path)
    row = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT]
    assert row[list(GREEK_COLUMNS)].isna().all()
    assert r.voided_greek_rows == 4


@pytest.mark.parametrize("iv", ["12.5", "inf"])
def test_an_absurd_iv_voids_the_block_but_keeps_the_quote(
    sample: str, tmp_path: Path, iv: str
) -> None:
    """Left to the schema, IV > 10 would quarantine the whole row -- a real
    quote lost over a vendor greek nobody uses."""
    r = _ingest(_set(sample, _OK_PUT, "implied_volatility", iv), tmp_path)
    assert (r.valid_rows, r.quarantined_rows, r.voided_greek_rows) == (22, 0, 4)
    row = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT]
    assert row[list(GREEK_COLUMNS)].isna().all()
    assert row["ask"] > 0


def test_a_blank_bid_is_unreadable_not_a_zero_bid(sample: str, tmp_path: Path) -> None:
    """A $0.00 bid is a real market state; inventing one from a blank would
    read as a vendor disagreement against optionsDX."""
    r = _ingest(_set(sample, _OK_PUT, "bid", ""), tmp_path)
    assert (r.valid_rows, r.unparsable_rows) == (21, 2)
    assert _OK_PUT not in set(read_year(2023, out_dir=tmp_path)["contract_id"])
    assert _reconciles(r)


def test_losses_reconcile_even_when_the_json_itself_is_broken(sample: str, tmp_path: Path) -> None:
    """A record that fails JSON parsing, and two records fused by a lost
    ``}, {``, were still READ, so the run record's total must include them."""
    broken = sample.replace(
        '"symbol": "SPY", "expiration": "2023-01-06", "strike": "376.00"',
        '"symbol": "SPY" "expiration": "2023-01-06", "strike": "376.00"',
        1,
    )
    r = _ingest(broken, tmp_path, chunk_chars=50)
    assert r.unparsable_rows == 2  # strike "N/A", the broken record
    assert r.records_read == 28
    assert _reconciles(r)


@pytest.mark.parametrize(("joint", "lost"), [("", 2), (", ", 1)])
def test_two_fused_records_are_two_records_read(
    sample: str, tmp_path: Path, joint: str, lost: int
) -> None:
    """Delete one ``}, {`` and two records become one span. Invalid JSON loses
    both; with ``, `` left it is VALID JSON with duplicate keys, and json keeps
    the second record while the first vanishes -- unless counted per key."""
    i = sample.index("}, {", sample.index(_OK_PUT))
    r = _ingest(sample[:i] + joint + sample[i + 4 :], tmp_path)
    assert r.records_read == 28
    assert r.unparsable_rows == 1 + lost
    assert _reconciles(r)


def test_a_cut_off_download_never_replaces_a_good_year(
    sample: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Half a year parses fine. A file that does not END like a complete year
    is refused even though it has valid puts -- or half a year would silently
    replace the whole one."""
    _ingest(sample, tmp_path)
    caplog.set_level("INFO", logger="tail_lab.ingestion.kaggle_spy")
    for cut in (len(sample) // 2, len(sample) - 1, sample.rfind("{") + 40):
        with pytest.raises(ValueError, match="cut off"):
            _ingest(sample[:cut], tmp_path)
    assert len(read_year(2023, out_dir=tmp_path)) == 22
    assert any("reason=" in r.getMessage() for r in caplog.records)


def test_a_complete_file_with_trailing_whitespace_is_not_cut_off(
    sample: str, tmp_path: Path
) -> None:
    for text in (sample + "\n", _as_date_object(sample) + "  \n"):
        assert _ingest(text, tmp_path).valid_rows == 22


@pytest.mark.parametrize(
    "garbage", ["", "<html><body>Please sign in</body></html>", "<script>var a = {x: 1};</script>"]
)
def test_a_file_with_no_quotes_never_overwrites_a_good_year(
    sample: str, tmp_path: Path, garbage: str
) -> None:
    """`curl -L` saves whatever page comes back. A login page parsed to zero
    rows must fail loud, and must not replace the year already on disk."""
    _ingest(sample, tmp_path)
    with pytest.raises(ValueError, match="0 valid puts"):
        _ingest(garbage, tmp_path)
    assert len(read_year(2023, out_dir=tmp_path)) == 22


@pytest.mark.parametrize(
    ("key", "value"), [("gamma", "inf"), ("vega", "1e400"), ("theta", "-inf"), ("rho", "Infinity")]
)
def test_an_infinite_greek_voids_the_block(
    sample: str, tmp_path: Path, key: str, value: str
) -> None:
    r = _ingest(_set(sample, _OK_PUT, key, value), tmp_path)
    row = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT]
    assert row[list(GREEK_COLUMNS)].isna().all()
    assert r.voided_greek_rows == 4


def _drop_brace(text: str, contract_id: str, which: str) -> str:
    rec = re.search(r'\{"contractID": "' + contract_id + r'"[^{}]*\}', text)
    assert rec is not None, contract_id
    cut = rec.group()[1:] if which == "open" else rec.group()[:-1]
    return text.replace(rec.group(), cut, 1)


_LAST_PUT = "SPY240119P00478000"
_PENULTIMATE_PUT = "SPY240119P00477000"


@pytest.mark.parametrize("which", ["open", "close"])
@pytest.mark.parametrize("shape", ["array", "object"])
def test_a_record_missing_a_brace_is_counted(
    sample: str, tmp_path: Path, which: str, shape: str
) -> None:
    """A record that lost either brace cannot be matched; it must still be a
    number in the run record, not a silently shorter year -- in both outer
    shapes, at any chunk size."""
    base = sample if shape == "array" else _as_date_object(sample)
    text = _drop_brace(base, _OK_PUT, which)
    for chunk in (7, 1 << 20):
        r = _ingest(text, tmp_path, chunk_chars=chunk)
        assert (r.records_read, r.valid_rows, r.unparsable_rows) == (28, 21, 2), chunk
        assert _reconciles(r)


@pytest.mark.parametrize("shape", ["array", "object"])
def test_every_record_left_open_at_eof_is_counted(sample: str, tmp_path: Path, shape: str) -> None:
    """Two final records missing their closing brace are two lost rows, not
    one -- whatever closes the outer structure after them."""
    text = sample if shape == "array" else _as_date_object(sample)
    for cid in (_PENULTIMATE_PUT, _LAST_PUT):
        text = _drop_brace(text, cid, "close")
    for chunk in (7, 1 << 20):
        r = _ingest(text, tmp_path, chunk_chars=chunk)
        assert (r.records_read, r.valid_rows, r.unparsable_rows) == (28, 20, 3), (shape, chunk)


@pytest.mark.parametrize(
    ("value", "stored"),
    [("", None), ("abc", None), ("1.9", None), ("inf", None), ("1e20", None), ("40", 40)],
)
def test_a_blank_or_garbled_size_is_absent_not_zero(
    sample: str, tmp_path: Path, value: str, stored: int | None
) -> None:
    _ingest(_set(sample, _OK_PUT, "bid_size", value), tmp_path)
    got = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT, "bid_size"]
    assert pd.isna(got) if stored is None else got == stored, got


def test_conflicting_copies_of_a_key_are_both_quarantined(sample: str, tmp_path: Path) -> None:
    """Keeping the first of two DIFFERENT rows could keep the bad one. Only
    repeats identical after parsing count as duplicates."""
    rec = re.search(r'\{"contractID": "' + _OK_PUT + r'"[^{}]*\}', sample)
    assert rec is not None
    worse = re.sub(r'"ask": "[^"]*"', '"ask": "999.00"', rec.group())
    text = sample.replace(rec.group(), worse + ", " + rec.group(), 1)
    r = _ingest(text, tmp_path)
    assert (r.duplicate_rows, r.quarantined_rows, r.valid_rows) == (1, 2, 21)
    assert _OK_PUT not in set(read_year(2023, out_dir=tmp_path)["contract_id"])
    assert _reconciles(r)


def test_a_year_that_all_fails_the_schema_keeps_its_evidence(
    sample: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Refuse the year, but keep the diagnosis of a schema or format drift
    (which is not a bad download) -- without clobbering the quarantine that
    belongs to the good year already on disk -- and log the refusal (§f)."""
    good = _ingest(sample.replace('"strike": "470.00"', '"strike": "-470.00"'), tmp_path)
    assert good.quarantine_path is not None
    caplog.set_level("INFO", logger="tail_lab.ingestion.kaggle_spy")
    text = re.sub(r'"expiration": "[^"]*"', '"expiration": "2000-01-01"', sample)
    with pytest.raises(ValueError, match=r"22 quarantined -> .*__refused_quarantine\.parquet"):
        _ingest(text, tmp_path)
    assert len(read_year(2023, out_dir=tmp_path)) == 21  # the good year stands
    assert len(pd.read_parquet(good.quarantine_path)) == 1  # and so does its quarantine
    refused = tmp_path / "kaggle_spy_chain_puts_2023__refused_quarantine.parquet"
    assert len(pd.read_parquet(refused)) == 22
    line = next(m for r in caplog.records if "kaggle_spy.refused" in (m := r.getMessage()))
    assert "records_read=28" in line and "quarantined_rows=22" in line


def test_a_clean_rerun_removes_a_stale_quarantine(sample: str, tmp_path: Path) -> None:
    first = _ingest(sample.replace('"strike": "470.00"', '"strike": "-470.00"'), tmp_path)
    assert first.quarantine_path is not None
    second = _ingest(sample, tmp_path)
    assert second.quarantine_path is None
    assert not Path(first.quarantine_path).exists()


def test_refusal_evidence_never_outlives_the_run_it_describes(sample: str, tmp_path: Path) -> None:
    """A refused-quarantine file is the diagnosis of ONE refused run. A later
    refusal with nothing to quarantine, or a later clean run, removes it --
    else it reads as the diagnosis of the wrong run."""
    refused = tmp_path / "kaggle_spy_chain_puts_2023__refused_quarantine.parquet"
    schema_fail = re.sub(r'"expiration": "[^"]*"', '"expiration": "2000-01-01"', sample)
    with pytest.raises(ValueError):
        _ingest(schema_fail, tmp_path)
    assert refused.exists()
    with pytest.raises(ValueError):
        _ingest("<html>Please sign in</html>", tmp_path)
    assert not refused.exists()
    with pytest.raises(ValueError):
        _ingest(schema_fail, tmp_path)
    _ingest(sample, tmp_path)
    assert not refused.exists()


@pytest.mark.parametrize("mixed", [False, True])
def test_a_wrong_year_file_never_replaces_a_good_year(
    sample: str, tmp_path: Path, mixed: bool
) -> None:
    """A well-formed file of ANOTHER year saved under this year's name used to
    be accepted and overwrite the real year. One off-year row refuses it."""
    _ingest(sample, tmp_path)
    if mixed:
        # A single 2019 row among real 2023 rows -- and one that would ALSO
        # fail the schema (crossed), so a check made on the validated rows
        # instead of everything parsed would quarantine it and accept the file.
        wrong = _set(_set(sample, _OK_PUT, "date", "2019-12-31"), _OK_PUT, "bid", "99.00")
    else:
        wrong = sample.replace('"date": "2023-', '"date": "2019-').replace(
            '"expiration": "2023-', '"expiration": "2019-'
        )
    with pytest.raises(ValueError, match=r"dated outside 2023.*wrong file"):
        _ingest(wrong, tmp_path)
    assert len(read_year(2023, out_dir=tmp_path)) == 22


def test_a_later_year_file_is_refused_too(sample: str, tmp_path: Path) -> None:
    """The check is "not this year", not "an earlier year"."""
    _ingest(sample, tmp_path)
    later = sample.replace('"date": "2023-', '"date": "2024-').replace(
        '"expiration": "2023-', '"expiration": "2024-'
    )
    with pytest.raises(ValueError, match=r"dated outside 2023.*wrong file"):
        _ingest(later, tmp_path)
    assert len(read_year(2023, out_dir=tmp_path)) == 22


@pytest.mark.parametrize(("key", "value"), [("gamma", "-0.00100"), ("vega", "-0.50000")])
def test_a_negative_gamma_or_vega_voids_the_block(
    sample: str, tmp_path: Path, key: str, value: str
) -> None:
    r = _ingest(_set(sample, _OK_PUT, key, value), tmp_path)
    row = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT]
    assert row[list(GREEK_COLUMNS)].isna().all()
    assert row["ask"] > 0
    assert r.voided_greek_rows == 4


def test_a_pinned_block_is_voided_and_counted(sample: str, tmp_path: Path) -> None:
    """delta -1, gamma 0, vega 0 beside a positive IV: 21,260 such 2023 puts.
    An IV that yields no gamma and no vega is not the one the block came from."""
    text = sample
    for key, value in (("delta", "-1.00000"), ("gamma", "0.00000"), ("vega", "0.00000")):
        text = _set(text, _OK_PUT, key, value)
    r = _ingest(text, tmp_path)
    row = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT]
    assert row[list(GREEK_COLUMNS)].isna().all()
    assert r.voided_greek_rows == 4


@pytest.mark.parametrize(
    "zeroed", [(), ("gamma",), ("vega",)], ids=["delta-only", "gamma-zero", "vega-zero"]
)
def test_delta_minus_one_with_either_sensitivity_alive_is_kept(
    sample: str, tmp_path: Path, zeroed: tuple[str, ...]
) -> None:
    """Pinned means ALL THREE: delta -1 with a non-zero gamma or a non-zero
    vega is rounding on a deep-ITM put, and voiding it would lose a block."""
    text = _set(sample, _OK_PUT, "delta", "-1.00000")
    for key in zeroed:
        text = _set(text, _OK_PUT, key, "0.00000")
    r = _ingest(text, tmp_path)
    assert r.voided_greek_rows == 3
    row = read_year(2023, out_dir=tmp_path).set_index("contract_id").loc[_OK_PUT]
    assert row["delta"] == -1.0


def test_another_underlying_is_not_kept(sample: str, tmp_path: Path) -> None:
    r = _ingest(_set(sample, _OK_PUT, "symbol", "QQQ"), tmp_path)
    assert (r.valid_rows, r.not_spy_put_rows) == (21, 4)
    assert _OK_PUT not in set(read_year(2023, out_dir=tmp_path)["contract_id"])


def test_an_extra_vendor_key_raises(tmp_path: Path) -> None:
    """A new key is a format change too -- it may be the renamed half of one."""
    text = FIXTURE.read_text().replace('"rho": ', '"rho2": "0.1", "rho": ')
    with pytest.raises(ValueError, match=r"unexpected \['rho2'\]"):
        _ingest(text, tmp_path)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("expiration", "one day before quote"),
        ("last", -1.0),
        ("bid", "above ask"),
    ],
)
def test_the_schema_rejects_each_impossible_quote(
    sample: str, tmp_path: Path, column: str, value: object
) -> None:
    _ingest(sample, tmp_path)
    good = read_year(2023, out_dir=tmp_path)
    bad = good.head(1).copy()
    bad["contract_id"] = "X"
    if value == "one day before quote":
        bad["expiration"] = bad["quote_date"] - pd.Timedelta(days=1)
    elif value == "above ask":
        bad["bid"] = bad["ask"] + 0.01
    else:
        bad[column] = value
    valid, quarantined = validate_and_quarantine(pd.concat([good, bad], ignore_index=True))
    assert len(valid) == len(good)
    assert list(quarantined["contract_id"]) == ["X"]


def test_the_boundary_cases_of_each_check_are_valid(sample: str, tmp_path: Path) -> None:
    """A quote on its expiry day, and a LOCKED quote (bid == ask), are real
    market states; a check one notch too strict would quarantine every one."""
    _ingest(sample, tmp_path)
    good = read_year(2023, out_dir=tmp_path).head(2).copy()
    good.loc[good.index[0], "expiration"] = good.loc[good.index[0], "quote_date"]
    good.loc[good.index[1], "bid"] = good.loc[good.index[1], "ask"]
    valid, quarantined = validate_and_quarantine(good)
    assert (len(valid), len(quarantined)) == (2, 0)


@pytest.mark.parametrize("column", ["bid", "strike", "ask"])
def test_the_unique_key_is_quote_date_and_contract_only(
    sample: str, tmp_path: Path, column: str
) -> None:
    """Two rows for one contract on one day are a conflict whatever field they
    differ in -- a key widened to include that field would let both through."""
    _ingest(sample, tmp_path)
    good = read_year(2023, out_dir=tmp_path)
    twin = good.head(1).copy()
    twin[column] = twin[column] + 0.5
    valid, quarantined = validate_and_quarantine(pd.concat([good, twin], ignore_index=True))
    assert len(quarantined) == 2
    assert len(valid) == len(good) - 1


def test_iv_distinct_ratio_is_the_median_day() -> None:
    """Three days at 1.0, 0.5 and 0.2 distinct-per-put: median 0.5, mean 0.57.
    A mean would let one smooth day hide among sharp ones."""
    day = pd.Timestamp
    frame = pd.DataFrame(
        {
            "quote_date": [day("2023-01-03")] * 2
            + [day("2023-01-04")] * 2
            + [day("2023-01-05")] * 5,
            "iv": [0.1, 0.2, 0.3, 0.3, 0.4, 0.4, 0.4, 0.4, 0.4],
        }
    )
    assert _iv_distinct_ratio(frame) == 0.5


def test_a_stray_open_brace_at_eof_is_one_lost_record() -> None:
    """A download cut inside a record's first key leaves ``{"contr`` -- no key
    to count, but still a record lost."""
    text = '[[{"contractID": "A"}, {"contr'
    batches = list(iter_record_batches(io.StringIO(text)))
    assert sum(b.unparsable for b in batches) == 1
    assert batches[-1].truncated


def test_iter_record_batches_counts_a_truncated_tail() -> None:
    """A download cut off mid-record is a partial year; it must show as a
    number, not as a silently shorter year."""
    text = FIXTURE.read_text()
    cut = text[: text.rfind("{") + 40]
    batches = list(iter_record_batches(io.StringIO(cut), chunk_chars=64))
    assert sum(len(b.records) for b in batches) == 27
    assert sum(b.unparsable for b in batches) == 1
    assert batches[-1].truncated
    assert not any(b.truncated for b in batches[:-1])


def test_a_malformed_record_costs_one_row_not_the_batch() -> None:
    good = '{"a": "1"}'
    text = f'[[{good}, {{"a": 1 2}}, {good}]]'
    batches = list(iter_record_batches(io.StringIO(text)))
    assert sum(len(b.records) for b in batches) == 2
    assert sum(b.unparsable for b in batches) == 1
    assert not batches[-1].truncated


def test_a_fused_span_is_counted_on_the_per_record_fallback_too() -> None:
    """One bad span sends the batch down the per-record path; a fused span
    that still parses there must still cost its swallowed record."""
    fused = '{"contractID": "A", "x": "1", "contractID": "B", "x": "2"}'
    text = f'[[{fused}, {{"contractID": "X" "bad"}}, {{"contractID": "C"}}]]'
    batches = list(iter_record_batches(io.StringIO(text)))
    assert sum(len(b.records) for b in batches) == 2
    assert sum(b.unparsable for b in batches) == 2


def test_a_changed_vendor_key_set_raises(tmp_path: Path) -> None:
    """A renamed key parsed as something else is plausible nonsense."""
    text = FIXTURE.read_text().replace('"implied_volatility"', '"iv"')
    with pytest.raises(ValueError, match="record keys changed"):
        _ingest(text, tmp_path)


def test_schema_quarantines_garbage_and_keeps_the_good(sample: str, tmp_path: Path) -> None:
    _ingest(sample, tmp_path)
    good = read_year(2023, out_dir=tmp_path)
    bad = good.head(3).copy()
    bad["contract_id"] = ["X1", "X2", "X3"]
    bad.loc[bad.index[0], "bid"] = -1.0  # negative premium
    bad.loc[bad.index[1], "expiration"] = pd.Timestamp("2020-01-01")  # expired before quoted
    bad.loc[bad.index[2], "strike"] = 0.0  # zero strike
    valid, quarantined = validate_and_quarantine(pd.concat([good, bad], ignore_index=True))
    assert len(valid) == len(good)
    assert sorted(quarantined["contract_id"]) == ["X1", "X2", "X3"]


def test_quarantine_is_written_beside_the_year(sample: str, tmp_path: Path) -> None:
    text = sample.replace('"strike": "470.00"', '"strike": "-470.00"')
    r = _ingest(text, tmp_path)
    assert r.quarantined_rows == 1
    assert r.quarantine_path is not None and Path(r.quarantine_path).exists()
    assert r.valid_rows == 21


def test_read_year_names_the_fix_when_absent(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make ingest-kaggle-spy YEAR=2019"):
        read_year(2019, out_dir=tmp_path)


def test_a_year_is_read_straight_out_of_a_zip_never_extracted(sample: str, tmp_path: Path) -> None:
    """The kaggle CLI can hand back a zip; 8.7 GB must never be unpacked."""
    with zipfile.ZipFile(tmp_path / "archive.zip", "w") as zf:
        zf.writestr(year_file_name(2023), sample)
    r = ingest_kaggle_spy_year(2023, vendor_dir=tmp_path, out_dir=tmp_path / "out")
    assert r.valid_rows == 22
    assert r.source.endswith("archive.zip!spy_options_data_23.json")
    assert not (tmp_path / year_file_name(2023)).exists()


def test_plain_file_source_and_absent_source(sample: str, tmp_path: Path) -> None:
    (tmp_path / year_file_name(2023)).write_text(sample)
    r = ingest_kaggle_spy_year(2023, vendor_dir=tmp_path, out_dir=tmp_path / "out")
    assert r.valid_rows == 22
    assert parquet_path(2023, tmp_path / "out").exists()
    with pytest.raises(FileNotFoundError, match="HUMAN_TODO"):
        ingest_kaggle_spy_year(2024, vendor_dir=tmp_path, out_dir=tmp_path / "out")


def test_year_outside_the_corpus_is_refused() -> None:
    with pytest.raises(ValueError, match="2014-2025"):
        year_file_name(2013)


def test_the_run_is_logged_with_every_loss(
    sample: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="tail_lab.ingestion.kaggle_spy")
    _ingest(sample, tmp_path)
    line = next(r.getMessage() for r in caplog.records if "kaggle_spy.run" in r.getMessage())
    for field in (
        "year=2023",
        "valid_rows=22",
        "duplicate_rows=1",
        "unparsable_rows=1",
        "voided_greek_rows=3",
        "iv_distinct_ratio=",
    ):
        assert field in line, field


# --- never served -------------------------------------------------------------
#
# The owner's rule: this corpus never feeds a verdict, a recommendation, or
# anything the API / live page serves. import-linter forbids the import; these
# catch what it cannot -- a path string, a dataset name read through the lake,
# or the vendor tree being copied into the image.

# Every module the app could reach -- not only api/ and research/, because
# research may import transforms, lake and contracts, and a path string in any
# of them would serve the corpus without an import the linter could see.
#
# scripts/ too: a script is not served, but one that wrote the parquet into
# the lake would make it servable. The one allowed script is read-only.
_SCANNED_TREES = ("src/tail_lab", "frontend/src", "scripts")
_COMPARISON_SCRIPT = "scripts/kaggle_spy_vs_optionsdx.py"
_ALLOWED = {
    "src/tail_lab/contracts/kaggle_spy.py",
    "src/tail_lab/ingestion/kaggle_spy.py",
    _COMPARISON_SCRIPT,
}
_TELLS = re.compile(r"kaggle", re.IGNORECASE)


def test_nothing_but_the_adapter_mentions_the_kaggle_corpus() -> None:
    offenders = [
        rel
        for tree in _SCANNED_TREES
        for path in (ROOT / tree).rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx", ".sh"}
        and (rel := str(path.relative_to(ROOT))) not in _ALLOWED
        and _TELLS.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_one_script_allowed_to_read_it_writes_nothing() -> None:
    text = (ROOT / _COMPARISON_SCRIPT).read_text()
    assert not re.search(r"\bwrite_|to_parquet|\.to_csv|open\(", text)


def test_import_linter_forbids_the_serve_side_importing_it() -> None:
    """The grep above is the backstop; this is the contract it backs up."""
    text = (ROOT / "pyproject.toml").read_text()
    block = text[text.index('name = "The Kaggle SPY corpus is never served"') :]
    end = block.find("[[tool.")
    block = block if end == -1 else block[:end]
    for module in (
        "tail_lab.api",
        "tail_lab.research",
        "tail_lab.contracts.kaggle_spy",
        "tail_lab.ingestion.kaggle_spy",
    ):
        assert f'"{module}"' in block, module


def test_the_vendor_tree_is_never_committed_or_shipped() -> None:
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "data/" in gitignore
    assert "data" in dockerignore
