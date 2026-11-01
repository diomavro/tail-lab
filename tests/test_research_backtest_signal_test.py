"""Tests for the signal test as the API serves it (``research/backtest/signal_test.py``).

The walk-forward orchestration (``walk_forward()``, ADR 0033) over a real lake
and the beyond-volatility test riding on the same periods (ADR 0035). The
statistics and the collector have their own tests in
``test_research_backtest_walk_forward.py``, whose lake builders are reused here.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.backtest import signal_test
from tail_lab.research.backtest import walk_forward as wf
from tail_lab.research.backtest.beyond_vol import BeyondVolTest, beyond_vol_test
from tail_lab.research.backtest.signal_test import (
    CONFIRMATORY_SETTING,
    FREEZE_AS_OF,
    MAX_BEYOND_VOL_PERIODS,
    SignalTestResult,
    walk_forward,
)
from tail_lab.research.backtest.strike_rule import ByDelta, ByMoneyness, StrikeRule
from tail_lab.research.backtest.walk_forward import SCREENS, Diagnostics, PeriodInputs
from tests.test_research_backtest_beyond_vol import _panel
from tests.test_research_backtest_walk_forward import (
    INGEST,
    SIGNAL_YEARS,
    TENOR_WEEKS,
    _closes,
    _names,
    _store,
)


def test_a_too_deep_moneyness_strike_gives_an_insufficient_data_verdict(tmp_path: Path) -> None:
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    result = walk_forward(
        store,
        symbols=_names(closes),
        as_of=INGEST,
        rule=ByMoneyness(12.0),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=2,
        n_random=50,
    )
    assert result.verdict.status == "insufficient_data"
    assert result.verdict.reasons == ["no_test_window"]


def test_walk_forward_end_to_end_is_complete_seeded_and_serialisable(tmp_path: Path) -> None:
    dates, closes = _closes()
    store = _store(tmp_path, closes, dates)
    kw = dict(
        symbols=_names(closes),
        as_of=INGEST,
        rule=ByDelta(0.10),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=2,
        n_random=200,
    )
    res = walk_forward(store, **kw)  # type: ignore[arg-type]
    again = walk_forward(store, **kw)  # type: ignore[arg-type]
    assert res.model_dump() == again.model_dump()
    assert res.strike_rule == "delta" and res.target_delta == 0.10 and res.moneyness_pct is None
    assert res.universe_size == 12 and res.n_periods == len(res.periods)
    assert res.test_start == res.periods[0].date and res.test_end == res.periods[-1].expiry_date
    assert [s.screen for s in res.screens] == list(SCREENS)
    assert res.caveats == ["survivorship", "model_priced_signal"]
    assert "caveats" not in wf.SignalVerdict.model_fields  # stated once, on the result
    assert res.code_sha == "unknown"
    # provenance: VIX, the benchmark and every loaded name are cited
    assert len(res.snapshot_ids) >= 14
    assert res.diagnostics.n_symbols_loaded == 12
    assert {r.symbol for r in res.per_name} <= set(_names(closes))
    assert res.model_dump_json()
    # the moneyness path labels itself too
    m = walk_forward(store, **{**kw, "rule": ByMoneyness(5.0)})  # type: ignore[arg-type]
    assert m.strike_rule == "moneyness" and m.moneyness_pct == 5.0 and m.target_delta is None


def _walk(store: DeltaLakeStore, names: list[str]) -> SignalTestResult:
    return walk_forward(
        store,
        symbols=names,
        as_of=INGEST,
        rule=ByDelta(0.10),
        tenor_weeks=TENOR_WEEKS,
        signal_years=SIGNAL_YEARS,
        top_k=2,
        n_random=50,
    )


@pytest.mark.parametrize("with_dividends", [False, True], ids=["no-tiingo", "tiingo"])
def test_provenance_is_what_the_ranking_would_cite_and_each_table_opens_twice_at_most(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_dividends: bool
) -> None:
    """``snapshot_ids`` must be the same list ``ranking.snapshot_ids_for`` gives
    (VIX, SPY and every name's price snapshots, then the dividend snapshot) --
    but collected as the data was read, so no table is opened a second time just
    to identify it. A Delta table is opened once to resolve the as-of partition
    (which also yields the id) and once to read it: never a third time."""
    from tail_lab.lake import store as store_module
    from tail_lab.research.backtest.ranking import BENCHMARK, snapshot_ids_for
    from tail_lab.research.dividends import _memo as dividend_memo
    from tests.test_research_dividends import seed_tiingo_eod

    dates, closes = _closes()
    names = _names(closes)
    root = tmp_path / "lake"
    seeded = _store(root, closes, dates)
    dividend_memo.clear()
    if with_dividends:
        seed_tiingo_eod(seeded, names, INGEST, annual_yield=0.03)
    opens: Counter[str] = Counter()

    class CountingTable(store_module.DeltaTable):  # type: ignore[misc]
        def __init__(self, table_uri: str, *args: object, **kwargs: object) -> None:
            opens[table_uri] += 1
            super().__init__(table_uri, *args, **kwargs)

    monkeypatch.setattr(store_module, "DeltaTable", CountingTable)
    fresh = DeltaLakeStore(root)  # cold caches: every resolution really opens
    dividend_memo.clear()
    cited = _walk(fresh, names).snapshot_ids
    existing = {
        uri: n for uri, n in opens.items() if Path(uri).exists()
    }  # absent tables fail to open
    assert len(existing) >= 14  # VIX, SPY, 12 names (+ the dividend table)
    assert max(existing.values()) <= 2, existing

    monkeypatch.undo()
    expected = snapshot_ids_for(DeltaLakeStore(root), INGEST, (BENCHMARK, *names))
    dividend_memo.clear()
    assert cited == expected
    assert (len(cited) == 1 + 13 + 1) if with_dividends else (len(cited) == 1 + 13)


# ==========================================================================
# The beyond-volatility test rides on the same periods (ADR 0035)
# ==========================================================================


def _walk_over(
    monkeypatch: pytest.MonkeyPatch,
    periods: list[PeriodInputs],
    *,
    rule: StrikeRule = ByDelta(0.10),
    tenor_weeks: float = 4.0,
    signal_years: float = 4.0,
    top_k: int = 5,
    as_of: dt.date = FREEZE_AS_OF,
) -> tuple[SignalTestResult, list[int]]:
    """``walk_forward`` over fixed periods (no lake), and a count of collects."""
    collects: list[int] = []

    def fake(*_a: object, **_k: object) -> wf.Collected:
        collects.append(1)
        return wf.Collected(periods, Diagnostics(), ("a",), ())

    monkeypatch.setattr(signal_test, "collect_periods", fake)
    res = walk_forward(
        object(),  # type: ignore[arg-type]
        symbols=["a"],
        as_of=as_of,
        rule=rule,
        tenor_weeks=tenor_weeks,
        signal_years=signal_years,
        top_k=top_k,
        n_random=20,
    )
    return res, collects


def test_the_signal_test_carries_the_beyond_vol_test_of_its_own_periods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parity: the field IS ``beyond_vol_test(periods)`` for the periods the
    signal test used, from the one collect (no extra I/O)."""

    world = _panel(seed=2, effect=1.5)
    res, collects = _walk_over(monkeypatch, world)
    assert collects == [1]
    assert res.beyond_vol is not None
    assert res.beyond_vol.model_dump() == beyond_vol_test(world).model_dump()
    assert res.beyond_vol.n_periods == res.n_periods == len(world)
    assert res.beyond_vol_mode == "confirmatory"
    assert res.beyond_vol_unavailable_reason is None
    assert res.model_dump_json()  # serialises with the field in it


def test_a_thin_universe_still_gets_an_insufficient_beyond_vol_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    res, _ = _walk_over(monkeypatch, [])
    assert res.beyond_vol is not None
    assert res.beyond_vol.status == "insufficient_data"
    assert res.beyond_vol.gamma_bar is None


def test_a_beyond_vol_refusal_never_fails_the_signal_test_and_says_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A constant SPY return makes the market-neutral intercept undefined, so
    the frozen test raises ValueError. The signal test still returns, with the
    field ``None`` and the reason both in the log and in the result."""

    flat = [dataclasses.replace(p, spy_return=0.01) for p in _panel(seed=2, effect=1.5)]
    with caplog.at_level("WARNING", logger=signal_test.__name__):
        res, _ = _walk_over(monkeypatch, flat)
    assert res.beyond_vol is None
    assert res.beyond_vol_unavailable_reason is not None
    assert "spy_return is constant" in res.beyond_vol_unavailable_reason
    assert res.n_periods == len(flat)
    assert any("beyond_vol_unavailable" in r.getMessage() for r in caplog.records)
    assert "spy_return is constant" in caplog.text


def test_a_non_value_error_from_the_beyond_vol_test_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the frozen procedure's own refusal (ValueError) is absorbed. A bug
    (here a KeyError) must fail the signal test loudly, not hide as 'no result'."""

    def boom(_periods: object) -> BeyondVolTest:
        raise KeyError("a bug")

    monkeypatch.setattr(signal_test, "beyond_vol_test", boom)
    with pytest.raises(KeyError):
        _walk_over(monkeypatch, [])


def test_the_beyond_vol_test_is_skipped_above_the_period_cap_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its simulated null allocates ~2.4 MB per period up front (368 MB peak at
    T = 81, 767 MB at T = 300), so a long run must not start it: the 1 GB VM
    would kill the process silently. At the cap it still runs; one over, it
    does not, and the result carries the reason."""

    world = _panel(seed=2, effect=1.5)
    ran: list[int] = []

    def spy(periods: Sequence[PeriodInputs]) -> BeyondVolTest:
        ran.append(len(periods))
        return beyond_vol_test([])

    monkeypatch.setattr(signal_test, "beyond_vol_test", spy)
    at_cap = (world * 5)[:MAX_BEYOND_VOL_PERIODS]
    assert len(at_cap) == MAX_BEYOND_VOL_PERIODS
    res, _ = _walk_over(monkeypatch, at_cap)
    assert ran == [MAX_BEYOND_VOL_PERIODS]
    assert res.beyond_vol is not None and res.beyond_vol_unavailable_reason is None

    ran.clear()
    over = (world * 5)[: MAX_BEYOND_VOL_PERIODS + 1]
    res, _ = _walk_over(monkeypatch, over)
    assert ran == []
    assert res.beyond_vol is None
    reason = res.beyond_vol_unavailable_reason
    assert reason is not None and str(MAX_BEYOND_VOL_PERIODS + 1) in reason
    assert res.beyond_vol_mode == "confirmatory"  # the mode is the run's; the result is absent
    assert res.n_periods == len(over)  # the signal test itself is untouched


def test_the_mode_is_confirmatory_only_at_the_preregistered_setting_and_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each of the four parts of the setting, changed alone, is exploratory; the
    setting on any other date is a monitoring re-run, never confirmatory."""
    assert (ByDelta(0.10), 4.0, 4.0, 5) == CONFIRMATORY_SETTING
    assert dt.date(2026, 10, 31) == FREEZE_AS_OF
    assert _walk_over(monkeypatch, [])[0].beyond_vol_mode == "confirmatory"
    for later in (FREEZE_AS_OF + dt.timedelta(days=1), FREEZE_AS_OF - dt.timedelta(days=1)):
        res, _ = _walk_over(monkeypatch, [], as_of=later)
        assert res.beyond_vol_mode == "monitoring", later
        assert res.beyond_vol is not None  # computed: shown without p or status
    off = [
        {"rule": ByDelta(0.15)},
        {"rule": ByMoneyness(10.0)},
        {"tenor_weeks": 2.0},
        {"signal_years": 3.0},
        {"top_k": 6},
    ]
    for change in off:
        for as_of in (FREEZE_AS_OF, FREEZE_AS_OF + dt.timedelta(days=7)):
            res, _ = _walk_over(monkeypatch, [], as_of=as_of, **change)  # type: ignore[arg-type]
            assert res.beyond_vol_mode == "exploratory", (change, as_of)
            assert res.beyond_vol is not None  # still computed: shown as exploratory
