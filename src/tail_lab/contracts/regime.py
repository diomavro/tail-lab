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

from collections.abc import Iterable, Sequence
from typing import Literal, get_args

RegimeLabel = Literal["calm", "elevated", "crisis"]

#: The label set, in increasing-stress order (useful for ordered axes/coverage).
REGIME_LABELS: tuple[RegimeLabel, ...] = get_args(RegimeLabel)

#: Upper bound (exclusive) of the "calm" bucket, in VIX points.
CALM_MAX = 17.0
#: Upper bound (exclusive) of the "elevated" bucket; at/above it is "crisis".
ELEVATED_MAX = 28.0

#: Deadband, in VIX points, applied around each threshold by
#: :func:`classify_vix_series`. A regime is only left once the level clears the
#: boundary by this much *in the direction of travel*.
#:
#: Why this exists: a bare threshold makes a VIX oscillating 16.9 -> 17.1 ->
#: 16.8 three regime changes in three days, and `docs/adr/0015` keys hypothesis
#: verdicts on ``(rule_hash, regime)`` and calls a rule ``confirmed`` once it
#: has paid in two of them. Without a deadband a rule can collect its second
#: regime from a boundary wobble rather than from a genuine change of state,
#: which makes the ``regime_only`` != ``confirmed`` distinction -- the crux of
#: that ADR -- weaker than it reads (`docs/PRIOR_ART.md` §8).
#:
#: 1.0 point is ~6% of the calm/elevated boundary: wide enough to absorb daily
#: noise, narrow enough that a real transition is never delayed more than a day
#: or two. Documented, not fitted -- same standing as the thresholds themselves.
HYSTERESIS_BAND = 1.0


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


def _next_label(previous: RegimeLabel, vix_close: float, band: float) -> RegimeLabel:
    """One hysteresis step: the label after observing ``vix_close``.

    Each boundary is crossed at ``threshold + band`` going up and
    ``threshold - band`` coming down, so the level must commit before the
    label follows. A move large enough to clear two boundaries at once skips
    the middle regime rather than stepping through it -- a VIX that gaps from
    14 to 40 was never briefly "elevated".
    """
    enter_elevated = CALM_MAX + band
    leave_elevated_down = CALM_MAX - band
    enter_crisis = ELEVATED_MAX + band
    leave_crisis_down = ELEVATED_MAX - band

    if previous == "calm":
        if vix_close >= enter_crisis:
            return "crisis"
        return "elevated" if vix_close >= enter_elevated else "calm"
    if previous == "elevated":
        if vix_close >= enter_crisis:
            return "crisis"
        return "calm" if vix_close < leave_elevated_down else "elevated"
    if vix_close < leave_elevated_down:
        return "calm"
    return "elevated" if vix_close < leave_crisis_down else "crisis"


def classify_vix_series(
    vix_closes: Sequence[float] | Iterable[float],
    *,
    band: float = HYSTERESIS_BAND,
    initial: RegimeLabel | None = None,
) -> list[RegimeLabel]:
    """Label a VIX series with hysteresis, causally.

    The first observation is labelled by plain level (or by ``initial`` when
    the caller is continuing an earlier series); every later one may only
    change regime by clearing the boundary by ``band``.

    **Causal by construction**: the label at ``t`` depends on ``vix_closes[:t+1]``
    and nothing after it, so this is safe under the platform's no-look-ahead
    invariant (`docs/adr/0009`). That is a property worth stating because the
    obvious alternative -- smoothing the series and classifying the smoothed
    version -- is *not* causal, and reads as a harmless denoising step
    (`docs/PRIOR_ART.md` §9).

    ``band=0.0`` recovers the bare thresholds exactly, which is what makes the
    change auditable against the previous behaviour.
    """
    if band < 0:
        raise ValueError("band must be non-negative")

    labels: list[RegimeLabel] = []
    current = initial
    for close in vix_closes:
        value = float(close)
        current = (
            classify_vix_level(value) if current is None else _next_label(current, value, band)
        )
        labels.append(current)
    return labels
