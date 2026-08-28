# 23. Three gates for code nobody reads before it merges

Date: 2026-08-28

## Status

**Accepted.** Implemented in `pyproject.toml` (the ratchet),
`.github/workflows/ci.yml` (`agent-review`), and
`.github/workflows/weekly-cleanup.yml`.

## Context

`docs/adr/0016` made ordinary agent increments auto-merge on green CI and
auto-deploy from green `main`. That was the right trade for velocity, and it
has worked: the platform grew a Put Lab, a memory layer, six sensitivity
metrics and five ingestion adapters without a human in the merge path.

What it also did, quietly, is make **CI the only reader**. And CI could see
almost everything about a change except whether it was any good.

Measured on 2026-08-28 across every merged PR:

| | agent PRs | human PRs |
|---|---|---|
| count | 40 (75%) | 13 |
| lines added | 16,079 | 10,904 |
| lines deleted | 1,020 | 4,579 |
| **added : deleted** | **15.8 : 1** | **2.4 : 1** |

The agent is 6.5x less willing to delete than the humans working in the same
repo. That is not a defect in the agent; it is the system behaving exactly as
specified. Three things compound:

1. **The value bar asks "does this measurably sharpen the cockpit?"** A
   refactor never scores on that question. New capability always does.
2. **The charter forbids cleanup** — "do not refactor for its own sake, or pad
   a small change with unrelated cleanup" (`docs/AGENT_MISSION.md`). That rule
   is *correct*: drive-by refactoring is how a reviewable diff stops being
   reviewable. But it had no counterpart, so nothing was ever anybody's job to
   delete.
3. **The gate was blind to design.** `ruff` selected `E,W,F,I,B,UP,RUF` — bugs
   and style. `mypy --strict` proves types. `import-linter` proves layer
   direction. Coverage floors prove tests exist. Not one of them can see
   duplication, dead abstraction, or a function acquiring its fourteenth
   parameter.

The shape of the resulting decay is worth stating precisely, because it is not
the one you would guess. Enabling `SIM`, `RET`, `PIE` and `ARG` on the existing
code produced **zero findings**. The agent does not write sloppy lines. What it
does is *extend rather than reshape* — because extending is always the smaller,
safer-looking diff. The measured evidence: 28 `too-many-arguments`, a 206-line
`run_put_roll` with 13 parameters, and 21 of 271 functions over 60 lines.

## Decision

Three gates, each aimed at a different one of the three causes.

**1. A ratchet, in `pyproject.toml`.** Enable the rule families that were
missing (`SIM`, `RET`, `PIE`, `ARG`, `C901`, `PLR0913`, `PLR0915`, `PLR0917`)
and set the counters **at today's worst offenders** — complexity 14, 13 args,
75 statements — with each threshold naming the function that set it.

Setting a limit where the code already is costs nothing today and makes it
impossible for the next four hundred agent PRs to make it worse. **The numbers
may only ever go down.** Lowering one is a one-line PR gated on an actual
refactor; raising one means an increment made the codebase measurably worse and
should be rejected instead of accommodated.

`ARG` is off for `tests/**`: a pytest fixture is requested by name and used for
its side effect, so a test taking `fixed_clock` and never referencing it is
correct, not dead.

**2. An adversarial design review, as a job in CI.** Runs only on `agent/*`
PRs. Explicitly *not* for correctness, types, layering or style — every one of
those is proven by another job in the same run, and a finding that ruff would
have caught is not this reviewer's finding. It looks at the four things no
linter here can see: duplication, dead or speculative code, accretion, and
unearned complexity.

It is a **job inside `ci.yml` rather than a `workflow_run` listener** because
GitHub suppresses `workflow_run` events from bot-initiated runs
(`docs/adr/0016`) — a listener would never fire on precisely the PRs it exists
to review. As a CI job it also gates auto-merge for free, since `automerge.yml`
only acts when the whole run succeeded.

**Its bar for blocking is deliberately high.** It gates the platform's only
daily increment, so a false positive costs a human's morning. It may block only
with a named file, a quoted line, and a statement of what should have happened
instead; taste disagreements are advisory. It is told, in those words, that
**when uncertain it passes** — and if it fails to produce a verdict at all the
job passes with a warning, because a reviewer that cannot run must not become a
gate that cannot open.

**3. A weekly cleanup agent that may only subtract.** One run a week
(`weekly-cleanup.yml`, Sundays) inverts the value bar: it ships no capability
and its entire remit is deletion, consolidation and reshaping. Its highest-value
move is refactoring one of the functions the ratchet names and **lowering that
number in the same PR** — which closes the loop, since the limit only ever
descends and this is what pushes it.

Its safety property is that **`tests/` must pass unchanged**. A refactor that
preserves behaviour does not need its tests edited; that is what distinguishes
it from a rewrite. A cleanup PR that edits an assertion has changed behaviour
and is in the wrong workflow. Its branches carry the `agent/` prefix on purpose,
so it is reviewed and merged by exactly the same path as everything else.

## Consequences

- **The 15.8:1 number is now observable and has an owner.** It was invisible for
  40 PRs. It should be re-measured periodically; if it is not falling, these
  gates are not working and that is itself the finding.
- **Agent PRs cost more.** One extra Claude invocation each. At roughly one
  agent PR a day that is affordable, and it buys the only reader the code
  otherwise has.
- **The reviewer is an LLM, and this is a second opinion, not a proof.** It will
  miss things. Its value is that it has no stake in the change landing and it
  reads the diff cold, which is exactly the perspective the authoring agent
  cannot have. Treating its PASS as a guarantee of quality would be a worse
  error than having no reviewer at all.
- **A new failure mode: the stalled pipeline.** If the reviewer becomes
  trigger-happy the daily increment stops, and the collection in
  `docs/adr/0020` is the one thing that cannot wait. The high blocking bar, the
  pass-on-uncertainty instruction and the pass-on-missing-verdict fallback all
  exist for that reason. If it blocks more than occasionally, loosen it — do not
  let it sit red.
- **What this does not fix.** None of these gates can tell whether the
  *architecture* is right, only whether it is degrading. A wrong abstraction
  introduced cleanly and extended consistently will pass all three forever.
  That judgement remains human, and `ARCHITECTURE.md` remains constitution for
  exactly that reason.
