from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from tail_lab.contracts.option_chain import DATASET, MAX_TENOR_DAYS
from tail_lab.ingestion.option_chain import (
    _FETCH_ATTEMPTS,
    QUARANTINE_DATASET,
    _fetch_with_retry,
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


# ---- per-symbol fetch retry -------------------------------------------------


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(response=response)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backoff is real in production and pointless in a test."""
    import tail_lab.ingestion.option_chain as option_chain_module

    monkeypatch.setattr(option_chain_module.time, "sleep", lambda _s: None)


def test_a_transient_fault_is_retried_and_the_symbol_is_saved() -> None:
    """The exact shape of a 2026-09-04-style blip, one seam earlier than the
    POST retry: a 500 on the first attempt, success on the second."""
    calls: list[int] = []

    def flaky(_symbol: str) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(500)
        return _payload(_put(95.0), symbol="SPY")

    result = _fetch_with_retry(flaky, "SPY")
    assert result["data"]["symbol"] == "SPY"
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("exc", "expected_calls"),
    [
        # A 4xx (other than 429) means Cboe understood and refused -- most
        # often a root this host does not list. Re-sending the identical GET
        # cannot change that answer.
        (_http_error(404), 1),
        # A 5xx means Cboe failed to answer a request it might answer next
        # time, so it gets the full attempt budget.
        (_http_error(503), _FETCH_ATTEMPTS),
        (requests.exceptions.ConnectionError("dns"), _FETCH_ATTEMPTS),
    ],
)
def test_only_faults_a_retry_could_fix_are_retried(exc: Exception, expected_calls: int) -> None:
    """Asserted as a CONTRAST, deliberately, mirroring
    ``test_scripts_chain_snapshot_retry.py``: "a 404 is fetched once" also
    passes with no retry loop at all, so only the 1-vs-N call count actually
    detects the loop's presence."""
    calls: list[int] = []

    def always_fails(_symbol: str) -> dict[str, Any]:
        calls.append(1)
        raise exc

    with pytest.raises(type(exc)):
        _fetch_with_retry(always_fails, "SPY")
    assert len(calls) == expected_calls


def test_a_non_network_error_from_an_injected_fetcher_is_not_retried() -> None:
    """A test/local ``fetch`` callable never raises a ``requests`` exception,
    so it is never mistaken for a transient fault -- one attempt, same as
    before this retry existed."""
    calls: list[int] = []

    def broken(_symbol: str) -> dict[str, Any]:
        calls.append(1)
        raise RuntimeError("chain unavailable")

    with pytest.raises(RuntimeError):
        _fetch_with_retry(broken, "BROKEN")
    assert len(calls) == 1


def test_a_transient_blip_no_longer_costs_the_symbol_its_whole_session(
    tmp_path: Path,
) -> None:
    """End to end through ``ingest_option_chain``: a symbol that fails once
    and then answers now lands in the sweep instead of ``symbols_failed``."""
    store = DeltaLakeStore(tmp_path)
    calls: list[int] = []

    def by_symbol(symbol: str) -> dict[str, Any]:
        if symbol.upper() == "SPY":
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(500)
            return _payload(_put(95.0), symbol="SPY")
        return _payload(_put(95.0), symbol="QQQ")

    result = ingest_option_chain(store, ["SPY", "QQQ"], fetch=by_symbol)

    assert result.symbols_ok == ("SPY", "QQQ")
    assert result.symbols_failed == ()


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


def test_the_partition_is_the_session_not_the_clock(tmp_path: Path) -> None:
    """A scheduled runner drifts. GitHub ran the 21:30 cron at 00:57 the next
    day on the very first sweep, and a clock-derived partition turned that
    into two bugs at once: the session landed under tomorrow's key, and
    tomorrow's real sweep would then no-op against it (bronze is immutable)
    and be lost. The quotes know which session they are."""
    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(
        store, ["SPY"], fetch=_fetcher(SPY=_payload(_put(95.0), symbol="SPY"))
    )

    session = dt.date.fromisoformat(QUOTE_DAY)
    assert result.quote_date == session
    assert f"ingest_date={QUOTE_DAY}" in result.bronze_path
    # And it is readable as of that session, not only as of "today".
    assert len(store.read_bronze_as_of(DATASET, session)) == 1


def test_a_late_rerun_of_the_same_session_no_ops(tmp_path: Path) -> None:
    """Immutability doing its job: the delayed run and the on-time run carry
    the same session, so the second is a no-op rather than a second partition
    that shadows the first."""
    store = DeltaLakeStore(tmp_path)
    payload = _payload(_put(95.0), symbol="SPY")
    first = ingest_option_chain(store, ["SPY"], fetch=_fetcher(SPY=payload))
    second = ingest_option_chain(store, ["SPY"], fetch=_fetcher(SPY=payload))

    assert first.bronze_path == second.bronze_path
    assert len(store.read_bronze_as_of(DATASET, dt.date.fromisoformat(QUOTE_DAY))) == 1
