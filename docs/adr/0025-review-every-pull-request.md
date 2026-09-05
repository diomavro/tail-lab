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

**The whole loop runs on every pull request: review, fix, re-review, merge.**

Dio stated the intended shape directly: *agent A prepares the PR, agent B does
an adversarial review, agent C checks whether the critiques are fair and
adjusts the PR as it should be, then back to B until C judges the review has
nothing important.* And then it merges. `docs/adr/0024` built exactly that, and
scoped it to `agent/*`.

1. `agent-review` triggers on `github.event_name == 'pull_request'` rather than
   on the branch prefix. Gated to pull requests because every step in the job
   reads `github.event.pull_request.*`, which is null on `push` and
   `workflow_dispatch`.
2. `can_fix` no longer requires an `agent/` head ref. A first pass at this ADR
   kept the fixer agent-only, reasoning that pushing commits to a branch a
   human is working on is a surprise. That was the wrong trade and Dio
   corrected it: a PR that gets the review but not the fixer is back to being
   an alarm rather than a control loop, which is the exact failure
   `docs/adr/0024` exists to fix. The fixer is instructed to record a reasoned
   disagreement rather than contort code to satisfy a finding it thinks is
   wrong, so step 3 really is a judgement and not compliance.
3. `automerge.yml` drops its `agent/*` gate, so the loop terminates in a merge
   for every author. It is renamed accordingly.
4. The reviewer's prompt no longer addresses "an autonomous agent's pull
   request". It is told the author may be the agent, a human, or a human's
   assistant, and to review identically either way — explicitly including a
   warning not to soften a finding because the author looks careful.

Verdict semantics are unchanged: `PASS` merges; `FINDINGS` drives up to
`MAX_FIX_ROUNDS` fix-and-re-review rounds and then merges anyway, the finding
surviving as queued work rather than as a stalled PR; `DEFECT` never
auto-merges.

**Two things still stop an auto-merge**, and they are the whole safety story
now that the branch gate is gone:

- **A draft PR.** A draft is a proposal, not a request to merge.
- **A PR touching the constitution** — `docs/adr/**`, all of `.github/**`
  (not just `workflows/`: a composite action or a dependabot config changes
  what CI does too), `pyproject.toml` (it holds the mypy strictness, the ruff
  rules, the import-linter contracts and the coverage floor), `README.md`,
  `ARCHITECTURE.md`, `docs/STANDARDS.md`, `docs/AGENT_MISSION.md`,
  `docs/END_STATE.md`. The rules governing the agents must not be changeable by
  the agents (`docs/adr/0022`). Note *where* this is enforced: the
  `constitution-guard` CI job only inspects `agent/*` branches, so for every
  other author the check inside `automerge.yml` is not a second line of defence,
  it is the only one. Do not remove it on the grounds that CI already covers it.

## Consequences

- **Every merged change has been read by something that did not write it.**
  That is the property `docs/adr/0023` was after; the branch scope was
  preventing it on the changes least likely to get a second reader.
- **Ordinary work merges without a human.** That is the point, and it is the
  same bargain `docs/adr/0024` already struck for the agent: a correct
  increment must not rot unmerged over a quality nit. Be precise about what
  the bar actually is, because it is weaker than "review passed": CI green,
  plus a review that returned `PASS` **or** `FINDINGS` that the loop did not
  resolve. `FINDINGS` merges as soon as there is nothing left to re-review —
  which includes round zero, when the fixer runs and changes nothing because
  it judged the findings wrong or could not act on them, and also the case
  where `AGENT_FIX_TOKEN` is absent so no fix is attempted at all. Only
  `DEFECT` blocks unconditionally. An earlier draft of this ADR said
  `FINDINGS` merges only after "up to three fix-and-re-review rounds"; that
  overstated the guarantee and is corrected here.
- **"Every pull request" is now literal, including forks.** A fork PR gets no
  secrets (this is `pull_request`, not `pull_request_target`), so
  `CLAUDE_CODE_OAUTH_TOKEN` is empty and the checkout of `github.head_ref`
  refers to a branch that does not exist in the base repo — the job fails
  rather than skipping, and the PR cannot merge. For a private single-author
  repo with no forks that is academic, and failing closed is the right
  direction, but it is a real behaviour change from the `agent/*` scope and is
  recorded so it is not rediscovered as a bug.
- **Cost rises with PR volume.** One review per pull request, plus fix rounds
  on any PR the reviewer blocks. Previously only agent PRs could incur rounds.
- **A reviewer outage now blocks, and that is deliberate.** `docs/adr/0023`
  originally treated a missing verdict as a pass, so that a reviewer which
  could not run never became a gate that could not open. Under this ADR that
  rule is too permissive: the same leniency that keeps a workflow-editing PR
  moving would, on an expired token, wave through every PR in the repo
  unreviewed. So a missing verdict is now classified by cause and only the
  workflow-edit case passes (see the table below). The original concern stands
  — a stalled pipeline is a real cost — but the answer is to fix the reviewer,
  not to let a green check stand for a review that never happened.
- **This ADR edits the constitution and therefore cannot be made by the daily
  agent** (`docs/adr/0022`). It was made by a human decision, recorded here so
  that a future session finding a reviewed human PR does not read it as a
  misconfiguration and narrow the scope back.

## When the reviewer silently does not run

Observed on the very PR that introduced this ADR (#79, 2026-09-03), which is
why it is recorded here rather than learned twice.

`claude-code-action` refuses to run when the calling workflow file differs from
the copy on the default branch:

> Skipping action due to workflow validation: Workflow validation failed. The
> workflow file must exist and have identical content to the version on the
> repository's default branch.

That is a sound security property, not a bug: without it a pull request could
edit the reviewer's own prompt — "approve everything" — in the same commit the
reviewer is asked to judge. The consequence is that **any PR touching
`.github/workflows/**` gets no review**, produces no verdict, and is converted
by the enforce step's no-verdict rule into a green `agent-review` check.

**A first version of this ADR claimed that opened no hole**, on the reasoning
that the set of PRs the reviewer cannot read is a subset of the set
`automerge.yml` refuses, since `.github/workflows/**` is on its constitution
list — so unreviewable and needs-a-human coincided exactly.

That is wrong, and it was disproved within the hour. The validation compares
the workflow file to the default branch's copy; it does not ask whether *this
PR* changed it. So it also fails when the branch is merely **stale** — main
changed `ci.yml` after the branch was cut. Such a PR touches no workflow, is
fully mergeable, and gets no review.

That is not a corner case, it is the common one: every branch older than the
last `ci.yml` change is in it. And it happened. PR #76 changed two Python
files, was skipped because main had moved on via #77, produced `verdict: NONE`,
was passed by the no-verdict rule and **auto-merged with no review at all** on
2026-09-03. It was a probe branch carrying deliberate duplication, so the
review it never got was the entire point of it.

So the three causes must be told apart, and the code now does:

| Cause | Reviewed? | Mergeable? | Verdict step |
|---|---|---|---|
| PR edits `.github/workflows/**` | no | no — constitution list | **pass** |
| Branch stale vs main's `ci.yml` | no | **yes** | **block**, "rebase and push" |
| Token expired / API outage / regression | no | **yes** | **block** |

Only the first is safe to wave through, and only because a different gate
catches it.

Three consequences, all easy to get wrong later:

- **Do not remove the constitution check in `automerge.yml`** on the grounds
  that `constitution-guard` in CI covers it. That job only inspects `agent/*`
  branches, so for every other author the automerge check is the only thing
  standing between an unreviewed workflow edit and an automatic merge.
- **Do not make the workflow-edit skip fatal.** That would make every workflow
  change unmergeable by CI while the thing it actually needs — a human reading
  it — is already required. Its green check is honest precisely because the
  merge is blocked elsewhere.
- **Do not make the stale-branch skip lenient**, however noisy it gets. Its
  cure is one rebase, and its alternative is the silent unreviewed merge above.
  A long-lived branch will hit it every time `ci.yml` changes; that is the
  mechanism working, not a false positive.

The general lesson, which is the one this repo keeps paying for: a gate that
cannot run is not the same as a gate that ran and found nothing, and any
"treat absence as success" rule must first ask *why* the thing was absent.

## Hardening required before this could ship

An adversarial review of this ADR's own implementation (2026-09-03) found seven
defects, two of them merge-safety holes. Recorded because the *shape* of them
repeats: each was a gate that looked present and was not.

- **`gh pr view --json files` truncates at 100 files, silently.** It issues a
  fixed GraphQL `files(first: 100)` with no cursor loop — confirmed against a
  live 252-file PR that returned exactly 100 paths and `hasNextPage: true`, with
  no warning. On any PR over 100 files a constitution edit sorting past position
  100 was invisible and would auto-merge. Now `gh api --paginate`, plus an
  assertion that the count read equals `changedFiles` so a short read fails
  closed instead of open.
- **A no-verdict was treated as benign in every case.** See the section above:
  that reasoning covered only the workflow-validation skip. The verdict step now
  records *why* no verdict appeared — the reviewer writes `/tmp/claude-review-ran`
  as its first act — and an outage blocks while a workflow skip passes.
- **No head-SHA pin.** `gh pr merge` merged whatever the head was at merge time,
  not the commit CI validated. Push A → CI-A; push B → CI-B; CI-A goes green and
  merges A+B, code never tested or reviewed. CI declares no `concurrency` group,
  so the superseded run is not cancelled and the race is reachable. Now compares
  the head to `workflow_run.head_sha` and passes `--match-head-commit` when the
  runner's `gh` supports it.
- **The PAT was persisted into `.git/config`.** `actions/checkout` defaults to
  `persist-credentials: true`, writing the token as an `http.extraheader` — and
  the review step runs `Bash` over a diff the PR author wrote. Prompt-injected
  text could have read it back. Now `persist-credentials: false`, with the token
  supplied only to the one `git push`.
- **A draft marked ready could never merge.** `ready_for_review` is not in the
  default `pull_request` activity set, so no CI run fired, so no `workflow_run`,
  so automerge was never re-evaluated. The PR sat green and stuck. Added to the
  trigger's `types`.
- **The draft check failed open while the file check failed closed.** Under
  `bash -e`, a failed command substitution inside `if [ ... ]` does not abort,
  but a bare assignment does. A rate-limited `gh` therefore yielded an empty
  string that is not `"true"` — and a draft merged. Both now assign first and
  require an explicit safe value.
- **Guard list gaps.** The regex was case-sensitive (`git mv README.md
  Readme.md` in the same PR that rewrites it passed) and omitted
  `pyproject.toml`, which holds the mypy strictness, the ruff rule set, the
  import-linter contracts and the coverage floor — flipping `strict = false`
  turns three CI gates green by deleting them. Now case-insensitive, `^\.github/`
  rather than `^\.github/workflows/`, and `pyproject.toml` is on the list.

## Notes

The general lesson is the one this repo keeps relearning, and it is worth
stating plainly because it has now cost four separate incidents: *specifying
the criterion is not the same as specifying the path the signal takes.* A
fallback that shared a failure domain with the thing it backed up; a `Makefile`
no gate could read; a review nobody was obliged to read; and now a reviewer
whose trigger excluded a whole class of author. In each case the intent was
right and the wiring silently excluded the case that mattered.
