"""Tests for the Book's "how much to hold" answer (hedge_sizing).

The pinned case is the two-state Kelly bet with a closed form: against a
flat index, a program that alternates +50% / -40% month on month is the coin
flip whose growth-optimal stake is ``f* = p/b - q/a = 0.5/0.4 - 0.5/0.5 =
0.25``. A wrong rebalancing segment, a wrong log-growth sum or a broken search
each moves that number.

The gate is the desk-facing rule: the size is 0 unless the hedge beats NO
hedge by more than 1bp/yr on BOTH legs, and an interior optimum is halved
because one history estimates it.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from tail_lab.research.backtest.hedge_overlay import WEIGHTS, blend_nav
from tail_lab.research.backtest.hedge_sizing import (
    MIN_MARGIN,
    W_TOL,
    Segments,
    SizingInputs,
    _recommend,
    golden_max,
    naive_kelly,
    segments,
    size,
    stability,
    w_star,
)
from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR


def _monthly(n: int, start: str = "2000-01-03") -> pd.DatetimeIndex:
    """One observation a month, on its first business day: every date is a
    rebalance, so each segment is exactly one month."""
    return pd.date_range(start, periods=n, freq="BMS")


def _levels(index: pd.DatetimeIndex, gross: list[float]) -> pd.Series:
    """Levels whose month-to-month gross returns are ``gross``."""
    return pd.Series(np.cumprod([1.0, *gross]), index=index)


def _two_state(n_pairs: int = 60, up: float = 1.5, down: float = 0.6) -> Segments:
    index = _monthly(2 * n_pairs + 1)
    program = _levels(index, [up, down] * n_pairs)
    flat = pd.Series(1.0, index=index)
    return segments(program, flat)


def _inputs(seg: Segments, **kw: object) -> SizingInputs:
    grid = max(WEIGHTS, key=lambda w: seg.log_sum(w))
    args: dict[str, object] = {
        "base": seg,
        "conservative": seg,
        "grid_best": grid,
        "withheld": None,
    }
    args.update(kw)
    return SizingInputs(**args)  # type: ignore[arg-type]


def test_two_state_kelly_lands_on_the_closed_form_quarter() -> None:
    seg = _two_state()
    assert w_star(seg) == pytest.approx(0.25, abs=1e-5)
    # And g itself is the analytic sum: ln(1 + w/2) + ln(1 - 0.4 w) per pair.
    w = 0.25
    per_pair = math.log(1 + 0.5 * w) + math.log(1 - 0.4 * w)
    assert seg.log_sum(w) == pytest.approx(60 * per_pair, rel=1e-12)


def test_segment_growth_reconciles_with_the_blend_the_page_draws() -> None:
    """The size must be computed on the same NAV the Rodman chart plots: the
    product of segment returns equals blend_nav's last value at every mix,
    on a daily calendar with partial first and last months."""
    index = pd.bdate_range("2003-03-12", "2009-07-17")
    rng = np.random.default_rng(7)
    program = pd.Series(np.cumprod(1 + rng.normal(0.0002, 0.01, len(index))), index=index)
    equity = pd.Series(np.cumprod(1 + rng.normal(0.0003, 0.012, len(index))), index=index)
    seg = segments(program, equity)
    for w in WEIGHTS:
        nav = blend_nav(program, equity, weight=w)
        assert math.exp(seg.log_sum(w)) == pytest.approx(float(nav.iloc[-1]), rel=1e-10)
    assert seg.years == pytest.approx((index[-1] - index[0]).days / DAYS_PER_YEAR)


def test_segments_refuse_misaligned_legs() -> None:
    a = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2020-01-02", "2020-01-03"]))
    b = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2020-01-02", "2020-01-06"]))
    with pytest.raises(ValueError, match="share one date index"):
        segments(a, b)


def test_the_refined_optimum_is_never_worse_than_the_grid() -> None:
    """Golden section refines the 0.1 grid; it must never hand back a ratio
    that grows slower than the grid's best (including at a bound)."""
    rng = np.random.default_rng(11)
    for _ in range(25):
        index = _monthly(121)
        program = _levels(index, list(1 + rng.normal(0.004, 0.05, 120)))
        equity = _levels(index, list(1 + rng.normal(0.006, 0.04, 120)))
        seg = segments(program, equity)
        best_grid = max(seg.log_sum(w) for w in WEIGHTS)
        assert seg.log_sum(w_star(seg)) >= best_grid - 1e-12


def test_golden_section_returns_a_bound_exactly() -> None:
    assert golden_max(lambda w: -w) == 0.0
    assert golden_max(lambda w: w) == 1.0
    assert golden_max(lambda w: -((w - 0.3) ** 2)) == pytest.approx(0.3, abs=W_TOL)


def test_an_interior_optimum_is_halved_and_says_why() -> None:
    answer = size(_inputs(_two_state()))
    assert answer.w_star == pytest.approx(0.25, abs=1e-5)
    assert answer.recommended_ratio == pytest.approx(0.125, abs=1e-5)
    assert "half of the full-Kelly" in answer.reason
    assert answer.w_star_grid == 0.2  # the 0.1 grid's best: 0.2 beats 0.3 here
    # Margins in CAGR units, on both legs, against NO hedge.
    assert answer.margin_by_leg["base"] == pytest.approx(answer.g_star - answer.g0)
    assert set(answer.margin_by_leg) == {"base", "conservative"}
    # g at the markers is the curve's own value.
    assert answer.g0 == pytest.approx(answer.curve[0].cagr)
    assert answer.curve[-1].weight == 1.0
    # Break-even: past w*, where growth falls back to g(0).
    assert answer.break_even_status == "found" and answer.break_even is not None
    seg = _two_state()
    assert answer.break_even > answer.w_star
    assert seg.log_sum(answer.break_even) == pytest.approx(seg.log_sum(0.0), abs=1e-6)


def test_half_kelly_keeps_about_three_quarters_of_the_gain_on_small_bets() -> None:
    """The 3/4 line is exact for a quadratic g; small symmetric bets are
    nearly quadratic, so the measured share sits close to it. It is served as
    measured because a fat-tailed program need not be."""
    answer = size(_inputs(_two_state(up=1.02, down=0.9805)))
    # Interior, not capped: (a - b) / 2ab = 0.0005 / 0.00078 = 0.641.
    assert answer.w_star == pytest.approx(0.0005 / (2 * 0.02 * 0.0195), abs=1e-4)
    assert answer.half_keeps == pytest.approx(0.75, abs=0.01)


def test_naive_kelly_agrees_on_small_symmetric_bets_and_is_undefined_without_variance() -> None:
    """The mean-variance contrast is the approximation the exact search
    replaces: close when returns are small and symmetric, and it says
    nothing when the program never differs from the index."""
    seg = _two_state(up=1.02, down=0.9805)
    nk = naive_kelly(seg)
    assert nk is not None and nk == pytest.approx(w_star(seg), rel=0.1)
    index = _monthly(13)
    same = _levels(index, [1.01] * 12)
    assert naive_kelly(segments(same, same)) is None


def test_a_program_that_always_lags_gets_none_with_the_reason() -> None:
    """The live PPUT shape: growth falls at every step of the hedge ratio."""
    index = _monthly(61)
    seg = segments(_levels(index, [1.004] * 60), _levels(index, [1.008] * 60))
    answer = size(_inputs(seg))
    assert answer.w_star == 0.0
    assert answer.recommended_ratio == 0.0
    assert answer.reason.startswith("growth falls at every step")
    assert answer.break_even is None and answer.break_even_status == "none"
    assert answer.half_keeps is None


def test_a_dominant_program_is_capped_at_half_and_not_called_half_kelly() -> None:
    index = _monthly(61)
    seg = segments(_levels(index, [1.01] * 60), _levels(index, [1.005] * 60))
    answer = size(_inputs(seg))
    assert answer.w_star == 1.0
    assert answer.recommended_ratio == 0.5
    assert "dominates" in answer.reason and "not half-Kelly" in answer.reason
    assert answer.break_even_status == "beyond_1"


@pytest.mark.parametrize(
    ("margins", "expected"),
    [
        # The smaller leg binds, and the bar is strict: exactly 1bp is no edge.
        ({"base": 5e-4, "conservative": MIN_MARGIN}, 0.0),
        ({"base": MIN_MARGIN, "conservative": 5e-4}, 0.0),
        ({"base": 5e-4, "conservative": MIN_MARGIN * 1.001}, 0.15),
    ],
)
def test_the_gate_is_strict_on_the_smaller_of_the_two_margins(
    margins: dict[str, float], expected: float
) -> None:
    ratio, reason = _recommend(_inputs(_two_state()), 0.3, margins)
    assert ratio == pytest.approx(expected)
    if expected == 0.0:
        assert "after the cash-drag add-back" in reason and "1bp/yr bar" in reason


def test_degraded_inputs_withhold_the_size_rather_than_gate_on_one_leg() -> None:
    answer = size(
        _inputs(
            _two_state(), conservative=None, withheld="sizing needs T-bills for the cash-drag check"
        )
    )
    assert answer.recommended_ratio is None
    assert answer.reason == "sizing needs T-bills for the cash-drag check"
    # The full-Kelly reading is still shown -- only the recommendation is withheld.
    assert answer.w_star == pytest.approx(0.25, abs=1e-5)
    assert set(answer.margin_by_leg) == {"base"}


def test_a_grid_that_disagrees_with_the_refined_value_across_the_bar_is_named() -> None:
    """The grid can miss an edge the refined search finds (VXTH-sized
    margins). The page must say which one decided."""
    answer = size(_inputs(_two_state(), grid_best=0.0))
    assert answer.grid_note is not None and "refined value decides" in answer.grid_note
    assert size(_inputs(_two_state())).grid_note is None


def _bleed_with_one_crash(n: int = 120, crash: int = 30) -> tuple[Segments, dt.date]:
    """A put-like program: -0.5% a month against a flat index, except one
    month that pays +80%. That month alone makes holding any of it worth it."""
    index = _monthly(n + 1)
    gross = [0.995] * n
    gross[crash] = 1.8
    seg = segments(_levels(index, gross), pd.Series(1.0, index=index))
    return seg, index[crash].date()


def test_leave_one_month_out_names_the_month_that_carries_the_hedge() -> None:
    seg, crash = _bleed_with_one_crash()
    answer = size(_inputs(seg))
    assert answer.w_star > 0.05
    top = answer.decisive_months[0]
    assert top.month == crash.strftime("%Y-%m")
    assert top.w_star_without == 0.0
    assert top.delta == pytest.approx(-answer.w_star)
    assert len(answer.decisive_months) <= 3


def test_halves_show_an_optimum_that_lives_in_one_half() -> None:
    seg, _ = _bleed_with_one_crash(crash=30)
    answer = size(_inputs(seg))
    first, second = answer.halves
    assert first.w_star > 0.0 and second.w_star == 0.0
    assert first.start == seg.starts[0] and second.end == seg.starts[-1]


def test_a_zero_optimum_everywhere_lists_no_decisive_month() -> None:
    index = _monthly(61)
    seg = segments(_levels(index, [1.004] * 60), _levels(index, [1.008] * 60))
    answer = size(_inputs(seg))
    assert answer.decisive_months == []
    assert all(h.w_star == 0.0 for h in answer.halves)


def test_a_flat_curve_is_not_described_as_falling() -> None:
    """A program identical to the index leaves g flat: "none" is right, but
    "growth falls at every step" would contradict the curve served beside it."""
    index = _monthly(61)
    same = _levels(index, [1.01, 0.99] * 30)
    answer = size(_inputs(segments(same, same)))
    assert answer.recommended_ratio == 0.0
    assert answer.reason == "no hedge ratio out-grows no hedge"


def test_size_itself_never_gates_on_one_leg() -> None:
    """The two-leg rule is enforced where the size is made, not only by the
    caller: a missing or NaN conservative margin withholds."""
    seg = _two_state()
    assert size(_inputs(seg, conservative=None)).recommended_ratio is None
    nan_leg = Segments(p=seg.p * np.nan, e=seg.e, starts=seg.starts, years=seg.years)
    assert size(_inputs(seg, conservative=nan_leg)).recommended_ratio is None


def test_the_conservative_margin_is_read_at_the_base_legs_w_star() -> None:
    """The plan sizes from the BASE leg's w* and asks whether that size also
    clears the conservative leg -- so the conservative margin is that leg's
    growth at the base w*, not at its own optimum or the grid's."""
    base = _two_state()
    index = _monthly(121)
    program = _levels(index, [1.5, 0.6] * 60)
    drifting = _levels(index, [1.004, 0.999] * 60)  # a different index leg
    cons = segments(program, drifting)
    answer = size(_inputs(base, conservative=cons, grid_best=0.2))
    w = answer.w_star
    assert answer.margin_by_leg["conservative"] == pytest.approx(
        cons.cagr(w) - cons.cagr(0.0), rel=1e-12
    )
    assert answer.margin_by_leg["conservative"] != pytest.approx(
        cons.cagr(0.2) - cons.cagr(0.0), rel=1e-6
    )


def test_naive_kelly_maximises_the_mean_variance_growth_including_the_index_covariance() -> None:
    """With the index moving too, the mean-variance optimum of E[x + w d] -
    Var[x + w d] / 2 carries the covariance of x with d; checked against a
    brute-force maximisation of that same objective."""
    index = _monthly(121)
    rng = np.random.default_rng(5)
    eq = 1.0 + rng.normal(0.006, 0.04, 120)
    prog = eq + np.where(eq < 0.97, 0.05, -0.004)  # pays when the index falls
    seg = segments(_levels(index, list(prog)), _levels(index, list(eq)))
    x, d = seg.e - 1.0, seg.p - seg.e
    grid = np.linspace(-40, 40, 8001)  # 0.01 apart
    objective = [float(np.mean(x + w * d) - np.var(x + w * d, ddof=1) / 2) for w in grid]
    coarse = grid[int(np.argmax(objective))]
    assert -39 < coarse < 39  # interior: the grid brackets the optimum
    nk = naive_kelly(seg)
    assert nk is not None and nk == pytest.approx(coarse, abs=0.006)
    # ...and the covariance matters here: ignoring it lands elsewhere.
    assert abs(float(d.mean()) / float(np.var(d, ddof=1)) - nk) > 0.1


def test_decisive_months_rank_by_size_of_move_whatever_its_sign() -> None:
    """Five +20% crash months carry the hedge; one -50% month argues against
    it. Removing the bad month moves w* up by more than removing any crash
    moves it down, and the many small bleed months move it a little up. A
    ranking by signed change would bury the crash months under those."""
    index = _monthly(121)
    gross = [0.997] * 120
    for k in range(5):
        gross[10 + 20 * k] = 1.2
    gross[110] = 0.5
    seg = segments(_levels(index, gross), pd.Series(1.0, index=index))
    _, decisive = stability(seg, w_star(seg))
    moves = [d.delta for d in decisive]
    assert moves[0] > 0 and any(m < 0 for m in moves)
    assert [abs(m) for m in moves] == sorted((abs(m) for m in moves), reverse=True)
    assert decisive[0].month == index[110].strftime("%Y-%m")


def test_w_star_just_under_the_cap_is_halved_not_capped() -> None:
    """The cap is w* = 1 to within 1e-4 (``_CAP``); a w* of 0.995 is an
    interior optimum and is held at half, flagged not at the cap."""
    b = 0.1 / 1.199  # (a - b) / 2ab = 0.995 for a = 0.1
    answer = size(_inputs(_two_state(up=1.1, down=1 - b)))
    assert answer.w_star == pytest.approx(0.995, abs=1e-4)
    assert answer.at_cap is False
    assert answer.recommended_ratio == pytest.approx(answer.w_star / 2)
    dominant = size(_inputs(segments(*(_levels(_monthly(61), [g] * 60) for g in (1.01, 1.005)))))
    assert dominant.at_cap is True


def test_a_fully_hedged_book_that_only_just_out_grows_none_is_beyond_1() -> None:
    """g(1) a hair above g(0) still means growth never falls back to no hedge
    within 0-100%: "beyond 100% hedged", not a break-even root."""
    a = 0.1
    b = 1 - math.exp(1e-6) / (1 + a)  # each pair grows by exactly e^(1e-6)
    seg = _two_state(up=1 + a, down=1 - b)
    assert 0 < seg.log_sum(1.0) - seg.log_sum(0.0) < 1e-3
    answer = size(_inputs(seg))
    assert answer.break_even_status == "beyond_1" and answer.break_even is None


def test_small_but_real_moves_are_listed_as_decisive() -> None:
    """Only the one crash month moves w* a lot; the next-largest moves are
    small (bleed months) but real, and the list shows them rather than
    stopping at the first."""
    seg, _ = _bleed_with_one_crash()
    answer = size(_inputs(seg))
    assert len(answer.decisive_months) == 3
    assert 10 * 1e-6 < abs(answer.decisive_months[-1].delta) < 0.05


def test_a_fully_hedged_book_exactly_level_with_none_is_beyond_1() -> None:
    """g(1) == g(0) exactly (the program's months are the index's, reordered):
    growth only returns to no hedge at 100% itself, never inside 0-100%, so
    the boundary is "beyond_1" -- the ``>=`` in ``_break_even`` -- not a
    break-even root found at 1.0."""
    n = 60
    starts = tuple(d.date() for d in _monthly(n))
    seg = Segments(
        p=np.array([1.5, 0.8] * (n // 2)),
        e=np.array([0.8, 1.5] * (n // 2)),
        starts=starts,
        years=5.0,
    )
    assert seg.log_sum(1.0) == seg.log_sum(0.0)
    answer = size(_inputs(seg))
    assert answer.w_star == pytest.approx(0.5, abs=1e-5)  # symmetric: interior
    assert answer.break_even_status == "beyond_1" and answer.break_even is None


def test_a_move_between_the_tolerance_and_a_percent_point_is_decisive() -> None:
    """300 up/down pairs (+50% / -40% against a flat index): w* = 1/4, and
    leaving out one month moves it by about 0.38pp -- far above the 10 * W_TOL
    noise floor, under 1pp. Closed form: without one up month w* is
    (0.1k - 0.5) / (0.4k - 0.2), without one down month (0.1k + 0.4) / (0.4k -
    0.2), k = 300. A real move that small is still listed."""
    k = 300
    starts = tuple(d.date() for d in _monthly(2 * k, start="1950-01-02"))
    seg = Segments(p=np.array([1.5, 0.6] * k), e=np.ones(2 * k), starts=starts, years=2 * k / 12)
    answer = size(_inputs(seg))
    assert answer.w_star == pytest.approx(0.25, abs=1e-5)
    without_up = (0.1 * k - 0.5) / (0.4 * k - 0.2)
    without_down = (0.1 * k + 0.4) / (0.4 * k - 0.2)
    assert len(answer.decisive_months) == 3
    for d in answer.decisive_months:
        assert 10 * W_TOL < abs(d.delta) < 1e-2
        # The two moves are equal in size, so which months rank first is noise.
        assert min(abs(d.w_star_without - x) for x in (without_up, without_down)) < 1e-5
