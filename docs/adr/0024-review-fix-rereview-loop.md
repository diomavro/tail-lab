# 24. The design review is a loop, not a verdict

Date: 2026-09-02

## Status

**Accepted.** Implemented in `.github/workflows/ci.yml` (`agent-review`).
Fully active only once `AGENT_FIX_TOKEN` exists (`HUMAN_TODO.md`); without it
the job reviews and blocks exactly as it did before, and cannot fix.

## Context

`docs/adr/0023` added an adversarial design reviewer to `agent/*` PRs. It
worked — it caught a `Makefile` committed with conflict markers that four green
jobs could not see. But it was only ever half a mechanism, and both halves of
its own verdict dead-ended:

- **PASS with advisory notes** merged within seconds, onto a thread nobody
  reopens. Fixed on 2026-09-02 by having the daily agent read those comments.
- **BLOCK** left the PR red, and the agent that wrote it is a one-shot daily run
  that never returns. Absent a human the PR rots — five did, for four days.

Dio named the missing shape directly: *agent makes a PR → adversarial agent
reviews → if the comments are important, the PR is fixed and a new adversarial
agent is sent → loop until the review brings nothing important.* That is a
control loop. What existed was an alarm.

The obstacle was mechanical and worth recording, because it is the same rule
that shaped `docs/adr/0016`: **a push made with `GITHUB_TOKEN` does not trigger
workflows.** A fix pushed by the job with the default token would never be
re-reviewed, so the loop would stall after exactly one round — which looks like
a working loop until the first time a fix is imperfect.

## Decision

**The `agent-review` job becomes review → fix → push, and the push starts a
fresh run that reviews again.** Termination is by the reviewer passing, not by
a counter, which is what makes it Dio's loop rather than a fixed number of
attempts.

1. **The fix is pushed with `AGENT_FIX_TOKEN`, a PAT** — not `GITHUB_TOKEN`.
   This is the whole reason the loop can exist. Without it the push is inert.
2. **Each round is a genuinely fresh review.** The re-review happens in a new
   workflow run, a new `claude-code-action` session, with no memory of having
   written the fix. It is told which commits are `[review-fix]` and to judge
   them as critically as anything else, because a fix that missed the point is
   a finding rather than a resolution. This is the property that makes the loop
   worth running: a reviewer grading its own repair is not adversarial.
3. **The fixer may disagree.** If a finding is wrong — it misreads the code, or
   objects to a pattern sanctioned elsewhere — it must leave the code alone and
   say so in the thread with evidence. **Code bent to satisfy a mistaken
   reviewer is a worse outcome than either an unfixed nit or a stalled PR**, and
   a loop that cannot end in disagreement will manufacture damage to terminate.
4. **The fixer re-runs the whole gate.** The other CI jobs in that run tested
   the code *before* the fix, so their green is stale the moment a file changes;
   the fixer is then the only thing between a broken change and a merge.
5. **`MAX_FIX_ROUNDS = 3`.** "Loop until the review is clean" and "loop forever"
   are the same program whenever the reviewer and the fixer disagree. On
   exhaustion the job fails with a message saying the loop gave up rather than
   spun, and a human reads the thread.
6. **The run that pushes a fix fails on purpose.** Its own checks describe
   superseded code; the fresh run carries the authoritative verdict. Failing is
   how this run declines to be the one that merges.
7. **Scope is the review, and only the review.** The fixer is told it is not
   there to improve the PR. Scope creep gives the next round a larger diff to
   audit and defeats the loop by making each iteration harder than the last.

## Consequences

- **The reviewer stops being advisory.** Until now its findings needed a human
  to act on them, and the measured record of that is two correct findings merged
  unaddressed and five PRs stalled four days.
- **Cost scales with disagreement, not with volume.** A clean PR is one review,
  as today. Only a blocked PR pays for a fix and a re-review, and at most three
  times.
- **It needs a credential this repo has deliberately avoided.** `AGENT_FIX_TOKEN`
  is a PAT with push rights, and `docs/adr/0019` was written specifically to keep
  privileged tokens out of this CI. The distinction: this token can push to an
  `agent/*` branch, and everything on that path is still gated by full CI,
  `constitution-guard` (which blocks `.github/workflows/**` and every rule
  document), and the review itself. It cannot deploy, cannot reach a broker, and
  cannot touch the constitution. Compared with a brokerage credential the blast
  radius is a bad commit on a branch, caught by the same gates as any other.
- **Until the secret exists, nothing changes.** The job reviews and blocks as
  before, and says in the log that the loop is disabled and why — the same
  no-op-until-provisioned pattern `deploy.yml` uses for `FLY_API_TOKEN`.
- **The risk to watch is a fixer that placates.** The instruction to disagree is
  the only thing preventing a loop that converges by making the code worse. If a
  `[review-fix]` commit ever weakens a test or contorts a design to satisfy a
  reviewer, that is this ADR failing, not working — and `MAX_FIX_ROUNDS` bounds
  the damage rather than preventing it.
