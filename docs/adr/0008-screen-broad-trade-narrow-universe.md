# 8. Screen broad, trade narrow

Date: 2026-08-17

## Status

Accepted

## Context

Sensitivity screening is more informative the broader the universe, but
live put-buying only works where options are actually liquid — screening
a name with no tradable options is trivia, not an actionable candidate.
Because the backtest is model-priced (`docs/adr/0004`), it can price a
hypothetical put on any underlying with a price history, so liquidity
doesn't bind for backtesting, only for live trading.

## Decision

The sensitivity leaderboard ranks the full broad universe from free daily
OHLCV. The OOM-put candidate list and the option-chain dataset
(`docs/DATA_CONTRACTS.md` #6) are restricted to the options-liquid
tradable subset. Backtests run over the broad universe now; the narrow
restriction only bites when a human is about to trade.

## Consequences

Research gets answered on the full breadth of free data; the cockpit
never dangles an untradeable candidate as actionable. Cost: two universe
definitions to keep straight — `contracts/` shapes, not naming alone,
should disambiguate which one a DTO refers to.
