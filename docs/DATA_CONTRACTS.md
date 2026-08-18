# Data contracts

The six core datasets (`README.md` §"Strategy & data at a glance"). Each
entry below is the spec an `ingestion/` adapter and a `contracts/`
pandera schema must match. Field lists are pandera-style: column, dtype,
validation constraints. Deferred datasets are listed at the end — do not
build adapters for them until `docs/END_STATE.md` §2.2 pulls one off the
backlog.

Every dataset's bronze write lands in that dataset's Delta table
(`docs/adr/0013`), one `ingest_date` partition per ingest, and is
**immutable** — a correction is a new bronze write (a new `ingest_date`
partition) with a later `ingested_at`, never an edit to an existing
partition. The "Bronze partition key" listed per dataset below is that
dataset's logical natural key (what an adapter must ingest to avoid
overwriting a *different* real-world observation under the same
`ingest_date`) — distinct from, and layered on top of, the physical
`ingest_date` Delta partition every dataset's table shares.
`transforms/validate.py` is the only path from bronze into silver, and it
validates against the schema below, quarantining rows that fail
(`docs/STANDARDS.md` §Data contracts).

---

## 1. Underlying OHLCV

**Purpose.** Deep, broad-universe daily price history — the base series
every sensitivity metric and the backtest engine's underlying paths are
computed from.

**Source (free, keyless).** Stooq (`stooq.com`) daily CSV downloads, no
key required. Yahoo Finance (via `yfinance`, unofficial/keyless) as a
fallback/cross-check adapter for symbols Stooq covers poorly.

**Implementation note (v1 deviation).** As of this writing, Stooq's CSV
export serves a JavaScript proof-of-work anti-bot challenge to plain HTTP
clients for symbol downloads (confirmed live, the same failure mode
`ingestion/vix.py` already hit for the VIX series and documents in
`README.md`). `ingestion/ohlcv.py` uses Yahoo Finance's public chart JSON
endpoint (`query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>`) instead —
still free and keyless, and the fallback named above, just promoted to
primary until Stooq's block lifts or a `yfinance`-package adapter is
built.

**Cadence.** Daily, after US market close (~21:00 UTC).

**Schema — `OhlcvRow`:**

| Column | Type | Constraints |
|---|---|---|
| `symbol` | `str` | non-null, uppercase, matches universe ticker format |
| `trade_date` | `date` | non-null, ≤ ingestion date (no future dates) |
| `open`, `high`, `low`, `close` | `float` | > 0; `low ≤ open,close ≤ high` |
| `volume` | `int` | ≥ 0 |
| `adj_close` | `float` | > 0 (splits/dividends applied) |
| `source_id` | `str` | one of `"stooq"`, `"yfinance"` |
| `ingested_at` | `datetime` (UTC) | non-null |

**Bronze partition key.** `source_id=<src>/trade_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** A bar is "known" as of the evening of `trade_date`
once the source publishes it — treat `ingested_at`, not `trade_date`, as
the as-of boundary `lake/asof.py` filters on, since a backfill run days
later still carries the original `trade_date` but must not be visible to
a simulation clock sitting between `trade_date` and the real
`ingested_at`.

---

## 2. Volatility complex

**Purpose.** The vol-surface proxy the model-priced backtest (`docs/adr/0004`)
and the regime panel are built from.

**Source (free, keyless).** CBOE historical data downloads
(`cboe.com/tradable_products/vix/vix_historical_data/`, and the equivalent
pages for VIX3M, VIX9D, VVIX, SKEW) — public CSV, no key. Realized vol is
**not fetched**; it's computed in `transforms/` from dataset #1.

**Cadence.** Daily, after CBOE publishes (~EOD).

**Schema — `VolComplexRow`:**

| Column | Type | Constraints |
|---|---|---|
| `series` | `str` | one of `"VIX"`, `"VIX3M"`, `"VIX9D"`, `"VVIX"`, `"SKEW"` |
| `trade_date` | `date` | non-null, ≤ ingestion date |
| `open`, `high`, `low`, `close` | `float` | > 0 (SKEW is typically 100–170, VIX-family >0; check per-series bounds, not one shared range) |
| `source_id` | `str` | `"cboe"` |
| `ingested_at` | `datetime` (UTC) | non-null |

**Bronze partition key.** `source_id=cboe/series=<series>/trade_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** Same shape as dataset #1 — as-of on `ingested_at`.
CBOE's official closing values are not typically revised, so this is
lower-risk than rates/credit below, but the rule is still enforced
uniformly rather than special-cased.

---

## 3. Rates

**Purpose.** The risk-free rate input to the option-pricing model and a
regime-panel input (yield curve shape/level).

**Source (needs a free API key → `HUMAN_TODO.md`).** FRED
(`fred.stlouisfed.org`) — Treasury curve (`DGS1MO`...`DGS30`), SOFR
(`SOFR`), fed funds (`DFF`). Free, but requires a registered API key —
this adapter cannot ship until the key exists (see `HUMAN_TODO.md`).

**Cadence.** Daily (FRED updates most series once per business day).

**Schema — `RatesRow`:**

| Column | Type | Constraints |
|---|---|---|
| `series_id` | `str` | one of the FRED series ids above |
| `obs_date` | `date` | non-null |
| `value` | `float` | not null (FRED gaps on holidays are legitimate absence, not a zero) |
| `vintage_date` | `date` | the FRED `realtime_start` this observation was published/revised under — **required**, see below |
| `source_id` | `str` | `"fred"` |
| `ingested_at` | `datetime` (UTC) | non-null |

**Bronze partition key.** `source_id=fred/series_id=<id>/obs_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** FRED series can be **revised after first
publication**. The adapter must request FRED's vintage/ALFRED-style
`realtime_start`/`realtime_end` parameters, not just the latest value, and
`vintage_date` is a required, validated column precisely so `lake/asof.py`
can filter on "the value as known on the simulation date," not "the value
as it reads today." A rates adapter that only pulls the latest value
without a vintage is a look-ahead bug, not a simplification.

---

## 4. Credit

**Purpose.** Credit-spread regime input; a candidate factor in
cross-asset sensitivity metrics.

**Source (needs a free API key → `HUMAN_TODO.md`, same key as #3).** FRED
— HY OAS (`BAMLH0A0HYM2`), IG OAS (`BAMLC0A0CM`).

**Cadence.** Daily.

**Schema — `CreditRow`:** identical shape to `RatesRow` (`series_id`,
`obs_date`, `value`, `vintage_date`, `source_id`, `ingested_at`) — kept as
a separate contract from rates because the two datasets are consumed by
different research code and may diverge in validation constraints later
(e.g. OAS is non-negative by construction; a Treasury yield is not).

**Bronze partition key.** `source_id=fred/series_id=<id>/obs_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** Same vintage requirement as #3 — FRED credit
spread series are revised too.

---

## 5. Event calendar (scheduled + manual)

**Purpose.** Drives the cockpit's proximity flags (§1.4 in
`docs/END_STATE.md`) and is a required input to research question 3/5
(IV behavior around FOMC/CPI/crises).

**Source (free, keyless, two parts).**
- *Scheduled*: Federal Reserve FOMC meeting calendar
  (`federalreserve.gov`, public HTML/ICS), BLS CPI release schedule
  (`bls.gov`, public), both keyless.
- *Manual*: a hand-maintained table (YAML/CSV under version control, not
  fetched) for unscheduled events — crises, surprise announcements,
  anything without a published future date.

**Cadence.** Scheduled: weekly refresh (calendars change rarely, but do
change). Manual: edited by commit, not on a cadence.

**Schema — `EventRow`:**

| Column | Type | Constraints |
|---|---|---|
| `event_id` | `str` | non-null, unique |
| `event_type` | `str` | one of `"FOMC"`, `"CPI"`, `"EARNINGS"`, `"MANUAL"` |
| `event_date` | `date` | non-null |
| `announced_at` | `datetime` (UTC) | non-null; see point-in-time rule |
| `symbol` | `str \| None` | required for `"EARNINGS"`, null otherwise |
| `description` | `str` | non-null, non-empty |
| `source_id` | `str` | `"fed_calendar"`, `"bls_calendar"`, or `"manual"` |
| `ingested_at` | `datetime` (UTC) | non-null |

**Bronze partition key.** `source_id=<src>/event_type=<type>/ingested_at=<YYYY-MM-DD>/`.

**Point-in-time rule.** This is the dataset with the sharpest look-ahead
trap. `announced_at` — when the event's *future occurrence* became
public knowledge — is the field `lake/asof.py` must filter on, **not**
`event_date`. A scheduled FOMC meeting six months out is legitimately
"known" as of today; a manual/unscheduled entry (by definition) is known
only as of whenever it was actually added, which for a genuine surprise is
at or after `event_date` itself. Backfilling a manual event with
`announced_at` set to before it was really known is exactly the kind of
error the adversarial point-in-time tests (`docs/STANDARDS.md`) must
catch.

---

## 6. Forward-collected option chains

**Purpose.** Ground truth for the narrow, options-liquid tradable subset
(`docs/adr/0008`) once real quotes are needed — starts accumulating from
whenever ingestion begins, not backfilled historically.

**Source (free, keyless).** Yahoo Finance option-chain endpoint (via
`yfinance`, unofficial/keyless) or CBOE's free delayed-quote pages, for
the narrow symbol list only (not the broad screening universe).

**Cadence.** Daily snapshot (forward-collected — there is no free
historical source for this, by design; see `docs/adr/0004`'s caveat on
why the backtest stays model-priced until this dataset has enough
history, or a paid historical source is added per `HUMAN_TODO.md`).

**Schema — `OptionChainRow`:**

| Column | Type | Constraints |
|---|---|---|
| `underlying_symbol` | `str` | non-null, in the tradable subset |
| `contract_symbol` | `str` | non-null, unique per snapshot |
| `expiry_date` | `date` | non-null, > `snapshot_date` |
| `strike` | `float` | > 0 |
| `option_type` | `str` | `"put"` or `"call"` |
| `bid`, `ask` | `float` | ≥ 0; `bid ≤ ask` |
| `last_price` | `float \| None` | ≥ 0 when present |
| `implied_vol` | `float \| None` | ≥ 0 when present |
| `open_interest`, `volume` | `int` | ≥ 0 |
| `snapshot_date` | `date` | non-null |
| `source_id` | `str` | `"yfinance_chains"` or `"cboe_delayed"` |
| `ingested_at` | `datetime` (UTC) | non-null |

**Bronze partition key.** `source_id=<src>/underlying_symbol=<sym>/snapshot_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** Trivially point-in-time by construction — this
dataset is never backfilled, so `snapshot_date` and `ingested_at` coincide
by design. The trap to avoid is the opposite direction: never let backtest
code treat this dataset as if it had history before its first real
snapshot; `lake/asof.py` returning "no data" for pre-collection dates is
correct behavior, not a bug to work around.

---

## Deferred datasets (backlog, not built)

Tracked in `docs/END_STATE.md` §2.2 — do not build adapters for these
until pulled onto `AGENT_TODO.md`:

- Single-name options at scale (beyond the narrow forward-collected set).
- CDS (credit default swap) spreads, single-name.
- ETF flows.
- CFTC positioning (Commitment of Traders).
- Margin debt.
- Market breadth indicators.

Point-in-time historical index constituents (`docs/adr/0010`) is tracked
separately in `docs/END_STATE.md` §2.3 — it's a research-validity
requirement, not an ordinary backlog item, and should be prioritized
accordingly once the six core datasets are stable.
