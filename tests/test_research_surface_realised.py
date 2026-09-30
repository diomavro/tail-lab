"""Pinned tests for the gated realised tail index.

Price series are built so their down-moves are a known sample: the price drops
by ``r_i`` from 100 and returns to 100 before the next drop, so the arithmetic
losses are exactly the ``r_i`` and the up-moves are discarded.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tail_lab.research.surface import karamata as karamata_module
from tail_lab.research.surface import realised as realised_module
from tail_lab.research.surface.realised import gated_realised_alpha, step_prices
from tests.test_research_surface_hill import pareto_quantile_sample
from tests.test_research_surface_karamata import spliced_sample


def prices_with_losses(losses: list[float]) -> pd.Series:
    values = [100.0]
    for r in losses:
        values += [100.0 * (1.0 - r), 100.0]
    return pd.Series(values)


@pytest.fixture
def loose_onset(monkeypatch: pytest.MonkeyPatch) -> None:
    """``alpha=None`` starts from the k=30 Hill estimate, biased on an exact
    Pareto top (3.17 vs 3.0), so the default 5% flatness tolerance refuses even
    a textbook sample (measured flatness 0.206). A loose tolerance lets the
    gate's positive path run against a sample whose answer is known."""
    original = realised_module.karamata_onset
    monkeypatch.setattr(
        realised_module,
        "karamata_onset",
        lambda s, *, alpha: original(s, alpha=alpha, tolerance=0.3),
    )


@pytest.mark.usefixtures("loose_onset")
def test_a_pareto_loss_sample_recovers_its_alpha() -> None:
    losses = pareto_quantile_sample(n=500, alpha=3.0, karamata_l=0.01)
    result = gated_realised_alpha(prices_with_losses(losses))
    assert result.refusal is None
    assert result.plateau is not None
    assert result.plateau.alpha == pytest.approx(3.0, rel=0.1)
    assert result.fit is not None
    assert result.n_beyond == result.fit.n_beyond


def test_no_down_move_is_a_refusal_not_an_exception() -> None:
    result = gated_realised_alpha(pd.Series([100.0, 101.0, 102.0]))
    assert result.plateau is None
    assert result.fit is None
    assert result.n_beyond == 0
    assert result.refusal is not None
    assert "no left tail" in result.refusal


def test_too_few_losses_for_an_onset_is_a_refusal() -> None:
    result = gated_realised_alpha(prices_with_losses([0.01, 0.02, 0.03]))
    assert result.plateau is None
    assert result.fit is None
    assert result.refusal is not None
    assert "Karamata" in result.refusal


def test_an_unconverged_onset_is_refused_not_just_labelled() -> None:
    losses = [x / 100 for x in spliced_sample(n=600, n_tail=200, alpha=3.0, onset=2.0)]
    original = karamata_module.MAX_FIXED_POINT_ITERATIONS
    try:
        karamata_module.MAX_FIXED_POINT_ITERATIONS = 1
        result = gated_realised_alpha(prices_with_losses(losses))
    finally:
        karamata_module.MAX_FIXED_POINT_ITERATIONS = original
    assert result.plateau is None
    assert result.fit is not None
    assert not result.fit.converged
    assert result.refusal == "the Karamata onset did not converge"


def test_a_sample_with_no_flat_stretch_is_refused() -> None:
    losses = [0.0005 * (1 + i) for i in range(90)]  # linear: nothing like a power law
    result = gated_realised_alpha(prices_with_losses(losses))
    assert result.plateau is None
    assert result.fit is not None
    assert not result.fit.is_flat
    assert result.refusal is not None
    assert "flat to tolerance" in result.refusal
    assert result.n_beyond == result.fit.n_beyond


@pytest.mark.usefixtures("loose_onset")
def test_no_plateau_beyond_the_onset_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realised_module, "stable_k", lambda *args, **kwargs: None)
    losses = pareto_quantile_sample(n=500, alpha=3.0, karamata_l=0.01)
    result = gated_realised_alpha(prices_with_losses(losses))
    assert result.plateau is None
    assert result.refusal == "no Hill plateau beyond the Karamata onset"


def test_the_horizon_is_calendar_days_converted_to_trading_rows() -> None:
    prices = pd.Series(range(1, 1001), dtype=float)
    # 30 calendar days = round(30 * 252 / 365) = 21 trading rows.
    stepped = step_prices(prices, horizon_days=30)
    assert list(stepped.index[:3]) == [0, 21, 42]
    assert len(stepped) == 48
    # 1 calendar day rounds to 0.69 rows; a step is never below one row.
    assert len(step_prices(prices, horizon_days=1)) == 1000


def test_the_result_carries_the_stepped_series_it_fitted() -> None:
    prices = pd.Series([100.0 + i for i in range(100)])
    result = gated_realised_alpha(prices, horizon_days=30)
    pd.testing.assert_series_equal(result.stepped_prices, step_prices(prices, horizon_days=30))


def test_a_horizon_below_one_day_is_rejected() -> None:
    with pytest.raises(ValueError, match="horizon_days"):
        step_prices(pd.Series([1.0, 2.0]), horizon_days=0)
