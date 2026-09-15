"""Tests for wiring ``quote_fills.QuoteSource`` into the roll engine
(``research/backtest/put_roll.py``'s ``PricingBasis.quotes`` path).

Every non-adversarial case pins its expected numbers by hand in the
docstring, recomputed from the fixture's own inputs (``docs/STANDARDS.md``:
pin against an independent computation, not a snapshot of the function's own
output) — most of them land on clean decimals by construction (premiums and
notionals chosen so ``contracts = notional / premium`` has no repeating
fraction), so a reader can check every number on paper. ``_FakeQuoteSource``
is a minimal ``QuoteSource`` test double: it never snaps or guards like
``OptionsDxQuoteSource`` does (that behaviour is already covered by
``test_research_backtest_quote_fills.py``), it just hands back exactly the
``Fill``/bid registered for an exact ``(date[, strike, expiry])`` key and logs
every call it receives — so these tests isolate what ``put_roll`` itself does
with a quote source, not what the source does internally.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.backtest.put_roll import (
    PricingBasis,
    annualized_return,
    run_put_roll,
)
from tail_lab.research.backtest.quote_fills import Fill, OptionsDxQuoteSource


def _build(px: list[float]) -> tuple[pd.Series, pd.Series, list[dt.date]]:
    """A business-day price / IV-proxy pair over ``len(px)`` sessions.

    IV is pinned flat at 0.20 everywhere so it is finite from day 0 — the
    only skips these fixtures produce are deliberate, counted quote-fill
    refusals (the thing under test), never an IV warm-up artifact.
    """
    idx = pd.date_range("2021-01-04", periods=len(px), freq="B")
    prices = pd.Series(px, index=idx, name="TEST")
    realized_vol = pd.Series(np.full(len(px), 0.20), index=idx)
    return prices, realized_vol, [ts.date() for ts in idx]


@dataclass
class _FakeQuoteSource:
    """A ``QuoteSource`` test double with full control and a call log.

    ``fills`` maps an exact ``entry_date`` to the ``Fill`` to hand back —
    any other date returns ``None`` (a session the panel doesn't quote).
    ``marks`` maps an exact ``(entry_date, strike, expiry)`` to the bid to
    hand back from ``mark`` — any other combination returns ``None``. Every
    call is appended to ``fill_calls``/``mark_calls`` in the order it
    happened, so a test can assert on what was asked and when, not just on
    the final numbers.
    """

    fills: dict[dt.date, Fill] = field(default_factory=dict)
    marks: dict[tuple[dt.date, float, dt.date], float] = field(default_factory=dict)
    fill_calls: list[dt.date] = field(default_factory=list)
    mark_calls: list[tuple[dt.date, float, dt.date]] = field(default_factory=list)

    def fill(
        self, *, entry_date: dt.date, spot: float, moneyness_pct: float, tenor_weeks: float
    ) -> Fill | None:
        self.fill_calls.append(entry_date)
        return self.fills.get(entry_date)

    def mark(
        self, *, entry_date: dt.date, strike: float, expiry: dt.date, basis: float = 1.0
    ) -> float | None:
        self.mark_calls.append((entry_date, strike, expiry))
        return self.marks.get((entry_date, strike, expiry))


# ---- 1. pinned end-to-end run -----------------------------------------------


def test_a_pinned_market_cycle_settles_at_hand_derived_numbers() -> None:
    """35 sessions, IV_WINDOW=20 -> first_entry=20 (``lookback_years=5`` makes
    the nominal window far wider than the panel, so ``max(n-lookback_days,
    IV_WINDOW)`` is pinned by the floor). The source quotes exactly one
    session, day 20: strike 90.0, premium (ask) 2.5, expiry day 34.

    By hand: ``contracts = notional/premium = 1000/2.5 = 400.0``;
    ``commission = COMMISSION_PER_CONTRACT * contracts/100 = 0.65*4 = 2.6``
    and the spread term is force-zeroed on the market path (req. #2/#4 of the
    spec), so ``cost = 2.6`` exactly; spot at expiry (day 34) is 84.0, so
    ``payoff = 400*(90-84) = 2400.0``; ``net = 2400-1000-2.6 = 1397.4``. With
    one cycle, ``total_premium=1000``, ``net_pnl=1397.4``,
    ``roi_on_premium=1.3974``.

    Day 34 is also the last session in the panel, and the engine always
    tries to re-enter on the day a roll expires -- that immediate re-attempt
    finds no quote (only day 20 is registered) and is counted as one skip,
    which is why ``n_cycles_skipped == 1`` below despite there being exactly
    one *settled* cycle.
    """
    px = [100.0] * 35
    px[34] = 84.0
    prices, realized_vol, dates = _build(px)
    source = _FakeQuoteSource(
        fills={
            dates[20]: Fill(
                premium=2.5,
                strike=90.0,
                expiry=dates[34],
                realized_moneyness_pct=10.0,
                realized_dte=14,
            )
        }
    )

    result = run_put_roll(
        prices,
        realized_vol,
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=2.0,
        lookback_years=5.0,
        basis=PricingBasis(quotes=source),
    )

    assert result.priced_from == "market"
    assert result.n_cycles == 1
    cyc = result.cycles[0]
    assert cyc.entry_date == dates[20]
    assert cyc.expiry_date == dates[34]
    assert cyc.strike == pytest.approx(90.0)
    assert cyc.premium == pytest.approx(2.5)
    assert cyc.contracts == pytest.approx(400.0)
    assert cyc.cost == pytest.approx(2.6)
    assert cyc.payoff == pytest.approx(2400.0)
    assert cyc.net == pytest.approx(1397.4)
    assert cyc.sigma == pytest.approx(0.20)

    assert result.total_premium == pytest.approx(1000.0)
    assert result.total_payoff == pytest.approx(2400.0)
    assert result.total_brokerage == pytest.approx(2.6)
    assert result.net_pnl == pytest.approx(1397.4)
    assert result.roi_on_premium == pytest.approx(1.3974)
    assert result.n_cycles_skipped == 1
    # fill_rate is fills/attempts. quote_coverage_pct is span traded over span
    # requested — a different question, and the one the "market" label needs
    # qualifying by. They were the same field until 2026-09-09, when the ratio
    # was found to swing 10x with tenor on identical data because a refusal
    # advances one DAY and a fill advances one CYCLE.
    assert result.fill_rate == pytest.approx(0.5)  # 1 filled of 2 attempts
    assert 0.0 < result.quote_coverage_pct <= 1.0


# ---- 2. no silent fallback ---------------------------------------------------


def test_a_source_that_fills_only_half_never_falls_back_to_the_model() -> None:
    """30 sessions, first_entry=20. Two sessions quote (day 20 -> day 23, day
    24 -> day 29); day 23 (the immediate re-entry attempt after cycle 1) and
    day 29 (the immediate re-entry attempt after cycle 2, also the panel's
    last session) do not. That is 2 fills of 4 attempts -- coverage 0.5 -- and
    both cycles land exactly out-of-the-money (spot pinned at 100 throughout,
    strikes 90/88), so every dollar of ``net_pnl`` is brokerage, computed only
    from the QUOTED premiums:

    cycle 1: contracts=1000/2.0=500.0, commission=0.65*5=3.25, payoff=0,
    net=-1003.25. cycle 2: contracts=1000/1.5=666.6666..., commission=
    0.65*6.6666...=4.3333..., payoff=0, net=-1004.3333....
    total_brokerage=3.25+4.3333...=7.5833...; net_pnl=-2007.5833....

    If a skip ever fell back to the model instead of being counted, this
    fixture would show either three cycles (a phantom fill on day 23 or 29)
    or a premium that is NOT exactly 2.0/1.5 -- the model, given the
    identical (spot=100, sigma=0.2) inputs at those strikes, produces a
    completely different number (and per quote_fills's own docstring, one
    that underflows toward zero at any real depth), so an exact-premium
    match here is strong evidence nothing but the quote was used.
    """
    px = [100.0] * 30
    prices, realized_vol, dates = _build(px)
    source = _FakeQuoteSource(
        fills={
            dates[20]: Fill(
                premium=2.0,
                strike=90.0,
                expiry=dates[23],
                realized_moneyness_pct=10.0,
                realized_dte=3,
            ),
            dates[24]: Fill(
                premium=1.5,
                strike=88.0,
                expiry=dates[29],
                realized_moneyness_pct=12.0,
                realized_dte=5,
            ),
        }
    )

    result = run_put_roll(
        prices,
        realized_vol,
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=1.0,
        lookback_years=5.0,
        basis=PricingBasis(quotes=source),
    )

    assert result.priced_from == "market"
    assert result.n_cycles == 2
    assert result.n_cycles_skipped == 2
    assert result.fill_rate == pytest.approx(0.5)
    assert 0.0 < result.quote_coverage_pct <= 1.0
    assert sorted(cyc.premium for cyc in result.cycles) == [pytest.approx(1.5), pytest.approx(2.0)]
    assert result.total_brokerage == pytest.approx(7.583333333333333)
    assert result.net_pnl == pytest.approx(-2007.583333333333)


# ---- 3. realized expiry, not i + tenor_days ----------------------------------


def test_realized_expiry_beats_the_naive_i_plus_tenor_days_settlement() -> None:
    """``tenor_weeks=2`` -> ``tenor_days = round(2*TRADING_DAYS_PER_WEEK) =
    10``, so the model path (or a buggy market path that reused the model's
    settlement index) would settle at ``i+tenor_days = 20+10 = 30``. The
    quoted contract's REAL listed expiry is day 45 instead -- 15 trading days
    further out, exactly the kind of gap the spec measured as "buys a median
    98-day option and settles it at day 84" for a 12-week request.

    Premium is 4.0 -> ``contracts = 1000/4 = 250.0`` (clean). Spot at the
    naive day (30) is 95.0, so the naive payoff would have been
    ``250*max(90-95,0) = 0``. Spot at the REALIZED expiry (day 45) is 70.0,
    so the actual payoff is ``250*max(90-70,0) = 250*20 = 5000.0`` --
    ``net = 5000 - 1000 - commission(0.65*2.5=1.625) = 3998.375``. The two
    numbers (0 vs 5000) could not differ more starkly.
    """
    px = [100.0] * 46
    px[30] = 95.0
    px[45] = 70.0
    prices, realized_vol, dates = _build(px)
    source = _FakeQuoteSource(
        fills={
            dates[20]: Fill(
                premium=4.0,
                strike=90.0,
                expiry=dates[45],
                realized_moneyness_pct=10.0,
                realized_dte=25,
            )
        }
    )

    result = run_put_roll(
        prices,
        realized_vol,
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=2.0,
        lookback_years=5.0,
        basis=PricingBasis(quotes=source),
    )

    assert result.n_cycles == 1
    cyc = result.cycles[0]
    assert cyc.expiry_date == dates[45]
    assert cyc.expiry_date != dates[30]  # the naive i+tenor_days settlement day
    assert cyc.payoff == pytest.approx(5000.0)
    assert cyc.net == pytest.approx(3998.375)

    naive_payoff = cyc.contracts * max(cyc.strike - px[30], 0.0)
    assert naive_payoff == pytest.approx(0.0)
    assert cyc.payoff != pytest.approx(naive_payoff)


# ---- 4. no double spread ------------------------------------------------------


def test_spread_scale_is_ignored_on_the_market_path() -> None:
    """The exact fixture from the pinned test above, run twice with
    ``spread_scale=1.0`` and ``spread_scale=0.0`` -- the only argument that
    differs. On the model path these would differ by a real half-spread
    (``brokerage.half_spread_frac`` is never zero); on the market path both
    must charge commission only (``cost=2.6`` exactly, per the pinned test),
    because the premium is already the historical ask and a half-spread on
    top of it would double-count a cost already paid.
    """
    px = [100.0] * 35
    px[34] = 84.0
    prices, realized_vol, dates = _build(px)

    def _source() -> _FakeQuoteSource:
        return _FakeQuoteSource(
            fills={
                dates[20]: Fill(
                    premium=2.5,
                    strike=90.0,
                    expiry=dates[34],
                    realized_moneyness_pct=10.0,
                    realized_dte=14,
                )
            }
        )

    kwargs = dict(
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=2.0,
        lookback_years=5.0,
    )
    result_spread = run_put_roll(
        prices, realized_vol, basis=PricingBasis(quotes=_source()), spread_scale=1.0, **kwargs
    )
    result_no_spread = run_put_roll(
        prices, realized_vol, basis=PricingBasis(quotes=_source()), spread_scale=0.0, **kwargs
    )

    assert result_spread.cycles[0].cost == pytest.approx(2.6)
    assert result_spread.cycles[0].cost == pytest.approx(result_no_spread.cycles[0].cost)
    assert result_spread.total_brokerage == pytest.approx(result_no_spread.total_brokerage)
    assert result_spread.net_pnl == pytest.approx(result_no_spread.net_pnl)


# ---- 5. annualize over the traded span ----------------------------------------


def test_annualization_uses_the_traded_span_not_the_nominal_window() -> None:
    """One 7-session cycle (day 20 -> day 27) inside a nominal
    ``lookback_years=2`` window it barely touches; days 27 and 28 (the two
    re-entry attempts that follow) find no quote, so ``n_cycles_skipped=2``
    triggers the traded-span annualization.

    By hand: premium 10.0 -> contracts=100.0, commission=0.65,
    payoff=100*max(90-85,0)=500, net=500-1000-0.65=-500.65,
    roi_on_premium=-0.50065. Naively annualizing that ROI over the nominal
    2-year window is a middling -29.3%/yr
    (``annualized_return(-0.50065, 2.0)``); the HONEST figure compounds the
    same ROI over the real 7/365.25-year traded span instead, which is
    dramatically worse (a 7-day span raises ``1+roi`` to the ~52nd power) --
    this mirrors the spec's measured -15.0%/yr nominal vs -26.2%/yr traded-span
    finding on a real SPY run, just with a starker gap because this fixture's
    traded span is far shorter than a typical one.
    """
    px = [100.0] * 29
    px[27] = 85.0
    prices, realized_vol, dates = _build(px)
    source = _FakeQuoteSource(
        fills={
            dates[20]: Fill(
                premium=10.0,
                strike=90.0,
                expiry=dates[27],
                realized_moneyness_pct=10.0,
                realized_dte=7,
            )
        }
    )

    result = run_put_roll(
        prices,
        realized_vol,
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=1.0,
        lookback_years=2.0,
        basis=PricingBasis(quotes=source),
    )

    assert result.n_cycles_skipped == 2
    assert result.roi_on_premium == pytest.approx(-0.50065)
    assert result.traded_start == dates[20]
    assert result.traded_end == dates[27]

    traded_years = (dates[27] - dates[20]).days / 365.25
    expected_traded = annualized_return(-0.50065, traded_years)
    naive = annualized_return(-0.50065, 2.0)

    assert result.annualized_return == pytest.approx(expected_traded)
    assert naive > -0.5  # the wrong, nominal-window figure looks mild
    assert result.annualized_return < -0.9  # the honest, traded-span figure is near-total
    assert result.annualized_return < naive  # materially worse, not just different


# ---- 6. adversarial: never reads a quote outside a cycle's own window --------


def test_adversarial_marks_never_read_a_quote_outside_the_cycles_own_window() -> None:
    """Reuses the two-cycle fixture from the coverage test and adds a TRAP: a
    mark registered for day 24 (cycle 2's own entry day, and the very next
    session after cycle 1's day-23 expiry) but keyed to cycle 1's OWN
    (strike, expiry) pair, with an absurd bid (999.0) that would corrupt the
    mark-to-market curve if the engine ever confused "the day after a cycle
    closed" with "still marking that closed cycle." Because
    ``_mark_to_market_curve`` looks up ``(day, open cycle's strike, open
    cycle's expiry)`` -- and cycle 1 is no longer the open cycle on day 24 --
    that trap key can never be constructed by the engine, which this test
    confirms directly by never seeing it in the call log.

    More generally: every ``fill`` call happens in non-decreasing date order
    (the engine walks forward, never backtracks to reconsider an earlier
    session once past it -- docs/adr/0009's invariant applied to put_roll's
    OWN loop, not just quote_fills's guards), and every ``mark`` call for a
    given open cycle is dated within THAT cycle's own ``[entry_date,
    expiry_date]`` -- never before it existed, never after it already
    settled.
    """
    px = [100.0] * 30
    prices, realized_vol, dates = _build(px)
    trap_key = (dates[24], 90.0, dates[23])  # cycle 1's contract, one day past its own expiry
    source = _FakeQuoteSource(
        fills={
            dates[20]: Fill(
                premium=2.0,
                strike=90.0,
                expiry=dates[23],
                realized_moneyness_pct=10.0,
                realized_dte=3,
            ),
            dates[24]: Fill(
                premium=1.5,
                strike=88.0,
                expiry=dates[29],
                realized_moneyness_pct=12.0,
                realized_dte=5,
            ),
        },
        marks={trap_key: 999.0},
    )

    result = run_put_roll(
        prices,
        realized_vol,
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=1.0,
        lookback_years=5.0,
        basis=PricingBasis(quotes=source),
    )

    assert source.fill_calls == sorted(source.fill_calls)
    assert trap_key not in source.mark_calls

    by_contract = {(cyc.strike, cyc.expiry_date): cyc for cyc in result.cycles}
    for as_of, strike, expiry in source.mark_calls:
        cyc = by_contract[(strike, expiry)]
        assert cyc.entry_date <= as_of <= cyc.expiry_date


# ---- 7. OptionsDxQuoteSource.mark(), the capability this wiring relies on ----


def _mark_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "underlying": "SPY",
        "quote_date": pd.Timestamp("2024-01-05"),
        "expiration": pd.Timestamp("2024-02-02"),
        "strike": 90.0,
        "bid": 1.20,
        "ask": 1.30,
        # `mark` never reads spot, but a QuoteSource that cannot also `fill`
        # is not a usable source, so `build_session_index` requires the whole
        # guard column set at construction rather than letting a partial panel
        # build and then KeyError from inside the first fill.
        "spot": 100.0,
    }
    base.update(overrides)
    return base


def test_optionsdx_mark_returns_the_bid_of_that_session_not_the_ask() -> None:
    """The same contract quoted on two sessions -- day 1 at 1.20/1.30, day 2
    (after some decay) at 1.05/1.15. ``mark`` on each day must return THAT
    day's bid (the sell-to-close price), never the ask ``fill`` would have
    paid, and never a stale day-1 value once day 2 is asked."""
    panel = pd.DataFrame(
        [
            _mark_row(quote_date=pd.Timestamp("2024-01-05"), bid=1.20, ask=1.30),
            _mark_row(quote_date=pd.Timestamp("2024-01-08"), bid=1.05, ask=1.15),
        ]
    )
    source = OptionsDxQuoteSource(panel, symbol="SPY")

    assert source.mark(
        entry_date=dt.date(2024, 1, 5), strike=90.0, expiry=dt.date(2024, 2, 2)
    ) == pytest.approx(1.20)
    assert source.mark(
        entry_date=dt.date(2024, 1, 8), strike=90.0, expiry=dt.date(2024, 2, 2)
    ) == pytest.approx(1.05)


def test_optionsdx_mark_refuses_a_session_with_no_matching_contract() -> None:
    """No session at all on the requested date, and a session that exists but
    lists nothing near the requested strike, both refuse (``None``) rather
    than guessing -- the caller (``_mark_to_market_curve``) treats ``None``
    as "carry the last real mark forward," which is only safe if a wrong
    number is never handed back in its place."""
    panel = pd.DataFrame([_mark_row()])
    source = OptionsDxQuoteSource(panel, symbol="SPY")

    assert (
        source.mark(entry_date=dt.date(2024, 1, 9), strike=90.0, expiry=dt.date(2024, 2, 2)) is None
    )
    assert (
        source.mark(entry_date=dt.date(2024, 1, 5), strike=250.0, expiry=dt.date(2024, 2, 2))
        is None
    )


# ---- 8. mark-to-market on the market path: bid, carry-forward, entry fallback -


def test_mtm_market_path_marks_at_the_bid_and_carries_forward() -> None:
    """One cycle, day 20 -> day 24 (5 marked days). ``contracts=200.0``
    (premium 5.0), ``cost=1.3`` (commission only). A bid is registered ONLY
    for day 22 (4.0); days 20, 21, 23 have none.

    By hand, per day (``unrealized = contracts*mark - notional - cost``):

    - day 20 (entry, no mark yet at all): falls back to the entry premium
      itself (5.0) -> ``200*5-1000-1.3 = -1.3``.
    - day 21 (still no mark ever seen): same fallback -> ``-1.3``.
    - day 22 (mark=4.0): ``200*4-1000-1.3 = -201.3``.
    - day 23 (no mark today, but 4.0 was seen on day 22): carried forward
      -> ``-201.3`` again.
    - day 24 (expiry): intrinsic ``max(90-80,0)=10`` (spot pinned to 80.0
      there) -> ``200*10-1000-1.3 = 998.7``, which must equal
      ``cycles[0].net`` exactly (the identity the module docstring states).
    """
    px = [100.0] * 25
    px[24] = 80.0
    prices, realized_vol, dates = _build(px)
    source = _FakeQuoteSource(
        fills={
            dates[20]: Fill(
                premium=5.0,
                strike=90.0,
                expiry=dates[24],
                realized_moneyness_pct=10.0,
                realized_dte=4,
            )
        },
        marks={(dates[22], 90.0, dates[24]): 4.0},
    )

    result = run_put_roll(
        prices,
        realized_vol,
        asset="TEST",
        as_of=dates[-1],
        notional=1000.0,
        moneyness_pct=10.0,
        tenor_weeks=1.0,
        lookback_years=5.0,
        basis=PricingBasis(quotes=source),
        include_curves=True,
    )

    assert [p.cum_pnl for p in result.mtm_curve] == [
        pytest.approx(v) for v in (-1.3, -1.3, -201.3, -201.3, 998.7)
    ]
    assert result.mtm_curve[-1].cum_pnl == pytest.approx(result.cycles[0].net)
    assert result.equity_curve[-1].cum_pnl == pytest.approx(result.cycles[0].net)


def test_a_fill_expiring_on_its_own_entry_day_cannot_hang_the_roll() -> None:
    """A listed expiry at or before the entry session must advance the loop.

    `bisect_left(dates, fill.expiry, lo=i)` returns `i` itself when the expiry
    is not after `dates[i]`, so `i = expiry_idx` left the index exactly where
    it was -- an unbounded loop appending a cycle every pass. Measured leaking
    90 MB/s, which exhausts the 1 GB production machine in about ten seconds.

    Reachable rather than theoretical: `marks._select_expiry` takes the nearest
    expiry AT OR AFTER `entry + round(tenor_weeks * 7)` days, so any
    `tenor_weeks <= 1/14` rounds that target to zero days and a 0DTE listing on
    the session satisfies it. The real SPY panel carries 100,956 rows across
    1,509 sessions where `expiration == quote_date`, and
    `/api/putlab/backtest` accepts `tenor_weeks` down to just above zero.

    The assertion is simply that this RETURNS. A hang has no traceback, and a
    test that hangs is indistinguishable from a slow one -- which is why the
    pytest timeout matters more than the value checked.
    """
    import numpy as np

    class _ZeroDteSource:
        def fill(
            self, *, entry_date: dt.date, spot: float, moneyness_pct: float, tenor_weeks: float
        ) -> Fill | None:
            return Fill(
                premium=0.06,
                strike=spot * 0.9,
                expiry=entry_date,  # settles the day it is bought
                realized_moneyness_pct=moneyness_pct,
                realized_dte=0,
            )

        def mark(
            self, *, entry_date: dt.date, strike: float, expiry: dt.date, basis: float = 1.0
        ) -> float | None:
            return None

    idx = pd.bdate_range("2024-01-02", periods=120)
    prices = pd.Series(np.linspace(100.0, 110.0, 120), index=idx)
    realized_vol = prices.pct_change().rolling(20).std() * np.sqrt(252)

    with pytest.raises(LookupError):
        # Every cycle is refused, so no roll completes and the engine reports
        # that rather than returning an empty result -- but it TERMINATES.
        run_put_roll(
            prices,
            realized_vol,
            asset="SPY",
            as_of=idx[-1].date(),
            notional=1000.0,
            moneyness_pct=10.0,
            tenor_weeks=0.05,
            lookback_years=0.4,
            basis=PricingBasis(quotes=_ZeroDteSource()),
            include_curves=False,
        )
