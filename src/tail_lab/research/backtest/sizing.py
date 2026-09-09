"""Pluggable premium-sizing seam (``docs/END_STATE.md`` §4 Q8), mirroring the
``OptionPricer`` seam (``docs/adr/0004``): a caller asks "how much may this leg
spend on premium" without the engine needing to know whether the answer is a
flat dollar figure or a slice of the investor's wealth.

Every consumer of this seam (``put_roll.compute_put_backtest``,
``portfolio.run_portfolio``, ``ranking.rank_universe``,
``roll_schedule.build_roll_schedule``) keeps its existing ``notional: float``
parameter untouched and adds an optional ``sizing_mode: SizingMode | None``
next to it, exactly the way those same functions already take
``pricer: OptionPricer | None``: when ``sizing_mode`` is given it resolves the
budget and ``notional`` is ignored; when it is ``None`` the caller's flat
``notional`` is used exactly as before. ``FixedPremium(amount)`` is that
default made explicit -- passing it is equivalent to leaving ``sizing_mode``
unset with ``notional=amount``.

This module intentionally says nothing about *where* a resolved budget must
be used. ``roll_schedule.py``'s own docstring already states the one binding
constraint: the roll schedule is placeable order intent, so a wealth fraction
must be resolved to a concrete cash figure *before* the ``RollSchedule``
artifact is built, never carried into it as a live formula.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class SizingMode(ABC):
    """Resolves the cash premium budget for one leg of an ``n_legs``-leg
    strategy."""

    @abstractmethod
    def resolve(self, *, n_legs: int) -> float:
        """Cash to spend on premium for one leg.

        Args:
            n_legs: how many independent legs share this sizing decision.
                Must be positive. What "a leg" means is the caller's choice
                (a single asset, a name in a screened universe, a position in
                a roll schedule, ...) -- see each call site's docstring for
                what it passes.
        """


@dataclass(frozen=True)
class FixedPremium(SizingMode):
    """Spend ``amount`` per leg, regardless of ``n_legs`` -- today's behavior,
    and every current call site's implicit default."""

    amount: float

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("amount must be positive")

    def resolve(self, *, n_legs: int) -> float:
        if n_legs <= 0:
            raise ValueError("n_legs must be positive")
        return self.amount


@dataclass(frozen=True)
class WealthFraction(SizingMode):
    """Spend ``alpha`` of ``wealth``, split evenly across ``n_legs`` legs.

    ``alpha`` is a fraction of wealth, not a leverage multiple: it must sit in
    ``(0, 1]``. Sizing above 100% of stated wealth is a leverage decision this
    seam deliberately does not make on a caller's behalf -- see the
    ``g(alpha)`` sweep queued in ``AGENT_TODO.md`` for where that question
    belongs.
    """

    alpha: float
    wealth: float

    def __post_init__(self) -> None:
        if not (0.0 < self.alpha <= 1.0):
            raise ValueError("alpha must be in (0, 1]")
        if self.wealth <= 0:
            raise ValueError("wealth must be positive")

    def resolve(self, *, n_legs: int) -> float:
        if n_legs <= 0:
            raise ValueError("n_legs must be positive")
        return self.alpha * self.wealth / n_legs
