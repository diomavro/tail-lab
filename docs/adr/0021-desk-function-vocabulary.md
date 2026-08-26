# 21. Name the platform after desk functions, and add the risk function it is missing

Date: 2026-08-26

## Status

**Accepted.** The vocabulary is in force for new surfaces from today. The
renames and the missing risk surface are queued in `AGENT_TODO.md`; nothing
here authorizes a sweeping rename PR.

## Context

Dio brought in a widely-circulated article on running a "one-person AI hedge
fund". Most of it is a product funnel — it sells a specific agent subscription
and a specific payments plugin, invites readers to DM their setup, and closes
by conceding that "calling it a hedge fund is aspirational shorthand" for a
signal-subscription business. Three of its six layer descriptions are the same
paragraph copy-pasted. Its costing (a "1,266x" saving) compares a thirty-person
institutional desk to a solo content business. Its citations are real papers
deployed decoratively, and at least one number is misquoted.

None of that is worth importing, and most of what it *does* propose —
execution bots wired to a broker, an LLC-forming operations bot, a self-funding
ad loop — is forbidden here by `docs/adr/0007` and would contradict the
constitution outright.

One idea in it survives contact, and it is a naming idea. Institutional desks
are not organised by pipeline stage. Nobody at a fund works in the "ingestion
layer". They work on a **desk function**: research, signal, risk, execution.
That vocabulary is stable across every serious shop, and a tool that speaks it
reads as an instrument rather than as somebody's ETL project.

tail-lab's layer names — `ingestion → lake → transforms → research → api` —
are *plumbing* names. They are correct, machine-checked (`pyproject.toml`), and
should not change. But they describe how data moves, not what the platform
*does*, and the user-facing surfaces have been named ad hoc alongside them:
"leaderboard" became "fragility screen" mid-flight, and "Recommendations",
"strategy tape" and "metric bake-off" each arrived on their own terms.

Applying the desk taxonomy honestly also surfaces a real hole. Of the six
functions a fund runs, this platform implements two, walls off one, and is
constitutionally uninterested in two more — and **has no risk function at all**,
despite its own North Star question ending with "and how much would the position
bleed if nothing happens?"

## Decision

**1. Two vocabularies, deliberately separate.** Code layers keep their plumbing
names and their import-linter contract. *Surfaces* — anything with a name a
human reads, in the UI or in `research/` — are named after desk functions. A
module may sit in `research/` and be called the Screen; that is not a
contradiction, it is the point.

**2. The desk map, stated honestly, walls included.**

| Desk function | tail-lab | Status |
|---|---|---|
| **Research** | metrics, regimes, data quality, the model-vs-market residual | implemented |
| **Signal** | the **Screen** (fragility ranking), the **Bake-off** (which metric sorts payoff), **Prior Art** (`docs/adr/0015` verdicts) | implemented |
| **Risk** | the **Carry Budget** — what the book bleeds if nothing happens, and the caps that follow | **missing — see 4** |
| **Execution** | order intent only (`/api/putlab/roll-schedule`); the crossing is `docs/adr/0019`'s separate process | walled |
| **Business operations** | — | out of scope (`docs/adr/0007`) |
| **Growth** | — | out of scope (`docs/adr/0007`) |

The two blanks are not gaps to be filled later. They are the wall, and writing
them into the map is what stops a future increment from treating them as
backlog.

**3. The vocabulary is closed.** New surfaces take a name from the desk list —
Screen, Tape, Marks, Book, Blotter, Carry, Surface, Regime, Prior Art,
Bake-off — or argue in an ADR for an addition. "Leaderboard", "dashboard",
"panel" and "tool" are not desk words and do not describe what a thing is.

**4. Add the Risk function, and make it about bleed.** For a long-put book the
risk that matters is not drawdown; it is **carry**. A put book bleeds by design,
and the question that decides whether the position is holdable is how much per
year, against what budget. The Carry Budget surface owes: annualised bleed per
candidate at its recommended strike and cadence, the same number for the whole
recommended set, and what fraction of a stated annual budget that consumes.

This is also where the article is most actively wrong and worth recording as
such: it prescribes a hard 5% drawdown auto-liquidation. Applied to a long-vol
book that is a rule to sell the hedge precisely when it has begun to work.

**5. Limits are declarative data, not logic — and the agent may not tune them.**
The one genuinely good principle in the source material: the risk function is
the only one where nothing negotiates. Carry budgets and position caps live in
a versioned config read by `research/`, not in code paths an increment can
adjust while chasing a nicer backtest. Changing a limit is a human edit with a
reason attached, on the same footing as a constitution change.

**6. Statistical discipline is part of the Signal function, not a footnote.**
The Bake-off compares many sensitivity metrics across strikes, tenors and
regimes. That is a multiple-testing machine, and the conventional t > 2.0
threshold the article names is exactly the hurdle Harvey, Liu & Zhu (2016)
showed is wrong once a literature has tested hundreds of factors — they argue
for roughly t > 3.0. Any Bake-off result that ranks metrics must carry a
multiple-testing correction and report how many comparisons produced it. A
winner selected at t > 2 across a grid this size is a coin that came up heads.

## Consequences

- **The tool reads as an instrument.** A page whose surfaces are the Screen, the
  Tape, the Carry Budget and Prior Art tells a reader what kind of object it is
  before they read a number. That is most of what "professional" means here.
- **The missing function became visible by naming the others.** The Carry Budget
  is not a new idea — the North Star has asked for it since day one — but it had
  no name, so it had no home, so it never got built.
- **A closed vocabulary is a brake on the daily agent.** New surfaces cannot
  accumulate names invented one PR at a time.
- **Existing names mostly survive.** Screen, Tape, Bake-off, Regime and Prior
  Art are already desk words. The renames this implies are small and queued, not
  a sweeping refactor.
- **What was rejected, so nobody re-derives it.** Fund framing, execution
  agents, business-operations automation and growth loops are out — not because
  they are uninteresting but because `docs/adr/0007` forbids them and this
  platform's value is that its wall is real. The "one-person hedge fund" is a
  subscription business wearing a fund's vocabulary; tail-lab is a research
  instrument, and should wear its own.
