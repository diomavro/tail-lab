# 1. Record architecture decisions

Date: 2026-08-17

## Status

Accepted

## Context

tail-lab is built incrementally by a daily autonomous agent
(`docs/AGENT_MISSION.md`). Without a durable record of *why* a structural
call was made, the agent could re-litigate settled decisions every run.

## Decision

Record architecturally significant decisions as ADRs in `docs/adr/`,
numbered sequentially, Nygard's short format:

```
# N. Title
Date: YYYY-MM-DD
## Status
Proposed | Accepted | Deprecated | Superseded by ADR-000N
## Context / ## Decision / ## Consequences
```

The agent may **propose** an ADR (`Status: Proposed`) but cannot accept its
own proposal or edit `ARCHITECTURE.md`/`docs/STANDARDS.md` to match it.

## Consequences

Future sessions read `docs/adr/` before re-deriving a settled decision.
ADRs are immutable once accepted — a changed decision gets a new ADR.
