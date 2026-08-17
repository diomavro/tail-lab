# 11. Initial setup: end-state docs plus a walking skeleton

Date: 2026-08-17

## Status

Accepted

## Context

tail-lab is meant to be extended a little every day by an autonomous
agent, indefinitely, against a written end-state. A fully-speced-but-empty
repo gives the agent no concrete pattern to extend — its first PR has to
invent conventions from scratch, exactly the "invents architecture"
failure mode `docs/AGENT_MISSION.md` warns against. A large human-built
first feature defeats the point of daily incremental growth.

## Decision

The initial setup is **end-state documentation plus a deployed walking
skeleton**: `README.md`, `ARCHITECTURE.md`, and `docs/` written first,
then one thin vertical slice proving every layer connects — one data
source (VIX, keyless) through bronze → silver → gold, one metric, one
dashboard tile. Paper-thin in scope, but complete, typed, tested,
CI-gated, and deployed — no shortcuts because "it's just a skeleton."

## Consequences

The agent's first daily PR has a concrete, correct pattern to extend, and
every later increment is judged against a real deployed baseline. Cost:
the skeleton itself takes effort across every layer before any research
question gets answered — deliberate, since a shortcut in one layer would
teach the agent the wrong pattern for it.
