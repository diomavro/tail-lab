from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from tail_lab.contracts.option_chain import (
    DATASET,
    DEFAULT_SNAPSHOT_SYMBOLS,
    MAX_TENOR_DAYS,
)
from tail_lab.ingestion.option_chain import (
    _FETCH_ATTEMPTS,
    MIN_SYMBOL_FRACTION,
    QUARANTINE_DATASET,
    IncompleteSweepError,
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


def _payload(
    *contracts: dict[str, Any],
    symbol: str = "SPY",
    spot: float = SPOT,
    day: str = QUOTE_DAY,
) -> dict[str, Any]:
    """``day`` exists so a test can serve a STALE session for one symbol, the
    way Cboe's CDN really does over a holiday weekend."""
    return {
        "timestamp": f"{day} 20:30:28",
        "symbol": symbol,
        "data": {
            "symbol": symbol,
            "security_type": "stock",
            "current_price": spot,
            "close": spot,
            "last_trade_time": f"{day}T16:00:00",
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
    available today — a single failing symbol must not abort the sweep.

    The universe SIZE is load-bearing since ``MIN_SYMBOL_FRACTION`` landed,
    and this test used to pass three symbols. The floor is a fraction, so
    "one dead chain" is only tolerable against a realistic universe: at
    production's 24 names one failure is 4.2%, inside the 10% allowed, while
    at three names it is 33% and the floor correctly refuses. The three-symbol
    version was the toy universe misrepresenting the invariant — the docstring
    this test defends says "the other sixty-nine", not the other two — so the
    fix is a representative universe, not a weaker floor. The competing
    invariant is pinned directly below.
    """
    live = [f"SYM{i:02d}" for i in range(23)]
    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(
        store,
        [*live, "BROKEN"],
        ingest_date=dt.date(2026, 8, 26),
        fetch=_fetcher(**{name: _payload(_put(95.0), symbol=name) for name in live}),
    )

    assert result.symbols_ok == tuple(live)
    assert result.symbols_failed == ("BROKEN",)
    assert result.valid_rows == 23
    assert result.committed is True


def test_the_floor_tolerates_exactly_two_lost_chains_at_the_real_universe() -> None:
    """The two tests around this one pull in opposite directions — "one dead
    chain must not abort the sweep" against "a partial sweep must not define
    the session" — and only ``MIN_SYMBOL_FRACTION`` keeps both true at once.

    An earlier version asserted ``required <= 23 and required > 15`` and
    claimed in its docstring that a future change to the constant would fail
    here. That claim was false: those bounds admit any fraction from 0.626 to
    0.958, i.e. anything tolerating 1 to 8 dead chains. It also hardcoded 24,
    so widening the universe would have left it asserting about a size that no
    longer existed.

    So: read the real universe, and pin the tolerated count exactly. Changing
    either the constant or the universe fails this test by design — go read
    the two invariants below and decide deliberately rather than drifting.
    """
    universe = len(DEFAULT_SNAPSHOT_SYMBOLS)
    tolerated = universe - max(1, math.ceil(universe * MIN_SYMBOL_FRACTION))
    assert (universe, tolerated) == (24, 2), (
        f"universe={universe}, fraction={MIN_SYMBOL_FRACTION} tolerates {tolerated} lost "
        "chains. Both invariants below depend on this number: one dead chain must still "
        "commit the rest, and the 15-of-24 sweep Cboe really served must be refused."
    )


def test_a_small_hand_run_universe_refuses_on_a_single_dead_chain(tmp_path: Path) -> None:
    """The behaviour change the floor introduced for HAND runs, pinned so it
    is a decision rather than a surprise.

    ``ceil`` means a universe below ten tolerates zero losses, so
    ``make ingest-option-chain CHAIN_SYMBOLS=spy,qqq`` with one chain dead now
    writes nothing where it used to write the survivor. That is the intended
    direction — a two-symbol partition would permanently define the session
    with half the data, and the operator can simply re-run — but it IS a
    change, it is reachable from a documented entry point, and no test covered
    it after the three-symbol case was reframed to a realistic universe.
    """
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(IncompleteSweepError):
        ingest_option_chain(
            store,
            ["SPY", "BROKEN", "QQQ"],
            ingest_date=dt.date(2026, 8, 26),
            fetch=_fetcher(
                SPY=_payload(_put(95.0), symbol="SPY"),
                QQQ=_payload(_put(95.0), symbol="QQQ"),
            ),
        )


def test_a_partial_sweep_refuses_to_create_the_partition(tmp_path: Path) -> None:
    """The competing invariant to the test above, and the reason the floor
    counts SYMBOLS rather than rows.

    Bronze is immutable, so the first write of a session is the only one: a
    partial sweep does not under-report, it permanently *defines* that
    session. Measured 2026-09-19 on the workstation, Cboe 429'd 9 of 24
    symbols and the run still exited 0 on 14,091 rows — far above the driving
    script's 200-row floor, which counts rows and therefore cannot see a
    missing symbol at all. Every one of the 24 chains clears 200 rows alone
    (thinnest: FXI at 213), so that floor tolerated losing 23 of 24 names.

    Refusing leaves the session recoverable until the next US open; writing
    does not. Hence: no partition at all, and a loud exception.
    """
    live = [f"SYM{i:02d}" for i in range(15)]
    dead = [f"DEAD{i:02d}" for i in range(9)]
    store = DeltaLakeStore(tmp_path)

    with pytest.raises(IncompleteSweepError, match="15 of 24"):
        ingest_option_chain(
            store,
            [*live, *dead],
            ingest_date=dt.date(2026, 8, 26),
            fetch=_fetcher(**{name: _payload(_put(95.0), symbol=name) for name in live}),
        )

    # The point is not merely that it raised — it must not have written.
    with pytest.raises(LookupError):
        store.bronze_snapshot_id(DATASET, dt.date(2026, 8, 26))


def test_the_floor_does_not_fire_when_the_session_is_already_captured(tmp_path: Path) -> None:
    """A re-run, a weekend or a holiday resolves to a session already in the
    lake, where the write is a no-op anyway. Raising there would turn a
    correct, benign outcome into a red alert on a job whose alerts must stay
    trustworthy — so the floor is checked only when the run would CREATE the
    partition."""
    live = [f"SYM{i:02d}" for i in range(24)]
    store = DeltaLakeStore(tmp_path)
    full = _fetcher(**{name: _payload(_put(95.0), symbol=name) for name in live})
    first = ingest_option_chain(store, live, ingest_date=dt.date(2026, 8, 26), fetch=full)
    assert first.committed is True

    # Same session, now with only 3 of 24 chains answering: far below the
    # floor, but the partition exists, so this is a no-op and not a refusal.
    partial = _fetcher(**{name: _payload(_put(95.0), symbol=name) for name in live[:3]})
    second = ingest_option_chain(store, live, ingest_date=dt.date(2026, 8, 26), fetch=partial)
    assert second.committed is False


def test_a_no_op_write_does_not_report_rows_it_did_not_commit(tmp_path: Path) -> None:
    """``write_bronze`` returns the same path string whether it wrote or
    short-circuited on an existing ingest_date, so before ``committed`` no
    caller could tell the two apart — and every one printed ``valid_rows``
    regardless. Measured 2026-09-19: two runs reported "committed 14091 rows"
    and "committed 19525 rows" against a partition already holding 19,572;
    the Delta log gained no version and neither wrote a byte. A monitor that
    cannot distinguish "captured" from "did nothing" is not a monitor.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    store = DeltaLakeStore(tmp_path)
    fetch = _fetcher(**{name: _payload(_put(95.0), symbol=name) for name in live})

    first = ingest_option_chain(store, live, ingest_date=dt.date(2026, 8, 26), fetch=fetch)
    second = ingest_option_chain(store, live, ingest_date=dt.date(2026, 8, 26), fetch=fetch)

    assert first.committed is True
    assert second.committed is False
    # Both still report the same in-memory row count and the same path, which
    # is exactly why the flag has to exist rather than the caller inferring it.
    assert second.valid_rows == first.valid_rows
    assert second.bronze_path == first.bronze_path


def test_committed_is_correct_when_an_EARLIER_partition_already_exists(tmp_path: Path) -> None:
    """Regression: the first version of this check went through
    ``bronze_snapshot_id``, which is as-of resolution and is memoised for
    ``_RESOLVE_TTL_S`` (45 s) WITHOUT being invalidated by a write. With an
    empty lake the lookup raised ``LookupError`` before anything was cached,
    so the bug was invisible; add any earlier partition and the second ingest
    re-read the pre-write answer and reported ``committed=True`` on a run that
    wrote nothing — the exact lie the flag exists to kill.

    The seeded 08-25 partition is the whole point of this test. Do not
    simplify it away.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    store = DeltaLakeStore(tmp_path)
    ingest_option_chain(
        store,
        live,
        ingest_date=dt.date(2026, 8, 25),
        fetch=_fetcher(**{n: _payload(_put(95.0), symbol=n, day="2026-08-25") for n in live}),
    )
    fresh = _fetcher(**{n: _payload(_put(95.0), symbol=n) for n in live})
    first = ingest_option_chain(store, live, ingest_date=dt.date(2026, 8, 26), fetch=fresh)
    second = ingest_option_chain(store, live, ingest_date=dt.date(2026, 8, 26), fetch=fresh)

    assert first.committed is True
    assert second.committed is False


def test_one_symbol_with_no_last_trade_time_cannot_elect_the_session(tmp_path: Path) -> None:
    """Regression for the worst defect this module has had.

    ``parse_cboe_chain`` falls back to the payload's ``timestamp`` when a
    symbol carries no ``last_trade_time`` -- and that field is Cboe's CDN
    **wall clock**, not a session. Measured live 2026-09-21: ``timestamp``
    read ``2026-09-21 09:11:43`` while the real session was ``2026-09-18``,
    three days apart.

    While the session was ``max(quote_date)``, one such symbol elected a
    session no symbol traded in, and the off-session split then quarantined
    all 23 CORRECT symbols: a one-row partition stamped with a future date,
    permanent by immutability, which also pre-claimed the next session's key
    so the next real sweep no-opped. Exit 0, no alert, two sessions lost.
    Measured before the fix: ``ok=1 stale=23``.

    A majority vote makes the outlier the outlier, whichever direction it
    points. Do NOT reintroduce ``max()`` here.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    rogue = live[0]
    payloads = {n: _payload(_put(95.0), symbol=n) for n in live}
    # The rogue payload has NO last_trade_time, so parsing falls back to the
    # CDN wall clock -- a LATER date than the session every other symbol
    # reports. This is the shape the live CDN actually serves.
    rogue_payload = _payload(_put(95.0), symbol=rogue)
    del rogue_payload["data"]["last_trade_time"]
    rogue_payload["timestamp"] = "2026-08-27 06:06:48"
    payloads[rogue] = rogue_payload

    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(store, live, fetch=_fetcher(**payloads))

    assert result.quote_date == dt.date.fromisoformat(QUOTE_DAY), "the majority session must win"
    assert result.symbols_off_session == (rogue,), "the outlier is the rogue, not the other 23"
    assert len(result.symbols_ok) == 23
    landed = store.read_bronze_as_of(DATASET, dt.date.fromisoformat(QUOTE_DAY))
    assert len(set(landed["underlying"])) == 23


@pytest.mark.parametrize(
    ("label", "split"),
    [
        ("12/12 tie", 12),
        ("8/8/8 three-way", 8),
    ],
)
def test_a_session_without_a_majority_is_refused(tmp_path: Path, label: str, split: int) -> None:
    """A plurality is not enough to name a partition after.

    The majority vote fixed the ONE-outlier case, but with a plurality rule
    the degenerate distributions still wrote tiny partitions, and an earlier
    tie-break preferred the LATER date -- precisely the side the wall-clock
    fallback makes bogus. Measured at 24 symbols under that rule: a 12/12 tie
    wrote 12 rows stamped to the wall clock, an 8/8/8 split wrote 8, and 24
    distinct dates wrote 1. Each named a partition for a future session,
    which bronze immutability then makes permanent AND which pre-claims the
    next session's key, so the next real sweep no-ops.

    Requiring MORE than half makes the winner unique, so it removes the
    tie-break instead of repairing it. When no session has a majority this
    sweep cannot say which session it captured, and that is exactly when
    writing an immutable partition is unforgivable.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    days = ["2026-08-27", QUOTE_DAY, "2026-08-25"]
    payloads = {
        name: _payload(_put(95.0), symbol=name, day=days[min(i // split, len(days) - 1)])
        for i, name in enumerate(live)
    }
    with pytest.raises(IncompleteSweepError, match="majority"):
        ingest_option_chain(DeltaLakeStore(tmp_path), live, fetch=_fetcher(**payloads))


def test_every_symbol_on_its_own_date_is_refused(tmp_path: Path) -> None:
    """The degenerate limit of the case above: 24 symbols, 24 dates, so the
    'winner' has one vote. Under the plurality rule this wrote a ONE-symbol
    partition and exited 0."""
    live = [f"SYM{i:02d}" for i in range(24)]
    base = dt.date.fromisoformat(QUOTE_DAY)
    payloads = {
        name: _payload(_put(95.0), symbol=name, day=(base - dt.timedelta(days=i)).isoformat())
        for i, name in enumerate(live)
    }
    with pytest.raises(IncompleteSweepError, match="majority"):
        ingest_option_chain(DeltaLakeStore(tmp_path), live, fetch=_fetcher(**payloads))


def test_a_symbol_whose_rows_all_fail_validation_is_not_counted_as_captured(
    tmp_path: Path,
) -> None:
    """``symbols_ok`` is appended on PARSE success, before validation.

    So a symbol whose every row failed the schema counted as captured, counted
    toward the floor, and was printed as part of the completeness number --
    while contributing nothing. Measured: nine symbols served with a null ask
    reported ``symbols_ok=24`` on a partition holding 15 names. That is the
    same 15-of-24 outcome the floor refuses by the fetch route, reached
    silently by the validation route.

    Such a symbol is RECOVERABLE (a re-run may return usable rows), so it
    belongs with the fetch failures and must count against the floor.
    """
    live = [f"SYM{i:02d}" for i in range(24)]

    def with_duds(duds: list[str]) -> dict[str, dict[str, Any]]:
        """Built from an explicit LIST, never a set.

        The first version of this test reused one payload dict and reset the
        duds via ``list(set_of_duds)[1:]``. Set iteration order is not stable
        across runs, so whether the asserted symbol stayed a dud depended on
        the hash seed and the test passed or failed at random -- observed
        both ways on identical source.
        """
        return {
            name: _payload(
                _put(95.0, bid=0.0, ask=0.0) if name in duds else _put(95.0), symbol=name
            )
            for name in live
        }

    with pytest.raises(IncompleteSweepError):
        ingest_option_chain(DeltaLakeStore(tmp_path), live, fetch=_fetcher(**with_duds(live[:9])))

    # Below the floor it refuses; at ONE bad symbol it must still commit, and
    # must not claim the dud among the captured names.
    result = ingest_option_chain(
        DeltaLakeStore(tmp_path / "one"), live, fetch=_fetcher(**with_duds([live[0]]))
    )
    assert live[0] in result.symbols_failed
    assert live[0] not in result.symbols_ok
    assert len(result.symbols_ok) == 23


def test_a_stale_majority_cannot_silently_discard_a_fresher_session(tmp_path: Path) -> None:
    """The one case where the majority rule is worse than the ``max()`` it
    replaced, refused explicitly rather than rebalanced.

    Cboe serves per-symbol CDN snapshots of wildly different ages — measured
    2026-09-21, the ``timestamp`` field spanned ten hours across the universe.
    So on a slow evening most chains can still be serving YESTERDAY while a
    minority already carry today. The majority then elects yesterday,
    ``write_bronze`` no-ops on its existing partition, and the run reports
    "already captured" and exits 0 — today gone, silently, with the fresh
    chains discarded as "off-session" and the warning naming the *fresh*
    symbols as the problem.

    Measured at 24 symbols with yesterday already in the lake: 13 stale chains
    lost the day and 20 stale chains lost the day, both exit 0. The signal is
    unambiguous — a no-op whose off-session rows are NEWER than the elected
    session — so it refuses, and the session stays recoverable until the next
    US open.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    yesterday, today = "2026-08-25", QUOTE_DAY
    store = DeltaLakeStore(tmp_path)
    ingest_option_chain(
        store,
        live,
        fetch=_fetcher(**{n: _payload(_put(95.0), symbol=n, day=yesterday) for n in live}),
    )

    stale = set(live[:20])
    with pytest.raises(IncompleteSweepError, match="already captured"):
        ingest_option_chain(
            store,
            live,
            fetch=_fetcher(
                **{
                    n: _payload(_put(95.0), symbol=n, day=yesterday if n in stale else today)
                    for n in live
                }
            ),
        )


def test_an_off_session_symbol_is_not_also_reported_as_failed(tmp_path: Path) -> None:
    """``failed`` gains symbols that parsed but landed no valid rows. An
    off-session symbol also lands no rows in ``valid`` — because the split
    already moved them to quarantine — so without the ``off_set`` guard it
    would be reported in BOTH lists, and the caller prints two contradictory
    warnings about the same names: "no quotes for X" beside "off-session
    quotes for X"."""
    live = [f"SYM{i:02d}" for i in range(24)]
    lagging = set(live[:2])
    result = ingest_option_chain(
        DeltaLakeStore(tmp_path),
        live,
        fetch=_fetcher(
            **{
                n: _payload(_put(95.0), symbol=n, day="2026-08-25" if n in lagging else QUOTE_DAY)
                for n in live
            }
        ),
    )
    assert set(result.symbols_off_session) == {n.upper() for n in lagging}
    assert not set(result.symbols_off_session) & set(result.symbols_failed)


def test_laggards_do_not_cost_the_healthy_chains_their_session(tmp_path: Path) -> None:
    """A fetch failure and a laggard must not share a denominator.

    A 429 is recoverable — re-run before the next US open and the chain
    arrives — so refusing to write is free and protects the session from being
    permanently defined by a partial sweep. A laggard, where Cboe serves a
    previous session, is NOT recoverable: the re-run returns the same stale
    chain. Counting laggards against the floor therefore discards every
    healthy chain alongside them, permanently, in exchange for nothing.

    Measured before the fix: three laggards refused a sweep whose other 21
    chains were fresh, and no re-run could ever have recovered them.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    lagging = set(live[:3])
    store = DeltaLakeStore(tmp_path)
    result = ingest_option_chain(
        store,
        live,
        fetch=_fetcher(
            **{
                n: _payload(_put(95.0), symbol=n, day="2026-08-25" if n in lagging else QUOTE_DAY)
                for n in live
            }
        ),
    )

    assert result.committed is True
    assert len(result.symbols_off_session) == 3
    assert len(result.symbols_ok) == 21

    # The other half of the contract: a genuine FETCH failure at the same
    # count must still refuse, or the floor has been neutered rather than
    # corrected.
    other = DeltaLakeStore(tmp_path / "second")
    with pytest.raises(IncompleteSweepError):
        ingest_option_chain(
            other,
            live,
            fetch=_fetcher(**{n: _payload(_put(95.0), symbol=n) for n in live[3:]}),
        )


def test_a_stale_symbol_is_quarantined_not_filed_under_the_wrong_session(
    tmp_path: Path,
) -> None:
    """Regression for a defect that is already in the production lake.

    ``session`` is ``max(quote_date)``, so when Cboe keeps serving one name's
    previous session its rows get filed under a session it never traded in.
    Live: ``ingest_date=2026-09-08`` holds 412 ARKK rows stamped 2026-09-04
    (ARKK's last trade before Labor Day). ARKK's 2026-09-08 session was never
    captured, and nothing noticed, because ``.max()`` reads the 23 current
    symbols and the partition looks healthy in aggregate.

    The stale rows must not be counted as that session's quotes. They go to
    quarantine — they are real quotes, just not this session's — and the
    symbol is named in ``symbols_off_session`` so the operator learns it has no
    quotes for the day rather than silently getting the wrong ones.
    """
    fresh = [f"SYM{i:02d}" for i in range(23)]
    store = DeltaLakeStore(tmp_path)
    payloads = {name: _payload(_put(95.0), symbol=name) for name in fresh}
    payloads["ARKK"] = _payload(_put(95.0), symbol="ARKK", day="2026-08-25")

    result = ingest_option_chain(
        store,
        [*fresh, "ARKK"],
        ingest_date=dt.date(2026, 8, 26),
        fetch=_fetcher(**payloads),
    )

    assert result.symbols_off_session == ("ARKK",)
    assert "ARKK" not in result.symbols_ok
    assert result.quote_date == dt.date(2026, 8, 26)
    # The committed partition carries only the session it is named for. Note
    # this holds because `ingest_date` was DERIVED from the quotes: the split
    # equalises rows against max(quote_date), not against an explicitly passed
    # `ingest_date`, so passing a mismatched one can still name a partition
    # after a session it contains none of. No production caller passes it.
    landed = store.read_bronze_as_of(DATASET, dt.date(2026, 8, 26))
    assert set(landed["quote_date"].dt.date.unique()) == {dt.date(2026, 8, 26)}
    assert "ARKK" not in set(landed["underlying"])
    # ...and the stale rows are preserved rather than dropped.
    held = store.read_bronze_as_of(QUARANTINE_DATASET, dt.date(2026, 8, 26))
    assert "ARKK" in set(held["underlying"])


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


def test_a_failed_quarantine_write_does_not_fail_the_sweep(tmp_path: Path) -> None:
    """The session is committed BEFORE quarantine, and bronze is immutable, so
    a quarantine failure cannot be undone by re-running — it can only no-op.

    Raising there turned a correct capture into a non-zero exit, which fires
    the chain-loss alert and tells the operator to re-run. Measured in
    production 2026-09-23: xbi lagged, its rows were correctly quarantined and
    the other 23 chains correctly committed, and the sweep still exited 2
    because the quarantine table carries a stale `__index_level_0__` column
    that a clean frame no longer matches.

    Quarantine is diagnostic. It must never hold the session hostage.
    """
    live = [f"SYM{i:02d}" for i in range(24)]
    lagging = live[0]
    payloads = {n: _payload(_put(95.0), symbol=n) for n in live}
    payloads[lagging] = _payload(_put(95.0), symbol=lagging, day="2026-08-25")

    class _QuarantineIsBroken(DeltaLakeStore):
        def write_bronze(self, dataset: str, ingest_date: dt.date, df: pd.DataFrame) -> str:
            if dataset.endswith("__quarantine"):
                raise RuntimeError("SchemaMismatchError: number of fields does not match: 13 vs 14")
            return super().write_bronze(dataset, ingest_date, df)

    result = ingest_option_chain(_QuarantineIsBroken(tmp_path), live, fetch=_fetcher(**payloads))

    assert result.committed is True, "the session must still be captured"
    assert len(result.symbols_ok) == 23
    assert result.symbols_off_session == (lagging.upper(),)
    assert result.quarantine_path is None, "the failure is recorded as an absent path"
