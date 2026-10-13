# 30. The daily agent reviews its own change before it opens a PR

Date: 2026-10-13

## Status

**Accepted.** Dio raised the problem on 2026-10-12 ("these auto agents are not
as high quality as plan agent who later executes and reviews in loops until
perfection"), chose to pilot the fix on tail-lab first, and approved the plan
after sixteen rounds of adversarial review. Amends `docs/adr/0025` §Decision 1
in one place, stated below. Builds on `docs/adr/0016`, `0022`, `0023` and
`0024`.

## Context

An interactive session here plans, builds, then sends fresh adversarial
reviewers at its own diff and loops until a round finds nothing. The scheduled
agents did none of that:

- **No model was pinned.** All four Claude steps (daily, weekly, CI review,
  CI fixer) ran the account default, `claude-sonnet-5-5`. The CI "adversarial
  review" of #136, a whole new tab, took 4 turns and about 15 seconds.
- **The only unattended reviewer reads design, not correctness.** Its prompt
  says correctness, types, layering and coverage "are already proven by other
  jobs… Do not re-check them". Nothing checked whether the code does what it
  claims, whether the tests could fail, or whether the PR body is true. That is
  what interactive review keeps catching (`806fd2f`: a mutation that "had
  survived every test").
- **The fix loop was not the bottleneck.** Agent PRs merged 2026-09-12 to
  10-12 had zero `[review-fix]` rounds, so "make CI fix more" was not the
  answer.
- **Agents redid open work.** The weekly agent proposed the same complexity
  refactor three Sundays running (#126, #135, #160) while each sat waiting for
  Dio, because it chose work from `main` and never looked at its own open PRs.

## Decision

1. **Opus on every Claude step.** `--model claude-opus-5-5` on the daily and
   weekly agents and on CI's reviewer and fixer.
2. **The daily agent reviews before it proposes.** The procedure is
   `docs/AGENT_MISSION.md` §Workflow 3a–3b; this ADR does not restate it. In
   short: three parallel `adversarial-reviewer` subagents
   (`.claude/agents/adversarial-reviewer.md`: correctness, test strength by
   mutation, claims), at most two rounds, inside a time budget, and push only
   when the PR is opened.
3. **A draft is the honest outcome when review did not come back clean.** A PR
   whose last round was not clean, or that ran no round, opens as a draft and
   waits for Dio. Feedback issues it touches stay open.
   **Amends `docs/adr/0025` §Decision 1:** `agent-review` skips drafts and runs
   when one is marked ready, so the CI fixer never rewrites a PR Dio is meant to
   judge as it stands.
4. **`.claude/**` joins the constitution.** The reviewers are the agent's own
   quality gate, so an agent may not edit them. It is added to `automerge.yml`'s
   guarded paths and to CI's `constitution-guard`. Root `CLAUDE.md` is loaded as
   instructions too, so `constitution-guard` also blocks agent PRs that touch
   it. It stays off `automerge.yml`'s list, because Dio's own PRs edit it
   routinely. That makes three kinds of
   change `docs/adr/0025` lists as stopping an auto-merge: drafts, the existing
   constitution, and now `.claude/**`.
5. **Neither agent redoes an open PR.** Both workflows list open `agent/*` PRs
   authored by `claude` in this repo before the agent runs. Fork PRs are
   ignored: the repo is public, and a fork branch named `agent/*` must not be
   able to stall an agent or put text in its prompt. The weekly agent runs only when no `agent/cleanup-*` PR
   is open. The daily agent skips anything an open PR covers, and stops while
   three or more of its drafts are open.

## Consequences

- **Cost.** tail-lab is public, so its Actions minutes are free. Usage is not:
  every agent, the CI reviewer on every PR (Dio's included, under
  `docs/adr/0025`) and these subagents share one subscription token. Actual
  start times are around 12:00–16:30 UTC for the daily agent and 10:00–12:00
  UTC on Sundays for the weekly one. If the usage limit is hit, `agent-review`
  fails and PRs wait.
- **Timeouts lose a run, not state.** The job has a 90-minute timeout and the
  Claude step 80 minutes. Nothing is pushed until the PR is opened, so a
  timed-out run leaves no branch and its item is retried. When the step times
  out, the action's own `if: always()` cleanup may not run, and its app token
  then simply expires.
- **"Reviewers don't edit" is a convention, not a sandbox.** Reviewers have
  Bash and do their mutation work in their own gitignored worktree. The parent
  agent checks that `git status` is clean and `HEAD` unchanged after every
  round, and discards the round otherwise.
- **Merging this changes `ci.yml`,** so open branches based on the old file
  become stale for `agent-review`.
- **Evaluation**, over 4–6 weeks, before porting this to quizkit:
  - significant findings per round, from each PR's Review log (round 1 is the
    "would have shipped" count);
  - `num_turns` and `total_cost_usd` from the run logs;
  - one interactive adversarial review of three agent PRs from before (#149,
    #152, #163) and three from after.
