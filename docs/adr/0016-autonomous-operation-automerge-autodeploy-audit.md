# 16. Autonomous operation: auto-merge, auto-deploy, detailed audit trail

Date: 2026-08-19

## Status

Accepted

## Context

The constitution as originally written said "every change is a reviewed
PR gated by CI; nothing auto-merges; nothing self-deploys." In practice
`.github/workflows/automerge.yml` already squash-merged the daily agent's
`agent/*` PRs on green CI, leaving the written rule and the enforced
behavior in contradiction — and deploys were still a manual
`flyctl deploy` by Dio, so merged work could sit undeployed for days.

Dio resolved this on 2026-08-19 with an explicit operating model: **the
work should be automatic without him.** He steers asynchronously — coming
in once in a while, reviewing the whole state (the deployed cockpit, the
merged PRs, the logs), and leaving feedback so future agent runs improve
(the ADR-0014 feedback panel is exactly this channel). Per-increment
human gating adds latency without adding safety, because the CI gates
(lint, mypy --strict, import-linter layer contracts, pytest with the
adversarial point-in-time tests, coverage floors, frontend build,
constitution-guard) are the review bar the agent's leash is built on.

Review-after-the-fact only works if there is something to review:
autonomy without a detailed record of what happened is unauditable.

## Decision

1. **Ordinary agent increments auto-merge.** A PR from an `agent/*`
   branch squash-merges automatically once every CI gate is green. No
   human approval is required. Red CI leaves the PR open for a human.
2. **Green `main` auto-deploys.** A continuous-deployment workflow
   (`.github/workflows/deploy.yml`) runs `flyctl deploy` for every
   commit that lands on `main` and passes CI, using a `FLY_API_TOKEN`
   repository secret provisioned once by Dio (`HUMAN_TODO.md`). The
   agent never runs a deploy itself and never holds the token — the
   deploy workflow is platform CD triggered by the merge, not an agent
   action, so the no-credentials wall (`docs/adr/0007`) stands.
3. **The constitution still requires a human.** CI's `constitution-guard`
   fails any `agent/*` PR touching `README.md`, `ARCHITECTURE.md`,
   `docs/STANDARDS.md`, `docs/AGENT_MISSION.md`, `docs/END_STATE.md`, or
   `docs/adr/` — a constitution change lands only through a PR a human
   reviews and merges. The agent may still *propose* such changes.
4. **Everything automated leaves a detailed audit trail.** Dio's standing
   directive: extremely detailed logging of everything the platform and
   its agents do. Every automated action must leave a reviewable record —
   PR descriptions that say what and why, CI/CD run logs, ingestion-run
   records (dataset, source, row counts, quarantine counts, bronze
   snapshot id), structured application logs, and over time an in-cockpit
   activity surface. The observability bar lives in `docs/STANDARDS.md`
   §(f); increments that add automated behavior without logging it do not
   meet the standard.

"The agent proposes; the human disposes" is restated as: the agent
proposes, CI disposes of ordinary increments, CD ships them, and the
human steers the whole system asynchronously — reviewing state and audit
trail, retiring or adding standing directives (`docs/adr/0014`), and
alone disposing of anything constitutional.

## Consequences

- `README.md` §Hard principles, `ARCHITECTURE.md` hard rule 8, and
  `docs/AGENT_MISSION.md`'s workflow/non-negotiables are reworded in the
  same change to match.
- The safety of the arrangement rests on CI's completeness. Weakening any
  CI gate (removing a check, lowering a coverage floor, loosening
  `constitution-guard`'s path list, altering `deploy.yml`'s green-CI
  precondition) is itself a constitution-level change requiring a
  human-reviewed PR.
- `FLY_API_TOKEN` must be provisioned as a GitHub Actions repo secret
  before CD works (`HUMAN_TODO.md`); until then the deploy workflow
  no-ops with a visible notice rather than failing red.
- **Every bot-path hop must be a `workflow_dispatch`** (learned in the
  first hours of operation, in two steps): GitHub's `GITHUB_TOKEN`
  recursion prevention suppresses (a) workflow triggers from
  `GITHUB_TOKEN` pushes — so an auto-merged squash commit gets no
  push-CI — and (b) the `workflow_run` events emitted by bot-initiated
  runs — so a `workflow_run`-triggered Deploy never fires on that path
  either (found empirically on PR #11). `workflow_dispatch` creations
  are exempt in both cases. Final mechanism: automerge dispatches CI on
  `main` (validating the actual squashed tree, not just the PR head),
  and CI's `trigger-deploy` job — gated on every CI job green and
  `ref == main` — dispatches Deploy, which is `workflow_dispatch`-only
  and deploys the tip of `main`. Direct human pushes enter the same
  chain at the ordinary `push`-CI event.
- A deploy now follows every green merge, so a bad-but-CI-green change
  reaches prod unattended. Mitigations: the Fly health check gates
  release, `flyctl releases` gives one-command rollback, and the audit
  trail (point 4) makes "what shipped while I was away" a five-minute
  review, not an investigation.
- A human-authored branch *named* `agent/*` will also auto-merge (and
  deploy) on green CI; use a differently-named branch for work that
  should wait for review.
- `docs/STANDARDS.md` gains §(f) Observability, the enforceable form of
  decision point 4.
