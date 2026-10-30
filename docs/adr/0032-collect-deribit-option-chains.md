# 32. Forward-collect Deribit's crypto option books, one dataset per book

Date: 2026-10-30

## Status

**Accepted** on the human merge of its PR. It touches guarded paths
(`README.md`, this ADR, `docs/adr/0020`, `docs/AGENT_MISSION.md`, `CLAUDE.md`,
`.github/workflows/`), so it ships as a **draft** PR. It amends
`docs/adr/0020` (see "Departures" below) and departs from one choice in
`docs/adr/0031`.

## Context

Deribit lists most of the world's crypto options. Its public API is keyless,
and two calls per book return everything a quote history needs:

- `public/get_book_summary_by_currency?currency=<C>&kind=option` -- bid, ask,
  mark, mark IV, the forward each expiry is marked against, open interest and
  volume;
- `public/get_instruments?currency=<C>&kind=option` -- strike, expiry (always
  08:00 UTC), put or call, and the contract terms.

Measured on 2026-10-30, with no key and no account:

| Book | Rows | Payload |
|---|---:|---:|
| BTC | 962 | 431 KB |
| ETH | 812 | 360 KB |
| USDC | 3,168 | 1.49 MB |

The USDC book covers seven underlyings: SOL, HYPE, BTC, ETH, XRP, AVAX and
TRX. Across six live captures (about 4,942 rows each) the instrument names
matched the book's exactly, and the contract below would have quarantined 0
rows.

**Why now, with nothing to read it.** A past Deribit order-book snapshot
cannot be had for free -- Tardis.dev sells them. That is the property
`docs/adr/0020` built the Cboe sweep on, and `docs/AGENT_MISSION.md`'s test
for irrecoverable work applies exactly: *"if an item's cost rises the longer
it waits ... take it first."* Each day not collected is a day that can later
only be bought. Two related sources ARE recoverable later and are queued in
`AGENT_TODO.md` instead: trade history (`history.deribit.com`, from
2016-11-29) and DVOL (daily, from at least 2021-06-01).

**Rejected: ccxt.** It is MIT-licensed and active, but it is built for
trading (unified auth and order placement), its Deribit adapter never calls
`history.deribit.com`, it pins exact transitive versions and ships no type
hints, and adding it means editing the guarded `pyproject.toml`. Six
`requests` GETs through `sources.retry_transient` do this job. ccxt may earn a
place later, inside the walled execution process (`docs/adr/0019`).

## Decision

### 1. Three books, three datasets

`deribit_options_btc`, `deribit_options_eth` and `deribit_options_usdc`,
named once in `contracts/deribit_options.DERIBIT_BOOKS`. All three share one
strict schema (`docs/DATA_CONTRACTS.md` #16). Each book is fetched, posted
and written on its own, so one book failing never costs another its session
-- `docs/adr/0031`'s lesson. The whole listed book is kept: no moneyness or
tenor band.

### 2. The same credential seam as the Cboe sweep

`.github/workflows/daily-deribit-snapshot.yml` fetches keylessly and POSTs
each book to `POST /api/ingest/deribit-options/{btc|eth|usdc}`, behind the
same bearer token. The app holds the lake keys. `make ingest-deribit`
(`scripts/deribit_snapshot.py --local`) writes directly, for a human.

### 3. One rulebook for both writers

`contracts/deribit_options.plan_deribit_write` decides every write, in this
order. No refusal writes anything.

Rules 1 onward are numbered as the code's comments and the tests number them
(rule 1b sits between 1 and 2). "Rule 0" is this ADR's name for the wire and
payload checks that run before the plan:

- **Rule 0 -- wire and frame.** On the route a missing or out-of-window
  (2016..2100) `creation_timestamp_ms` is a 422 from the request model. On
  the fetch, a book payload that is not a list, or a book row that is not an
  object, has no string `instrument_name`, or no integer `creation_timestamp`,
  fails the whole book as `DeribitApiError`. A field fault elsewhere costs
  only its row (`invalid_value`).
- **Rule 1 -- freshness, two-sided:** an observation more than 30 minutes
  old, or more than 5 minutes in the future, is refused (409). Under
  `--local`, which has no wire, an out-of-window creation timestamp is also
  refused here, as a 409 "bad timestamp". This runs first, so a replayed copy
  of a captured body is stale, not "already captured".
- **Rule 1b -- the right book:** refused (409) when more than half of the
  posted rows carry another book's currency. Fewer are quarantined
  `wrong_book`.
- **Rule 2 -- partition:** D is the UTC date of the earliest observation. If
  D exists, the answer is a green no-op.
- **Rule 3 -- too early:** before 08:05 UTC is a 425.
- **Rule 4 -- expired rows** (expiry at or before the observation) are
  dropped and counted. A row with no instrument expiry falls back to the date
  its name spells. A row whose instrument expiry is out of window, which has
  no instrument expiry and a name that spells no usable date, or whose
  instrument expiry DISAGREES with its name's date is quarantined `unparseable_expiry` -- never dropped as expired, since
  either date would be a guess and one of them would drop a live row.
- **Rule 5 -- nothing left** is a 422.
- **Rule 6 -- coverage:** more than 1% of the live listed names with no row
  is a 409; a `listed` with no live name at all is a 409 "no live listings".
  It runs before the quarantine split, so a coverage failure is always a 409.
- **Rule 7 -- quarantine, then commit:** bad rows are quarantined, the rest
  committed, and the script exits 1 above a 1% share -- red, with nothing
  lost. A row's reason is the plan's own (`invalid_value` > `wrong_book` >
  `unparseable_expiry`), else its first failing column check, else
  `duplicate_instrument`.
- **Rule 8 -- previous day:** each answer says whether D - 1 exists, and the
  script exits 1 when D - 1 is missing on a committed or may-have-landed
  answer: that is the red signal for a lost previous session. An "already
  captured" answer to a retry whose earlier attempt may have landed is red as
  well, because that attempt's quarantine share and blob status are unknown.

### 4. The quarantine is per-capture JSON, not a Delta table

Quarantined rows keep their full wire payload in one blob per capture,
`ops/quarantine/<dataset>/<D>/<earliest observed ms>.json`, written after the
dataset and best-effort. If the blob write fails, every row is logged instead,
one line each, and the script exits 1.

### 5. Schedule

Crons at 08:15 UTC and a 10:45 UTC catch-up. The catch-up guards against a
dropped run, not a late one: same-day lateness of this repo's morning crons
is correlated about 1.0 (median 5.5 h, worst 12.4 h, measured 2026-08-26 to
2026-10-30). A catch-up more than 13.25 h late lands after midnight and gets
a 425 for the next day. That red run is **usually spurious**: check
`GET /api/ingest/deribit-options/status` (`last_snapshot_date`,
`missing_days`) before treating it as a loss.

### 6. Collection starts before the data-terms question is answered

Whether Deribit's API terms allow storing this data is in `HUMAN_TODO.md`.
Collection starts anyway, because a missed session cannot be bought back for
free, while stopping and deleting later costs nothing. Nothing reads the data
until that question is answered.

## Departures from ADR 0020 and 0031

- **0020 §2 (store a slice):** the whole listed chain is stored. The books
  are small (about 2.3 MB a day in all), and nothing yet says which slice a
  crypto reader will want.
- **0020 §4 (one narrow endpoint):** a third write endpoint. It is as narrow:
  three named datasets, one contract.
- **0020 §5 (one write per day for the whole set):** three per-book datasets
  and POSTs, so one book's failure cannot cost another's session.
- **0020 §5b (a Delta `__quarantine`):** per-capture JSON blobs. One
  immutable Delta partition per day silently dropped a second capture's
  quarantine behind the first, and an all-null typed column broke the next
  write.
- **0020 §6 ("every other scheduled job here may legitimately do nothing"):**
  this job may not either. An empty or refused book is red.
- **0020 §7 (the narrow tradable set):** these books are outside the strategy
  universe. Justified by README's "irrecoverable work outranks the value bar".
- **0031 (no `right` column):** every Deribit row carries `right`. The
  datasets hold both rights by design, and the strict, different schema
  keeps them from being unioned with the equity put datasets.

## Point-in-time

- Partition D is knowable only from its `observed_at`, which can fall
  anywhere from 08:05 to 23:59:59 UTC (late crons, retries, manual dispatch).
  Crypto has no close.
- A reader deciding before that time must read `as_of = D - 1`, or filter on
  `observed_at`.
- The date-level as-of read does not enforce this. The first reader adds a
  test in the style of `tests/test_point_in_time_clock.py` (queued in
  `AGENT_TODO.md`).

## Preconditions and fallbacks

- **One machine.** The route holds a per-dataset lock around the plan and both
  writes; without it two retried POSTs both pass the exists-check and both
  append. The lock works only within one process. Production runs one uvicorn
  worker, but the Fly machine count is **unverified** (`fly.toml` sets
  `min_machines_running = 1` and no maximum): `HUMAN_TODO.md` asks Dio to
  confirm `fly scale show`.
- **`--local` runs outside that lock.** A manual production run must never
  overlap the cron.
- **If the runners cannot reach Deribit**, the fallback is fetching from the
  Fly app (region `fra`). That needs its own ADR, because `api` may not import
  `ingestion`.
- **Wire changes are out of scope.** The cron runs main's script against the
  *deployed* app, and the wire is versionless (`extra="forbid"`, append-only
  Delta tables without `schema_mode`). Any later change to the wire or the
  stored columns needs its own deploy-ordered plan, and a schema-evolution
  decision for a new column.

## Accepted gaps

- **Top-of-book sizes** are not captured and cannot be recovered later.
  `public/ticker` is one call per instrument; a burst of 800 drew 581 HTTP
  429s, about 4 minutes a run (queued).
- **Snapshot times drift** between 08:05 and 23:59:59 UTC; `observed_at`
  records each.
- **An underlying that vanishes from both endpoints** commits as a delisting.
  Coverage against `listed` still catches the book losing it.
- **A lost response goes red, but cannot say why.** If the committing
  response is lost and the retry answers "already captured", the run is red
  (the retry carries `may_have_landed`): a missing D - 1 is named, but the
  landed attempt's quarantine share and blob status are unknown, so the
  script points at `ops/quarantine/<dataset>/<D>/` and the app log's
  `quarantine_unwritten` event instead. If every attempt fails after a landed
  commit, the run is red as "retries exhausted" and the next catch-up answers
  "already captured" green. If the blob write also failed, the rows exist
  only in Fly's log.
- **The log fallback depends on Fly's log retention,** which is unmeasured.
- **The raw Deribit response is not stored:** quarantine payloads hold the
  mapped wire dict, so a renamed source field's value is lost.
- **Unmeasured:** whether `get_instruments` lists a new 08:00 expiry before
  the book does. If so, an on-time 08:15 run could trip coverage; the
  catch-up covers it, and the logged coverage numbers will measure it.
- **Inverse premiums are in coin.** USD value = premium x `index_price`;
  computing it is the reader's job.
- **No local fallback while Actions is blocked.** `scripts/local_chain_sweep.sh`
  plus a crontab is the precedent to copy if a block recurs.

## Consequences

- From the first green run the platform accumulates a free, point-in-time
  record of the crypto option books, which can later only be bought.
- Two scheduled jobs now may not do nothing: the Cboe sweep and this one. The
  daily agent reads both status routes before picking work.
- Nothing in `research/` or `api/` reads these datasets yet. Pricer support
  for inverse options and BTC/ETH as screen candidates are queued.
