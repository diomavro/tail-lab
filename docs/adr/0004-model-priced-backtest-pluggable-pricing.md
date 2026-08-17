# 4. Model-priced backtest, behind a pluggable pricing interface

Date: 2026-08-17

## Status

Accepted

## Context

Backtesting OOM-put buying needs option prices on historical underlying
paths. Real historical option quotes are paid (`HUMAN_TODO.md`); no free
source has the needed depth/history. Waiting on a budget decision before
any backtest exists would stall the whole research program.

## Decision

v1 prices OOM puts with Black-Scholes plus a vol-surface proxy from the
VIX/SKEW complex and FRED rates, behind a pluggable `OptionPricer`
protocol (`research/pricing/interface.py`). Real quotes become a second
implementation later, gated on the `HUMAN_TODO.md` budget item — an
upgrade, not a prerequisite.

**Caveat, repeated everywhere a model-priced result appears:** the S1
thesis is that the market *misprices* tail risk; a model priced off its
own vol inputs cannot see that mispricing. Treat results as a **relative
ranking of sensitivity metrics**, not P&L truth, until real quotes arrive.

## Consequences

Research starts immediately on free data. Cost: every model-priced result
needs the caveat attached wherever shown, so absolute numbers are never
mistaken for expected P&L — `docs/END_STATE.md` §3 tracks the v2 upgrade.
