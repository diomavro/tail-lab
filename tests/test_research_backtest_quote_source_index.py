"""Equivalence and correctness tests for the ``OptionsDxQuoteSource`` index
(AGENT_TODO perf item: make the quote source fast/small enough for a route).

The production refactor moved the per-symbol filter and the ``quote_date``
lookup from "re-scan the whole panel on every ``fill``/``mark`` call" to
"filter once in ``__init__``, then hash a timestamp." That move is only safe
if it changes nothing about WHAT is returned -- so the test that matters most
here (test 1) is not "does the indexed source work", it's "does the indexed
source return EXACTLY what the pre-refactor, re-scan-every-call version
would have returned", across a grid that includes the guard-refusal (``None``)
cases as well as the successful ones. ``_NaiveQuoteSource`` below is a pinned
copy of that pre-refactor orchestration (re-scanning ``panel`` on every call),
kept here purely as a test oracle -- it is not maintained as production code
and must never be imported from ``src/``.
"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest.marks import _select_expiry, _select_strike
from tail_lab.research.backtest.quote_fills import (
    MAX_SNAP_MONEYNESS_PP,
    Fill,
    OptionsDxQuoteSource,
    _basis,
    _basis_is_plausible,
    _is_liquid,
)

DATASET = "optionsdx_quotes_spy"


class _NaiveQuoteSource:
    """Pinned copy of ``OptionsDxQuoteSource`` as it existed before the
    ``__init__``-time index: a fresh boolean mask over the WHOLE panel on
    every ``fill``/``mark`` call. Exists only so test 1 below has an
    independent oracle to diff the indexed version against -- see the module
    docstring. Orchestration only; the guard arithmetic itself (``_basis``,
    ``_select_expiry``, ``_select_strike``, ``_is_liquid``) is shared with
    production and is exercised for correctness separately in
    ``test_research_backtest_quote_fills.py``.
    """

    def __init__(self, panel: pd.DataFrame, *, symbol: str) -> None:
        self._panel = panel
        self._symbol = symbol.upper()

    def fill(
        self, *, entry_date: dt.date, spot: float, moneyness_pct: float, tenor_weeks: float
    ) -> Fill | None:
        session = self._panel[
            (self._panel["underlying"].str.upper() == self._symbol)
            & (self._panel["quote_date"] == pd.Timestamp(entry_date))
        ]
        if session.empty:
            return None

        panel_spot = float(session["spot"].iloc[0])
        basis = _basis(panel_spot, spot)
        if basis is None or not _basis_is_plausible(basis):
            return None

        target_expiry = entry_date + dt.timedelta(days=round(tenor_weeks * 7))
        expiry = _select_expiry(session, target_expiry)
        if expiry is None:
            return None

        target_strike = panel_spot * (1.0 - moneyness_pct / 100.0)
        row = _select_strike(session[session["expiration"] == expiry], target_strike)
        if row is None:
            return None

        realized_moneyness_pct = (1.0 - float(row["strike"]) / panel_spot) * 100.0
        if abs(realized_moneyness_pct - moneyness_pct) > MAX_SNAP_MONEYNESS_PP:
            return None

        if not _is_liquid(row):
            return None

        return Fill(
            premium=float(row["ask"]) / basis,
            strike=float(row["strike"]) / basis,
            expiry=expiry.date(),
            realized_moneyness_pct=realized_moneyness_pct,
            realized_dte=(expiry.date() - entry_date).days,
            basis=basis,
        )

    def mark(
        self, *, entry_date: dt.date, strike: float, expiry: dt.date, basis: float = 1.0
    ) -> float | None:
        session = self._panel[
            (self._panel["underlying"].str.upper() == self._symbol)
            & (self._panel["quote_date"] == pd.Timestamp(entry_date))
            & (self._panel["expiration"] == pd.Timestamp(expiry))
        ]
        if session.empty:
            return None

        panel_strike = strike * basis
        row = _select_strike(session, panel_strike)
        if row is None or not math.isclose(float(row["strike"]), panel_strike, rel_tol=1e-3):
            return None

        bid = float(row["bid"]) / basis
        return bid if bid > 0.0 else None


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "underlying": "SPY",
        "quote_date": pd.Timestamp("2024-01-05"),
        "expiration": pd.Timestamp("2024-02-02"),
        "strike": 90.0,
        "bid": 1.20,
        "ask": 1.30,
        "volume": 500,
        "spot": 100.0,
        "iv": 0.28,
        "delta": -0.32,
        "vega": 0.15,
        "theta": -12.0,
    }
    base.update(overrides)
    return base


#: A multi-session, multi-symbol panel. SPY and QQQ share every quote_date
#: and several strikes/expiries are numerically close across the two symbols
#: on purpose -- a leak between symbols would show up as a plausible-looking
#: wrong number, not a crash, so isolation is worth stressing this way rather
#: than with obviously-disjoint data (test 2 below).
_MULTI_SYMBOL_PANEL = pd.DataFrame(
    [
        # ---- SPY ----
        _row(
            quote_date=pd.Timestamp("2024-01-05"),
            expiration=pd.Timestamp("2024-02-02"),
            strike=90.0,
            bid=1.20,
            ask=1.30,
            spot=100.0,
        ),
        _row(
            quote_date=pd.Timestamp("2024-01-05"),
            expiration=pd.Timestamp("2024-02-02"),
            strike=80.0,
            bid=0.50,
            ask=0.55,
            spot=100.0,
        ),
        _row(
            quote_date=pd.Timestamp("2024-01-05"),
            expiration=pd.Timestamp("2024-02-02"),
            strike=60.0,
            bid=0.0,
            ask=0.10,
            spot=100.0,
        ),  # illiquid: zero bid
        _row(
            quote_date=pd.Timestamp("2024-01-05"),
            expiration=pd.Timestamp("2024-01-19"),
            strike=95.0,
            bid=0.80,
            ask=0.85,
            spot=100.0,
        ),
        _row(
            quote_date=pd.Timestamp("2024-01-08"),
            expiration=pd.Timestamp("2024-02-05"),
            strike=91.0,
            bid=1.25,
            ask=1.35,
            spot=101.0,
        ),
        _row(
            quote_date=pd.Timestamp("2024-01-08"),
            expiration=pd.Timestamp("2024-02-05"),
            strike=50.0,
            bid=1.0,
            ask=2.0,
            spot=101.0,
        ),  # wide spread: illiquid
        _row(
            quote_date=pd.Timestamp("2024-01-12"),
            expiration=pd.Timestamp("2024-02-09"),
            strike=90.0,
            bid=1.0,
            ask=1.05,
            spot=99.0,
        ),
        # ---- QQQ ---- numerically close to SPY's rows above, on purpose.
        _row(
            underlying="QQQ",
            quote_date=pd.Timestamp("2024-01-05"),
            expiration=pd.Timestamp("2024-02-02"),
            strike=180.0,
            bid=2.0,
            ask=2.1,
            spot=200.0,
        ),
        _row(
            underlying="QQQ",
            quote_date=pd.Timestamp("2024-01-05"),
            expiration=pd.Timestamp("2024-02-02"),
            strike=160.0,
            bid=1.0,
            ask=1.05,
            spot=200.0,
        ),
        _row(
            underlying="QQQ",
            quote_date=pd.Timestamp("2024-01-08"),
            expiration=pd.Timestamp("2024-02-05"),
            strike=182.0,
            bid=2.05,
            ask=2.15,
            spot=202.0,
        ),
        _row(
            underlying="QQQ",
            quote_date=pd.Timestamp("2024-01-12"),
            expiration=pd.Timestamp("2024-02-09"),
            strike=90.0,
            bid=9.0,
            ask=9.05,
            spot=99.0,
        ),
    ]
)

#: (entry_date, spot, moneyness_pct, tenor_weeks) grid for `fill`. Includes
#: successes, an illiquid refusal, a snap-tolerance refusal, a no-listed-
#: expiry refusal, a missing-session refusal, and a basis refusal --
#: everything a guard in quote_fills.py's docstring can produce.
_FILL_GRID: list[tuple[dt.date, float, float, float]] = [
    (dt.date(2024, 1, 5), 100.0, 10.0, 4.0),  # clean fill: strike 90
    (dt.date(2024, 1, 5), 100.0, 20.0, 4.0),  # clean fill: strike 80
    (dt.date(2024, 1, 5), 100.0, 40.0, 4.0),  # illiquid: strike 60, zero bid
    (dt.date(2024, 1, 5), 100.0, 5.0, 2.0),  # clean fill: strike 95, 2wk
    (dt.date(2024, 1, 5), 100.0, 60.0, 4.0),  # snap-tolerance miss: no strike near 40
    (dt.date(2024, 1, 5), 100.0, 10.0, 52.0),  # no listed expiry that far out
    (dt.date(2024, 1, 8), 101.0, 10.0, 4.0),  # clean fill: strike 91
    (dt.date(2024, 1, 8), 101.0, 50.0, 4.0),  # wide spread: illiquid
    (dt.date(2024, 1, 12), 99.0, 9.0, 4.0),  # clean fill: strike 90
    (dt.date(2024, 1, 20), 100.0, 10.0, 4.0),  # no session at all on this date
    (dt.date(2024, 1, 5), 150.0, 10.0, 4.0),  # implausible basis (100/150)
]

#: (entry_date, strike, expiry) grid for `mark`, in the CALLER's basis
#: (basis=1.0 throughout here; the basis-conversion arithmetic itself is
#: pinned in test_research_backtest_quote_priced_roll.py). Per-symbol,
#: because the two symbols list different strikes -- a shared grid built
#: around SPY's strikes would refuse for every cell on QQQ and pass the
#: equivalence assertions vacuously (both always None is trivially "equal").
_MARK_GRID_BY_SYMBOL: dict[str, list[tuple[dt.date, float, dt.date]]] = {
    "SPY": [
        (dt.date(2024, 1, 5), 90.0, dt.date(2024, 2, 2)),  # exact contract, present
        (dt.date(2024, 1, 5), 60.0, dt.date(2024, 2, 2)),  # exact contract, zero bid
        (dt.date(2024, 1, 5), 95.0, dt.date(2024, 1, 19)),  # exact contract, present
        (dt.date(2024, 1, 8), 91.0, dt.date(2024, 2, 5)),  # exact contract, present
        (dt.date(2024, 1, 5), 90.0, dt.date(2024, 1, 19)),  # wrong expiry for that strike
        (dt.date(2024, 1, 20), 90.0, dt.date(2024, 2, 2)),  # no session at all
    ],
    "QQQ": [
        (dt.date(2024, 1, 5), 180.0, dt.date(2024, 2, 2)),  # exact contract, present
        (dt.date(2024, 1, 5), 160.0, dt.date(2024, 2, 2)),  # exact contract, present
        (dt.date(2024, 1, 8), 182.0, dt.date(2024, 2, 5)),  # exact contract, present
        (dt.date(2024, 1, 5), 180.0, dt.date(2024, 1, 19)),  # wrong expiry for that strike
        (dt.date(2024, 1, 20), 180.0, dt.date(2024, 2, 2)),  # no session at all
    ],
}


@pytest.mark.parametrize("symbol", ["SPY", "QQQ"])
def test_indexed_fill_matches_the_naive_whole_panel_scan_across_the_grid(symbol: str) -> None:
    """The claim under test: building the ``quote_date`` index in ``__init__``
    is a PURE performance change. If it silently altered which session a
    request resolves against, or which guard fired, this would catch it as a
    mismatched (or wrongly-None / wrongly-non-None) result somewhere in the
    grid -- across two symbols sharing every quote_date in the panel."""
    naive = _NaiveQuoteSource(_MULTI_SYMBOL_PANEL, symbol=symbol)
    indexed = OptionsDxQuoteSource(_MULTI_SYMBOL_PANEL, symbol=symbol)

    saw_a_fill = False
    saw_a_none = False
    for entry_date, spot, moneyness_pct, tenor_weeks in _FILL_GRID:
        expected = naive.fill(
            entry_date=entry_date, spot=spot, moneyness_pct=moneyness_pct, tenor_weeks=tenor_weeks
        )
        actual = indexed.fill(
            entry_date=entry_date, spot=spot, moneyness_pct=moneyness_pct, tenor_weeks=tenor_weeks
        )
        assert actual == expected, (entry_date, spot, moneyness_pct, tenor_weeks)
        saw_a_fill = saw_a_fill or actual is not None
        saw_a_none = saw_a_none or actual is None

    # The grid must actually exercise both branches, or this test could pass
    # vacuously (e.g. if every cell refused for the same trivial reason).
    assert saw_a_fill
    assert saw_a_none


@pytest.mark.parametrize("symbol", ["SPY", "QQQ"])
def test_indexed_mark_matches_the_naive_whole_panel_scan_across_the_grid(symbol: str) -> None:
    naive = _NaiveQuoteSource(_MULTI_SYMBOL_PANEL, symbol=symbol)
    indexed = OptionsDxQuoteSource(_MULTI_SYMBOL_PANEL, symbol=symbol)

    saw_a_mark = False
    saw_a_none = False
    for entry_date, strike, expiry in _MARK_GRID_BY_SYMBOL[symbol]:
        expected = naive.mark(entry_date=entry_date, strike=strike, expiry=expiry)
        actual = indexed.mark(entry_date=entry_date, strike=strike, expiry=expiry)
        if expected is None:
            assert actual is None, (entry_date, strike, expiry)
        else:
            assert actual == pytest.approx(expected), (entry_date, strike, expiry)
        saw_a_mark = saw_a_mark or actual is not None
        saw_a_none = saw_a_none or actual is None

    assert saw_a_mark
    assert saw_a_none


def test_symbol_isolation_a_spy_source_never_returns_a_qqq_row() -> None:
    """SPY and QQQ share every quote_date in ``_MULTI_SYMBOL_PANEL``, so a
    filter that leaked (e.g. an ``__init__`` that forgot to filter by
    ``underlying`` before grouping by ``quote_date``, or that grouped first
    and filtered second) would hand a SPY-symbol source a QQQ row for the
    same session. QQQ's premiums (2.0-2.15, 9.0-9.05) and strikes (160-182)
    are far from any SPY value in the panel, so a leak reads as an
    obviously-wrong number rather than a coincidental match."""
    spy_source = OptionsDxQuoteSource(_MULTI_SYMBOL_PANEL, symbol="SPY")
    qqq_source = OptionsDxQuoteSource(_MULTI_SYMBOL_PANEL, symbol="QQQ")

    spy_fill = spy_source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert spy_fill is not None
    assert spy_fill.strike == pytest.approx(90.0)  # the SPY strike, never 180.0/160.0

    qqq_fill = qqq_source.fill(
        entry_date=dt.date(2024, 1, 5), spot=200.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert qqq_fill is not None
    assert qqq_fill.strike == pytest.approx(180.0)  # the QQQ strike, never 90.0/80.0

    # SPY and QQQ list the IDENTICAL (strike=90, expiry=2024-02-09) contract
    # on 2024-01-12, at deliberately different bids (SPY 1.00, QQQ 9.00) --
    # the sharpest isolation check available: same key, different answer, so
    # a leak in either direction reads as a 9x-wrong number, not a match.
    assert qqq_source.mark(
        entry_date=dt.date(2024, 1, 12), strike=90.0, expiry=dt.date(2024, 2, 9)
    ) == pytest.approx(9.0)
    assert spy_source.mark(
        entry_date=dt.date(2024, 1, 12), strike=90.0, expiry=dt.date(2024, 2, 9)
    ) == pytest.approx(1.0)

    # A SPY source asked about a session where only QQQ quoted this specific
    # strike/expiry combination must refuse, not silently answer from QQQ's row.
    spy_only_mark = spy_source.mark(
        entry_date=dt.date(2024, 1, 5), strike=180.0, expiry=dt.date(2024, 2, 2)
    )
    assert spy_only_mark is None  # 180 strike is QQQ's, not SPY's, on this session


def test_symbol_isolation_covers_every_session_for_both_symbols() -> None:
    """Stronger than the spot-check above: for every (session, symbol) pair
    in the fixture, the built index's session frame contains only rows for
    that symbol's actual listed strikes -- checked by comparing the indexed
    source's resolved strike set against a direct pandas filter of the raw
    panel, independently of any guard logic."""
    for symbol in ("SPY", "QQQ"):
        source = OptionsDxQuoteSource(_MULTI_SYMBOL_PANEL, symbol=symbol)
        for quote_date in _MULTI_SYMBOL_PANEL["quote_date"].unique():
            expected_strikes = set(
                _MULTI_SYMBOL_PANEL[
                    (_MULTI_SYMBOL_PANEL["underlying"] == symbol)
                    & (_MULTI_SYMBOL_PANEL["quote_date"] == quote_date)
                ]["strike"]
            )
            session = source._sessions.get(pd.Timestamp(quote_date))
            actual_strikes = set(session["strike"]) if session is not None else set()
            assert actual_strikes == expected_strikes, (symbol, quote_date)


# ---- from_store: projects only the columns __init__ needs -----------------


def test_from_store_reads_only_the_projected_columns(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of `from_store` is to never materialise the vendor
    greek columns (`iv`, `delta`, `vega`, `theta`) or `volume` -- none of
    which `fill`/`mark` read -- for a caller that only needs six. Captures
    the frame `read_bronze_columns_as_of` actually returns (i.e. the frame
    handed to `OptionsDxQuoteSource.__init__`) and asserts those columns are
    simply absent, not merely unused."""
    store = DeltaLakeStore(tmp_path)
    as_of = dt.date(2024, 1, 6)
    store.write_bronze(DATASET, dt.date(2024, 1, 5), _MULTI_SYMBOL_PANEL)

    captured: dict[str, pd.DataFrame] = {}
    real_read = store.read_bronze_columns_as_of

    def recording(dataset: str, requested_as_of: dt.date, columns: object) -> pd.DataFrame:
        frame = real_read(dataset, requested_as_of, columns)
        captured["frame"] = frame
        return frame

    monkeypatch.setattr(store, "read_bronze_columns_as_of", recording)

    source = OptionsDxQuoteSource.from_store(store, symbol="SPY", as_of=as_of)

    frame = captured["frame"]
    for absent in ("iv", "delta", "vega", "theta", "volume"):
        assert absent not in frame.columns, absent

    # And the resulting source still behaves like the in-memory constructor.
    fill = source.fill(
        entry_date=dt.date(2024, 1, 5), spot=100.0, moneyness_pct=10.0, tenor_weeks=4.0
    )
    assert fill is not None
    assert fill.strike == pytest.approx(90.0)


# ---- LakeStore.read_bronze_columns_as_of: the projected-read contract -----


def _bronze_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "underlying": ["SPY", "SPY"],
            "quote_date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
            "expiration": pd.to_datetime(["2026-02-02", "2026-02-02"]),
            "strike": [90.0, 80.0],
            "bid": [1.20, 0.50],
            "ask": [1.30, 0.55],
            "volume": [500, 300],
            "spot": [100.0, 100.0],
            "iv": [0.28, 0.31],
            "delta": [-0.32, -0.22],
            "vega": [0.15, 0.12],
            "theta": [-12.0, -9.0],
        }
    )


def test_read_bronze_columns_as_of_raises_lookup_error_with_no_snapshot(tmp_path) -> None:
    store = DeltaLakeStore(tmp_path)
    with pytest.raises(LookupError):
        store.read_bronze_columns_as_of(DATASET, dt.date(2026, 1, 1), ["strike", "bid"])


def test_read_bronze_columns_as_of_raises_key_error_for_an_unknown_column(tmp_path) -> None:
    store = DeltaLakeStore(tmp_path)
    store.write_bronze(DATASET, dt.date(2026, 1, 5), _bronze_frame())
    with pytest.raises(KeyError):
        store.read_bronze_columns_as_of(DATASET, dt.date(2026, 1, 5), ["strike", "nonexistent"])


def test_read_bronze_columns_as_of_does_not_poison_the_frame_cache(tmp_path) -> None:
    """CRITICAL per the module docstring: a projected read must never seed
    `_frame_cache` -- that cache holds whole partitions, and a caller reading
    the FULL panel afterwards must still get every column, not the two this
    test happens to project. Reads projected first (so if this bug existed,
    the cache would already be poisoned), then reads the full partition and
    checks nothing is missing."""
    store = DeltaLakeStore(tmp_path)
    original = _bronze_frame()
    store.write_bronze(DATASET, dt.date(2026, 1, 5), original)

    projected = store.read_bronze_columns_as_of(DATASET, dt.date(2026, 1, 5), ["strike", "bid"])
    assert list(projected.columns) == ["strike", "bid"]
    assert (DATASET, "2026-01-05") not in store._frame_cache  # not cached at all

    full = store.read_bronze_as_of(DATASET, dt.date(2026, 1, 5))
    assert set(original.columns) <= set(full.columns)
    assert list(full["iv"]) == list(original["iv"])  # a column the projection never touched


def test_read_bronze_columns_as_of_reuses_an_already_resident_partition(
    tmp_path, monkeypatch
) -> None:
    """The other half of the CRITICAL contract: when the whole partition is
    ALREADY cached (a prior `read_bronze_as_of` call), the projected read
    must serve the projection from that resident frame rather than issuing a
    second physical read -- the expensive part (the storage read) has
    already happened."""
    store = DeltaLakeStore(tmp_path)
    store.write_bronze(DATASET, dt.date(2026, 1, 5), _bronze_frame())

    calls = [0]
    original_partition_read = store._read_bronze_partition

    def counting(table_uri: str, snapshot_date: dt.date) -> pd.DataFrame:
        calls[0] += 1
        return original_partition_read(table_uri, snapshot_date)

    monkeypatch.setattr(store, "_read_bronze_partition", counting)

    store.read_bronze_as_of(DATASET, dt.date(2026, 1, 5))  # populates _frame_cache
    assert calls[0] == 1

    projected = store.read_bronze_columns_as_of(DATASET, dt.date(2026, 1, 5), ["strike", "bid"])
    assert calls[0] == 1  # served from the cached partition, no second physical read
    assert list(projected["strike"]) == [90.0, 80.0]
