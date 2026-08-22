# 19. Paper execution lives in a separate process that authors no code

Date: 2026-08-23

## Status

**Proposed.** Not in force. `docs/adr/0007` stands until a human accepts this
one and says so here — that is what 0007 asks for, and an agent marking its own
supersession as Accepted would be the exact failure the wall exists to prevent.

Supersedes `docs/adr/0007` **in part** if accepted: the wall moves rather than
falls. See "What stays" below.

## Context

`docs/adr/0007` says the agent never trades and never touches money or
credentials: "No code path may call a brokerage API, hold a trading credential,
or place an order." `docs/AGENT_MISSION.md` §The wall says the same. That was
written on 2026-08-17, before the platform could say what a position *was*.

It can now. The Recommendations view names each name's best strategy — ticker,
strike as % out of the money, roll cadence — and `/api/putlab/roll-schedule`
emits it as order intent with a deterministic `schedule_id`. Dio wants a process
of his to execute those against an IBKR **paper** account.

Three things make the naive reading of that request — "let the daily agent
trade" — worse than it sounds, and all three are properties of this repo rather
than general caution:

**A credential in CI is reachable by the thing that writes the CI.**
`automerge.yml` squash-merges any `agent/*` PR once CI is green (`docs/adr/0016`),
and green `main` auto-deploys. A brokerage secret in GitHub Actions is therefore
reachable by an agent that can propose a workflow change and have it merged with
no human in the loop. 0007's guarantee — "blast radius bounded to a bad PR,
caught in review" — would stop holding, and it would stop holding *quietly*.

**Paper and live differ by one digit.** IB Gateway listens on 4002 for paper and
4001 for live; TWS on 7497 and 7496. Same binary, same credential family. A
misconfigured port does not fail closed — it trades the live account.

**CI cannot host a broker session anyway.** IB Gateway is a long-running process
requiring periodic re-authentication. An ephemeral runner cannot hold it, so the
daily-agent workflow is structurally the wrong home regardless of policy.

## Decision

Execution is permitted, in a **separate process**, under all of the following.
These are conjunctive: drop one and this ADR does not authorize the setup.

1. **Separation of powers.** The process that holds the brokerage credential
   **authors no code and opens no pull requests**. The agent that writes code
   **holds no credential and places no orders**. Neither may acquire the other's
   capability; that is the invariant this ADR protects, and it is what makes it
   a narrower claim than "the agent may trade".
2. **Paper only.** Live trading remains outside every automated path. Raising
   this needs its own ADR, argued on its own evidence.
3. **The credential never enters this repository's CI.** Not in GitHub Actions
   secrets, not in `.env`, not in the deployed Fly app. It lives with the
   executor, on hardware the human controls.
4. **tail-lab stays read-only.** No code in this repo calls a brokerage API. The
   interface is `/api/putlab/roll-schedule` — the executor pulls; nothing here
   pushes. `research/backtest/roll_schedule.py` is the whole of the crossing.
5. **The executor is a separate repository.** Not a package here, not a
   `scripts/` entry. Physical separation is what makes rule 1 checkable rather
   than a promise.
6. **Every order is logged in detail**, per `docs/STANDARDS.md` §f: schedule_id,
   leg, target vs snapped strike and expiry, model premium vs fill premium,
   contract count, timestamp.

## Consequences

- **The gap becomes measurable forward.** Rule 6's model-vs-fill premium is the
  same measurement `make skew` made on 210 historical dates, running
  continuously and free. That is the evidence
  `docs/adr/0018`'s `MODEL_PRICED_MAX_MONEYNESS_PCT` would move on. Execution is
  not just a use of the platform's output; it is an input to its open research
  question.
- **The results will not resemble the backtest, and that is the finding.** At
  8–10% OOM the measured market/model premium ratio is roughly 3–7×, so real
  fills cost multiples of what the backtest assumed per roll and returns will
  land far below the Recommendations page's figures. Treating that divergence as
  a bug would be the misreading; it is the skew the model omits, priced by
  someone else.
- **What stays.** Everything else in 0007 is unchanged and still binding: the
  agent's scope remains data, backtests, metrics, dashboard, docs, tests, CI; it
  still never touches money, a live account, or any credential; and
  `docs/AGENT_MISSION.md` §The wall needs one edit — the wall now separates
  *code-authoring from credential-holding*, rather than separating the platform
  from all execution.
- **Blast radius, restated.** An agent mistake still cannot place an order. An
  executor mistake can place a **paper** order, and cannot change any code. The
  worst case is a wrong paper position, visible in the log rule 6 requires.
- **If the separation ever collapses** — the executor gains commit rights, or
  the agent gains the credential — this ADR is void and 0007's blanket wall
  applies again. That is not a style note; it is the condition the permission
  rests on.

## Not decided here

Broker choice, the executor's language, whether it runs on a schedule or on
demand, and how fills flow back into the lake. None of those change the wall,
and all of them are cheaper to decide after the first schedule has been placed
by hand.
