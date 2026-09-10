"""Time-average growth of a benchmark+hedge portfolio (``docs/END_STATE.md``
§4 Q8) -- every other headline ``PutBacktestResult`` reports
(``roi_on_premium``, ``annualized_return``, ``hit_rate``) describes the put in
isolation and cannot say how much of wealth to hold. Growth can, because
compounding is exactly where a right-skewed payoff and its arithmetic return
diverge (Peters, *Optimal leverage from non-ergodicity*, Quantitative Finance
11(11), 2011).

Deliberately decoupled from ``put_roll.py``'s ``PricePoint``/``EquityPoint``
models (``price_path`` is a plain ``(date, value)`` tuple sequence instead) so
this stays a leaf module any price series -- a single leg's ``price_path``
today, a portfolio's later -- can feed without a new dependency edge.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence


def time_average_growth(
    price_path: Sequence[tuple[dt.date, float]],
    hedge_final: float = 0.0,
    *,
    wealth: float,
) -> float | None:
    """Geometric growth rate of ``wealth`` held in the benchmark for the whole
    traded window, with the hedge's realized P&L layered on top::

        g = (1 / T) * log(W_T / W_0)
        W_0 = wealth
        W_T = wealth * (S_T / S_0) + hedge_final

    ``price_path`` is ``(date, price)`` for the benchmark (e.g. a
    ``PutBacktestResult.price_path``); ``hedge_final`` is the hedge's realized
    cumulative P&L as of ``price_path``'s last date (e.g. the last
    ``cum_pnl`` of its ``mtm_curve``, marked to model where not yet settled).
    Only the two endpoints of ``price_path`` matter: a time-average growth
    rate is defined by a trajectory's start and end, not its interior path,
    so ``T`` is the number of years between ``price_path``'s first and last
    date.

    This treats the *entire* ``wealth`` as continuously held in the benchmark
    (the "keep investing the same way" half of Dio's ask) with the hedge
    funded as a separate, additive cash flow sized by ``alpha * wealth`` --
    not ``(1 - alpha) * wealth`` in the benchmark and ``alpha * wealth`` held
    back in cash. That is a modeling choice, not the only defensible one; the
    alternative (a smaller benchmark position funding the premium out of the
    same pool) changes ``W_0`` too and is a candidate for the ``g(alpha)``
    sweep queued in ``AGENT_TODO.md`` to compare against once it lands.

    Returns ``None`` -- the same "undefined, not a crash" convention
    ``annualized_sharpe`` uses -- when there are fewer than two price points,
    ``wealth`` or the starting price is not positive, the window spans zero
    or negative years, or combined wealth is ever non-positive (``log`` of a
    non-positive number is undefined; a benchmark that fell to zero is a real
    scenario for a leveraged sweep, not a bug to raise on).
    """
    if len(price_path) < 2 or wealth <= 0.0:
        return None
    start_date, start_price = price_path[0]
    end_date, end_price = price_path[-1]
    if start_price <= 0.0:
        return None
    years = (end_date - start_date).days / 365.25
    if years <= 0.0:
        return None
    w0 = wealth
    w1 = wealth * (end_price / start_price) + hedge_final
    if w1 <= 0.0:
        return None
    return float(math.log(w1 / w0) / years)
