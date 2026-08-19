"""The screening universe + each name's options-listing cadence
(``docs/END_STATE.md`` §1.1/§1.2).

A LEAF module: imports nothing else from ``tail_lab``. It is the single source
of truth for *which* underlyings the Put Lab screens (the frontend dropdown,
the ranking sweep, and the cadence tile all read it) and how often each lists
expiries.

Cadence is hand-maintained *for now* — a slow-moving, public property of each
chain. Every name below is genuinely weekly- or monthly-optionable and liquid.
A keyless live adapter (Yahoo ``/v7/finance/options`` expiration dates) is a
queued follow-up (``AGENT_TODO.md``); until then this table is the contract,
and `cadence_for` falls back to a flagged weekly default for anything unlisted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Cadence = Literal["weekly", "monthly"]


class OptionsCadence(BaseModel):
    """One universe member: ticker, display name, and its listed cadence."""

    symbol: str
    name: str
    cadence: Cadence
    avg_gap_days: float
    label: str
    detail: str


# (symbol, display name, cadence, avg gap days). Weeklies dominate the liquid
# US single-name/ETF chains; the gap is the effective spacing a retail chain
# shows. Grouped by kind for legibility.
_UNIVERSE: tuple[tuple[str, str, Cadence, float], ...] = (
    # broad indices / market ETFs
    ("SPY", "S&P 500 (SPY)", "weekly", 2.5),
    ("QQQ", "Nasdaq-100 (QQQ)", "weekly", 2.5),
    ("IWM", "Russell 2000 (IWM)", "weekly", 3.5),
    ("DIA", "Dow 30 (DIA)", "weekly", 3.5),
    # sector ETFs
    ("XLF", "Financials (XLF)", "weekly", 3.5),
    ("XLE", "Energy (XLE)", "weekly", 3.5),
    ("XLK", "Technology (XLK)", "weekly", 3.5),
    ("XLU", "Utilities (XLU)", "monthly", 7.0),
    ("SMH", "Semiconductors (SMH)", "weekly", 3.5),
    ("KRE", "Regional Banks (KRE)", "weekly", 3.5),
    ("XBI", "Biotech (XBI)", "weekly", 3.5),
    ("XRT", "Retail (XRT)", "monthly", 7.0),
    # rates / credit / commodities
    ("TLT", "20y Treasuries (TLT)", "weekly", 3.5),
    ("HYG", "High-Yield Credit (HYG)", "monthly", 7.0),
    ("LQD", "IG Credit (LQD)", "monthly", 7.0),
    ("GLD", "Gold (GLD)", "weekly", 3.5),
    ("SLV", "Silver (SLV)", "weekly", 3.5),
    ("USO", "Crude Oil (USO)", "weekly", 3.5),
    ("EEM", "Emerging Mkts (EEM)", "monthly", 7.0),
    ("FXI", "China Large-Cap (FXI)", "weekly", 3.5),
    ("ARKK", "ARK Innovation (ARKK)", "weekly", 3.5),
    # mega-cap tech
    ("AAPL", "Apple (AAPL)", "weekly", 3.5),
    ("MSFT", "Microsoft (MSFT)", "weekly", 3.5),
    ("NVDA", "Nvidia (NVDA)", "weekly", 3.5),
    ("AMZN", "Amazon (AMZN)", "weekly", 3.5),
    ("META", "Meta (META)", "weekly", 3.5),
    ("GOOGL", "Alphabet (GOOGL)", "weekly", 3.5),
    ("TSLA", "Tesla (TSLA)", "weekly", 3.5),
    ("AMD", "AMD (AMD)", "weekly", 3.5),
    ("NFLX", "Netflix (NFLX)", "weekly", 3.5),
    # high-vol / cyclical single names
    ("JPM", "JPMorgan (JPM)", "weekly", 3.5),
    ("BA", "Boeing (BA)", "weekly", 3.5),
    ("XOM", "Exxon (XOM)", "weekly", 3.5),
    ("COIN", "Coinbase (COIN)", "weekly", 3.5),
    ("PLTR", "Palantir (PLTR)", "weekly", 3.5),
)


def _cadence(symbol: str, name: str, cadence: Cadence, gap: float) -> OptionsCadence:
    if cadence == "weekly":
        label, detail = "Weeklies", "Weekly + monthly expiries"
    else:
        label, detail = "Monthlies", "Third-Friday monthlies, thin weeklies"
    return OptionsCadence(
        symbol=symbol, name=name, cadence=cadence, avg_gap_days=gap, label=label, detail=detail
    )


_CALENDAR: dict[str, OptionsCadence] = {
    sym: _cadence(sym, name, cad, gap) for sym, name, cad, gap in _UNIVERSE
}

#: Fallback for a symbol not in the table — a flagged weekly default, so a
#: caller never mistakes it for a verified listing.
_DEFAULT = OptionsCadence(
    symbol="?",
    name="?",
    cadence="weekly",
    avg_gap_days=3.5,
    label="Weeklies (assumed)",
    detail="No verified listing on file; weekly default assumed",
)


def screening_universe() -> list[OptionsCadence]:
    """Every universe member, in a stable display order (the order above)."""
    return list(_CALENDAR.values())


def universe_symbols() -> tuple[str, ...]:
    """Just the tickers, lower-cased (the lake dataset key convention)."""
    return tuple(m.symbol.lower() for m in _CALENDAR.values())


def cadence_for(symbol: str) -> OptionsCadence:
    """Listing cadence for ``symbol`` (case-insensitive), or a flagged weekly
    default if it isn't in the table."""
    found = _CALENDAR.get(symbol.upper())
    if found is not None:
        return found
    return _DEFAULT.model_copy(update={"symbol": symbol.upper(), "name": symbol.upper()})
