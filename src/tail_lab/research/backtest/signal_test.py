"""The signal test as the API serves it: the out-of-sample walk-forward
(``walk_forward.py``, ADR 0033) plus the beyond-volatility Fama-MacBeth test
(``beyond_vol.py``, ADR 0035), on the same periods.

This is the top layer. ``walk_forward`` owns the statistics and the collection
of periods; ``beyond_vol`` imports from it (and is frozen); this module imports
both, so neither needs to import back.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from tail_lab.lake.store import LakeStore
from tail_lab.research.backtest.beyond_vol import BeyondVolTest, beyond_vol_test
from tail_lab.research.backtest.strike_rule import ByDelta, ByMoneyness, StrikeRule
from tail_lab.research.backtest.walk_forward import (
    Evaluation,
    PeriodInputs,
    collect_periods,
    evaluate_screens,
)

_LOGGER = logging.getLogger(__name__)

#: The one setting the beyond-volatility test (ADR 0035) was pre-registered at:
#: (strike rule, tenor weeks, signal years, top_k). The API warms its signal
#: test at this setting (``putlab_routes._WARM_SIGNAL_TEST`` IS this tuple).
CONFIRMATORY_SETTING: tuple[StrikeRule, float, float, int] = (ByDelta(0.10), 4.0, 4.0, 5)

#: The data date the pre-registered test was run on: the lake as of 2026-10-31
#: (``docs/research/0035/REAL_RESULT.json``, ADR 0035). The pre-registration
#: covers this setting AND this date; the same setting on a later date is a
#: re-run that cannot confirm anything.
FREEZE_AS_OF = dt.date(2026, 10, 31)

#: The beyond-volatility test is computed only up to this many periods. Its
#: simulated null allocates about 2.4 MB per period up front, so a long run
#: (a 1-week rail run has T of roughly 320-420) could exceed the 1 GB VM and be
#: killed silently. Measured peaks: 368 MB at T = 81 and 767 MB at T = 300, each
#: including a 237 MB baseline. The pre-registered run has T = 81.
MAX_BEYOND_VOL_PERIODS = 120

BeyondVolMode = Literal["confirmatory", "monitoring", "exploratory"]


class SignalTestResult(Evaluation):
    as_of: dt.date
    moneyness_pct: float | None
    strike_rule: Literal["moneyness", "delta"]
    target_delta: float | None
    tenor_weeks: float
    signal_years: float
    top_k: int
    n_random: int
    seed: int
    universe_size: int
    #: The bronze snapshots read (VIX, SPY, one set per loaded name, the
    #: dividend snapshot) and the code that computed it; ``code_sha`` is
    #: filled in by the API route, which may read ``Settings``.
    snapshot_ids: list[str] = Field(default_factory=list)
    code_sha: str = "unknown"
    #: Does fragility predict put outcomes beyond trailing vol (Fama-MacBeth,
    #: ``beyond_vol.py``, ADR 0035)? Computed from this run's own periods.
    #: ``None`` when the test did not run on them; ``beyond_vol_unavailable_reason``
    #: then says why (degenerate inputs, or more than
    #: :data:`MAX_BEYOND_VOL_PERIODS` periods).
    #: ``beyond_vol_mode`` says what the run is. Only ``"confirmatory"`` -- the
    #: pre-registered setting on the pre-registered date -- may show its
    #: ``p_value`` or ``status``. ``"monitoring"`` is that setting on a later
    #: date, and ``"exploratory"`` any other setting: a reader may be shown
    #: ``gamma_bar`` and ``ci90`` alone.
    beyond_vol: BeyondVolTest | None = None
    beyond_vol_mode: BeyondVolMode = "exploratory"
    beyond_vol_unavailable_reason: str | None = None


def _beyond_vol(periods: Sequence[PeriodInputs]) -> tuple[BeyondVolTest | None, str | None]:
    """The beyond-volatility test on these periods and, when it did not run, why.

    It never fails the signal test: too many periods (memory) or a frozen
    procedure that refuses them (``ValueError``: degenerate inputs) gives
    ``(None, reason)``. Any other exception is a bug and propagates."""
    if len(periods) > MAX_BEYOND_VOL_PERIODS:
        reason = (
            f"{len(periods)} periods is more than the {MAX_BEYOND_VOL_PERIODS} this server tests"
        )
        _LOGGER.warning("event=beyond_vol_unavailable reason=%s", reason)
        return None, reason
    try:
        return beyond_vol_test(periods), None
    except ValueError as exc:
        _LOGGER.warning("event=beyond_vol_unavailable reason=%s", exc)
        return None, str(exc)


def _mode(setting: tuple[StrikeRule, float, float, int], as_of: dt.date) -> BeyondVolMode:
    if setting != CONFIRMATORY_SETTING:
        return "exploratory"
    return "confirmatory" if as_of == FREEZE_AS_OF else "monitoring"


def walk_forward(
    store: LakeStore,
    *,
    symbols: Sequence[str],
    as_of: dt.date,
    rule: StrikeRule,
    tenor_weeks: float,
    signal_years: float = 4.0,
    top_k: int = 5,
    n_random: int = 2000,
    seed: int = 0,
) -> SignalTestResult:
    """The out-of-sample signal test over ``symbols`` as of ``as_of``.

    Point-in-time throughout (see the module docstring); ``rule`` is the
    strike rule every roll uses (the API defaults to ``ByDelta(0.10)`` -- at
    realised vol, labelled so). Raises ``LookupError`` if VIX or SPY is
    missing; a universe too thin for any period returns an
    ``insufficient_data`` verdict rather than raising."""
    collected = collect_periods(
        store,
        symbols=symbols,
        as_of=as_of,
        rule=rule,
        tenor_weeks=tenor_weeks,
        signal_years=signal_years,
        top_k=top_k,
    )
    evaluation = evaluate_screens(
        collected.periods,
        top_k=top_k,
        n_random=n_random,
        seed=seed,
        diagnostics=collected.diagnostics,
    )
    beyond, unavailable = _beyond_vol(collected.periods)
    return SignalTestResult.model_validate(
        {
            **{f: getattr(evaluation, f) for f in Evaluation.model_fields},
            "as_of": as_of,
            "moneyness_pct": rule.pct if isinstance(rule, ByMoneyness) else None,
            "strike_rule": rule.kind,
            "target_delta": rule.target if isinstance(rule, ByDelta) else None,
            "tenor_weeks": tenor_weeks,
            "signal_years": signal_years,
            "top_k": top_k,
            "n_random": n_random,
            "seed": seed,
            "universe_size": len(collected.loaded),
            "snapshot_ids": list(collected.snapshot_ids),
            "beyond_vol": beyond,
            "beyond_vol_unavailable_reason": unavailable,
            "beyond_vol_mode": _mode((rule, tenor_weeks, signal_years, top_k), as_of),
        }
    )
