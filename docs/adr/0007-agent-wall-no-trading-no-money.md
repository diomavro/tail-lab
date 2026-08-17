# 7. Agent wall — never trades, never touches money or credentials

Date: 2026-08-17

## Status

Accepted

## Context

The daily agent runs autonomously, writes code, and opens PRs against a
platform whose purpose is informing a real trading decision. An agent
with any path to money or brokerage credentials — however indirect — turns
a documentation mistake into a financial one, and every PR review into a
security review instead of a research review.

## Decision

**The agent never trades, and never touches money or credentials.** Its
scope is strictly the platform: data, backtests, metrics, dashboard, docs,
tests, CI (`docs/AGENT_MISSION.md` §"The wall"). Every trade is placed by
Dio, by hand. No code path may call a brokerage API, hold a trading
credential, or place an order.

## Consequences

The blast radius of an agent mistake is bounded to "bad PR, caught in
review" — never an unauthorized trade or a leaked credential. This is
non-negotiable: changing it needs a new ADR that explicitly supersedes
this one, not scope creep in an unrelated PR.
