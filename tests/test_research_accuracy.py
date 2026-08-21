"""Tests for the accuracy context that must travel with every result.

The constitution's requirement is not "compute a residual" but "never leave
the surface silent", so most of what is pinned here is *degradation*: what the
report says when a piece of the lake is missing.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tail_lab.lake.store import DeltaLakeStore
from tail_lab.research.accuracy import (
    AccuracyReport,
    RegimeShare,
    benchmark_comparisons,
    blend_residual,
    compute_accuracy_report,
    model_accuracy,
    reference_distance,
    regime_mix,
    select_reference,
    standing_assumptions,
)
from tail_lab.research.backtest.index_replication import (
    DividendSensitivityPoint,
    IndexReplicationResult,
    ResidualBucket,
)

AS_OF = dt.date(2024, 12, 31)


def _pput(**overrides: object) -> IndexReplicationResult:
    """A PPUT replication result shaped like the real one, with the measured
    regime signs: cheap in calm, dear in crisis."""
    base: dict[str, object] = dict(
        index_symbol="PPUT",
        description="S&P 500 + 5% OTM one-month put, rolled monthly",
        start=dt.date(1990, 1, 19),
        end=dt.date(2024, 12, 20),
        years=35.0,
        n_rolls=420,
        rolls_per_year=12.0,
        moneyness_pct=5.0,
        rate=0.04,
        dividend_yield=0.019,
        mean_premium_pct=0.0056,
        correlation=0.99,
        tracking_error=0.017,
        annualized_drag=0.0134,
        replicated_cagr=0.0911,
        actual_cagr=0.0777,
        by_year=[],
        by_regime=[
            ResidualBucket(
                key="calm",
                n_rolls=210,
                years=17.5,
                mean_residual=0.001,
                annualized_drag=0.0155,
                tracking_error=0.009,
            ),
            ResidualBucket(
                key="elevated",
                n_rolls=185,
                years=15.0,
                mean_residual=0.001,
                annualized_drag=0.0165,
                tracking_error=0.018,
            ),
            ResidualBucket(
                key="crisis",
                n_rolls=43,
                years=3.5,
                mean_residual=-0.001,
                annualized_drag=-0.0146,
                tracking_error=0.033,
            ),
        ],
        dividend_sensitivity=[
            DividendSensitivityPoint(dividend_yield=0.014, annualized_drag=0.0088),
            DividendSensitivityPoint(dividend_yield=0.024, annualized_drag=0.0181),
        ],
        rolls=[],
    )
    base.update(overrides)
    return IndexReplicationResult(**base)  # type: ignore[arg-type]


def _timeline(labels: list[str], *, end: dt.date) -> pd.Series:
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=len(labels))
    return pd.Series(labels, index=idx)


# ------------------------------------------------------------- regime mix


def test_regime_mix_shares_sum_to_one_and_rank_by_size() -> None:
    timeline = _timeline(["calm"] * 60 + ["crisis"] * 40, end=AS_OF)

    mix = regime_mix(timeline, start=dt.date(2000, 1, 1), end=AS_OF)

    assert [s.regime for s in mix] == ["calm", "crisis"]
    assert sum(s.share for s in mix) == pytest.approx(1.0)
    assert sum(s.n_days for s in mix) == 100


def test_a_regime_the_window_never_entered_is_omitted_not_shown_as_zero() -> None:
    """A row reading 'crisis 0%' invites the reader to conclude the window was
    tested against a crisis. It wasn't."""
    mix = regime_mix(_timeline(["calm"] * 30, end=AS_OF), start=dt.date(2000, 1, 1), end=AS_OF)
    assert [s.regime for s in mix] == ["calm"]


def test_a_window_with_no_trading_days_has_no_mix() -> None:
    timeline = _timeline(["calm"] * 30, end=AS_OF)
    assert regime_mix(timeline, start=dt.date(1980, 1, 1), end=dt.date(1980, 6, 1)) == []


# --------------------------------------------------------------- blending


def test_blended_residual_weights_each_regime_by_its_share() -> None:
    mix = [
        RegimeShare(regime="calm", n_days=75, share=0.75),
        RegimeShare(regime="crisis", n_days=25, share=0.25),
    ]
    blended = blend_residual({"calm": 0.02, "crisis": -0.02}, mix)
    assert blended == pytest.approx(0.75 * 0.02 + 0.25 * -0.02)


def test_a_crisis_heavy_window_is_told_its_error_points_the_other_way() -> None:
    """The whole reason the residual is blended rather than quoted globally."""
    calm_window = [
        RegimeShare(regime="calm", n_days=90, share=0.9),
        RegimeShare(regime="crisis", n_days=10, share=0.1),
    ]
    crisis_window = [
        RegimeShare(regime="calm", n_days=10, share=0.1),
        RegimeShare(regime="crisis", n_days=90, share=0.9),
    ]
    buckets = {"calm": 0.0155, "crisis": -0.0146}

    assert blend_residual(buckets, calm_window) > 0.0
    assert blend_residual(buckets, crisis_window) < 0.0


def test_regimes_without_a_measured_residual_are_dropped_and_weights_renormalized() -> None:
    mix = [
        RegimeShare(regime="calm", n_days=50, share=0.5),
        RegimeShare(regime="elevated", n_days=50, share=0.5),
    ]
    assert blend_residual({"calm": 0.02}, mix) == pytest.approx(0.02)


def test_no_overlap_at_all_yields_no_number_rather_than_zero() -> None:
    """Zero would read as 'the model is unbiased here', which is a claim."""
    mix = [RegimeShare(regime="crisis", n_days=10, share=1.0)]
    assert blend_residual({"calm": 0.02}, mix) is None
    assert blend_residual({}, mix) is None
    assert blend_residual({"calm": 0.02}, []) is None


# ----------------------------------------------------------- applicability


def test_the_residual_applies_directly_to_a_matching_index_run() -> None:
    mix = [RegimeShare(regime="calm", n_days=100, share=1.0)]
    acc = model_accuracy([_pput()], mix, asset="spy", moneyness_pct=5.0, tenor_weeks=4.0)

    assert acc.applicability == "direct"
    assert acc.expected_optimism == pytest.approx(0.0155)
    assert "0.88%" in acc.basis and "1.81%" in acc.basis  # the yield range travels with it


def test_a_single_name_run_is_marked_indicative_not_direct() -> None:
    mix = [RegimeShare(regime="calm", n_days=100, share=1.0)]
    acc = model_accuracy([_pput()], mix, asset="tsla", moneyness_pct=15.0, tenor_weeks=2.0)

    assert acc.applicability == "indicative"
    assert "TSLA" in acc.caveat
    assert "floor" in acc.caveat  # not a correction to subtract


def test_a_matching_underlying_at_a_far_strike_is_still_only_indicative() -> None:
    mix = [RegimeShare(regime="calm", n_days=100, share=1.0)]
    acc = model_accuracy([_pput()], mix, asset="spy", moneyness_pct=20.0, tenor_weeks=4.0)
    assert acc.applicability == "indicative"


def test_with_no_replication_the_panel_says_unmeasured_rather_than_nothing() -> None:
    acc = model_accuracy([], [], asset="spy", moneyness_pct=5.0, tenor_weeks=4.0)

    assert acc.applicability == "unmeasured"
    assert acc.expected_optimism is None
    assert "not been measured" in acc.caveat
    assert acc.residual_by_regime == {}


def test_a_replication_without_a_sensitivity_still_reports_a_basis() -> None:
    acc = model_accuracy(
        [_pput(dividend_sensitivity=[])],
        [RegimeShare(regime="calm", n_days=1, share=1.0)],
        asset="spy",
        moneyness_pct=5.0,
        tenor_weeks=4.0,
    )
    assert "1.34%" in acc.basis


# ------------------------------------------------------------- benchmarks


def _strategy_bronze(dates: pd.DatetimeIndex) -> pd.DataFrame:
    ramp = np.linspace(100.0, 150.0, len(dates))
    return pd.concat(
        [
            pd.DataFrame({"index_symbol": sym, "trade_date": dates, "close": ramp * mult})
            for sym, mult in (("PPUT", 1.0), ("SPX", 1.0), ("CLL", 1.0))
        ],
        ignore_index=True,
    )


def test_benchmarks_report_total_and_annualized_over_the_window() -> None:
    dates = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=252)
    rows = benchmark_comparisons(
        _strategy_bronze(dates), start=dates[0].date(), end=AS_OF, years=1.0
    )

    assert {r.index_symbol for r in rows} == {"PPUT", "SPX", "CLL"}
    for row in rows:
        assert row.total_return == pytest.approx(0.5)
        assert row.annualized == pytest.approx(0.5)
        assert row.label  # the catalogue description, not just the ticker


def test_a_program_whose_history_misses_the_window_is_omitted_not_zeroed() -> None:
    """CLL starts in 2008. Showing 0% for a window it does not cover would read
    as 'flat', not 'not applicable'."""
    dates = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=252)
    bronze = _strategy_bronze(dates)
    bronze = bronze[~((bronze["index_symbol"] == "CLL") & (bronze["trade_date"] > dates[5]))]

    rows = benchmark_comparisons(bronze, start=dates[0].date(), end=AS_OF, years=1.0)

    assert "CLL" not in {r.index_symbol for r in rows}
    assert "PPUT" in {r.index_symbol for r in rows}


def test_a_nonpositive_starting_level_is_skipped_rather_than_dividing_by_zero() -> None:
    dates = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=252)
    bronze = _strategy_bronze(dates)
    bronze.loc[bronze["index_symbol"] == "PPUT", "close"] = 0.0

    rows = benchmark_comparisons(bronze, start=dates[0].date(), end=AS_OF, years=1.0)
    assert "PPUT" not in {r.index_symbol for r in rows}


# ------------------------------------------------------------ assumptions


def test_assumptions_carry_the_measured_optimism_where_it_is_known() -> None:
    measured = standing_assumptions(rate=0.04, expected_optimism=0.0134)
    premium = next(a for a in measured if a.name == "Option premiums")
    assert premium.leverage is not None and "+1.34%/yr" in premium.leverage

    unmeasured = standing_assumptions(rate=0.04, expected_optimism=None)
    premium = next(a for a in unmeasured if a.name == "Option premiums")
    assert premium.leverage == "unmeasured for this window"


def test_the_raw_close_choice_is_recorded_as_deliberate() -> None:
    """It looks like a bug to anyone who has not read DISCOVERIES #1."""
    prices = next(
        a for a in standing_assumptions(rate=0.04, expected_optimism=None) if a.name == "Prices"
    )
    assert "deliberate" in (prices.leverage or "")


# ------------------------------------------------- end-to-end degradation


def _seed(store: DeltaLakeStore, *, ingest: dt.date, days: int = 900) -> pd.DatetimeIndex:
    dates = pd.bdate_range(end=pd.Timestamp(ingest), periods=days)
    ramp = np.linspace(100.0, 160.0, days)
    store.write_bronze("cboe_strategy", ingest, _strategy_bronze(dates))
    store.write_bronze("vix", ingest, pd.DataFrame({"date": dates, "close": np.full(days, 15.0)}))
    store.write_bronze(
        "ohlcv_spy",
        ingest,
        pd.DataFrame(
            {
                "symbol": "SPY",
                "trade_date": dates,
                "open": ramp,
                "high": ramp * 1.001,
                "low": ramp * 0.999,
                "close": ramp,
                "volume": np.full(days, 1_000_000, dtype=int),
                "adj_close": ramp,
            }
        ),
    )
    return dates


def test_the_report_assembles_every_block_when_the_lake_is_complete(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2024, 12, 31)
    _seed(store, ingest=ingest)

    report = compute_accuracy_report(
        store,
        asset="spy",
        as_of=ingest,
        years=2.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        replications=[_pput()],
    )

    assert isinstance(report, AccuracyReport)
    assert report.asset == "SPY"
    assert report.model.expected_optimism is not None
    assert report.model.regime_mix and report.benchmarks
    assert report.data_quality_flags == 0
    assert "clean" in report.data_quality_note
    assert len(report.assumptions) == 5


def test_a_missing_cboe_snapshot_costs_the_benchmarks_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Every block degrades independently — the panel must never go dark
    wholesale, because a blank panel reads as 'no concerns'."""
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2024, 12, 31)
    dates = pd.bdate_range(end=pd.Timestamp(ingest), periods=400)
    ramp = np.linspace(100.0, 160.0, 400)
    store.write_bronze("vix", ingest, pd.DataFrame({"date": dates, "close": np.full(400, 15.0)}))
    store.write_bronze(
        "ohlcv_spy",
        ingest,
        pd.DataFrame(
            {
                "symbol": "SPY",
                "trade_date": dates,
                "open": ramp,
                "high": ramp * 1.001,
                "low": ramp * 0.999,
                "close": ramp,
                "volume": np.full(400, 1_000_000, dtype=int),
                "adj_close": ramp,
            }
        ),
    )

    report = compute_accuracy_report(
        store,
        asset="spy",
        as_of=ingest,
        years=1.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
    )

    assert report.benchmarks == []
    assert report.model.applicability == "unmeasured"  # replication needs the same snapshot
    assert report.model.regime_mix  # VIX survived
    assert report.data_quality_flags == 0  # so did the price scan


def test_a_missing_price_snapshot_is_said_out_loud(tmp_path: Path) -> None:
    store = DeltaLakeStore(tmp_path)
    ingest = dt.date(2024, 12, 31)
    _seed(store, ingest=ingest)

    report = compute_accuracy_report(
        store,
        asset="nosuchticker",
        as_of=ingest,
        years=1.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
        replications=[_pput()],
    )

    assert report.data_quality_flags is None
    assert report.data_quality_note == "no price snapshot to scan"


def test_an_empty_lake_still_produces_a_readable_report(tmp_path: Path) -> None:
    report = compute_accuracy_report(
        DeltaLakeStore(tmp_path),
        asset="spy",
        as_of=AS_OF,
        years=3.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
    )

    assert report.model.applicability == "unmeasured"
    assert report.model.expected_optimism is None
    assert report.benchmarks == []
    assert report.data_quality_flags is None
    assert report.assumptions  # the assumptions never depend on the lake


def test_the_window_start_is_derived_from_the_lookback(tmp_path: Path) -> None:
    report = compute_accuracy_report(
        DeltaLakeStore(tmp_path),
        asset="spy",
        as_of=AS_OF,
        years=4.0,
        moneyness_pct=5.0,
        tenor_weeks=4.0,
    )
    assert (AS_OF - report.window_start).days == round(4.0 * 365.25)


def _pput3m(**overrides: object) -> IndexReplicationResult:
    """The quarterly 10%-OTM sibling. Its residual is roughly double PPUT's,
    because that is where the skew a flat-vol model ignores actually lives."""
    base = _pput().model_dump()
    base.update(
        index_symbol="PPUT3M",
        description="S&P 500 + 10% OTM quarterly put (Cboe S&P 500 Tail Risk Index)",
        moneyness_pct=10.0,
        rolls_per_year=4.0,
        annualized_drag=0.0271,
        by_regime=[
            ResidualBucket(
                key="calm",
                n_rolls=47,
                years=11.7,
                mean_residual=0.006,
                annualized_drag=0.0256,
                tracking_error=0.0096,
            ),
            ResidualBucket(
                key="crisis",
                n_rolls=8,
                years=2.0,
                mean_residual=0.007,
                annualized_drag=0.0289,
                tracking_error=0.0065,
            ),
        ],
    )
    base.update(overrides)
    return IndexReplicationResult(**base)


def test_a_deep_otm_run_is_measured_against_the_deep_otm_program() -> None:
    """Quoting PPUT's 5%-OTM residual at a 20%-OTM tail hedge would understate
    its optimism by half — the residual grows with strike depth."""
    chosen = select_reference([_pput(), _pput3m()], moneyness_pct=12.0, tenor_weeks=12.0)
    assert chosen is not None and chosen.index_symbol == "PPUT3M"

    shallow = select_reference([_pput(), _pput3m()], moneyness_pct=5.0, tenor_weeks=4.0)
    assert shallow is not None and shallow.index_symbol == "PPUT"


def test_the_deeper_reference_reports_the_larger_optimism() -> None:
    mix = [RegimeShare(regime="calm", n_days=100, share=1.0)]
    shallow = model_accuracy(
        [_pput(), _pput3m()], mix, asset="spy", moneyness_pct=5.0, tenor_weeks=4.0
    )
    deep = model_accuracy(
        [_pput(), _pput3m()], mix, asset="spy", moneyness_pct=10.0, tenor_weeks=13.0
    )

    assert shallow.expected_optimism is not None and deep.expected_optimism is not None
    assert deep.expected_optimism > shallow.expected_optimism
    assert "PPUT3M" in deep.caveat or "PPUT3M" in deep.basis
    assert deep.applicability == "direct"


def test_an_unknown_program_is_never_selected_as_a_reference() -> None:
    """A stray replication (say CLL) must not silently become the error bar for
    a put strategy it does not describe."""
    assert reference_distance("CLL", moneyness_pct=5.0, tenor_weeks=4.0) == float("inf")
    assert select_reference([_pput(index_symbol="CLL")], moneyness_pct=5.0, tenor_weeks=4.0) is None


def test_losing_one_reference_still_leaves_an_error_bar() -> None:
    mix = [RegimeShare(regime="calm", n_days=100, share=1.0)]
    acc = model_accuracy([_pput3m()], mix, asset="spy", moneyness_pct=5.0, tenor_weeks=4.0)

    assert acc.expected_optimism is not None
    assert acc.applicability == "indicative"  # PPUT3M does not describe a 5%/4wk run
