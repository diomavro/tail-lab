from __future__ import annotations

import datetime as dt

import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.errors import SchemaErrors

from tail_lab.contracts.option_chain import (
    CALL_DATASET,
    CALL_MONEYNESS_MAX,
    CALL_MONEYNESS_MIN,
    CALL_QUARANTINE_DATASET,
    DATASET,
    MAX_TENOR_DAYS,
    MONEYNESS_MAX,
    MONEYNESS_MIN,
    QUARANTINE_DATASET,
    WINGS,
    OptionCallChainSnapshotSchema,
    OptionChainSnapshotSchema,
    missing_sessions,
    split_valid_and_quarantined,
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


# ---- whole missing sessions --------------------------------------------------

_D = dt.date


def test_a_trading_day_with_no_partition_is_reported() -> None:
    """The case the verifier missed: 2026-09-23 had no partition at all, and
    walking only the partitions that exist can never see that."""
    captured = [_D(2026, 9, 21), _D(2026, 9, 22), _D(2026, 9, 24)]
    trading = [_D(2026, 9, 21), _D(2026, 9, 22), _D(2026, 9, 23), _D(2026, 9, 24), _D(2026, 9, 25)]

    assert missing_sessions(captured, trading) == [_D(2026, 9, 23)]


def test_an_acknowledged_lost_session_is_not_reported() -> None:
    captured = [_D(2026, 9, 22), _D(2026, 9, 24)]
    trading = [_D(2026, 9, 22), _D(2026, 9, 23), _D(2026, 9, 24), _D(2026, 9, 25)]

    assert missing_sessions(captured, trading, acknowledged={_D(2026, 9, 23)}) == []


def test_the_newest_completed_session_is_left_to_the_forward_check() -> None:
    """It may still be captured tonight (or by the morning catch-up), so
    flagging it here would alarm on a healthy evening."""
    captured = [_D(2026, 9, 23), _D(2026, 9, 24)]
    trading = [_D(2026, 9, 23), _D(2026, 9, 24), _D(2026, 9, 25)]

    assert missing_sessions(captured, trading) == []


def test_holidays_and_days_before_the_window_are_not_gaps() -> None:
    """A holiday is not in Nasdaq's trading days, so it cannot be missing; days
    older than the oldest captured partition are outside what was inspected."""
    captured = [_D(2026, 9, 4), _D(2026, 9, 8)]
    trading = [_D(2026, 9, 1), _D(2026, 9, 4), _D(2026, 9, 8), _D(2026, 9, 9)]  # 09-07 Labor Day

    assert missing_sessions(captured, trading) == []


def test_nothing_to_compare_reports_nothing() -> None:
    assert missing_sessions([], [_D(2026, 9, 23)]) == []
    assert missing_sessions([_D(2026, 9, 23)], []) == []


def test_an_unreadable_trading_day_is_ignored_not_a_crash() -> None:
    """Nasdaq's parser keeps an unreadable date as NaT; reaching ``max`` with
    it crashed the verifier into a false chain alert (fix-batch review)."""
    captured = [_D(2026, 9, 22), _D(2026, 9, 24)]
    trading = [_D(2026, 9, 22), _D(2026, 9, 23), pd.NaT, _D(2026, 9, 24), _D(2026, 9, 25)]

    assert missing_sessions(captured, trading) == [_D(2026, 9, 23)]  # type: ignore[arg-type]


def test_a_null_captured_session_is_ignored_not_a_crash() -> None:
    captured = [pd.NaT, _D(2026, 9, 22), _D(2026, 9, 24)]
    trading = [_D(2026, 9, 22), _D(2026, 9, 23), _D(2026, 9, 24), _D(2026, 9, 25)]

    assert missing_sessions(captured, trading) == [_D(2026, 9, 23)]  # type: ignore[arg-type]


# ---- the call wing (docs/adr/0031) ------------------------------------------


def test_every_chain_dataset_name_lives_once_in_the_contract() -> None:
    """Four bronze names, one home. The put quarantine name used to be spelt
    twice (adapter and route), so the two writers could drift apart; the call
    names must not start life that way. Pinned literally because a reader
    that unions the wrong pair corrupts a backtest silently."""

    assert DATASET == "option_chain_snapshot"
    assert QUARANTINE_DATASET == "option_chain_snapshot__quarantine"
    assert CALL_DATASET == "option_chain_call_snapshot"
    assert CALL_QUARANTINE_DATASET == "option_chain_call_snapshot__quarantine"


def test_each_wing_maps_to_its_own_dataset_schema_and_band() -> None:
    """The right is mapped to (dataset, quarantine, schema, band) in ONE
    place. A call band of 0.80-2.50 is log-symmetric with the put floor
    (1 / 0.40), wide enough to hold a 10-delta 30-day call on a sigma~0.9
    name (K ~ 1.44 S), which a 1.30 cap would lose for good."""

    put, call = WINGS["P"], WINGS["C"]
    assert (put.dataset, put.quarantine_dataset, put.schema) == (
        DATASET,
        QUARANTINE_DATASET,
        OptionChainSnapshotSchema,
    )
    assert (put.moneyness_min, put.moneyness_max) == (MONEYNESS_MIN, MONEYNESS_MAX)
    assert (call.dataset, call.quarantine_dataset, call.schema) == (
        CALL_DATASET,
        CALL_QUARANTINE_DATASET,
        OptionCallChainSnapshotSchema,
    )
    assert (call.moneyness_min, call.moneyness_max) == (CALL_MONEYNESS_MIN, CALL_MONEYNESS_MAX)
    assert (CALL_MONEYNESS_MIN, CALL_MONEYNESS_MAX) == (0.80, 2.50)


def test_a_call_delta_must_be_non_negative_and_a_put_delta_non_positive() -> None:
    """The one column on which the wings disagree. Each schema refuses the
    other wing's sign, so a call row that reached the put dataset (or the
    reverse) is quarantined rather than stored as a put."""

    OptionCallChainSnapshotSchema.validate(_frame(_row(delta=0.62)), lazy=True)
    OptionCallChainSnapshotSchema.validate(_frame(_row(delta=1.0)), lazy=True)
    with pytest.raises(SchemaErrors):
        OptionCallChainSnapshotSchema.validate(_frame(_row(delta=-0.19)), lazy=True)
    with pytest.raises(SchemaErrors):
        OptionChainSnapshotSchema.validate(_frame(_row(delta=0.62)), lazy=True)


@pytest.mark.parametrize("delta", [None, 0.0, 1.0])
def test_the_call_contract_keeps_null_zero_and_unit_deltas(delta: float | None) -> None:
    """Measured on the 2026-10-26 live call body: 1,096 calls with a null
    delta (Cboe zero-filled, mapped to null) and 338 with delta exactly 0.0
    (deep OTM). A contract that refused either would quarantine that many
    real rows every session."""
    OptionCallChainSnapshotSchema.validate(_frame(_row(delta=delta)), lazy=True)


_WING_DELTA_EDGES = [
    # (schema, kept at the edge, refused just past it)
    (OptionCallChainSnapshotSchema, 0.0, -0.0001),
    (OptionCallChainSnapshotSchema, 1.0, 1.0001),
    (OptionChainSnapshotSchema, 0.0, 0.0001),
    (OptionChainSnapshotSchema, -1.0, -1.0001),
]


@pytest.mark.parametrize(
    ("schema", "edge", "past"),
    _WING_DELTA_EDGES,
    ids=["call-low", "call-high", "put-high", "put-low"],
)
def test_each_wing_keeps_its_delta_edge_and_refuses_just_past_it(
    schema: type[OptionChainSnapshotSchema], edge: float, past: float
) -> None:
    """Below MAX_WRONG_SIGN_SHARE a wrong-sign row is caught by the contract
    alone (ADR 0031 §5: below a fifth, only the bad rows are quarantined), so
    each wing's bound must sit exactly at zero -- for the puts as for the
    calls."""
    schema.validate(_frame(_row(delta=edge)), lazy=True)
    with pytest.raises(SchemaErrors):
        schema.validate(_frame(_row(delta=past)), lazy=True)


@pytest.mark.parametrize(
    ("right", "deltas", "share"),
    [
        ("P", [-0.2, -0.5, None, 0.0], 0.0),  # a correct put body
        ("P", [0.3, 0.6, None, 0.0], 1.0),  # calls sent to the put route
        ("P", [-0.2, 0.4], 0.5),
        ("C", [0.3, 0.6, None], 0.0),
        ("C", [-0.2, -0.5, 0.1], 2 / 3),
        ("C", [None, 0.0], 0.0),  # no signed delta: no evidence either way
    ],
)
def test_wrong_sign_share_counts_only_rows_with_a_signed_delta(
    right: str, deltas: list[float | None], share: float
) -> None:
    frame = pd.DataFrame({"delta": deltas})
    assert WINGS["C" if right == "C" else "P"].wrong_sign_share(frame) == pytest.approx(share)


@pytest.mark.parametrize(
    ("wrong", "misrouted"), [(0, False), (2, False), (20, False), (21, True), (29, True)]
)
def test_misrouted_is_more_than_a_fifth_of_one_symbols_signed_deltas(
    wrong: int, misrouted: bool
) -> None:
    """Pins MAX_WRONG_SIGN_SHARE from both sides. A sign glitch on a few rows
    (2 in 100; FXI has 176 signed puts) is not misrouting -- quarantining the
    symbol would count it against the floor, and three such chains refused a
    put session. 21 in 100 is refused, and 29 -- the thinnest symbol of a
    measured both-wings body (SLV on the call route) -- must be too."""
    deltas = [-0.2] * (100 - wrong) + [0.3] * wrong + [None, 0.0]
    frame = _frame(
        *(_row(underlying="spy", strike=500.0 + i, delta=d) for i, d in enumerate(deltas))
    )
    assert WINGS["P"].split(frame).misrouted == (("SPY",) if misrouted else ())


def test_misrouted_symbols_are_named_sorted_not_in_body_order() -> None:
    """The tuple is what the route's ``symbols_misrouted``, the log line and
    the floor's refusal print; a body need not arrive alphabetised."""
    zzz = [_row(underlying="ZZZ", strike=80.0 + i, delta=0.3) for i in range(3)]
    aaa = [_row(underlying="AAA", strike=80.0 + i, delta=0.3) for i in range(3)]
    assert WINGS["P"].split(_frame(*zzz, *aaa)).misrouted == ("AAA", "ZZZ")


def test_one_symbols_wrong_signs_cost_that_symbol_and_no_other() -> None:
    """The share is per SYMBOL. Pooled over a body, one glitching chain among
    many is a small share, and refusing the body whole lost the session on
    the POST path while the local writer kept the rest. Every row of the
    glitching symbol is quarantined -- its null-delta row too, which the
    contract alone would keep -- and nothing of the others is."""
    spy = [_row(strike=600.0 + i) for i in range(50)]
    xle = [_row(underlying="XLE", strike=80.0, delta=0.3), _row(underlying="XLE", delta=None)]
    valid, quarantined, misrouted, misrouted_rows = WINGS["P"].split(_frame(*spy, *xle))

    assert (misrouted, misrouted_rows) == (("XLE",), 2)
    assert set(valid["underlying"]) == {"SPY"}
    assert len(valid) == 50
    assert sorted(quarantined["underlying"]) == ["XLE", "XLE"]


def test_split_without_a_misrouted_symbol_is_the_contract_split() -> None:
    frame = _frame(_row(strike=600.0), _row(strike=610.0, ask=0.0))
    valid, quarantined, misrouted, misrouted_rows = WINGS["P"].split(frame)
    assert (len(valid), len(quarantined), misrouted, misrouted_rows) == (1, 1, (), 0)


def test_a_lower_case_underlying_is_misrouted_whole_all_the_same() -> None:
    """The route builds its frame from the body as sent and never upper-cases
    ``underlying``, so the split's own case-folding is the only defence: calls
    posted as "spy" to the put route must not commit their null-delta rows as
    puts."""
    calls = [_row(underlying="spy", strike=800.0 + i, delta=0.3) for i in range(5)]
    nulls = [_row(underlying="spy", strike=900.0 + i, delta=None) for i in range(3)]
    valid, quarantined, misrouted, misrouted_rows = WINGS["P"].split(_frame(*calls, *nulls))

    assert valid.empty
    assert len(quarantined) == 8
    assert (misrouted, misrouted_rows) == (("SPY",), 8)


def test_the_call_schema_differs_from_the_put_schema_only_in_delta() -> None:
    """Same columns, same key, same strictness -- a subclass, not a copy, so a
    later tightening of the put contract reaches the calls too."""

    put = OptionChainSnapshotSchema.to_schema()
    call = OptionCallChainSnapshotSchema.to_schema()
    assert list(put.columns) == list(call.columns)
    assert call.unique == put.unique
    assert call.strict == put.strict

    def spec(column: pa.Column) -> tuple[str, bool, str]:
        return (str(column.dtype), column.nullable, repr(column.checks))

    differing = [
        name for name in put.columns if spec(put.columns[name]) != spec(call.columns[name])
    ]
    assert differing == ["delta"]


def test_the_split_validates_against_the_schema_it_is_given() -> None:
    """Reused unchanged, the split would hard-code the put schema and
    quarantine every call the sweep collected."""

    calls = _frame(_row(strike=800.0, delta=0.40), _row(strike=810.0, delta=-0.40))
    valid, quarantined = split_valid_and_quarantined(calls, schema=OptionCallChainSnapshotSchema)
    assert valid["strike"].tolist() == [800.0]
    assert quarantined["strike"].tolist() == [810.0]

    valid, quarantined = split_valid_and_quarantined(calls, schema=OptionChainSnapshotSchema)
    assert valid["strike"].tolist() == [810.0]
    assert quarantined["strike"].tolist() == [800.0]
