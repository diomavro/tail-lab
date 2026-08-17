# 10. Survivorship bias — point-in-time constituents as a validity requirement

Date: 2026-08-17

## Status

Accepted

## Context

A free "current constituents" pull is survivorship-biased by
construction: it omits exactly the names that blew up and delisted, the
most tail-sensitive assets of all. For a platform built around tail
sensitivity, backtesting only survivors risks understating both the
frequency and payoff of the events S1 exists to capture.

## Decision

Point-in-time historical index constituents are a **first-class
research-validity requirement**, tracked explicitly in
`docs/END_STATE.md` §2.3, not buried in the general deferred backlog.
Until a genuine point-in-time feed exists, every result derived from a
"current constituents" universe carries an explicit, structural caveat —
not a footnote a reader can miss.

## Consequences

Early results are usable but explicitly caveated as survivorship-biased,
keeping the platform honest about its own limits. This costs real future
effort (point-in-time constituents are harder to source free); the
milestone ordering (`docs/END_STATE.md` §5) places it early, not last.
