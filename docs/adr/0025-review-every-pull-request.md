# 25. The adversarial review covers every pull request

Date: 2026-09-03

## Status

**Accepted.** Implemented in `.github/workflows/ci.yml` (`agent-review`),
superseding the branch scope set by `docs/adr/0023` and carried into
`docs/adr/0024`.

## Context

`docs/adr/0023` introduced the adversarial design reviewer and scoped it to
`agent/*` branches. The reasoning was that unattended code is what needs a
second reader, and the daily agent is the unattended author here.

The reasoning was half right, and the half it got wrong is the expensive half.
On 2026-09-03 Dio asked that everything produced in an assistant session be run
through an adversarial reviewer. Checking what the existing job actually
covered showed it had never once looked at such a change: every PR opened from
this session reported `agent-review=SKIPPED`. The reviewer had been built in
this repo and then routed around by its own author.

Two things were conflated under the word "unattended":

- **Unattended at write time** — nobody watches the code being produced. True
  of the daily agent, and equally true of an assistant session: the human reads
  a summary of a change, not the change.
- **Unattended at merge time** — nobody is present to act on a review. True of
  the daily agent, which is a one-shot run that never returns. Not true of a
  human branch.

Only the second justifies the auto-fixer. The first — which is what the
*review* answers — applies to both. Scoping the review by branch prefix used
the merge-time property to gate the write-time mechanism.

The same session supplied the direct evidence. A manually spawned adversarial
review of one assistant-written change found, and independently verified, a
memory fault that would read a 681 MB panel on a 1 GB machine on every request,
an unguarded index that would take down the panel it was meant to protect, and
a coverage figure scoped to the wrong period — all in code that passed ruff,
mypy `--strict`, import-linter and 726 tests. Every one of those gates was
green. Nothing about that change's authorship made it safer than an agent's;
what made it reviewable was that a reader with no stake in it landing read it.

## Decision

**The review runs on every pull request. The auto-fixer stays scoped to the
daily agent.**

1. `agent-review` triggers on `github.event_name == 'pull_request'` rather than
   on the branch prefix. Gated to pull requests because every step in the job
   reads `github.event.pull_request.*`, which is null on `push` and
   `workflow_dispatch`.
2. `can_fix` additionally requires `startsWith(github.head_ref, 'agent/')`. The
   fixer commits and pushes to the PR branch; that is correct for a one-shot
   author whose findings otherwise rot, and wrong for a branch a human is
   working on, where rewriting the branch underneath them is a surprise and the
   human is present to act on the review anyway.
3. The reviewer's prompt no longer addresses "an autonomous agent's pull
   request". It is told the author may be the agent, a human, or a human's
   assistant, and to review identically either way — explicitly including a
   warning not to soften a finding because the author looks careful.

Verdict semantics are unchanged: `PASS` merges, `FINDINGS` merges after at most
`MAX_FIX_ROUNDS` (immediately, on a human branch, since there is no fix round),
`DEFECT` never auto-merges. Auto-merge itself is unaffected — `automerge.yml`
is independently gated on `agent/*`, so a human PR that passes review still
waits for a human to merge it.

## Consequences

- **Every merged change has been read by something that did not write it.**
  That is the property `docs/adr/0023` was after; the branch scope was
  preventing it on the changes least likely to get a second reader.
- **Human PRs get a verdict but not a rewrite.** A `DEFECT` blocks. `FINDINGS`
  reports and merges, since `can_fix` is false and the enforce step already
  treats unfixable findings as advisory rather than fatal — the daily agent
  picks them up from the comment.
- **Cost rises with PR volume, not with agent volume.** One review per pull
  request. The fix-and-re-review rounds, which are the expensive part, remain
  agent-only.
- **A reviewer outage now affects everyone.** `docs/adr/0024`'s no-verdict rule
  (`NONE` is treated as `PASS`, with a warning) is what keeps this from
  becoming a repo-wide stall, and it matters more under this ADR than it did
  before. A repeated no-verdict must be fixed or the job removed; a green check
  standing for a review that never happened is worse than no check.
- **This ADR edits the constitution and therefore cannot be made by the daily
  agent** (`docs/adr/0022`). It was made by a human decision, recorded here so
  that a future session finding a reviewed human PR does not read it as a
  misconfiguration and narrow the scope back.

## Notes

The general lesson is the one this repo keeps relearning, and it is worth
stating plainly because it has now cost four separate incidents: *specifying
the criterion is not the same as specifying the path the signal takes.* A
fallback that shared a failure domain with the thing it backed up; a `Makefile`
no gate could read; a review nobody was obliged to read; and now a reviewer
whose trigger excluded a whole class of author. In each case the intent was
right and the wiring silently excluded the case that mattered.
