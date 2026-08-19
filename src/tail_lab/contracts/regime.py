"""Market-regime labels from the volatility complex (``docs/END_STATE.md``
§1.3, and the prerequisite for the phase-2 memory layer's ``regime_only``
distinction).

A LEAF module: imports nothing else from ``tail_lab``. The regime *label*
type and the pure level→label classifier live here so every layer can use
them — ``research`` builds the timeline, the memory layer keys verdicts by
regime, the API serves the panel — without any of them importing each other.

First-cut classifier: bucket the VIX close by level. The thresholds below are
deliberately simple and documented, not fitted — a z-score / hidden-Markov
regime model is a future upgrade behind this same label type. Levels are
chosen against the VIX's own history: its long-run median sits in the high
teens, sustained readings above the high-20s mark genuine stress, so:

    calm      VIX < 17
    elevated  17 <= VIX < 28
    crisis    VIX >= 28
"""

from __future__ import annotations

from typing import Literal, get_args

RegimeLabel = Literal["calm", "elevated", "crisis"]

#: The label set, in increasing-stress order (useful for ordered axes/coverage).
REGIME_LABELS: tuple[RegimeLabel, ...] = get_args(RegimeLabel)

#: Upper bound (exclusive) of the "calm" bucket, in VIX points.
CALM_MAX = 17.0
#: Upper bound (exclusive) of the "elevated" bucket; at/above it is "crisis".
ELEVATED_MAX = 28.0


def classify_vix_level(vix_close: float) -> RegimeLabel:
    """Label a single VIX close into its market regime by level.

    Pure and total (every finite non-negative VIX maps to a label). The
    thresholds are module constants so the memory layer, the API, and any
    backtest all agree on where one regime ends and the next begins.
    """
    if vix_close < CALM_MAX:
        return "calm"
    if vix_close < ELEVATED_MAX:
        return "elevated"
    return "crisis"
