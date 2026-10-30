"""The Deribit write rules (``contracts/deribit_options``, ``docs/adr/0032``).

Every fixture here is synthetic, shaped like the live payloads measured on
2026-10-30: no Deribit data is committed to this public repo. The helpers
(:func:`record`, :func:`records`, :func:`run_plan`) are imported by the
writer tests too, so all of them build bodies the same way.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Collection, Sequence

import pandas as pd
import pytest

from tail_lab.contracts import deribit_options as contract
from tail_lab.contracts.deribit_options import (
    DERIBIT_BOOKS,
    DeribitApiError,
    DeribitRefusal,
    DeribitWritePlan,
    check_book_rows,
    check_instrument_rows,
    deribit_frame,
    name_expiry,
    plan_deribit_write,
    prepare_quarantine,
    quarantine_document,
    quarantine_key,
    quarantine_log_records,
    serialize_quarantine,
)

NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.UTC)
DAY = NOW.date()


def ms(when: dt.datetime) -> int:
    return int(when.timestamp() * 1000)


NOW_MS = ms(NOW)
CREATED_MS = NOW_MS - 60_000


def _expiry_ms(name: str) -> int | None:
    expiry = name_expiry(name)
    return None if expiry is None else int(expiry.value // 1_000_000)


def record(name: str = "BTC-27NOV26-80000-P", **overrides: object) -> dict[str, object]:
    """One valid wire record (``DeribitRecord``) for ``name``: a linear USDC
    name (``SOL_USDC-...``) gets the USDC book's currencies."""
    linear = "_USDC" in name
    base = name.split("-")[0].split("_")[0]
    row: dict[str, object] = {
        "instrument_name": name,
        "creation_timestamp_ms": CREATED_MS,
        "expiration_timestamp_ms": _expiry_ms(name),
        "underlying": base,
        "premium_currency": "USDC" if linear else base,
        "strike": float(name.split("-")[2]) if name.count("-") >= 3 else 100.0,
        "strike_currency": "USDC" if linear else "USD",
        "right": name.rsplit("-", 1)[-1] if name.count("-") >= 3 else "P",
        "contract_size": 10.0 if linear else 1.0,
        "tick_size": 0.0005,
        "min_trade_amount": 0.1,
        "bid": 0.0145,
        "ask": 0.016,
        "mark": 0.0152,
        "mark_iv_pct": 52.0,
        "underlying_price": 77255.59,
        "underlying_index": name.rsplit("-", 2)[0] if name.count("-") >= 3 else base,
        "index_price": 77257.01,
        "interest_rate": 0.0,
        "open_interest": 3.5,
        "volume": 0.0,
        "volume_usd": 0.0,
        "invalid_fields": None,
    }
    row.update(overrides)
    return row


def book_names(n: int, currency: str = "BTC", *, expiry: str = "27NOV26") -> list[str]:
    """``n`` distinct live names in ``currency``'s book."""
    if currency == "USDC":
        return [f"SOL_USDC-{expiry}-{10 + i}-{'PC'[i % 2]}" for i in range(n)]
    return [f"{currency}-{expiry}-{1000 + 100 * i}-{'PC'[i % 2]}" for i in range(n)]


def records(n: int, currency: str = "BTC", **overrides: object) -> list[dict[str, object]]:
    return [record(name, **overrides) for name in book_names(n, currency)]


def run_plan(
    recs: Sequence[dict[str, object]],
    *,
    listed: Collection[str] | None = None,
    now: dt.datetime = NOW,
    captured: Collection[dt.date] = (),
    currency: str = "BTC",
) -> DeribitWritePlan:
    """``plan_deribit_write`` over ``recs`` with a fake lake holding ``captured``."""
    return plan_deribit_write(
        deribit_frame(recs),
        currency=currency,
        listed=[str(r["instrument_name"]) for r in recs] if listed is None else listed,
        now=now,
        partition_exists=lambda day: day in captured,
        has_earlier_partition=lambda day: any(c < day for c in captured),
    )


def refusal(recs: Sequence[dict[str, object]], **kwargs: object) -> DeribitRefusal:
    with pytest.raises(DeribitRefusal) as caught:
        run_plan(recs, **kwargs)  # type: ignore[arg-type]
    return caught.value


# ---- the frame ----------------------------------------------------------------


def test_a_clean_book_commits_every_row() -> None:
    plan = run_plan(records(50))
    assert len(plan.valid) == 50
    assert plan.quarantined.empty
    assert plan.snapshot_date == DAY
    assert plan.quarantine_share == 0.0


def test_mark_iv_is_divided_by_100_exactly_once() -> None:
    """A double division (52% -> 0.0052) would still pass ``0 < x <= 10``,
    so only the exact value proves it."""
    plan = run_plan(records(5, mark_iv_pct=52.0))
    assert (plan.valid["mark_iv"] == 52.0 / 100).all()


def test_timestamps_are_naive_utc_from_integer_ms() -> None:
    plan = run_plan(records(3))
    assert plan.valid["observed_at"].dt.tz is None
    assert plan.valid["observed_at"].iloc[0] == pd.Timestamp(CREATED_MS, unit="ms")
    assert plan.valid["expiration"].iloc[0] == pd.Timestamp("2026-11-27 08:00")


def test_a_null_bid_stays_null() -> None:
    """No resting bid is not a bid of zero."""
    recs = records(3)
    recs[1]["bid"] = None
    plan = run_plan(recs)
    assert plan.valid["bid"].isna().tolist() == [False, True, False]


def test_an_all_null_column_still_has_a_type() -> None:
    frame = deribit_frame(records(3, bid=None, underlying_index=None))
    assert frame["bid"].dtype == "float64"
    assert str(frame["underlying_index"].dtype) == "str"


def test_a_naive_now_is_refused() -> None:
    """A Budapest-local ``datetime.now()`` would make every --local run stale."""
    with pytest.raises(TypeError):
        run_plan(records(3), now=NOW.replace(tzinfo=None))


def test_now_in_another_zone_is_converted() -> None:
    budapest = dt.timezone(dt.timedelta(hours=1))
    assert run_plan(records(3), now=NOW.astimezone(budapest)).snapshot_date == DAY


# ---- name expiry ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("BTC-1NOV26-80000-P", "2026-11-01 08:00"),
        ("SOL_USDC-13NOV26-140-C", "2026-11-13 08:00"),
        ("ETH-26MAR27-2600-C", "2027-03-26 08:00"),
    ],
)
def test_name_expiry_reads_single_digit_days_and_usdc_names(name: str, expected: str) -> None:
    assert name_expiry(name) == pd.Timestamp(expected)


@pytest.mark.parametrize("name", ["BTC", "BTC-XX-1-P", "BTC-1JAN69-1-P", "BTC-31FEB27-1-P"])
def test_name_expiry_is_total(name: str) -> None:
    """Malformed, or before 2016 (``1JAN69`` parses as 1969): ``None``."""
    assert name_expiry(name) is None


# ---- rule 1: freshness --------------------------------------------------------


def test_stale_is_refused() -> None:
    assert refusal(records(10, creation_timestamp_ms=NOW_MS - 31 * 60_000)).status == 409


def test_future_is_refused() -> None:
    exc = refusal(records(10, creation_timestamp_ms=NOW_MS + 6 * 60_000))
    assert exc.status == 409
    assert "future" in exc.detail


def test_one_bad_timestamp_among_fresh_rows_is_refused() -> None:
    """``min()`` skips NaT and ``now - NaT`` compares False: without the
    NaT-first check this book would commit. An out-of-window creation only
    reaches here through ``--local`` (the wire bounds it)."""
    recs = records(120)
    recs[57]["creation_timestamp_ms"] = 10**17
    exc = refusal(recs)
    assert exc.status == 409
    assert "bad timestamp" in exc.detail


def test_a_replayed_captured_body_is_stale_not_already_captured() -> None:
    """Freshness runs before the already-captured shortcut: otherwise
    yesterday's captured body replayed today answers green and today is lost."""
    exc = refusal(records(10), now=NOW + dt.timedelta(days=1), captured={DAY})
    assert exc.status == 409


# ---- rule 1b: the book ------------------------------------------------------------


def test_a_fully_mismatched_body_is_refused_and_says_which_book() -> None:
    exc = refusal(records(10, "ETH"), currency="BTC")
    assert exc.status == 409
    assert "for book ETH, not BTC" in exc.detail


def test_null_currencies_never_count_toward_the_refusal() -> None:
    """60% null and 40% mismatched: 40% of all rows is not above half, so it
    commits -- with every one of those rows quarantined ``wrong_book``."""
    recs = records(100)
    for i, row in enumerate(recs):
        row["premium_currency"] = None if i < 60 else "ETH"
    plan = run_plan(recs)
    assert plan.valid.empty
    assert set(plan.quarantined["reason"]) == {"wrong_book"}
    assert plan.quarantine_share == 1.0


def test_the_book_share_is_over_all_posted_rows_before_the_expiry_drop() -> None:
    """45 mismatched and 20 expired matching rows of 100: 45% -> commits. A
    denominator taken after the drop would be 45/80 = 56% and refuse."""
    live = records(80)
    for row in live[:45]:
        row["premium_currency"] = "ETH"
    expired = [record(n) for n in book_names(20, expiry="29OCT26")]
    plan = run_plan(live + expired, listed=[str(r["instrument_name"]) for r in live])
    assert plan.expired_dropped == 20
    assert (plan.quarantined["reason"] == "wrong_book").sum() == 45


def test_a_few_null_currencies_are_quarantined_not_refused() -> None:
    recs = records(100)
    recs[3]["premium_currency"] = None
    recs[9]["premium_currency"] = None
    plan = run_plan(recs)
    assert len(plan.valid) == 98
    assert plan.quarantined["reason"].tolist() == ["wrong_book", "wrong_book"]


def test_the_refusal_detail_counts_nulls_apart() -> None:
    recs = records(10, "ETH")
    recs[0]["premium_currency"] = None
    assert "1 with no currency" in refusal(recs, currency="BTC").detail


# ---- rule 2: partition ---------------------------------------------------------


def test_the_partition_is_the_earliest_observation_before_any_drop() -> None:
    """The earliest row is expired and dropped; the partition is still ITS
    date, not the date of the rows that survive."""
    late = dt.datetime(2026, 10, 30, 23, 59, 59, tzinfo=dt.UTC)
    early_row = record("BTC-30OCT26-1000-P", creation_timestamp_ms=ms(late))
    after = [record(n, creation_timestamp_ms=ms(late) + 6_000) for n in book_names(10)]
    plan = run_plan([early_row, *after], now=late + dt.timedelta(minutes=1))
    assert plan.snapshot_date == dt.date(2026, 10, 30)
    assert plan.expired_dropped == 1


def test_already_captured_returns_without_checking_rows() -> None:
    """Rows that would be quarantined are not even looked at."""
    recs = records(10, mark=-1.0)
    plan = run_plan(recs, captured={DAY})
    assert plan.already_captured
    assert plan.valid.empty and plan.quarantined.empty
    assert plan.quarantine_share is None


def test_already_captured_wins_over_too_early_for_a_fresh_body() -> None:
    early = dt.datetime(2026, 10, 30, 7, 0, tzinfo=dt.UTC)
    recs = records(10, creation_timestamp_ms=ms(early) - 60_000)
    assert run_plan(recs, now=early, captured={DAY}).already_captured


# ---- rule 3: too early -----------------------------------------------------------


def test_before_0805_utc_is_too_early() -> None:
    early = dt.datetime(2026, 10, 30, 8, 4, tzinfo=dt.UTC)
    exc = refusal(records(10, creation_timestamp_ms=ms(early)), now=early)
    assert exc.status == 425


# ---- rule 4: expiry -----------------------------------------------------------------


def test_expired_rows_are_dropped_and_counted_including_an_unmatched_one() -> None:
    """The unmatched expired row has no instrument expiry: the name decides."""
    live = records(100)
    gone = record("BTC-30OCT26-1000-P")
    unmatched = record("BTC-29OCT26-2000-C", expiration_timestamp_ms=None, strike=None)
    plan = run_plan([*live, gone, unmatched], listed=[str(r["instrument_name"]) for r in live])
    assert plan.expired_dropped == 2
    assert len(plan.valid) == 100
    assert plan.quarantined.empty


def test_the_instruments_expiry_is_used_when_the_name_spells_none() -> None:
    """A name that spells no date still gets the instruments' expiry."""
    expiry = pd.Timestamp("2026-12-25 08:00")
    recs = records(100)
    recs.append(
        record(
            "ODDNAME",
            premium_currency="BTC",
            expiration_timestamp_ms=int(expiry.value // 1_000_000),
        )
    )
    plan = run_plan(recs)
    assert len(plan.valid) == 101
    assert plan.valid.loc[100, "expiration"] == expiry


def test_an_instrument_expiry_that_disagrees_with_the_name_is_quarantined() -> None:
    """A 2017 expiry on a ``27NOV26`` name: trusting either would be a guess,
    and trusting the instrument would drop a live row silently as expired."""
    recs = records(200)
    recs[6]["expiration_timestamp_ms"] = int(pd.Timestamp("2017-03-31 08:00").value // 1_000_000)
    recs[7]["expiration_timestamp_ms"] = int(pd.Timestamp("2026-12-25 08:00").value // 1_000_000)
    plan = run_plan(recs)
    assert plan.expired_dropped == 0
    assert plan.quarantined["instrument_name"].tolist() == [
        recs[6]["instrument_name"],
        recs[7]["instrument_name"],
    ]
    assert set(plan.quarantined["reason"]) == {"unparseable_expiry"}


def test_an_int64_edge_expiry_beside_a_null_one_does_not_crash_the_frame() -> None:
    """Ints plus one ``None`` in ``pd.DataFrame(records)`` go float64, and
    2**63 - 1 rounds to 2**63, which the ``Int64`` cast refuses with a
    ``TypeError``: a 500 on the route, a crash of the whole --local run."""
    recs = records(200)
    recs[4]["expiration_timestamp_ms"] = None
    recs[9]["expiration_timestamp_ms"] = 2**63 - 1
    plan = run_plan(recs)
    assert len(plan.valid) == 199
    assert plan.quarantined["instrument_name"].tolist() == [recs[9]["instrument_name"]]
    assert plan.quarantined["reason"].tolist() == ["unparseable_expiry"]


@pytest.mark.parametrize("bad", [10**17, 2**63 - 1, 0])
def test_an_out_of_window_instrument_expiry_is_quarantined_with_no_name_fallback(
    bad: int,
) -> None:
    recs = records(200)
    recs[5]["expiration_timestamp_ms"] = bad
    plan = run_plan(recs)
    assert plan.quarantined["instrument_name"].tolist() == [recs[5]["instrument_name"]]
    assert plan.quarantined["reason"].tolist() == ["unparseable_expiry"]
    assert plan.expired_dropped == 0


def test_a_malformed_unmatched_name_is_quarantined_not_a_500() -> None:
    recs = records(100)
    recs.append(record("GARBLED", expiration_timestamp_ms=None, premium_currency="BTC"))
    plan = run_plan(recs, listed=[str(r["instrument_name"]) for r in recs[:100]])
    assert plan.quarantined["reason"].tolist() == ["unparseable_expiry"]


def test_a_pre_2016_name_expiry_is_quarantined_not_dropped_as_expired() -> None:
    recs = records(100)
    recs.append(record("BTC-1JAN69-1000-P", expiration_timestamp_ms=None))
    plan = run_plan(recs, listed=[str(r["instrument_name"]) for r in recs[:100]])
    assert plan.expired_dropped == 0
    assert plan.quarantined["reason"].tolist() == ["unparseable_expiry"]


# ---- rule 5: empty -----------------------------------------------------------------


def test_all_rows_expired_is_422_before_coverage() -> None:
    recs = [record(n) for n in book_names(10, expiry="29OCT26")]
    exc = refusal(recs, listed=["BTC-27NOV26-1000-P"])
    assert exc.status == 422


# ---- rule 6: coverage ----------------------------------------------------------------


def test_more_than_one_percent_of_listed_missing_is_refused() -> None:
    recs = records(200)
    listed = [str(r["instrument_name"]) for r in recs] + book_names(3, expiry="25DEC26")
    exc = refusal(recs, listed=listed)
    assert exc.status == 409
    assert "3 of 203" in exc.detail


def test_a_whole_underlying_missing_from_the_book_is_refused() -> None:
    sol = records(180, "USDC")
    trx = [f"TRX_USDC-27NOV26-{i}-P" for i in range(20)]
    exc = refusal(sol, listed=[str(r["instrument_name"]) for r in sol] + trx, currency="USDC")
    assert exc.status == 409


def test_an_underlying_delisted_from_both_endpoints_commits() -> None:
    """Accepted gap (round 3): coverage is against ``listed``, so a name gone
    from both is a delisting."""
    sol = records(100, "USDC")
    assert len(run_plan(sol, currency="USDC").valid) == 100


def test_todays_expiry_still_listed_is_filtered_out_of_coverage() -> None:
    """``get_instruments`` may still list today's 08:00 expiry. Those names
    are 5% of ``listed`` here -- far above 1% -- so only the filter saves
    this book."""
    recs = records(100)
    listed = [str(r["instrument_name"]) for r in recs] + book_names(5, expiry="30OCT26")
    assert len(run_plan(recs, listed=listed).valid) == 100


def test_no_live_listings_is_refused() -> None:
    exc = refusal(records(10), listed=book_names(3, expiry="29OCT26"))
    assert exc.status == 409
    assert "no live listings" in exc.detail


def test_a_quarantined_row_still_counts_as_present() -> None:
    """300 listed, 2 missing (0.67%) and 2 quarantined: a version counting
    only valid rows would see 4/300 = 1.33% missing and refuse. The two are
    LISTED names failing a schema check (mark_iv 15 > 10)."""
    recs = records(298)
    recs[10]["mark_iv_pct"] = 1500.0
    recs[20]["mark_iv_pct"] = 1500.0
    listed = [str(r["instrument_name"]) for r in recs] + book_names(2, expiry="25DEC26")
    plan = run_plan(recs, listed=listed)
    assert len(plan.quarantined) == 2
    assert plan.quarantined["reason"].str.startswith("mark_iv:").all()


# ---- rule 7: quarantine ----------------------------------------------------------------


def test_bad_rows_are_quarantined_and_the_rest_committed() -> None:
    recs = records(200)
    for i in (3, 50, 99, 150, 199):
        recs[i]["mark"] = -1.0
    plan = run_plan(recs)
    assert len(plan.valid) == 195
    assert len(plan.quarantined) == 5
    assert plan.quarantine_share == 0.025


def test_every_row_quarantined_is_a_plan_not_a_refusal() -> None:
    plan = run_plan(records(20, strike=None))
    assert plan.valid.empty
    assert plan.quarantine_share == 1.0
    assert set(plan.quarantined["reason"]) == {"strike:not_nullable"}


def test_an_unmatched_row_is_quarantined() -> None:
    recs = records(100)
    recs.append(
        record(
            "BTC-27NOV26-99999-P",
            expiration_timestamp_ms=None,
            strike=None,
            strike_currency=None,
            right=None,
            contract_size=None,
            tick_size=None,
            min_trade_amount=None,
        )
    )
    plan = run_plan(recs, listed=[str(r["instrument_name"]) for r in recs[:100]])
    assert plan.quarantined["instrument_name"].tolist() == ["BTC-27NOV26-99999-P"]
    assert plan.quarantined["reason"].tolist() == ["strike:not_nullable"]


def test_reason_precedence_invalid_beats_wrong_book_beats_expiry_beats_schema() -> None:
    recs = records(400)
    recs[1].update(invalid_fields=["bid=nan"], premium_currency="ETH", mark=-1.0)
    recs[2].update(premium_currency="ETH", expiration_timestamp_ms=0, mark=-1.0)
    recs[3].update(expiration_timestamp_ms=0, mark=-1.0)
    recs[4].update(mark=-1.0, strike=None)
    plan = run_plan(recs)
    reasons = dict(zip(plan.quarantined.index, plan.quarantined["reason"], strict=True))
    assert reasons == {
        1: "invalid_value",
        2: "wrong_book",
        3: "unparseable_expiry",
        4: "strike:not_nullable",
    }


def test_a_duplicate_that_also_fails_a_column_gets_the_column_reason() -> None:
    recs = records(300)
    recs.append(record(str(recs[7]["instrument_name"]), mark=-1.0))
    plan = run_plan(recs)
    reasons = dict(zip(plan.quarantined.index, plan.quarantined["reason"], strict=True))
    assert reasons[300] == "mark:greater_than_or_equal_to(0.0)"
    assert reasons[7] == "duplicate_instrument"


def test_a_failure_with_no_row_index_refuses_with_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame-level failure that names no row cannot be quarantined; letting
    it pass would commit unchecked rows. Injected by smuggling a working
    column into the validated frame (strict schema -> column_in_schema)."""
    monkeypatch.setattr(contract, "STORED_COLUMNS", (*contract.STORED_COLUMNS, "invalid_fields"))
    assert refusal(records(10)).status == 500


# ---- rule 8: previous day ---------------------------------------------------------


@pytest.mark.parametrize(
    ("captured", "expected"),
    [((), None), ({DAY - dt.timedelta(days=3)}, False), ({DAY - dt.timedelta(days=1)}, True)],
)
def test_previous_partition_exists(captured: set[dt.date], expected: bool | None) -> None:
    assert run_plan(records(5), captured=captured).previous_partition_exists is expected


def test_previous_partition_is_reported_on_already_captured() -> None:
    plan = run_plan(records(5), captured={DAY, DAY - dt.timedelta(days=2)})
    assert plan.previous_partition_exists is False


# ---- the quarantine document --------------------------------------------------------


def test_the_quarantine_joins_each_row_to_its_own_wire_payload() -> None:
    """Expired rows come BEFORE the injected row, and it is not at position 0:
    a ``reset_index`` anywhere would flag an innocent row, which the exact
    list check below catches before the payload check could pass vacuously."""
    expired = [record(n) for n in book_names(3, expiry="29OCT26")]
    live = records(200)
    live[7]["mark"] = -1.0
    recs = expired + live
    plan = run_plan(recs, listed=[str(r["instrument_name"]) for r in live])
    doc = quarantine_document(dataset="deribit_options_btc", plan=plan, records=recs, code_sha="x")
    rows = doc["rows"]
    assert isinstance(rows, list)
    assert [r["instrument_name"] for r in rows] == [live[7]["instrument_name"]]
    assert rows[0]["payload"] == live[7]
    assert rows[0]["payload"]["instrument_name"] == live[7]["instrument_name"]


def test_the_quarantine_key_is_per_capture() -> None:
    assert (
        quarantine_key("deribit_options_btc", DAY, 1793364731777)
        == "ops/quarantine/deribit_options_btc/2026-10-30/1793364731777.json"
    )


def test_prepare_quarantine_keys_on_the_earliest_observation() -> None:
    recs = records(200)
    recs[0]["creation_timestamp_ms"] = CREATED_MS - 5
    recs[1]["mark"] = -1.0
    plan = run_plan(recs)
    parts = prepare_quarantine(dataset="d", plan=plan, records=recs, code_sha="abc")
    assert parts is not None
    assert parts.key == f"ops/quarantine/d/2026-10-30/{CREATED_MS - 5}.json"
    assert parts.doc["code_sha"] == "abc"
    assert (
        prepare_quarantine(dataset="d", plan=run_plan(records(3)), records=[], code_sha="") is None
    )


def test_a_non_serialisable_quarantine_is_a_500_refusal() -> None:
    with pytest.raises(DeribitRefusal) as caught:
        serialize_quarantine({"rows": [{"payload": {"bid": float("nan")}}]})
    assert caught.value.status == 500
    with pytest.raises(DeribitRefusal):
        serialize_quarantine({"rows": {1, 2}})


def test_log_records_are_one_per_row_with_the_payload_last_as_json() -> None:
    doc: dict[str, object] = {
        "rows": [
            {"instrument_name": "A", "reason": "r1", "payload": {"bid": None}},
            {"instrument_name": "B", "reason": "r2", "payload": {"bid": 1.5}},
        ]
    }
    lines = quarantine_log_records(doc, "cap")
    assert [list(line) for line in lines] == [
        ["capture", "instrument_name", "reason", "payload"]
    ] * 2
    assert [json.loads(line["payload"]) for line in lines] == [{"bid": None}, {"bid": 1.5}]
    assert {line["capture"] for line in lines} == {"cap"}


# ---- the payload shape check -------------------------------------------------------


def _book_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "instrument_name": "BTC-27NOV26-80000-P",
        "creation_timestamp": CREATED_MS,
        "bid_price": 0.01,
        "mark_iv": 50,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "payload",
    [
        {"not": "a list"},
        [None],
        [_book_row(instrument_name=None)],
        [{"creation_timestamp": CREATED_MS}],
        [_book_row(creation_timestamp="1793364731777")],
        [_book_row(creation_timestamp=float("nan"))],
        [_book_row(creation_timestamp=True)],
        [_book_row(creation_timestamp=10**400)],
        [_book_row(creation_timestamp=None)],
    ],
)
def test_structural_book_faults_fail_the_book(payload: object) -> None:
    with pytest.raises(DeribitApiError):
        check_book_rows(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bid_price", True),
        ("bid_price", "0.01"),
        ("bid_price", float("nan")),
        ("bid_price", float("inf")),
        ("mark_price", 10**400),
        ("base_currency", 5),
    ],
)
def test_field_faults_cost_one_field(field: str, value: object) -> None:
    (row,) = check_book_rows([_book_row(**{field: value})])
    assert row[field] is None  # type: ignore[literal-required]
    assert list(row["invalid"]) == [field]
    assert repr(row["invalid"][field]) == repr(value)


def test_integers_become_floats_and_absences_stay_none() -> None:
    (row,) = check_book_rows([_book_row()])
    assert row["mark_iv"] == 50.0 and isinstance(row["mark_iv"], float)
    assert row["ask_price"] is None
    assert row["invalid"] == {}


@pytest.mark.parametrize("value", [True, 10**400, 1.5, "1793433600000"])
def test_a_bad_instrument_expiry_is_a_field_fault(value: object) -> None:
    rows, dropped = check_instrument_rows(
        [{"instrument_name": "BTC-27NOV26-80000-P", "expiration_timestamp": value}]
    )
    assert dropped == 0
    assert rows[0]["expiration_timestamp"] is None
    assert "expiration_timestamp" in rows[0]["invalid"]


def test_malformed_instrument_rows_are_dropped_and_counted() -> None:
    rows, dropped = check_instrument_rows(
        [None, {"strike": 1.0}, {"instrument_name": "BTC-27NOV26-80000-P", "strike": 1}]
    )
    assert dropped == 2
    assert [r["instrument_name"] for r in rows] == ["BTC-27NOV26-80000-P"]


def test_a_non_list_instruments_payload_fails_the_book() -> None:
    with pytest.raises(DeribitApiError):
        check_instrument_rows({"result": []})


def test_the_books_are_keyed_uppercase_and_name_their_datasets_once() -> None:
    assert {k: b.dataset for k, b in DERIBIT_BOOKS.items()} == {
        "BTC": "deribit_options_btc",
        "ETH": "deribit_options_eth",
        "USDC": "deribit_options_usdc",
    }
    assert all(k == b.currency for k, b in DERIBIT_BOOKS.items())


def test_an_empty_frame_is_refused() -> None:
    """The wire's ``min_length=1``, for ``--local``, which has no wire."""
    assert refusal([]).status == 422


# ---- review round 1, test strength ------------------------------------------------------


def test_the_quarantine_share_is_over_live_rows_only() -> None:
    """Expired rows are dropped before the share is taken: 2 of 100 live is
    2%, not 2 of 150."""
    live = records(100)
    live[1]["mark"] = -1.0
    live[2]["mark"] = -1.0
    expired = [record(n) for n in book_names(50, expiry="29OCT26")]
    plan = run_plan(live + expired, listed=[str(r["instrument_name"]) for r in live])
    assert plan.expired_dropped == 50
    assert plan.quarantine_share == 0.02


def test_unparseable_listed_names_still_count_for_coverage() -> None:
    """A listed name whose date cannot be read is kept, not filtered as
    expired: 2 of 102 missing is 1.96%."""
    recs = records(100)
    listed = [str(r["instrument_name"]) for r in recs] + ["BTC-PERPETUAL-X", "BTC-PERPETUAL-Y"]
    assert refusal(recs, listed=listed).status == 409


def test_coverage_at_exactly_one_percent_missing_commits() -> None:
    recs = records(100)
    assert len(run_plan(recs[:99], listed=[str(r["instrument_name"]) for r in recs]).valid) == 99


@pytest.mark.parametrize(("mismatched", "refused"), [(51, True), (50, False)])
def test_the_wrong_book_threshold_is_strictly_above_half(mismatched: int, refused: bool) -> None:
    recs = records(100, "ETH")
    for row in recs[:mismatched]:
        row["premium_currency"] = "BTC"
    if refused:
        assert refusal(recs, currency="ETH").status == 409
    else:
        plan = run_plan(recs, currency="ETH")
        assert len(plan.valid) == 100 - mismatched


def test_an_expiry_at_ms_max_is_out_of_window() -> None:
    """The window is half-open: exactly 2100-01-01 is outside it. The name
    spells no date, so nothing else can catch it."""
    recs = records(100)
    recs.append(record("ODDNAME", premium_currency="BTC", expiration_timestamp_ms=contract.MS_MAX))
    plan = run_plan(recs)
    assert plan.quarantined["reason"].tolist() == ["unparseable_expiry"]


def test_exactly_thirty_minutes_old_is_still_fresh() -> None:
    plan = run_plan(records(10, creation_timestamp_ms=NOW_MS - 30 * 60_000))
    assert len(plan.valid) == 10


def test_an_expiry_equal_to_the_observation_is_expired() -> None:
    recs = records(100)
    recs.append(record("ODDNAME", premium_currency="BTC", expiration_timestamp_ms=CREATED_MS))
    plan = run_plan(recs, listed=[str(r["instrument_name"]) for r in recs[:100]])
    assert plan.expired_dropped == 1
    assert len(plan.valid) == 100


def test_the_valid_frame_is_the_schemas_coerced_output() -> None:
    """The second validation is what coerces ``snapshot_date`` to
    ``datetime64[ns]`` (the frame builds it at microseconds): every capture
    then writes the same Delta column type."""
    plan = run_plan(records(10))
    assert plan.valid["snapshot_date"].dtype == "datetime64[ns]"
    coerced = contract.DeribitOptionsSchema.validate(plan.valid.copy())
    assert (plan.valid.dtypes == coerced.dtypes).all()


# ---- review round 2 -------------------------------------------------------------------


def test_coverage_is_checked_before_the_quarantine_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 6 before rule 7, as the ADR orders them: an incomplete capture is a
    409 even when the split would fail with a non-attributable 500."""
    monkeypatch.setattr(contract, "STORED_COLUMNS", (*contract.STORED_COLUMNS, "invalid_fields"))
    recs = records(100)
    listed = [str(r["instrument_name"]) for r in recs] + book_names(5, expiry="25DEC26")
    assert refusal(recs, listed=listed).status == 409


def test_exactly_0805_utc_keeps_every_row_and_a_millisecond_earlier_is_425() -> None:
    at = dt.datetime(2026, 10, 30, 8, 5, tzinfo=dt.UTC)
    assert len(run_plan(records(10, creation_timestamp_ms=ms(at)), now=at).valid) == 10
    before = records(10, creation_timestamp_ms=ms(at) - 1)
    assert refusal(before, now=at).status == 425


def test_exactly_five_minutes_in_the_future_is_still_fresh() -> None:
    plan = run_plan(records(10, creation_timestamp_ms=NOW_MS + 5 * 60_000))
    assert len(plan.valid) == 10


def test_an_empty_invalid_fields_list_is_not_a_fault() -> None:
    plan = run_plan(records(10, invalid_fields=[]))
    assert len(plan.valid) == 10


def test_an_expiry_at_ms_min_is_in_window_and_expired() -> None:
    """2016-01-01 is inside the half-open window: an undated name with that
    expiry is a real, long-expired row -- dropped, not quarantined."""
    recs = records(100)
    recs.append(record("ODDNAME", premium_currency="BTC", expiration_timestamp_ms=contract.MS_MIN))
    plan = run_plan(recs, listed=[str(r["instrument_name"]) for r in recs[:100]])
    assert plan.expired_dropped == 1
    assert plan.quarantined.empty
