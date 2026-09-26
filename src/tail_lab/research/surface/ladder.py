"""The strike ladder -- deeper puts priced relative to one quoted anchor
(``docs/adr/0026`` §2; ``AGENT_TODO.md`` P1).

The paper is explicit that its approach "isn't about absolute mispricing of tail
options, but relative to a given strike closer to the money". ``put_ratio``
already expresses that algebraically -- it is free of ``l`` -- but nothing
stops a caller building a ``ParetanTail`` by hand and reading absolute prices
off it (``ParetanTail.put_price`` stays public). This module closes that door
for ITS entry point only: ``build_ladder`` cannot be called without an
``Anchor``, and every Paretan price it produces is a multiple of the anchor's
price. Whether the anchor is a real quote is the caller's responsibility --
``Anchor`` validates that the numbers are a coherent quote, not that anyone
traded at them. Other entry points that bypass this module are still a rule a
reviewer enforces (``docs/adr/0026`` §2).

Each ``LadderRung`` puts the Paretan price beside the market's own mid, and both
beside their Black-Scholes implied vols. The IV ratio is the object the Surface
reads: below 1, the power law prices the strike cheaper than the market does;
above 1, dearer. Neither is "right" -- this module compares, it does not judge.

What this module deliberately does NOT do: choose the anchor (``implied_alpha``
and the experiment choose it, by moneyness or delta, and must say which), fit
``alpha`` (``implied_alpha``), or read the lake. It is pure arithmetic over
arguments.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tail_lab.research.backtest.index_replication import DAYS_PER_YEAR
from tail_lab.research.skew import implied_vol_put
from tail_lab.research.surface.paretan import anchor_l_put, put_ratio


@dataclass(frozen=True)
class Anchor:
    """One real, quoted put that every Paretan price on its ladder is relative to.

    ``price`` is the mid actually used as the anchor; ``bid`` and ``ask`` travel
    with it because the anchor's own spread is the floor on how precisely any
    rung below it can be known. ``spot`` is the quote's own spot (as-traded, for
    the optionsDX panel), never one joined in from another dataset.
    """

    underlying: str
    quote_date: dt.date
    expiration: dt.date
    spot: float
    strike: float
    price: float
    bid: float
    ask: float

    def __post_init__(self) -> None:
        values = {
            "spot": self.spot,
            "strike": self.strike,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
        }
        bad = [name for name, value in values.items() if not math.isfinite(value)]
        if bad:
            raise ValueError(f"anchor fields must be finite: {', '.join(bad)}")
        for name in ("quote_date", "expiration"):
            if isinstance(getattr(self, name), dt.datetime):
                # A datetime is a date subclass: two of them pass validation and
                # floor the day count (23:00 -> 01:00 a month later is 29 days,
                # not 30). A quote session is a calendar date. This includes
                # pd.Timestamp -- build anchors from rows with `.date()`.
                raise ValueError(f"anchor {name} must be a date, not a datetime")
        if not self.underlying:
            raise ValueError("anchor underlying must be named")
        if not 0.0 < self.bid <= self.price <= self.ask:
            # A zero bid is not a price: nobody would sell at it, and marking the
            # anchor at ask/2 biases a fitted alpha by -0.34 (AGENT_TODO P2).
            raise ValueError(
                f"anchor quote must satisfy 0 < bid <= price <= ask, got "
                f"bid={self.bid} price={self.price} ask={self.ask}"
            )
        if not 0.0 < self.strike < self.spot:
            raise ValueError(
                f"anchor strike {self.strike} must lie in (0, spot={self.spot}) -- a downside put"
            )
        if self.expiration <= self.quote_date:
            raise ValueError(
                f"anchor expiration {self.expiration} must follow its quote date {self.quote_date}"
            )

    @property
    def t_years(self) -> float:
        """Time to expiry on the same day-count ``research/skew.py`` uses."""
        return (self.expiration - self.quote_date).days / DAYS_PER_YEAR


@dataclass(frozen=True)
class LadderRung:
    """One strike below the anchor: the Paretan price beside the market's.

    ``market_price`` is ``None`` when the strike has no usable quote; the rung
    still exists, because the Paretan price is defined without one. Either IV is
    ``None`` when ``implied_vol_put`` refuses the price (below discounted
    intrinsic, or above what ``IV_MAX`` reaches), and ``iv_ratio`` is then
    ``None`` too -- a refused inversion is reported, never clamped.
    """

    strike: float
    paretan_price: float
    market_price: float | None
    paretan_iv: float | None
    market_iv: float | None
    iv_ratio: float | None


def build_ladder(
    anchor: Anchor,
    *,
    alpha: float,
    strikes: Sequence[float],
    market_mids: Mapping[float, float],
    r: float,
    q: float,
) -> tuple[LadderRung, ...]:
    """Price each of ``strikes`` relative to ``anchor`` at tail index ``alpha``.

    Raises ``ValueError`` when the anchor lies outside the tail ``alpha`` implies
    (``anchor_l_put`` is the only domain check: ``put_ratio`` never sees ``l``
    and so cannot make it), when any strike is at or above the anchor strike
    (the model extrapolates deeper, never shallower), or when a market mid is
    not a positive finite number.

    ``market_mids`` is looked up per strike by exact float equality (callers
    should key it with the same strike values they pass); a strike absent from
    it gets a rung without market fields. Rungs are returned nearest the money first.
    ``r`` and ``q`` are the caller's -- this module does not choose a rate.
    """
    anchor_l_put(price=anchor.price, strike=anchor.strike, spot=anchor.spot, alpha=alpha)
    if not (math.isfinite(r) and math.isfinite(q)):
        # A NaN rate makes every Black-Scholes price NaN and every inversion
        # None -- indistinguishable from "unreachable" unless refused here.
        raise ValueError(f"r and q must be finite, got r={r} q={q}")

    too_shallow = sorted(k for k in strikes if k >= anchor.strike)
    if too_shallow:
        raise ValueError(
            f"strikes {too_shallow} are at or above the anchor strike {anchor.strike}; "
            "the ladder only extrapolates to deeper strikes"
        )

    return tuple(
        _rung(anchor, strike=strike, alpha=alpha, market_mid=market_mids.get(strike), r=r, q=q)
        for strike in sorted(set(strikes), reverse=True)
    )


def _rung(
    anchor: Anchor,
    *,
    strike: float,
    alpha: float,
    market_mid: float | None,
    r: float,
    q: float,
) -> LadderRung:
    if market_mid is not None and (
        isinstance(market_mid, bool) or not (math.isfinite(market_mid) and market_mid > 0.0)
    ):
        raise ValueError(
            f"market mid at strike {strike} must be positive and finite, got {market_mid}"
        )

    paretan_price = anchor.price * put_ratio(
        k_from=anchor.strike, k_to=strike, spot=anchor.spot, alpha=alpha
    )
    # A zero price is where put_ratio clamps ulp-scale cancellation noise (only
    # at absurdly deep strikes). Inverting it would return IV_MIN -- the
    # fabricated vol docs/adr/0026 §1 names -- so it is reported as refused.
    paretan_iv = (
        None if paretan_price == 0.0 else _iv(anchor, strike=strike, price=paretan_price, r=r, q=q)
    )
    market_iv = (
        None if market_mid is None else _iv(anchor, strike=strike, price=market_mid, r=r, q=q)
    )
    iv_ratio = None if paretan_iv is None or market_iv is None else paretan_iv / market_iv
    return LadderRung(
        strike=strike,
        paretan_price=paretan_price,
        market_price=market_mid,
        paretan_iv=paretan_iv,
        market_iv=market_iv,
        iv_ratio=iv_ratio,
    )


def _iv(anchor: Anchor, *, strike: float, price: float, r: float, q: float) -> float | None:
    return implied_vol_put(price, spot=anchor.spot, strike=strike, t_years=anchor.t_years, r=r, q=q)
