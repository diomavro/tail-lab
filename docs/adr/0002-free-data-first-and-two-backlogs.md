# 2. Free-data-first, and two separate backlogs

Date: 2026-08-17

## Status

Accepted

## Context

The daily agent must never touch money or credentials (`docs/adr/0007`).
Unconstrained, "build the best data platform" drifts toward paid data
(options history, CDS, breadth) the agent cannot acquire — an account, a
card, a key all need a human. Without an explicit split it either stalls
or improvises around the blocker.

## Decision

1. **Free-data first.** Every v1 dataset has a free, ideally keyless,
   source (`docs/DATA_CONTRACTS.md`). Paid/account-gated sources are
   opt-in upgrades, never a v1 dependency.
2. **Two backlogs.** `AGENT_TODO.md` is the agent's queue.
   `HUMAN_TODO.md` is Dio's — anything needing an account, key, or money.
   The agent appends to `HUMAN_TODO.md` on a real-world blocker and stops;
   it never acts on, removes, or reorders an item there.

## Consequences

The agent's scope stays cleanly bounded and every paid-resource decision
stays deliberately human. Cost: some datasets sit deferred until Dio
clears the matching `HUMAN_TODO.md` item — accepted as a feature.
