"""Marks — attach real listed contracts and real quotes to a roll schedule.

``roll_schedule.build_roll_schedule`` emits *targets*: a strike at
``spot x (1 - moneyness/100)`` that no chain lists, an expiry counted in
trading days that no board resolves, and a Black-Scholes premium at a flat
vol. Those are the right outputs for a screen and the wrong ones for an order.

How wrong is not a matter of opinion. On 2026-08-27 the schedule's top leg was
KRE at 8% OOM, 2 weeks, model premium **$0.0019/share**; the listed put nearest
that target quoted a **$1.13 mid** -- roughly **600x**. Every return figure on
that leg divided by the $0.0019, which is why it advertised a 1867% return on
premium. Sizing a real position from it would have been sizing from fiction.

This module closes that gap using the forward-collected chain
(``docs/adr/0020``): snap each leg to a **listed** strike and expiry, price it
at the **real** offer, size the contract count from that, and record the
market/model ratio so the error is measured per leg rather than assumed.

It is also where the platform first refuses a recommendation on **liquidity**.
The screen ranks on fragility and has never looked at whether anyone trades the
contract it names; the KRE legs above carried open interest of 0 and 1, and no
bid at all. A put with no bid cannot be exited and its mid is arithmetic on a
number nobody is offering.

**Pure, like the builder it complements**: the chain arrives as a frame, so the
lake read stays at the API layer and every case here is testable without a
store. Coverage is partial by design -- ``DEFAULT_SNAPSHOT_SYMBOLS`` collects 24
of the 70 screened names -- so an unquoted leg reports *why* in
``quote_status`` rather than silently looking like a quoted one.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Literal

import pandas as pd
from pydantic import Field

from tail_lab.research.backtest.roll_schedule import RollLeg, RollSchedule

__all__ = [
    "MAX_RELATIVE_SPREAD",
    "MIN_OPEN_INTEREST",
    "SCHEDULE_MARKS_NOTES",
    "MarkedLeg",
    "MarkedSchedule",
    "QuoteStatus",
    "mark_schedule",
]

#: Contracts per option (US equity/ETF convention). The premium budget buys
#: whole contracts of 100 shares, never fractions.
SHARES_PER_CONTRACT = 100

#: A contract nobody holds is a contract nobody trades. Deliberately low --
#: this is a "does this exist in practice" floor, not a size test.
MIN_OPEN_INTEREST = 10

#: Widest bid-ask, as a fraction of mid, that still counts as a market. Beyond
#: this the mid is arithmetic rather than a price, and the fill will not
#: resemble it.
MAX_RELATIVE_SPREAD = 0.50

QuoteStatus = Literal[
    "quoted",  # a listed, liquid contract was found and priced
    "not_collected",  # the underlying is not in the daily chain sweep
    "no_listed_contract",  # collected, but nothing listed near the target
    "illiquid",  # listed, but no bid / too little interest / spread too wide
]

SCHEDULE_MARKS_NOTES: tuple[str, ...] = (
    "A leg with quote_status='quoted' carries a LISTED strike and expiry and a real"
    " offer: size from market_contracts, not model_contracts.",
    "market_to_model_ratio is the measurement docs/adr/0018 bounds the strategy search"
    " on. A ratio far from 1 means the expected_* figures on that leg, which all divide"
    " by the model premium, are optimistic by roughly that factor.",
    "quote_status='not_collected' is a coverage gap, not a judgement: the forward"
    " collection covers 24 of the 70 screened names (docs/adr/0020).",
    "quote_status='illiquid' means a listed contract exists but has no bid, too little"
    " open interest, or a spread too wide to call a price. Do not place it.",
)


class MarkedLeg(RollLeg):
    """A roll leg with the market's own answer attached.

    Every market field is optional because coverage is partial and because a
    listed contract may still fail the liquidity floor. ``quote_status`` says
    which of those happened, so an executor never has to infer why a number is
    missing.
    """

    listed_strike: float | None = None
    listed_expiry: dt.date | None = None
    market_bid: float | None = None
    market_ask: float | None = None
    market_mid: float | None = None
    market_open_interest: int | None = None
    #: The exchange's own delta, off the real smile — not ours off a flat vol.
    market_delta: float | None = None
    #: Whole contracts the premium budget buys at the OFFER. You pay the ask.
    market_contracts: int | None = None
    #: market_mid / model_premium. The size of the platform's pricing error on
    #: this specific leg, on this specific day.
    market_to_model_ratio: float | None = None
    quote_status: QuoteStatus = "not_collected"


class MarkedSchedule(RollSchedule):
    """A schedule whose legs have been marked against the listed chain."""

    legs: list[MarkedLeg]  # type: ignore[assignment]
    quote_session: dt.date | None = None
    quoted_legs: int = 0
    marks_notes: list[str] = Field(default_factory=lambda: list(SCHEDULE_MARKS_NOTES))


def _select_expiry(chain: pd.DataFrame, target: dt.date) -> pd.Timestamp | None:
    """The nearest listed expiry at or after ``target``.

    At-or-after, never before: a put that expires earlier than the backtest
    assumed is a shorter, cheaper, different position, and rounding *down* on
    expiry is the one direction that quietly flatters the result.
    """
    expiries = chain["expiration"].drop_duplicates().sort_values()
    eligible = expiries[expiries >= pd.Timestamp(target)]
    if not eligible.empty:
        return pd.Timestamp(eligible.iloc[0])
    return None


def _select_strike(at_expiry: pd.DataFrame, target: float) -> pd.Series | None:
    """The listed strike nearest the target, at the chosen expiry."""
    if at_expiry.empty:
        return None
    # argmin on the reset positional axis rather than idxmin + .loc: a
    # duplicated index (two writes of the same session) makes .loc return a
    # frame instead of a row, and that silent shape change is exactly the kind
    # of thing that only shows up in production.
    position = int((at_expiry["strike"] - target).abs().to_numpy().argmin())
    return at_expiry.iloc[position]


def _is_liquid(row: pd.Series) -> bool:
    """Whether this contract is one an executor could actually work.

    A zero bid disqualifies on its own: it means no one is offering to take the
    position back, so the mid is half of a number that does not exist.
    """
    bid = float(row["bid"])
    ask = float(row["ask"])
    if bid <= 0 or ask <= 0:
        return False
    if int(row["open_interest"]) < MIN_OPEN_INTEREST:
        return False
    mid = (bid + ask) / 2
    return mid > 0 and (ask - bid) / mid <= MAX_RELATIVE_SPREAD


#: Columns ``_mark_one`` cannot work without. An empty ``pd.DataFrame()`` has no
#: columns at all, so this is a real case and not defensive padding: it is the
#: state of the lake before the first sweep, and the state of any as-of read
#: that predates it.
_REQUIRED_COLUMNS = ("underlying", "expiration", "strike", "bid", "ask", "open_interest")


def _mark_one(leg: RollLeg, chain: pd.DataFrame) -> MarkedLeg:
    marked = MarkedLeg(**leg.model_dump())
    if chain.empty or not set(_REQUIRED_COLUMNS) <= set(chain.columns):
        marked.quote_status = "not_collected"
        return marked
    for_asset = chain[chain["underlying"].str.upper() == leg.asset.upper()]
    if for_asset.empty:
        marked.quote_status = "not_collected"
        return marked

    expiry = _select_expiry(for_asset, leg.target_expiry)
    if expiry is None:
        marked.quote_status = "no_listed_contract"
        return marked

    row = _select_strike(for_asset[for_asset["expiration"] == expiry], leg.target_strike)
    if row is None:
        marked.quote_status = "no_listed_contract"
        return marked

    marked.listed_strike = float(row["strike"])
    marked.listed_expiry = expiry.date()
    marked.market_bid = float(row["bid"])
    marked.market_ask = float(row["ask"])
    marked.market_mid = (marked.market_bid + marked.market_ask) / 2
    marked.market_open_interest = int(row["open_interest"])
    delta = row.get("delta")
    marked.market_delta = None if delta is None or pd.isna(delta) else float(delta)

    if not _is_liquid(row):
        marked.quote_status = "illiquid"
        return marked

    marked.quote_status = "quoted"
    cost_per_contract = marked.market_ask * SHARES_PER_CONTRACT
    marked.market_contracts = math.floor(leg.premium_budget / cost_per_contract)
    if leg.model_premium and leg.model_premium > 0 and marked.market_mid is not None:
        marked.market_to_model_ratio = marked.market_mid / leg.model_premium
    return marked


def mark_schedule(schedule: RollSchedule, chain: pd.DataFrame) -> MarkedSchedule:
    """Attach listed contracts and real quotes to every leg of ``schedule``.

    ``chain`` is one session of ``option_chain_snapshot`` (``docs/adr/0020``).
    The schedule's ``schedule_id`` is carried through unchanged: marking adds
    what the market says about the same recommendation, it does not change the
    recommendation, so an executor still dedupes on the identity the screen
    produced.
    """
    has_session = not chain.empty and "quote_date" in chain.columns
    session = chain["quote_date"].max().date() if has_session else None
    legs = [_mark_one(leg, chain) for leg in schedule.legs]
    return MarkedSchedule(
        **{k: v for k, v in schedule.model_dump().items() if k != "legs"},
        legs=legs,
        quote_session=session,
        quoted_legs=sum(1 for leg in legs if leg.quote_status == "quoted"),
    )
