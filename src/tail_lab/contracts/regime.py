"""Market-regime labels from the volatility complex and credit spreads
(``docs/END_STATE.md`` §1.3 — "volatility complex + credit spreads +
rates" — and the prerequisite for the phase-2 memory layer's
``regime_only`` distinction).

A LEAF module: imports nothing else from ``tail_lab``. The regime *label*
type and the pure level→label classifiers live here so every layer can use
them — ``research`` builds the timeline, the memory layer keys verdicts by
regime, the API serves the panel — without any of them importing each other.

First-cut classifier: bucket a level by threshold, independently for VIX and
for credit spreads, then take the more severe of the two
(:func:`combine_regime_labels`) — credit stress can only escalate the VIX
view, never make it look calmer. Both threshold sets are deliberately simple
and documented, not fitted — a z-score / hidden-Markov regime model is a
future upgrade behind this same label type.

VIX levels are chosen against its own history: its long-run median sits in
the high teens, sustained readings above the high-20s mark genuine stress:

    calm      VIX < 17
    elevated  17 <= VIX < 28
    crisis    VIX >= 28

Credit levels (HY OAS, ``docs/DATA_CONTRACTS.md`` #4) are chosen against its
own history: the long-run range for a calm credit market is roughly 3-4%,
single episodes of real but non-crisis stress (the 2016 oil selloff, the
2018 Q4 selloff) sit in the high-single-digits, and both the 2020 COVID
spike (~10.9%) and the 2008 crisis peak (~19.9%, this module's own schema
ceiling) clear it comfortably:

    calm      HY OAS < 5.0
    elevated  5.0 <= HY OAS < 8.0
    crisis    HY OAS >= 8.0
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal, get_args

RegimeLabel = Literal["calm", "elevated", "crisis"]

#: The label set, in increasing-stress order (useful for ordered axes/coverage).
REGIME_LABELS: tuple[RegimeLabel, ...] = get_args(RegimeLabel)

#: Stress rank of each label, for :func:`combine_regime_labels`.
_SEVERITY: dict[RegimeLabel, int] = {label: rank for rank, label in enumerate(REGIME_LABELS)}

#: Upper bound (exclusive) of the "calm" bucket, in VIX points.
CALM_MAX = 17.0
#: Upper bound (exclusive) of the "elevated" bucket; at/above it is "crisis".
ELEVATED_MAX = 28.0

#: Upper bound (exclusive) of the "calm" bucket, in HY OAS percentage points.
CREDIT_CALM_MAX = 5.0
#: Upper bound (exclusive) of the "elevated" bucket; at/above it is "crisis".
CREDIT_ELEVATED_MAX = 8.0

#: Deadband for the credit classifier, in OAS percentage points — same role
#: as :data:`HYSTERESIS_BAND` below, sized the same way: ~10% of the
#: calm/elevated gap (0.5 of 3.0 points), wide enough to absorb daily noise,
#: narrow enough not to meaningfully delay a genuine transition.
CREDIT_HYSTERESIS_BAND = 0.5

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


def _classify_level(value: float, calm_max: float, elevated_max: float) -> RegimeLabel:
    """Bucket a single level by threshold. Pure and total (every finite
    non-negative level maps to a label)."""
    if value < calm_max:
        return "calm"
    if value < elevated_max:
        return "elevated"
    return "crisis"


def classify_vix_level(vix_close: float) -> RegimeLabel:
    """Label a single VIX close into its market regime by level.

    The thresholds are module constants so the memory layer, the API, and any
    backtest all agree on where one regime ends and the next begins.
    """
    return _classify_level(vix_close, CALM_MAX, ELEVATED_MAX)


def classify_credit_level(hy_oas: float) -> RegimeLabel:
    """Label a single HY OAS print into its market regime by level. Same
    shape as :func:`classify_vix_level`, over the credit thresholds."""
    return _classify_level(hy_oas, CREDIT_CALM_MAX, CREDIT_ELEVATED_MAX)


def _next_label(
    previous: RegimeLabel, value: float, band: float, calm_max: float, elevated_max: float
) -> RegimeLabel:
    """One hysteresis step: the label after observing ``value``.

    Each boundary is crossed at ``threshold + band`` going up and
    ``threshold - band`` coming down, so the level must commit before the
    label follows. A move large enough to clear two boundaries at once skips
    the middle regime rather than stepping through it -- a VIX that gaps from
    14 to 40 was never briefly "elevated".
    """
    enter_elevated = calm_max + band
    leave_elevated_down = calm_max - band
    enter_crisis = elevated_max + band
    leave_crisis_down = elevated_max - band

    if previous == "calm":
        if value >= enter_crisis:
            return "crisis"
        return "elevated" if value >= enter_elevated else "calm"
    if previous == "elevated":
        if value >= enter_crisis:
            return "crisis"
        return "calm" if value < leave_elevated_down else "elevated"
    if value < leave_elevated_down:
        return "calm"
    return "elevated" if value < leave_crisis_down else "crisis"


def _classify_series(
    values: Sequence[float] | Iterable[float],
    *,
    band: float,
    initial: RegimeLabel | None,
    calm_max: float,
    elevated_max: float,
) -> list[RegimeLabel]:
    """Label a level series with hysteresis, causally.

    The first observation is labelled by plain level (or by ``initial`` when
    the caller is continuing an earlier series); every later one may only
    change regime by clearing the boundary by ``band``.

    **Causal by construction**: the label at ``t`` depends on ``values[:t+1]``
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
    for raw in values:
        value = float(raw)
        current = (
            _classify_level(value, calm_max, elevated_max)
            if current is None
            else _next_label(current, value, band, calm_max, elevated_max)
        )
        labels.append(current)
    return labels


def classify_vix_series(
    vix_closes: Sequence[float] | Iterable[float],
    *,
    band: float = HYSTERESIS_BAND,
    initial: RegimeLabel | None = None,
) -> list[RegimeLabel]:
    """Label a VIX series with hysteresis, causally. See :func:`_classify_series`."""
    return _classify_series(
        vix_closes, band=band, initial=initial, calm_max=CALM_MAX, elevated_max=ELEVATED_MAX
    )


def classify_credit_series(
    hy_oas_values: Sequence[float] | Iterable[float],
    *,
    band: float = CREDIT_HYSTERESIS_BAND,
    initial: RegimeLabel | None = None,
) -> list[RegimeLabel]:
    """Label an HY OAS series with hysteresis, causally. Same shape as
    :func:`classify_vix_series`, over the credit thresholds."""
    return _classify_series(
        hy_oas_values,
        band=band,
        initial=initial,
        calm_max=CREDIT_CALM_MAX,
        elevated_max=CREDIT_ELEVATED_MAX,
    )


def combine_regime_labels(*labels: RegimeLabel) -> RegimeLabel:
    """The most severe of several independently-classified regime labels.

    The widened regime view (`docs/END_STATE.md` §1.3) escalates the
    VIX-only picture when credit spreads are separately stressed, but never
    the reverse -- calm credit does not talk a crisis-VIX day down. Total and
    order-independent; raises ``ValueError`` on an empty call, since "the
    regime" is undefined with no inputs.
    """
    if not labels:
        raise ValueError("combine_regime_labels needs at least one label")
    return max(labels, key=lambda label: _SEVERITY[label])
