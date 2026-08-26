from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from tail_lab.contracts.option_chain import DATASET, MAX_TENOR_DAYS
from tail_lab.ingestion.option_chain import (
    QUARANTINE_DATASET,
    cboe_symbol,
    ingest_option_chain,
    parse_cboe_chain,
    parse_osi_symbol,
    sweep_to_records,
    validate_and_quarantine,
)
from tail_lab.lake.store import DeltaLakeStore

# A quote session, and an expiry 30 days out that every well-formed row uses.
QUOTE_DAY = "2026-08-26"
NEAR_EXPIRY = "260925"  # 2026-09-25, 30 days out
SPOT = 100.0


def _contract(osi: str, **overrides: Any) -> dict[str, Any]:
    """One Cboe contract record, shaped exactly like the live CDN's."""
    row: dict[str, Any] = {
        "option": osi,
        "bid": 1.20,
        "ask": 1.30,
        "iv": 0.2415,
        "open_interest": 811.0,
        "volume": 42.0,
        "delta": -0.19,
        "gamma": 0.01,
        "vega": 0.08,
        "theta": -0.04,
        "rho": -0.01,
        "theo": 1.25,
    }
    row.update(overrides)
    return row


def _payload(*contracts: dict[str, Any], symbol: str = "SPY", spot: float = SPOT) -> dict[str, Any]:
    return {
        "timestamp": f"{QUOTE_DAY} 20:30:28",
        "symbol": symbol,
        "data": {
            "symbol": symbol,
            "security_type": "stock",
            "current_price": spot,
            "close": spot,
            "last_trade_time": f"{QUOTE_DAY}T16:00:00",
            "options": list(contracts),
        },
    }


def _put(strike: float, expiry: str = NEAR_EXPIRY, **overrides: Any) -> dict[str, Any]:
    return _contract(f"SPY{expiry}P{int(strike * 1000):08d}", **overrides)


def _call(strike: float, expiry: str = NEAR_EXPIRY, **overrides: Any) -> dict[str, Any]:
    return _contract(f"SPY{expiry}C{int(strike * 1000):08d}", **overrides)


# ---- OSI symbol parsing ----------------------------------------------------


def test_parse_osi_symbol_splits_a_real_contract() -> None:
    assert parse_osi_symbol("SPY260826P00500000") == ("SPY", dt.date(2026, 8, 26), "P", 500.0)


def test_parse_osi_symbol_handles_a_fractional_strike_and_long_root() -> None:
    """Strikes carry three implied decimals, and roots are variable-length —
    parsing from the left would mis-split either one."""
    assert parse_osi_symbol("GOOGL270115P00187500") == (
        "GOOGL",
        dt.date(2027, 1, 15),
        "P",
        187.5,
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "garbage",
        "SPY260826X00500000",  # not a call or a put
        "SPY2608AAP00500000",  # non-numeric date
        "SPY260826P0050000A",  # non-numeric strike
        "SPY261332P00500000",  # month 13
        "260826P00500000",  # no root
    ],
)
def test_parse_osi_symbol_returns_none_for_garbage(bad: str) -> None:
    """A garbled contract must be countable, not fatal: the sweep has one
    chance a day and a single bad row cannot be allowed to end it."""
    assert parse_osi_symbol(bad) is None


def test_cboe_symbol_underscores_cash_indices_only() -> None:
    assert cboe_symbol("SPX") == "_SPX"
    assert cboe_symbol("^VIX") == "_VIX"
    assert cboe_symbol("SPY") == "SPY"
    assert cboe_symbol("aapl") == "AAPL"


# ---- slicing ---------------------------------------------------------------


def test_parse_keeps_puts_and_drops_calls() -> None:
    frame, unparsed = parse_cboe_chain(_payload(_put(95.0), _call(95.0)))
    assert unparsed == 0
    assert len(frame) == 1
    assert frame.iloc[0]["strike"] == 95.0


def test_parse_drops_strikes_outside_the_moneyness_band() -> None:
    """0.40-1.05 of spot. A $20 strike on a $100 name is a lottery ticket
    nobody prices; a $130 strike is discounted stock."""
    frame, _ = parse_cboe_chain(
        _payload(_put(20.0), _put(45.0), _put(100.0), _put(105.0), _put(130.0))
    )
    assert sorted(frame["strike"].tolist()) == [45.0, 100.0, 105.0]


def test_parse_drops_tenors_past_the_cap_but_keeps_the_boundary() -> None:
    quote = dt.date.fromisoformat(QUOTE_DAY)
    at_cap = (quote + dt.timedelta(days=MAX_TENOR_DAYS)).strftime("%y%m%d")
    past_cap = (quote + dt.timedelta(days=MAX_TENOR_DAYS + 1)).strftime("%y%m%d")
    frame, _ = parse_cboe_chain(_payload(_put(95.0, expiry=at_cap), _put(96.0, expiry=past_cap)))
    assert frame["strike"].tolist() == [95.0]


def test_parse_drops_already_expired_contracts() -> None:
    """An expiry before the quote session is a stale row in the CDN, not a
    negative-tenor option."""
    stale = (dt.date.fromisoformat(QUOTE_DAY) - dt.timedelta(days=1)).strftime("%y%m%d")
    frame, _ = parse_cboe_chain(_payload(_put(95.0, expiry=stale), _put(96.0)))
    assert frame["strike"].tolist() == [96.0]


def test_quote_date_comes_from_the_payload_not_the_clock() -> None:
    """Point-in-time correctness (docs/adr/0009) starts here: a sweep that
    runs at 02:00 UTC must still stamp the *session* the quotes came from,
    or every downstream as-of read is off by a day."""
    frame, _ = parse_cboe_chain(_payload(_put(95.0)))
    assert frame.iloc[0]["quote_date"] == pd.Timestamp(QUOTE_DAY)


def test_zero_iv_is_read_as_absence_and_takes_its_greeks_with_it() -> None:
    """Cboe zero-fills the greek block when it cannot compute one. A listed
    put cannot have 0.0 implied vol, so storing the zero as a number is the
    exact error docs/DATA_VERDICTS.md caught in the vendor file — it must
    arrive typed as missing, and delta/theo alongside it, because Cboe
    computes the three together."""
    frame, _ = parse_cboe_chain(_payload(_put(95.0, iv=0.0, delta=0.0, theo=0.0), _put(96.0)))
    dead = frame[frame["strike"] == 95.0].iloc[0]
    live = frame[frame["strike"] == 96.0].iloc[0]
    assert pd.isna(dead["iv"]) and pd.isna(dead["delta"]) and pd.isna(dead["theo"])
    assert live["iv"] == pytest.approx(0.2415)
    assert live["delta"] == pytest.approx(-0.19)


def test_parse_counts_unparseable_contracts_without_dropping_the_rest() -> None:
    frame, unparsed = parse_cboe_chain(_payload(_contract("!!broken!!"), _put(95.0)))
    assert unparsed == 1
    assert len(frame) == 1


def test_parse_returns_empty_for_a_payload_missing_its_spot() -> None:
    """No spot means no moneyness, so every filter is undefined. Better an
    honest empty frame the sweep counts as a failure than rows sliced
    against a guess."""
    payload = _payload(_put(95.0))
    payload["data"]["current_price"] = None
    frame, _ = parse_cboe_chain(payload)
    assert frame.empty


# ---- validation ------------------------------------------------------------


def test_a_row_with_no_ask_is_quarantined_not_kept() -> None:
    """A zero *bid* is a real market state (nobody bids for a far-OTM put);
    a row with no ask is not a quote at all."""
    frame, _ = parse_cboe_chain(_payload(_put(95.0, bid=0.0, ask=0.0), _put(96.0)))
    valid, quarantined = validate_and_quarantine(frame)
    assert valid["strike"].tolist() == [96.0]
    assert quarantined["strike"].tolist() == [95.0]


def test_valid_rows_survive_validation_untouched() -> None:
    frame, _ = parse_cboe_chain(_payload(_put(95.0), _put(96.0)))
    valid, quarantined = validate_and_quarantine(frame)
    assert len(valid) == 2
    assert quarantined.empty


# ---- orchestration ---------------------------------------------------------


def _fetcher(**by_symbol: dict[str, Any]) -> Any:
    def fetch(symbol: str) -> dict[str, Any]:
        try:
            return by_symbol[symbol.upper()]
        except KeyError as exc:
            raise RuntimeError(f"chain unavailable for {symbol}") from exc

    return fetch


def test_ingest_commits_one_partition_for_the_whole_universe(tmp_path: Path) -> None:
    """One partition per day, not one per symbol: a half-finished sweep must
    not look complete to an as-of read (docs/adr/0009)."""
    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(
        store,
        ["SPY", "QQQ"],
        ingest_date=dt.date(2026, 8, 26),
        fetch=_fetcher(
            SPY=_payload(_put(95.0), _put(96.0), symbol="SPY"),
            QQQ=_payload(_put(95.0), symbol="QQQ"),
        ),
    )

    assert result.valid_rows == 3
    assert result.symbols_ok == ("SPY", "QQQ")
    assert result.quote_date == dt.date.fromisoformat(QUOTE_DAY)
    stored = store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))
    assert sorted(stored["underlying"].unique()) == ["QQQ", "SPY"]


def test_one_dead_chain_does_not_cost_the_others_their_snapshot(tmp_path: Path) -> None:
    """The whole point of the schedule is that today's quotes are only
    available today — a single failing symbol must not abort the sweep."""
    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(
        store,
        ["SPY", "BROKEN", "QQQ"],
        ingest_date=dt.date(2026, 8, 26),
        fetch=_fetcher(
            SPY=_payload(_put(95.0), symbol="SPY"),
            QQQ=_payload(_put(95.0), symbol="QQQ"),
        ),
    )

    assert result.symbols_ok == ("SPY", "QQQ")
    assert result.symbols_failed == ("BROKEN",)
    assert result.valid_rows == 2


def test_quarantined_rows_are_persisted_for_inspection(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(
        store,
        ["SPY"],
        ingest_date=dt.date(2026, 8, 26),
        fetch=_fetcher(SPY=_payload(_put(95.0, bid=0.0, ask=0.0), _put(96.0), symbol="SPY")),
    )

    assert result.valid_rows == 1
    assert result.quarantined_rows == 1
    quarantined = store.read_bronze_as_of(QUARANTINE_DATASET, dt.date(2026, 8, 26))
    assert quarantined["strike"].tolist() == [95.0]


def test_sweep_to_records_emits_json_ready_rows() -> None:
    """The credential-free half of the scheduled sweep (docs/adr/0020): the
    workflow produces these, the app writes them."""
    records = sweep_to_records(["SPY"], fetch=_fetcher(SPY=_payload(_put(95.0), symbol="SPY")))
    assert len(records) == 1
    assert records[0]["quote_date"] == QUOTE_DAY
    assert records[0]["expiration"] == "2026-09-25"
    assert records[0]["underlying"] == "SPY"


def test_sweep_to_records_skips_a_failing_symbol() -> None:
    records = sweep_to_records(
        ["SPY", "BROKEN"], fetch=_fetcher(SPY=_payload(_put(95.0), symbol="SPY"))
    )
    assert [r["underlying"] for r in records] == ["SPY"]
