# 31. Collect the call wing in the daily chain sweep, as its own dataset

Date: 2026-10-27

## Status

**Accepted.** Dio commissioned this change and had its plan adversarially
reviewed on 2026-10-27. It touches guarded paths (`README.md`, this ADR,
`docs/adr/0020`, `.github/workflows/`), so it ships in a **draft** PR and the
human merge of that PR is the ratification. It amends `docs/adr/0020`
decision 2 ("Puts only") and reverses the rationale in
`docs/DATA_CONTRACTS.md` §6 "Deltas from the original plan".

## Context

`docs/adr/0020` collects the put wing only, and `docs/DATA_CONTRACTS.md` §6
gave the reason: the call wing "doubles storage to answer nothing currently
asked". That premise no longer holds:

- **The repo's own blocker.** `docs/PRIOR_ART.md` §16 and `AGENT_TODO.md` PG1
  name the put-wing-only sweep as what truncates a model-free implied variance
  strip, the implied leg of the VRP column. A naive strip over one wing is
  biased low by construction.
- **Put-call parity gives an approximate implied forward** per expiry, and so
  an implied `q` to set beside `tiingo_eod`'s measured one. **It is
  approximate.** Every name in the set is American-style, so near-the-money
  parity is a pair of bounds, not an equality. The early-exercise premium is
  largest around ex-dividend dates and for ITM puts. A usable signal needs
  near-ATM strikes at short tenor, or a de-Americanised (binomial) fit. This
  ADR does not promise a fix for the dividend-yield weakness (FXI, HYG/TLT in
  `option_pricer.py` assumption 4).
- **Upside skew and risk reversals** as a regime signal, and a staleness
  cross-check on the put quotes.

What forces the decision now is `docs/adr/0020`'s own argument. A live chain
cannot be bought retroactively, so every session without calls is lost for
good. The raw optionsDX archive (2010-2023) carries calls, but bronze
`optionsdx` is sliced to the put wing (`ingestion/optionsdx.py`). A call
backfill would mean re-slicing the raw archive, which is out of scope here.

This changes what is **collected**, not what is **traded**. The platform still
buys puts.

## Decision

1. **A separate bronze dataset, `option_chain_call_snapshot`**, with
   quarantine `option_chain_call_snapshot__quarantine`. All four chain names
   (put, call, and both quarantines) live once in `contracts/option_chain.py`.
   The put quarantine name was spelt twice before. It is **not** a `right`
   column on `option_chain_snapshot`: every reader of that dataset (marks,
   strike preview, Surface, greeks check, putlab routes, the status route,
   the verify scripts) assumes each row is a put, and its schema pins
   `delta <= 0` with no `right` in the key.

   **Never union the call dataset** with `option_chain_snapshot`,
   `option_quotes` or `optionsdx`. They share the key `(underlying,
   quote_date, expiration, strike)` and none has a `right` column, so a concat
   (which `contracts/option_chain` advertises for the put-side datasets) would
   silently file calls as puts.

2. **Contract.** `OptionCallChainSnapshotSchema` subclasses the put schema and
   overrides only `delta` (`[0, 1]`). `split_valid_and_quarantined` takes the
   schema, with no default, so no caller can validate calls against the put
   contract by omission. `WINGS` maps a right to `(dataset, quarantine,
   schema, band)` in one place, and every writer reads it through
   `Wing.split`.

3. **Band: calls 0.80-2.50 of spot**, the same 180-day tenor cap, and the same
   24 symbols. 2.50 is log-symmetric with the put floor (1 / 0.40). At
   sigma ~0.9 (MSTR, COIN, SMCI, TSLA) a 25-delta call at 90 days sits near
   K = 1.50 S and a 10-delta call at 30 days near 1.44 S, both outside a 1.30
   cap. The band was kept at 0.80 after the body-size gate below.

4. **One fetch, two parses, three failure boundaries.** Each Cboe payload is
   fetched once, by `fetch_chains`; `ingest_option_chain` takes only those
   payloads and never fetches. A second fetch loop would repeat 2026-09-19
   (48 requests, 429 on 9 of 24) and draw the wings from different CDN
   snapshots. A failed fetch costs that symbol both wings. A failed put parse
   still parses the calls. A failed call parse, or a failed call record
   conversion, keeps the puts and logs
   `event=ingestion.option_chain.symbol_failed` with
   `dataset=option_chain_call_snapshot`. So a call-only bug cannot push the
   put session under `MIN_SYMBOL_FRACTION`. In `--local`, a symbol whose fetch
   failed logs one traceback in `fetch_chains` and then one `symbol_unfetched`
   line per wing, with no second traceback. Each wing gets its own session
   plan, floor, write and audit record (`ingestion.option_chain.run` with
   `dataset=`). `unparsed_contracts` counts OSI-unparseable symbols before the
   right filter (so both records carry them), plus that wing's rows with an
   unparseable `iv`.

5. **Route.** `POST /api/ingest/option-chain-calls` uses the same token,
   session plan, witness and floor. Both routes run one shared body
   (`_ingest_chain`), so the session rules cannot drift between the wings.
   Both log `api.ingest.option_chain` and tell the wings apart by `dataset=`,
   as the ingestion events do. Neither contract can keep a call out of the
   put dataset on its own: there is no `right` column, and a zero-filled row
   has a null (or exactly zero) delta, which both contracts accept. So both
   writers, each route and the local ingest, quarantine every row of a SYMBOL
   (null-delta rows included) when more than a fifth of that symbol's rows
   with a non-null, non-zero delta carry the other wing's sign (`Wing.split`,
   `MAX_WRONG_SIGN_SHARE`), logged with `dataset=`; the symbol floor then
   decides the session. Measured on the 2026-10-26 live bodies, a correct
   body scores 0 (0 of 16,665 puts, 0 of 21,942 calls), a misrouted body
   scores 1, and a body carrying both wings scores every symbol at least
   0.29 (SLV on the call route; 0.43 pooled), which a first threshold of one
   half let through. A misrouted or two-wing body therefore lands nothing
   (every symbol trips; 422 on a route). Per symbol, not per body: a
   whole-body refusal would let one misrouted symbol cost a session on the
   `--post` path that `--local` keeps. A fifth rather than less, because a
   misrouted symbol counts against the floor: at 1%, two sign-flipped rows
   among FXI's 176 puts cost all of FXI, and the same on three chains refused
   a put session that the contract alone commits with just the bad rows
   quarantined. Below a fifth that is still what happens, so the put dataset
   keeps the rule it had. Without the guard, the fixture's call body POSTed
   to the put route committed its two null-delta ITM calls as puts. A
   misrouted symbol is named as such (`symbols_misrouted` in the route's
   answer and in `IngestResult`, and apart from "Missing" in the floor's
   refusal), and the script reports it by that cause rather than as "no
   quotes", which would point the operator at a re-run that sends the same
   chain.

6. **Script exit contract** (`scripts/chain_snapshot.py`, both modes):

   | put outcome | calls | exit |
   |---|---|---|
   | unsettled (425 / `SessionInProgress`) | not attempted | 0 |
   | committed or no-op | attempted | 0 if the calls land, else 1 |
   | any hard failure | still attempted | 1 |

   The calls have no "skipped". They come from the same payloads, so when the
   puts are unsettled the calls are too. Attempting them anyway would let the
   call dataset's own witness turn a benign skip into a red 409. The exit 0
   on that row therefore vouches for the PUT dataset's previous session
   only: a call session lost earlier went red on the run that lost it. A
   call 425 is a failure, logged as an `::error::`, never as
   `calls: SKIPPED`; after puts that landed it also says the two wings
   disagreed about the session. One witness read serves both bodies. The
   `--token` check now runs first. Every transport failure maps to a failed
   wing and never to a traceback that skips the other wing. That includes the
   dropped connections and non-JSON 200s that urllib leaves unwrapped, and a
   refusal whose body cannot be read. An unforeseen exception (a bug) still
   ends the run. A call-only failure prints `calls only; puts landed`. A
   no-op answer after a retried failure that followed the send (a drop, a raw
   timeout awaiting the answer, or a 5xx) prints "landed ... may have
   committed it", never "wrote nothing".

7. **Accepted gaps.**
   - **Deploy lag.** The workflow runs main's script against the deployed app.
     Until a merge is deployed, the call POST gets a 405 from the SPA mount.
     The run goes red with the puts landed, and the annotation says
     "call route not deployed yet (check deploy.yml)". A 404 never gets that
     hint, because the auth gate answers 404 on both routes when the app's
     token is unset. The first call capture therefore needs `deploy.yml` to
     have shipped the merge.
   - **No call freshness route.** `docs/adr/0020` §8 ("Freshness is public")
     and `docs/AGENT_MISSION.md`'s health check cover puts only. Until
     `AGENT_TODO.md`'s follow-up lands, a calls-only failure is visible only
     as a red `daily-chain-snapshot` run. `chain_sweep_verify.sh` still checks
     puts only. `chain_sweep_alert.sh` fires on any non-zero `--local` exit,
     so a calls-only failure raises its "option chain was NOT captured" toast,
     which is false about the puts. The script's distinct `calls only; puts
     landed` line is what the toast must learn to quote (also queued). The
     timer is inactive today.

## Body-size gate (measured before writing this ADR)

The production app is one 1 GB machine with no swap (`fly.toml`), and the
2026-09-04 transient 500 has two plausible causes, a cold start or memory
pressure parsing a ~5 MB body (`scripts/chain_snapshot.py`). The call body
is larger than the put body, so it was measured first.

**Rows and bytes.** Live session 2026-10-26, fetched 2026-10-27 08:20 UTC,
24 symbols, both wings sliced from the same 24 payloads:

| wing / band | rows | POST body |
|---|---:|---:|
| puts 0.40-1.05 | 17,330 | 3,898,125 B |
| calls 0.80-2.50 (chosen) | 23,376 | 5,229,913 B |
| calls 0.90-2.50 | 20,155 | 4,509,956 B |
| calls 0.95-2.50 | 17,944 | 4,015,611 B |

That is 1.35x as many call rows as put rows. GLD is 2,356 calls against 1,054
puts (2.2x) and SPY is 3,374 against 3,109. Lowering the 2.50 cap does not
help: the planning review measured SPY at 3,374 in-band calls for both
[0.80, 2.50] and [0.80, 2.0].

**Memory.** `uvicorn` ran this branch under `systemd-run --user -p
MemoryMax=924M -p MemorySwapMax=0`, with `TAIL_LAB_WARM_RANKING=true`. The cap
is 1024 MiB less ~100 MiB assumed for the VM and OS. That overhead is not
measured on Fly. The put body and then the call body were POSTed to the same
process:

| point | cgroup `memory.peak` |
|---|---:|
| started, idle | 281.3 MiB |
| after the put POST (200, 17,330 rows) | 370.8 MiB |
| after the call POST (200, 23,376 rows) | 408.5 MiB |

Both answered 200 and `oom_kill` stayed 0. **Pass.**

**Worst case not measured.** 2026-10-26 was a light put day: 17,330 puts,
against 20,882 puts / 4.68 MB on 2026-08-26 (`contracts/option_chain.py`). At
the measured 1.35x call ratio and ~225 B per call row, a day like that sends
about 28.2k calls, roughly **6.3 MB**. That is 20% above the call body that
passed this gate. Budget any widening of the symbol set against 6.3 MB, not
5.2 MB.

**Caveat.** The lake was an empty temporary local one. There is no populated
local lake, and the production lake's credentials were deliberately not used
for this experiment. So the warm screen failed at once (`no bronze data for
dataset 'vix'`), and its working set is **not** in these numbers. The
remaining check is the first production call POST. The order makes that safe:
the puts are POSTed and committed first, so a call-side OOM restarts the app
after the put session is already in bronze. The run then goes red with
`calls only; puts landed`. If that happens, the documented levers apply in
order: raise the call floor to 0.90, then 0.95 (parity needs only the
0.95-1.05 overlap), then shorten the call tenor. More Fly memory costs money
and goes to `HUMAN_TODO.md`. Splitting the POST is rejected, because it
breaks the one-partition-per-session rule.

**Zero-filled calls.** 1,096 of the 23,376 in-band calls carry `iv = 0` (HYG
97 of 370, SPY 172, GLD 54). Most are ITM, and some have real-looking deltas.
The house rule is unchanged: Cboe computes the greek block together, so a zero
`iv` nulls `delta` and `theo` too (`ingestion/option_chain._greek`). A null
call delta is the rule working, not a parse fault.

## Consequences

- The call wing accrues from the first session after this merge is deployed.
  Every earlier session stays put-only for good. PG1's truncation holds for
  all of them.
- Each new symbol now costs both wings, about 1.35x the put rows again.
  Widening the set toward 70 names must be checked against the measured
  `memory.peak` margin above, not only the put body.
- Consumers are not part of this change. They are queued in `AGENT_TODO.md`:
  a parity-implied forward / `q` against `tiingo_eod` (with the American
  caveat); a risk-reversal regime signal; the verify and alert scripts and the
  daily agent's health check covering the call dataset; and model-free IV
  with both wings from this ADR's first session (PG1).
