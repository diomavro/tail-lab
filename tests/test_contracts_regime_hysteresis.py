from __future__ import annotations

import pytest

from tail_lab.contracts.regime import (
    CALM_MAX,
    ELEVATED_MAX,
    HYSTERESIS_BAND,
    classify_vix_level,
    classify_vix_series,
)


def test_a_zero_band_reproduces_the_bare_thresholds_exactly() -> None:
    """The audit hatch. Every published verdict before 2026-08-27 was produced
    by the level classifier, so the new code has to be able to reproduce it
    exactly -- otherwise the change is unfalsifiable rather than measured."""
    closes = [9.0, 16.99, 17.0, 27.99, 28.0, 60.0, 12.0, 22.0]
    assert classify_vix_series(closes, band=0.0) == [classify_vix_level(v) for v in closes]


def test_boundary_chatter_produces_one_label_not_three() -> None:
    """The defect this exists to fix. A VIX oscillating either side of 17 is
    not three changes of market regime, and `docs/adr/0015` keys hypothesis
    verdicts on regime -- so counting it as three lets a rule collect a second
    regime, and a `confirmed` verdict, from noise."""
    chatter = [16.8, 17.1, 16.9, 17.2, 16.7, 17.05]
    labels = classify_vix_series(chatter)

    assert set(labels) == {"calm"}
    # ...and the bare thresholds really would have flip-flopped, so the test
    # is measuring a difference rather than asserting a tautology.
    assert len(set(classify_vix_series(chatter, band=0.0))) == 2


def test_a_decisive_move_still_changes_regime() -> None:
    """Hysteresis must damp noise without deafening the classifier."""
    labels = classify_vix_series([12.0, 13.0, CALM_MAX + HYSTERESIS_BAND + 0.1])
    assert labels == ["calm", "calm", "elevated"]


def test_leaving_a_regime_needs_the_band_on_the_way_down_too() -> None:
    """Asymmetric entry alone would just move the threshold, not damp it."""
    # Enter elevated decisively, then sit just below the raw threshold.
    labels = classify_vix_series([12.0, 19.0, CALM_MAX - 0.5])
    assert labels == ["calm", "elevated", "elevated"]
    # Clear the band and it does drop.
    assert classify_vix_series([12.0, 19.0, CALM_MAX - HYSTERESIS_BAND - 0.1])[-1] == "calm"


def test_a_gap_skips_the_middle_regime() -> None:
    """A VIX that gaps from 14 to 40 was never briefly 'elevated'."""
    assert classify_vix_series([14.0, 40.0]) == ["calm", "crisis"]
    assert classify_vix_series([40.0, 14.0]) == ["crisis", "calm"]


def test_the_first_observation_is_labelled_by_level() -> None:
    assert classify_vix_series([22.0])[0] == "elevated"
    assert classify_vix_series([22.0], initial="calm")[0] == "elevated"
    # ...but a continuation seeded mid-band keeps its state.
    assert classify_vix_series([CALM_MAX + 0.2], initial="calm")[0] == "calm"


def test_a_negative_band_is_rejected() -> None:
    with pytest.raises(ValueError):
        classify_vix_series([15.0], band=-1.0)


# ---- causality, with the positive control that makes it evidence ------------
#
# `docs/PRIOR_ART.md` §9: a no-lookahead assertion with no contrast passes just
# as happily when the value is constant, absent, or never computed. Each test
# below therefore pairs "the causal path does not move" with "a deliberately
# non-causal path DOES move on the same data" -- the second half is what proves
# the first could have failed.

_SERIES = [12.0, 16.0, 18.0, 21.0, 19.0, 16.5, 15.0]
_FUTURE = [45.0, 50.0, 55.0]


def test_appending_future_observations_cannot_change_an_earlier_label() -> None:
    before = classify_vix_series(_SERIES)
    after = classify_vix_series(_SERIES + _FUTURE)
    assert after[: len(_SERIES)] == before


def test_and_a_non_causal_classifier_on_the_same_data_DOES_change() -> None:
    """The positive control. If this ever stops failing to be stable, the test
    above has stopped being able to detect look-ahead and is decoration."""

    def centred_smoothed_labels(closes: list[float]) -> list[str]:
        # A centred window peeks forward -- exactly the "harmless denoising"
        # that silently imports the future.
        out = []
        for i in range(len(closes)):
            window = closes[max(0, i - 1) : i + 2]
            out.append(classify_vix_level(sum(window) / len(window)))
        return out

    before = centred_smoothed_labels(_SERIES)
    after = centred_smoothed_labels(_SERIES + _FUTURE)
    assert after[: len(_SERIES)] != before, (
        "the control did not move: this data no longer exercises look-ahead, "
        "so the causality test above proves nothing"
    )


def test_every_close_maps_to_a_label_and_only_known_labels() -> None:
    labels = classify_vix_series([0.0, 9.0, CALM_MAX, ELEVATED_MAX, 200.0])
    assert len(labels) == 5
    assert set(labels) <= {"calm", "elevated", "crisis"}
