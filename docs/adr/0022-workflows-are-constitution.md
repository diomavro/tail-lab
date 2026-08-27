# 22. The workflow files are part of the constitution

Date: 2026-08-27

## Status

**Accepted.** Enforced by `constitution-guard` in `.github/workflows/ci.yml`
and re-checked in `.github/workflows/automerge.yml`.

## Context

`docs/adr/0016` gave the daily agent autonomy with a specific leash: ordinary
`agent/*` PRs auto-merge on green CI, but a `constitution-guard` job fails any
agent PR touching `docs/adr/**`, `ARCHITECTURE.md`, `docs/STANDARDS.md`,
`README.md`, `docs/AGENT_MISSION.md` or `docs/END_STATE.md`, so those land only
through a human merge.

That list was drawn up as "the documents that state the rules". It missed where
the rules are actually *executed*.

`.github/workflows/daily-agent.yml` holds the agent's operating prompt — which
documents it must read, that it must check the chain collection before picking
work, that it may not merge its own PR. `automerge.yml` holds the rule that
merges it. `ci.yml` holds `constitution-guard` itself. None of the three was
guarded. An `agent/*` PR could therefore have edited its own instructions,
relaxed the auto-merge condition, or deleted the guard, and that PR would have
auto-merged on green CI with no human in the loop — and, being a workflow
change, it would have looked like ordinary plumbing in the diff.

Nothing exploited this. It was found while checking whether a set of rules
added on 2026-08-26 would actually survive, which is the right question to ask
of any rule written for an autonomous process.

`docs/adr/0019` already reasoned to the same shape one step away: *"A credential
in CI is reachable by the thing that writes the CI."* The generalisation is that
**anything in CI is reachable by the thing that writes the CI** — instructions
included. Prose in `README.md` was protected while the executable copy of the
same policy was not.

## Decision

**`.github/workflows/**` joins the guarded set.** An `agent/*` PR that touches
any workflow fails `constitution-guard` and does not auto-merge.

This blocks *auto-merge*, not the change. The agent may still propose a workflow
edit, argue for it in the PR body, and have Dio merge it — the same path every
other constitution change takes.

The guarded list now lives in three places (`ci.yml`, `automerge.yml`, the
agent's prompt) and they must stay in sync. That duplication is deliberate:
`automerge.yml` re-checks what `ci.yml` already enforced because a
belt-and-suspenders copy is cheap and a missed guard is not.

## Consequences

- **Rules written for the agent now outlive it.** A lesson recorded in a prompt
  is worth as much as the prompt's immutability. Before this, every rule added
  to `daily-agent.yml` was one unreviewed agent PR from being removed.
- **Workflow changes get slower, and should.** Adding a scheduled job or
  changing a gate now waits for a human. Workflows run with secrets, can merge
  code and can deploy; they are the highest-privilege files in the repo and had
  the lowest bar.
- **The guarded set now has a stated principle**, so the next addition does not
  need re-litigating: *a file is constitutional if changing it changes what the
  agent is allowed to do.* That covers the rule documents and the machinery that
  enforces them, and it is why `AGENT_TODO.md` is deliberately still unguarded —
  the agent owns its backlog, and reordering work is not widening permission.
- **What this does not fix.** The agent could still, in principle, propose a
  code change that indirectly weakens a gate — a loosened test, a lowered
  coverage floor. Those live in `pyproject.toml` and `tests/`, both unguarded by
  design, and the defence there is that CI failures are visible and the diff is
  reviewable, not that the files are locked. If that turns out to be optimistic
  it is a separate ADR with separate evidence.
