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

**Source (free, keyless) — an ordered chain, not one endpoint.**
`ingestion/ohlcv.py` resolves through `ingestion/sources.py`:

1. **Nasdaq** (`api.nasdaq.com/api/quote/{SYM}/historical`) — primary.
   Keyless (browser UA + JSON Accept header required).
2. **Yahoo chart JSON** (`query1.finance.yahoo.com/v8/finance/chart/<SYM>`)
   — fallback. Was primary until 2026-08-21; now returns HTTP 429 to
   residential *and* datacenter clients, which is what motivated the chain.

Stooq, the original v1 primary, remains behind a JavaScript proof-of-work
wall and is not in the chain. Cboe is deliberately **not** in this chain:
its CDN serves indices, not ETFs or single names, and its `delayed_quotes`
endpoint carries only a current-day bar — a one-row source would satisfy
the chain and then shadow a multi-year partition under as-of resolution.

**`adj_close` basis differs by source — measured, and it matters.** Nasdaq
prices are split-adjusted but **not dividend-adjusted**, and Nasdaq exposes
no separate adjusted series, so the adapter sets `adj_close = close` for
Nasdaq rows. Measured on SPY over the 1,253-day overlap with the previous
Yahoo partition (2026-08-21):

| | Yahoo | Nasdaq |
|---|---|---|
| `close` agreement | — | **max abs diff 0.000029** (i.e. identical) |
| `adj_close` diff | — | max **$29.62**, mean **$14.00** |
| total return over overlap | **+82.43%** | **+70.50%** (gap **11.93pp**) |

The raw price series cross-validates almost exactly; the gap is entirely
the dividend stream. Every consumer of `adj_close` (`research/backtest/
put_roll.py`, `research/data_quality.py`) is therefore basis-sensitive, and
a Nasdaq partition must not be compared
against a Yahoo one. `IngestResult.adjustment_basis` and the run log record
which basis produced a given partition; a single partition is always
internally consistent, since as-of resolution reads one partition.

**History depth.** Nasdaq caps at ~2,513 rows (~10 years) regardless of the
`fromdate` requested — measured, not documented by Nasdaq. That is double
Yahoo's 5-year default and still nowhere near the GFC; a 2008-era backtest
needs `docs/DATA_SOURCING.md` §10.1's dataset, not this adapter.

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

**Source (free, keyless) — an ordered chain.** `ingestion/vix.py`
resolves through `ingestion/sources.py`:

1. **Cboe** — `cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`,
   `DATE,OPEN,HIGH,LOW,CLOSE` from **1990-01-02**. Cboe computes the VIX, so
   this is the index's publisher rather than a redistributor. Promoted to
   primary 2026-08-21; the first live run committed **9,255 rows covering
   1990-01-02 → 2026-08-20**, against the ~126 rows Yahoo's 6-month default
   had been supplying.
2. **Yahoo chart JSON** — fallback, for the 429 reason in dataset #1.

**The rest of the family, ingested 2026-09-07.** `ingestion/vix_complex.py`
fetches VIX3M, VIX9D, VVIX and SKEW from the same CDN host into a second,
independent bronze dataset (`vix_complex`) — deliberately not merged into
`vix` above, since doing that (and widening spot VIX itself to OHLC) still
touches `transforms/vix.py`, `research/vix_stretch.py` and the dashboard
tile, and stays its own queued increment (`AGENT_TODO.md`). Confirmed live
2026-09-07: VIX3M/VIX9D serve full `DATE,OPEN,HIGH,LOW,CLOSE` like spot VIX,
but VVIX/SKEW serve a bare `DATE,<TICKER>` (no OHLC on the wire at all) —
the parser tolerates both shapes, and `open`/`high`/`low` are null wherever
the source never claimed them. SKEW is the hard blocker on the skew-aware
pricer (`AGENT_TODO.md`); it is now ingestable, but no `research/` consumer
reads it yet and it has not been run against prod (no live-run credential in
the daily agent's workflow). Realized vol is **not fetched** by either
dataset; it's computed in `transforms/` from dataset #1.

**Cadence.** Daily, after CBOE publishes (~EOD).

**Schema — `vix` (spot VIX only, `contracts/vix.py`):** `date` (non-null,
unique), `close` (float, `0 <= close <= 200`).

**Schema — `vix_complex` (`contracts/vix_complex.py`), long-format, keyed by
`(series, trade_date)`:**

| Column | Type | Constraints |
|---|---|---|
| `series` | `str` | one of `"VIX3M"`, `"VIX9D"`, `"VVIX"`, `"SKEW"` — spot `"VIX"` is deliberately excluded, see above |
| `trade_date` | `date` | non-null |
| `open`, `high`, `low` | `float` | nullable — absent for VVIX/SKEW's bare-column source files |
| `close` | `float` | non-null, per-series bound (VIX3M/VIX9D 0–200, VVIX 0–300, SKEW 50–250) — one shared range could not honour both a normal SKEW print (~100–170) and a VIX3M print an order of magnitude smaller |

**Bronze partition key.** `vix`: `source_id=cboe/trade_date=<YYYY-MM-DD>/`.
`vix_complex`: `series=<series>/trade_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** Same shape as dataset #1 — as-of on `ingested_at`.
CBOE's official closing values are not typically revised, so this is
lower-risk than rates/credit below, but the rule is still enforced
uniformly rather than special-cased.

---

## 3. Rates

**Purpose.** The risk-free rate input to the option-pricing model and a
regime-panel input (yield curve shape/level).

**Source.** FRED (`fred.stlouisfed.org`) — Treasury curve
(`DGS1MO`...`DGS30`), SOFR (`SOFR`), fed funds (`DFF`). Free, keyed; the
`FRED_API_KEY` repo secret exists (`HUMAN_TODO.md`, done 2026-08-17).
`ingestion/rates.py` + `make ingest-rates` ship the adapter. It fetches
FRED's full vintage history so `vintage_date` below is real, not a
latest-value stand-in. FRED caps one request at 2,000 vintage dates and the
Treasury series have ~5,100, so the fetch pages them (`docs/PRIOR_ART.md`
§19): enumerate `series/vintagedates`, take the first vintage whole from one
single-vintage `output_type=1` request (exact: nothing precedes it), request
the rest in batches of ≤400 via `vintage_dates=…&output_type=3` with a
bounded observation window, and melt the wide (vintage-in-column-name)
frame to long. It never STORES rows from a chunked realtime window, which
clips `realtime_start` and fabricates revisions (it uses one only to find
which vintages to re-ask).
Each series is then reconciled against FRED's snapshot at its newest
vintage; for dates that disagree (a backfill or revision outside a
batch's window) the revising vintages are found and re-fetched, and
anything still disagreeing fails the run rather than commit an incomplete
history. **Not
yet run against prod.** First consumer:
`research/backtest/contribution_plan.bill_levels` (DGS3MO first vintages).

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
publication**. The adapter must request FRED's vintage history (rates:
`vintage_dates` batches, above; credit: the `realtime_start`/`realtime_end`
window), not just the latest value, and
`vintage_date` is a required, validated column precisely so `lake/asof.py`
can filter on "the value as known on the simulation date," not "the value
as it reads today." A rates adapter that only pulls the latest value
without a vintage is a look-ahead bug, not a simplification.

---

## 4. Credit

**Purpose.** Credit-spread regime input; a candidate factor in
cross-asset sensitivity metrics.

**Source.** FRED (`fred.stlouisfed.org`) — HY OAS (`BAMLH0A0HYM2`), IG OAS
(`BAMLC0A0CM`). Free, keyed; same `FRED_API_KEY` repo secret as #3.
`ingestion/credit.py` + `make ingest-credit` (`SERIES=` override) ship the
adapter, requesting FRED's ALFRED-style full vintage history exactly as
`ingestion/rates.py` does. **Not yet run against prod** — no `credit`
bronze partition exists yet. `research/regimes/timeline.py` now reads HY OAS
(`load_credit_oas`) and escalates the VIX-only regime label with it
(`compute_regime_timeline`) whenever a credit snapshot exists, falling back
to the VIX-only label until then — so this dataset goes live for the
cockpit's regime panel the moment the live run above happens, with no
further code change.

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

**Source (free, keyless, three parts).**
- *Scheduled — macro*: Federal Reserve FOMC meeting calendar
  (`federalreserve.gov`, public HTML/ICS), BLS CPI release schedule
  (`bls.gov`, public), both keyless. **The FOMC half ships**
  (`ingestion/fomc.py`, done 2026-09-01; written since 2026-09-24 through
  `make ingest-event-calendar`) — HTML-only
  (the ICS feed 404s), parsing `fomccalendars.htm`'s 2021-2027 window.
  `announced_at` is set to the ingestion timestamp for every row rather than
  the true historical announcement date (conservative and point-in-time-safe,
  not a look-ahead risk, but under-informative for pre-ingest simulation
  dates — see the module docstring). BLS CPI is not yet built (blocked on an
  Akamai bot-block, `AGENT_TODO.md`).
- *Scheduled — earnings*: Nasdaq's unofficial calendar endpoint
  (`api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`, browser UA + JSON
  Accept header, keyless). **Ships 2026-09-06** (`ingestion/earnings.py`; written since 2026-09-24
  through `make ingest-event-calendar`) — one JSON page per calendar date, so this adapter
  sweeps a rolling 30-day-forward window per run rather than the source's
  full 2010+ history (a deep backfill would be thousands of sequential
  requests to an endpoint the sourcing note already flags as
  "unofficial: pace and cache"; the forward window is what the cockpit's
  proximity flags need day-to-day). Same conservative `announced_at`
  treatment as FOMC, for the same reason. Historical backfill is a
  follow-up, tracked in `AGENT_TODO.md`.
- *Manual*: a hand-maintained table (YAML/CSV under version control, not
  fetched) for unscheduled events — crises, surprise announcements,
  anything without a published future date. Not yet built.

**One writer, all sources.** Bronze immutability is keyed on `(dataset,
ingest_date)`, not on which producer wrote it (`docs/adr/0013`), so a second
per-source write on a day another already committed silently no-ops and
loses its rows. As two writers, FOMC and earnings failed this way every day
(measured 2026-09-21). Since 2026-09-24 `ingestion/event_calendar.py` is the
only writer: `fomc.py` and `earnings.py` only fetch and parse, and every
source lands in ONE snapshot (`make ingest-event-calendar`, in the daily
refresh). It refuses to write when the FOMC page yields no valid meetings,
when more than half the earnings dates fail, or when either source's valid
rows fall below half of what the previous partition held for it (a source
answering only PARTIALLY), since a half-calendar partition would shadow the
last complete one. The next source (BLS CPI) joins that write; it
does not get its own.

**Cadence.** Scheduled: daily, in the local refresh (the earnings window
moves every day; the FOMC half changes rarely). Manual: edited by commit, not
on a cadence.

**Schema — `EventRow`:**

| Column | Type | Constraints |
|---|---|---|
| `event_id` | `str` | non-null, unique |
| `event_type` | `str` | one of `"FOMC"`, `"CPI"`, `"EARNINGS"`, `"MANUAL"` |
| `event_date` | `date` | non-null |
| `announced_at` | `datetime` (UTC) | non-null; see point-in-time rule |
| `symbol` | `str \| None` | required for `"EARNINGS"`, null otherwise (enforced by a schema-level check, not just convention) |
| `description` | `str` | non-null, non-empty |
| `source_id` | `str` | `"fed_calendar"`, `"bls_calendar"`, `"manual"`, or `"nasdaq_earnings"` |
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

**Status: LIVE since 2026-08-26** (`docs/adr/0020`). What follows is what
ships, not what was planned — the plan below it differed in three ways and
the deltas are recorded rather than quietly overwritten.

**Purpose.** The one dataset here that cannot be backfilled. Nobody sells a
free retroactive option chain, so this accumulates forward from first run and
a session not captured is lost at any price. It is a deposit against
`docs/adr/0004`'s model-priced caveat: in a year it turns the model-vs-market
residual from a constant measured on one vendor's SPY history into a function
of name and regime.

**Source (free, keyless).** Cboe's delayed-quote CDN,
`https://cdn-api.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json` —
15-minute delayed, the exchange's own feed, no key, no cookie, no crumb. Cash
indices take an underscore prefix (`_SPX`, `_VIX`); equities and ETFs use the
bare root. Yahoo's `/v7/finance/options`, the originally-planned source, now
answers **401** without a cookie+crumb pair and is no longer viable.

**Coverage.** `DEFAULT_SNAPSHOT_SYMBOLS` — 24 options-liquid names, not the
70-name screening universe. Screen broad, trade narrow (`docs/adr/0008`).

**Cadence.** Weekdays at 21:30 UTC, after the 16:00 ET close in both DST
regimes (`.github/workflows/daily-chain-snapshot.yml`). An empty sweep exits
non-zero and turns the workflow red: this is the one scheduled job here that
may not legitimately do nothing.

**The slice.** Puts only, strikes within 0.40-1.05 of spot, tenors 0-180
days. SPY's full document is 5.9 MB / 13,288 contracts; the slice is ~3,600
rows. Bands match §"real historical option quotes" deliberately, so the
vendor back-history and this forward collection union on their shared columns
with no translation layer.

**Schema — `OptionChainSnapshotSchema`** (`contracts/option_chain.py`). The
first nine columns are exactly `OptionQuoteSchema`:

| Column | Type | Constraints |
|---|---|---|
| `underlying` | `str` | non-null |
| `quote_date` | `Timestamp` | non-null; the session, from the payload's own stamp |
| `expiration` | `Timestamp` | non-null |
| `strike` | `float` | > 0, ≤ 100,000 |
| `bid` | `float` | ≥ 0 (a zero bid is a real market state) |
| `ask` | `float` | **> 0** (no offer means there was no quote) |
| `volume` | `int` | ≥ 0 |
| `open_interest` | `int` | ≥ 0 |
| `spot` | `float` | > 0; carried per row so a quote is self-describing |
| `iv` | `float \| None` | 0-10 when present |
| `delta` | `float \| None` | -1 to 0 when present |
| `theo` | `float \| None` | ≥ 0 when present |

Key: `(underlying, quote_date, expiration, strike)`.

**The zero-fill trap.** Cboe **zero-fills** `iv`/`delta`/`theo` when it
cannot compute them (expiring contracts, no two-sided market) rather than
omitting them. A listed put cannot have 0.0 implied vol, so that zero is a
sentinel — the exact class of error `docs/DATA_VERDICTS.md` caught in the
vendor parquet. The adapter maps it to `None` on the way in, and because
Cboe computes the block together, a zero IV nulls delta and theo on the same
row. **Never read a 0.0 in these columns as a measurement.**

A malformed (non-numeric) `iv` cell is folded into this same sentinel path
rather than raised: it becomes `None`, and delta/theo on that row null with
it, the same as a real zero-fill. That row is still kept — only its greek
block is absent — so one bad cell costs nothing else on this unrecoverable
dataset (`docs/adr/0020`; found by adversarial review 2026-09-26).

**Bronze partition key.** `ingest_date=<YYYY-MM-DD>/` — ONE partition per
day for the whole set, not one per symbol. Bronze is immutable and
re-ingesting an `ingest_date` is a no-op (`lake/store.py`), so a per-symbol
write would silently persist only the first symbol. It also means a
half-finished sweep cannot look complete to an as-of read.

**Point-in-time rule.** `quote_date` comes from the payload's own last-trade
stamp, never from the wall clock, so a sweep that runs late still lands on
the session it belongs to. The trap to avoid is the opposite direction:
nothing in `research/` or `api/` may treat this dataset as having history
before its first snapshot, and a `LookupError` for pre-collection dates is
correct behavior, not a bug to work around.

**Freshness.** `GET /api/ingest/option-chain/status` reports the last session
collected, `stale_days`, and `missing_symbols` — anyone in
`DEFAULT_SNAPSHOT_SYMBOLS` absent from that partition, which `stale_days`
alone cannot see (a sweep landing 23 of 24 chains is not stale). Public, and
read by the daily agent before it picks any work.

### Deltas from the original plan (kept for the record)

The plan in this section before 2026-08-26 specified Yahoo/`yfinance` as the
source, both puts and calls with an `option_type` column, and a
`source_id`/`contract_symbol`/`ingested_at` trio partitioned by symbol.
Shipped instead: Cboe (Yahoo now 401s), puts only (this platform buys puts,
and the call wing doubles storage to answer nothing currently asked — the
dispersion question in `docs/END_STATE.md` §4 Q7 is the one that would need
it, and it can widen the slice when it lands), and the `option_quotes`-
compatible column set so the two quote datasets concatenate.

---

## 7. Cboe option-strategy benchmark indices

**Purpose.** The platform's **real-quote benchmark** for a put-buying tail
strategy. The backtester prices options with a Black-Scholes proxy
(`docs/adr/0004`), and the S1 thesis is that the market *misprices* tails —
which a model-priced backtest structurally cannot see. Cboe's strategy
indices are the realised daily P&L of real, executed option programs, so
the difference between a modelled run and the matching index *is* the
mispricing the platform is looking for. See `docs/DATA_SOURCING.md` §9.1
for why this replaced a $99/mo–$1,495 purchase.

**Source (free, keyless).** Cboe's public index CSVs,
`https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv`
— same host and URL family as dataset #2's vol complex. Per Cboe's
published methodology, each roll is priced at the **volume-weighted average
of actual OPRA transaction prices** (fallback: last reported ask), which is
what makes these real quotes rather than model output.

**Tickers.** The catalogue and the default ingest set live in
`contracts/cboe_strategy.py` (`STRATEGY_INDEX_CATALOGUE`,
`DEFAULT_TICKERS`) — that module, not this doc, is the source of truth for
which indices are ingested. Default set: `PPUT`, `PPUT3M`, `VXTH`, `LTV`,
`CLL`, `CLL3M`, `CLLZ`, `PUT`, `PUTY`, `SPX`.

**Cadence.** Daily, after Cboe publishes (~EOD). The files are full
history every time, so a run is a complete re-fetch, not an increment.

**Schema — `CboeStrategyRow`:**

| Column | Type | Constraints |
|---|---|---|
| `index_symbol` | `str` | non-null, uppercase Cboe ticker |
| `trade_date` | `date` | non-null |
| `close` | `float` | > 0, ≤ 1e6 (NAV-style level rebased to 100 at inception; the ceiling catches unit errors, not compounding) |

Unique on `(index_symbol, trade_date)` — **not** on `trade_date` alone: the
whole family shares one dataset, so two indices quoting the same trading
day is the normal case, not a duplicate.

**Bronze partition key.** `source_id=cboe/index_symbol=<ticker>/trade_date=<YYYY-MM-DD>/`.

**Point-in-time rule.** Same shape as datasets #1 and #2 — as-of on
`ingested_at`, not `trade_date`. Cboe restates strategy-index levels only
rarely, but the rule is enforced uniformly rather than special-cased, and
bronze immutability means a restatement arrives as a new `ingest_date`
partition.

**Known source quirks (measured on the first live run, 2026-08-21).** Two
CDN files start later than the index is quoted elsewhere: `CLL` begins
**2008-08-26** and `PUT` begins **1991-03-04**. Prefer `CLLZ` (1986-06-20)
and `PUTY` (1986-06-30) when long history matters. Dates are `MM/DD/YYYY`
and are parsed with an explicit format — an inferred parse would silently
shift observations by months around a crash.

**`LTV` is not a NAV (measured 2026-10-05).** Every other ticker in the
family is either a NAV-style level a strategy actually earned or, for `SPX`,
the price-return index those strategies are written on; `LTV` opens at
**3.63** on 2006-01-03 and rose **~457%** from 2008-01-02 to 2009-03-09 while
PPUT fell 36%. It is a quoted level — the price of left-tail protection —
so a return computed from it is not an investable return. It shares the
schema because it shares the file format; never blend or compound it (the
Book tab excludes it for exactly this reason).

---

## 8. `option_quotes` — real historical put smile (optional, licence-limited)

**Schema.** `contracts/option_quotes.py`. Columns: `underlying`,
`quote_date`, `expiration`, `strike`, `bid`, `ask`, `volume`,
`open_interest`, `spot`. Keyed by `(underlying, quote_date, expiration,
strike)`.

**Source.** A **local file**, not the network: the lambdaclass `data-v1` SPY
chains, downloaded and SHA-256-verified by hand and refereed against Cboe's
PPUT in `docs/DATA_VERDICTS.md`. `make ingest-option-quotes` extracts the
slice; `ingestion/option_quotes.py` is the only adapter here that never
opens a socket.

**A slice, not the file.** 632 MB and 24.7M rows in, **42,131 rows out** — the
put wing (40%–105% of spot) at each of 210 monthly roll dates, for the expiry
that roll buys. First quote 2008-01-18, last 2025-11-21. Committing the whole
file would cost real object storage to answer questions nobody asks.

**Columns deliberately absent.** The vendor ships `mark`,
`implied_volatility` and the greeks. All are sentinel-filled before 2011 —
`mark` is $0.01 in 87–91% of 2008–2009 rows, IV sits on a 0.01488 floor in
60% of them (`docs/DATA_VERDICTS.md`). The schema drops them rather than
carrying them with a warning: a schema is the cheapest place to make a bad
column unavailable. Anything downstream prices off `(bid+ask)/2` and inverts
its own IV (`research/skew.py`).

**`spot` is denormalized onto every row** so a quote is self-describing.
Moneyness is the only question anyone asks of it, and recovering it should not
require joining an OHLCV dataset that may have been re-ingested from a
different vendor since (`docs/DISCOVERIES.md` #2).

**Optional by construction.** This dataset is licence-limited (cleared for
private research use only, `HUMAN_TODO.md`) and its upstream has vanished once
already. **Nothing in `research/` or `api/` may require it.** The one consumer,
`research/skew.py`, is a hand-run measurement that is allowed to fail with
`LookupError` when the snapshot is absent — unlike the accuracy panel, which
must never go silent.

**Bronze partition key.** `ingest_date=<YYYY-MM-DD>`, same as every other
dataset. Bad rows go to `option_quotes__quarantine`; an empty extraction
raises rather than committing a partition that would shadow a good one
(`docs/DISCOVERIES.md` #3).

---

## 9. Minneapolis Fed Market-Based Probability Densities (MPD)

**Purpose.** A genuine **skew/kurtosis** panel backed out of real option
prices via Breeden-Litzenberger — a calibration/validation target for
`docs/END_STATE.md` §4 Q2 ("how did skew evolve before crashes") and the
skew-aware pricer, unlike VIX (a single implied-vol number) or the
model-priced pricer's own flat-vol proxy.

**Source.** The Minneapolis Fed's public CSV,
`https://www.minneapolisfed.org/-/media/files/banking/mpd/mpd_stats.csv`.
Free, keyless, official (a Federal Reserve Bank). `ingestion/mpd.py` +
`make ingest-mpd` ship the adapter. One file carries the whole market
family — `sp12m`/`sp6m` (S&P 500, the markets this platform cares about),
several single-name/commodity/FX/rate/inflation markets — in long format,
so unlike the per-symbol OHLCV adapter there is no per-ticker fetch: one
GET, one parse, one bronze partition. **Not yet run against prod** — no
`mpd` bronze partition exists yet, and wiring a `research/` consumer is a
follow-up (`AGENT_TODO.md`), same precedent `rates.py`/`credit.py` followed.

**Cadence.** Weekly. `sp12m` runs 2007-01-12 to date (measured
2026-08-21, `AGENT_TODO.md`), covering the whole 2008 crisis.

**Schema — `MpdRowSchema`:**

| Column | Type | Constraints |
|---|---|---|
| `market` | `str` | non-null, e.g. `sp12m`, `bac`, `infl1y` |
| `obs_date` | `date` | non-null |
| `maturity_months` | `float` | nullable — the source leaves this blank for a real subset of rows |
| `mu`, `sd`, `skew`, `kurt`, `p10`, `p50`, `p90` | `float` | nullable; `sd >= 0` |
| `prob_large_decline`, `prob_large_increase` | `float` | nullable, `0 <= p <= 1` |

**Bronze partition key.** `ingest_date=<YYYY-MM-DD>`, one partition per run
covering every market the file carries (mirrors `cboe_strategy` — the
source itself is one fetch, so there is no partial-run case to guard
against). Unique on `(market, obs_date)`.

**Point-in-time rule.** None beyond the ordinary `ingest_date` partition —
the Minneapolis Fed does not publish a vintage/revision history for this
series, unlike FRED's ALFRED data (#3, #4).

**One quirk, not a bug.** For the inflation markets (`infl1y`/`infl2y`/
`infl5y`), the source's own preamble says the "large move" threshold behind
`prob_large_decline`/`prob_large_increase` is `<1%`/`>3%`, not the
symmetric +/-20% band the equity/commodity/FX markets use. The adapter
carries the resulting probabilities as-is; a reader comparing them across
market families should know the threshold defining "large" is not the same
number everywhere.

---

## 10. Point-in-time S&P 500 constituents

**Purpose.** Closes `docs/adr/0010`'s survivorship-bias gap at membership
granularity (`docs/END_STATE.md` §2.3). Today's screening universe is
"current constituents," which by construction omits exactly the names that
blew up and delisted — the most tail-sensitive assets of all. This dataset
answers "which tickers were actually in the index on date D" for any
historical D.

**Source (free, keyless).** `fja05680/sp500` on GitHub (MIT-licensed,
community-maintained): a single raw CSV over HTTPS,
`raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv`.
Refetched in full on every pull (like dataset #7's adapter), so a partial or
failed fetch can never truncate a good partition. Live-verified 2026-08-29:
2,718 observation dates, 1996-01-02 to 2026-06-30.

**Schema — `Sp500ConstituentsRowSchema`:**

| Column | Type | Constraints |
|---|---|---|
| `obs_date` | `date` | non-null, unique |
| `tickers` | `str` | non-null, non-empty; comma-joined membership list |

**Shape — kept as the source's own, not exploded.** One row per observation
date holds that date's *full* membership as a single comma-joined string,
not one row per `(date, ticker)`. Exploding to long format would cost ~1.3M
rows for ~500 tickers x ~2,700 dates with no consumer yet to justify it —
`contracts/sp500_constituents.py` documents the "resolve latest obs_date
<= target, then split" read pattern a future consumer follows.

**Cadence.** Observation dates are irregular — the source republishes the
full list only when membership actually moves (roughly weekly, sometimes
months apart), not one row per trading day.

**Point-in-time note.** Unlike dataset #5's `announced_at` trap, this
dataset needs no separate point-in-time field: `obs_date` already *is* the
date the recorded membership was true, mirroring how `date`/`close` works
for dataset #1's OHLCV. The lake's `ingest_date`-based `read_bronze_as_of`
still governs which *ingestion* of this file a backtest may see (guarding
against a future corrected re-ingest silently rewriting old history); a
research consumer resolving membership for a specific historical date reads
within the returned snapshot's `obs_date` column, the same second-level
filter `research/regimes/timeline.py` already does for VIX closes.

**Bronze partition key.** `ingest_date=<YYYY-MM-DD>`, same as every other
dataset.

**Not yet wired.** This adapter ships alone — no `research/` orchestrator
reads it yet, and the existing screening universe
(`contracts/options_calendar.py`) is still "current constituents." Wiring a
survivorship-aware universe (and the loud caveat `docs/END_STATE.md` §2.3
requires until one exists) is a separate, later increment.

---

## 11. VIX futures term structure

**Purpose.** Every research question this platform answers about "which
regime" or "how expensive is the tail right now" reads spot VIX; none of
them can see the *term structure* (contango/backwardation, how a shock moves
the front month versus the back) that a curve of contract settlements
provides for free. Feeds the constant-maturity curve `transforms/` builds
next (`AGENT_TODO.md`).

**Source (free, keyless).** Cboe's public per-contract settlement CSVs,
`https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry}.csv`
— same keyless CDN host as datasets #2 and #7. Each file is one contract's
*entire* trade history from listing to expiry, so a refetch is always safe
(matches dataset #7's full-history-every-time shape). Verified live
2026-09-02: this URL pattern only serves contracts expiring on or after
**2013-01-16**; earlier dates 403/`AccessDenied`. Pre-2013 contracts live at
a different path (`.../resources/futures/archive/volume-and-price/CFE_{month
code}{YY}_VX.csv`) with an apparent 10x price-scaling difference from the
modern series — confirmed reachable but **not ingested by this adapter**,
deliberately: gluing two differently-scaled sources into one dataset without
validating the scaling first would be worse than shipping half of it
honestly (`ingestion/vix_futures.py` module docstring).

**Which contracts.** `ingestion/vix_futures.py`'s `default_expiries` picks
the next 6 not-yet-expired monthly contracts as of the ingest date — a
deliberately conservative window CBOE has listed throughout the product's
history, so the default `make ingest-vix-futures` run never guesses past
what is actually listed. A caller wanting deeper history (a full
2013-present backfill) passes an explicit expiry list.

**Expiry computation.** A VX contract settles the Wednesday 30 calendar days
before the third Friday of the *following* calendar month
(`compute_vx_expiry`), verified against four real contracts spanning
2013-2026. **No holiday adjustment**: Cboe moves the real settlement date to
the preceding business day when that Wednesday is a market holiday, and this
adapter does not. The failure mode is loud, not silent — a wrong guess 404s
against the one-file-per-exact-date URL rather than fetching a different
contract's data.

**Schema — `VxFuturesRow`:**

| Column | Type | Constraints |
|---|---|---|
| `contract_expiry` | `date` | non-null; the join key identifying the contract |
| `trade_date` | `date` | non-null |
| `open` / `high` / `low` / `close` | `float \| None` | > 0, ≤ 300 when present; null on a no-trade day (see below) |
| `settle` | `float` | non-null, > 0, ≤ 300 |
| `volume` | `int` | non-null, ≥ 0 |
| `open_interest` | `int` | non-null, ≥ 0 |

Unique on `(contract_expiry, trade_date)` — two contracts trade on the same
calendar day every day, so uniqueness on `trade_date` alone would reject the
very panel this dataset exists to build.

**Zero-fill convention.** Cboe zero-fills OHLC on a day the contract did not
trade — a real futures price is never exactly $0.00 — so the adapter maps
that sentinel to a typed null before validation, the same convention
`ingestion/option_chain.py` uses for its zero-filled greeks. `settle` is the
exchange's own computed settlement price and is never zero-filled, so it
stays non-nullable; a missing or non-positive `settle` is a genuinely bad
row, not a quiet day.

**Cadence.** Daily, after Cboe publishes (~EOD).

**Bronze partition key.** `ingest_date=<YYYY-MM-DD>`, holding the whole
requested curve as one immutable snapshot — a partial run must not
half-overwrite a previously-complete curve.

**Point-in-time rule.** Same shape as datasets #1, #2 and #7 — as-of on
`ingest_date`, not `trade_date`.

**Not yet wired.** This adapter ships alone — no `transforms/` constant-
maturity curve and no `research/` consumer read it yet, and it has not been
run against prod (no live-run credential in the daily agent's workflow).
Same ship-the-adapter-first precedent every other dataset here has followed.

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

Point-in-time historical index constituents (`docs/adr/0010`) is now dataset
#10 above — the adapter exists, but nothing yet reads it (see that section's
"Not yet wired"). It remains a research-validity requirement, not an
ordinary backlog item, per `docs/END_STATE.md` §2.3.

---

## 12. optionsDX historical end-of-day chains (optional, licence-limited)

**Status: INGESTED for VIX, 2026-09-03.** The deepest quote source in this
repo: real end-of-day bid/ask/IV/greeks for six underlyings over 2010-2023,
downloaded by hand from optionsDX. `option_quotes` (#8) is one vendor's SPY
monthly rolls; `option_chain_snapshot` (#6) only began collecting on
2026-08-26; this reaches back fourteen years.

That matters because `docs/adr/0004` bounds every backtest here with
"model-priced, so treat it as a relative ranking, not P&L truth". **Where this
dataset has coverage, that caveat can be replaced with a measurement.**

**Source.** `data/vendor/optionsdx/` — 83 `.7z` archives, 1.1 GB, gitignored,
absent on CI and in production. No network. Nothing in `research/` or `api/`
may require it to exist.

### Coverage is uneven, and it is the first thing to know

Measured against the lake 2026-09-09. An earlier version of this table showed
SPY at 63 months with 105 gaps; it was written before the rest of the corpus
was downloaded on 2026-09-03, and is corrected here.

| sym | months | span | gaps |
|---|---|---|---|
| **vix** | 168 | 2010-01 .. 2023-12 | **0** |
| **spy** | 168 | 2010-01 .. 2023-12 | **0** |
| qqq | 144 | 2012-01 .. 2023-12 | 0 |
| nvda | 96 | 2016-01 .. 2023-12 | 0 |
| tsla | 96 | 2016-01 .. 2023-12 | 0 |
| spx | — | **NOT INGESTED** | — |

SPX stays absent: the ingest is OOM-killed at ~3.9 GB and needs a chunked
bronze write (`AGENT_TODO.md`).

**The obligation on consumers is unchanged even though the gaps are gone.**
`contracts/optionsdx.month_coverage` still reports present/missing, and any
consumer spanning a date range must still consult it and refuse, or say loudly
what it skipped — a backtest that skips absent months silently draws a smooth
equity curve that is an artefact of the skipping, and no test of the roll engine
would catch it because the engine is correct on the rows it is given
(`docs/adr/0009`). Today the answer is "nothing is missing"; the check is what
makes that a finding rather than an assumption.

**The binding constraint is now the price series, not this panel.** Bronze OHLCV
is Nasdaq's ~10-year split-adjusted history (measured 2026-10-10: `ohlcv_spy`
2016-10-10 .. 2026-10-08) and this panel ends 2023-12, so a consumer needing both
has **1,818 overlapping trading days**. (This line used to say "a rolling
five-year Tiingo window, ~589 days"; the OHLCV source is Nasdaq, and Tiingo now
feeds #14, not OHLCV.)

**Two joins that are silently wrong.** The panel's `spot` is as-traded; the OHLCV
`close` is split-adjusted. Panel/OHLCV median: spy 1.0000, qqq 1.0000, tsla
1.0003, **nvda 10.0000** (the 2024 10:1 split). And VIX cannot be joined at all —
its `spot` here is the *index* while VIX options settle on VIX *futures*, so
`strike/spot` is not moneyness against the contract, and there is no `ohlcv_vix`
dataset regardless.

### The slice

Puts only, strike within **0.60-1.02** of spot, **0-120 days** to expiry — the
existing sweep grid (30% out, 12 weeks) with headroom, not a maximal band. The
corpus is 8.2 GB uncompressed against ~8.8 GB free, so it is never extracted
whole: archives are expanded one at a time into a temp directory that is
reclaimed before the next. Peak disk is one archive (~40 MB for a VIX year,
~200 MB for the largest SPX quarter).

### Schema — `OptionsDxQuoteSchema`

The first eight columns are `OptionQuoteSchema` minus `open_interest` (this
vendor does not publish it), so the three quote datasets concatenate. Plus
`iv`, `delta`, `vega`, `theta`, all nullable. Key:
`(underlying, quote_date, expiration, strike)`.

**`theta` is per YEAR**, as the vendor publishes it — *not* the per-day
convention `research/option_pricer.PutGreeks` uses. Converting on ingest would
bury a provenance difference inside a number; the consumer converts and says so.

### Three traps, all found on the first real run

1. **A blank `P_IV` voids the whole greek block.** The vendor does not blank
   the rest when its solver fails — it fills it with garbage. Real row, VIX
   2010-01-22, spot 27.70, strike 18: IV blank, `P_DELTA` pinned to exactly
   `-1.0` (true value near zero that far out), gamma and theta `0.0`, vega
   `-41.4`. **A `-1.0` delta passes the schema**, so taking those at face value
   put nonsense in the lake silently. Same rule as Cboe's zero-fill in
   `ingestion/option_chain.py`, reached independently from a second vendor —
   which is reason enough to treat it as the house rule for any greek source.
   73% of VIX rows have usable greeks; the other 27% keep their quotes.
2. **A re-downloaded archive silently deletes a year.** A browser copy landed
   as `vix_eod_2010-0pjoap (2).7z` beside the original. The glob read both,
   every row collided on the unique key, pandera quarantined *both* copies —
   and the run reported "168 months present, 0 missing" while the stored data
   began in 2011. Archives are now deduped by canonical name, and overlapping
   archives (the corpus mixes year files with quarter files like
   `tsla_eod_2022q2_3`) are deduped at row level, because repeated data is not
   bad data and does not belong in quarantine.
3. **Coverage must be measured on what was PARSED, not what survived.** A month
   rejected wholesale otherwise falls outside the reported span and reads as
   "never downloaded" rather than "downloaded and unusable" — opposite
   problems, and the first is invisible. That is precisely how trap 2 hid.

### Verified

`make ingest-optionsdx OPTIONSDX_SYMBOL=vix` → **168,351 quotes,
2010-01-04..2023-12-29, 168/168 months, 0 duplicate keys, 0 garbage deltas,
2,139 quarantined (1.3%)**, ~23 s.

---

## 13. Kaggle SPY 2014-2025 chain (OFFLINE CROSS-CHECK ONLY, licence-limited)

**Status: adapter built 2026-10-05; 2023 verified on the real file (below).** Decided by the
owner 2026-10-05: this corpus is an **offline cross-check only**. It never
feeds a verdict, a recommendation, or anything the API or the live page
serves. Its two jobs:

1. **2023 vendor disagreement vs optionsDX SPY (#12)** on overlapping dates —
   strike sets and put bid/ask — via `make kaggle-spy-vs-optionsdx`
   (`scripts/kaggle_spy_vs_optionsdx.py`, prints only). Headline below.
2. **Black-Scholes pricing residual over 2024-2025**, after optionsDX ends.
   Not built yet. Needs a spot from elsewhere (below).

**Source.** Kaggle `shankerabhigyan/s-and-p500-options-spy-implied-volatility-2019-24`
("S&P500 Options (SPY) Implied Volatility (2014-25)", v3, last updated
2026-07-26, 8,687,075,414 bytes). Twelve files, one per calendar year,
`spy_options_data_14.json` .. `spy_options_data_25.json`, 346 MB (2014) to
1,055 MB (2021). The human downloads one year at a time into
`data/vendor/kaggle_spy/` (gitignored via `data/`, excluded from the image via
`.dockerignore`); exact commands are in `HUMAN_TODO.md`. No network in the
adapter.

**Format** (confirmed 2026-10-05 by byte-range reads of all twelve files'
first bytes, and of the 2014, 2023 and 2025 records; not assumed). Each file
is a single line of JSON, in one of **two outer shapes**: 2019-2024 are an
array of trading days, each an array of records (`[[{...}, ...], [{...}]]`);
2014-2018 and 2025 are an object keyed by date with holidays as empty lists
(`{"2014-01-01": [], "2014-01-02": [{...}, ...]}`). The adapter skips the
outer structure, so both ingest identically (pinned by a test). Records are
flat objects whose 20 keys and **string** values are character-for-character Alpha Vantage
`HISTORICAL_OPTIONS`: `contractID, symbol, expiration, strike, type, last,
mark, bid, bid_size, ask, ask_size, volume, open_interest, date,
implied_volatility, delta, gamma, theta, vega, rho`. Calls and puts. **No
underlying price** — a consumer joins spot from elsewhere and says which.
~8,300 records per day in late 2023.

**Licence posture.** Kaggle says CC0. The schema says Alpha Vantage, and an
uploader cannot waive rights they do not hold, so **AV's terms are the real
constraint** — the same unresolved posture as the lambdaclass file. Private
offline research only: never redistributed, never served, never committed.

### Never served — enforced, not promised

- The output is **not in the lake**: one parquet per year at
  `data/vendor/kaggle_spy/parquet/kaggle_spy_chain_puts_<YEAR>.parquet`. The
  lake is what the API reads (Tigris on Fly), so keeping this out of it is the
  structural guarantee.
- import-linter contract *"The Kaggle SPY corpus is never served"* forbids
  `tail_lab.api` and `tail_lab.research` from importing
  `contracts.kaggle_spy` or `ingestion.kaggle_spy`.
- `tests/test_ingestion_kaggle_spy.py` greps every module in `src/tail_lab`
  (bar the contract and adapter) and `frontend/src` for any mention of the
  corpus — a path string in `transforms/` would otherwise reach the API with
  no import for the linter to see — and pins `data/` in both `.gitignore` and
  `.dockerignore`. The `.dockerignore` line is the hard barrier: the corpus
  cannot ship.

### Ingest — `make ingest-kaggle-spy YEAR=2023`

Year by year, **streamed**: the JSON is read in 16 MB text chunks and only
complete `{...}` records are parsed, so a year is never held as text or as
Python objects. A `.zip` (the kaggle CLI's delivery) is read as a stream,
never extracted — 8.7 GB uncompressed against ~15 GB free. Kept: SPY **puts**
with an ask (`ask > 0`), every strike and tenor (no moneyness slice: there is
no spot to slice against). Only repeats identical after parsing count as
duplicates; two *different* rows under one `(quote_date, contract_id)` are a
conflict nobody can resolve by keeping the first, so the schema's unique key
quarantines both. Every record read is kept or lands in exactly one named
count in the run record (`event=ingestion.kaggle_spy.run`), and `valid_rows +
not_spy_put_rows + unparsable_rows + no_ask_rows + duplicate_rows +
quarantined_rows == records_read` holds by construction; what makes it mean
something is that a lost record is counted wherever its `"contractID"` key
appears outside a complete record, even when the JSON itself is broken.
`unparsable_rows` covers a record that fails JSON parsing, a truncated final
record (each `"contractID"` left open is one row), a record missing either
brace, a blank date, expiry, strike or **bid** (a blank bid is not a $0.00
bid), and each extra record in a span fused by a lost `}, {`.
`voided_greek_rows` counts kept rows with no usable greeks.

**A bad file cannot replace a good year — for the three kinds of bad file the
adapter knows.** Re-running a year overwrites its parquet atomically (same
input, same output; this is not immutable bronze), except that these are
refused, raise, and write nothing: (1) a file with ANY readable put dated
outside the year (checked on every parsed row, not only the validated ones, so a row that also fails the
schema still refuses the file) — the wrong file, well-formed, which was once
accepted and replaced the real 971,972-row 2023 with 22 rows; (2) one yielding
zero valid puts (a login page, an empty download); (3) one that does not *end*
like a complete year (`]]` or `]}`) — a cut-off download, refused even though
the half it holds parses fine. A file that is the right year, complete, and
well-formed but simply holds less (a vendor revision dropping days) is not
detected; compare `trading_days` in the run record. Every refusal is logged
(`event=ingestion.kaggle_spy.refused`, `reason=` plus every count); rows that
failed the schema go to a separate `..._refused_quarantine.parquet`, named in
the error, without touching the good year's own quarantine. A clean run
deletes any older `__quarantine` or `__refused_quarantine` file, and a refusal
with nothing quarantined deletes an older refusal's, so no quarantine file
outlives the run it describes.

**Verified on the real 2023 file** (2026-10-05, 803,334,917 bytes): 1,943,946
records → **971,972 puts** over 250 trading days (2023-01-03..2023-12-29),
971,973 calls/other, 1 no-ask, 0 unparsable, 0 duplicate, 0 quarantined (0
crossed, 0 off-year), 21,260 voided greek blocks (all the pinned signature,
below), `iv_distinct_ratio` 0.56. **34.1 MB parquet**; 1 m 50 s, 811 MB peak
RSS.

### Schema — `KaggleSpyPutSchema`

`contract_id, quote_date, expiration, strike, last, mark, bid, ask, bid_size,
ask_size, volume, open_interest, iv, delta, gamma, theta, vega, rho`. Key
`(quote_date, contract_id)`; `expiration >= quote_date`; `ask > 0`,
`bid >= 0`, `last >= 0`, and `bid <= ask` (a crossed quote is quarantined).
Greek block nullable. `bid_size`, `ask_size`, `volume`,
`open_interest` are nullable integers: blank, garbled or fractional is
`<NA>`, never a fabricated 0. `theta` stored as published (magnitudes read
as per *day*, unlike optionsDX's per-year; unverified, not converted).

### The IV caveat — vendor greeks are decorative

AV's IV is **not per-contract inverted**, and how badly varies by year.
Measured from byte-range samples on 2026-10-05: 2014-01-02, 1,667 puts carry
133 distinct IVs; 2025-01-02 (first 2 MB of the day only), 2,422 puts carry
158 (one expiry: 57 distinct across 111 strikes); 2023-12-29, 4,143 puts carry
2,244 — although within one expiry 222 of 224 are distinct, so 2023's
repetition is across expiries. And deep-ITM long-dated put deltas plateau near
-0.47 where the truth is near -1. So `option_pricer.PutGreeks` does the work
and the vendor block is kept for provenance only, under **the house rule**
(Cboe zero-fill, optionsDX blank `P_IV`): any greek blank or infinite, an IV
that is zero or above 10, a put delta outside [-1, 0], a negative gamma/vega,
or the **pinned** block — delta exactly -1 with gamma and vega exactly 0
beside a positive IV, the same signature optionsDX's failed solver leaves
(21,260 such 2023 puts, IVs 0.02-6.84; 18 more with delta -1.00000 but a
non-zero gamma or vega are kept as rounding) — **voids the whole block to
null**, never 0.0, and the quote on the row is kept. Each run records
`iv_distinct_ratio` (median over days of distinct IVs / puts with an IV) so
the smoothing is a number per year: ~0.07 on 2025-01-02, 0.56 for all of 2023.

### 2023 vendor disagreement — the headline

`make kaggle-spy-vs-optionsdx YEAR=2023`, 2026-10-05, on moneyness 0.6-1.02,
**calendar DTE 1-119** on both sides, ask > 0:

| | |
|---|---|
| dates compared | 250 (none one-sided) |
| (expiry, strike) keys | 346,357 both · 62 only Kaggle · 114 only optionsDX |
| per-day key overlap (Jaccard) | median 1.000; worst 2023-02-16 0.935, 2023-04-20 0.963 |
| bid / ask diff (K − O) | median 0.00, median \|diff\| $0.01 both sides |
| matched quotes > 2 cents apart on bid or ask | **32.4%** (integer cents) |

The DTE band is one day inside each of optionsDX's ingest edges because
optionsDX was sliced on its vendor DTE column, which disagrees with the
calendar count *at* the edges: compared on 0-120, 755 of 817 Kaggle-only keys
sat exactly on an edge (390 at 120 days, 365 at 0) and made 2023-08-17 and
2023-08-31 the "worst days". The tolerance is compared in integer cents: in
float, every exact two-cent gap counted as a disagreement (36.9% vs 31.2% on
the 0-120 band).
The 114 optionsDX-only keys sit on two dates (2023-02-16, 2023-04-20) — a real
vendor difference worth a look, not an artefact.

---

## 14. `tiingo_eod` — as-traded closes, dividends and splits (Tiingo)

**Status: built 2026-10-10 (PR "tiingo_eod dividends"); first ingest is a human
action** (`HUMAN_TODO.md`: approve the weekly timer). Until it runs, every price
in the app is unchanged: each roll reads `q = 0` labelled `unknown`.

**Why it exists.** Every option price needs the underlying's dividend yield
`q`, and nothing in the lake carried a dividend: the roll backtest priced every
name at `q = 0`, which under-priced puts on income names worst (HYG's delta was
off by 0.197 against the exchange's own). Nasdaq's keyless dividend endpoint
serves no history at all for NYSE Arca ETFs (SPY, HYG, IWM, LQD — probed
2026-10-10), so the source is Tiingo (free, keyed, personal use —
`docs/DATA_SOURCING.md` §7).

**Shape — `TiingoEodSchema`** (`contracts/tiingo_eod.py`), keyed
`(symbol, trade_date)`:

| column | meaning |
|---|---|
| `symbol` | lower-case universe ticker |
| `trade_date` | the session |
| `close` | **as traded** — not split-adjusted |
| `adj_close` | Tiingo's dividend-and-split-adjusted close (a total-return index: `adj_t/adj_{t-1} = (close_t + div_t)/close_{t-1}`, verified to 3.7e-12 over 8,482 SPY rows) |
| `div_cash` | cash dividend per share paid **on this ex-date**, as paid; 0 otherwise |
| `split_factor` | split effective this day (4:1 = 4.0); 1.0 otherwise |

**One dataset, all symbols.** The whole universe (`universe_symbols()`, 70
names) lands in one partition per run, following the chain sweep's precedent
rather than the per-symbol `ohlcv_<sym>` layout: an as-of read takes exactly one
partition, so a per-symbol write would let one failed symbol hide the rest.
Each weekly partition re-states the full history from 1993 (~400k rows,
≈20 MB/yr of Tigris growth).

**Write policy** (`ingestion/tiingo_eod.py`): a **failed fetch aborts the whole
write** (a partition missing a symbol would hide its history); an **invalid row
is quarantined** (`tiingo_eod__quarantine`) and its symbol is **withheld** from
the valid partition, so readers see it absent (`q` unknown) rather than a
silently wrong yield; a run on a day whose partition exists is a logged no-op.

**Budget.** Free tier ≈ 50 requests/hour; one request per symbol returns its
whole history, and the adapter spaces them 80 s apart (45/hour), so a run takes
~92 minutes (69 throttled gaps of 80 s). Scheduled weekly (`docs/systemd/tail-lab-tiingo.timer`, Saturday
10:00 UTC) through `scripts/weekly_tiingo_refresh.sh`, which holds the
dataset's own lock (`.tiingo-refresh.lock`).

**From rows to `q`** — `transforms/dividend_yield.py`, read through
`research/dividends.py` (one projected read per snapshot, memoised on the
lake and snapshot id, built once per key even under concurrent requests):

* the **indicated annual dividend**: the last `N` payments of the current
  *run* (payments since the last gap longer than 3× the seed period — a
  suspension), `N` = the run's mean gap over its payments in the last 700
  days (at least its last two), snapped to 1, 2, 4 or 12 a year, each payment
  divided by the splits after its ex-date; then
  `q = -ln(1 - D / close)` against the same day's as-traded close. No
  calendar window: every 365-day variant miscounted around ex-dates.
* labelled sources: `measured`, `non_payer`, `suspended` (last payment older
  than twice the period), `short_history` (scaled to a year), `carried` /
  `stale` (past the data's last row; stale after 21 days), `unknown` (priced at
  `q = 0`).
* **Point-in-time.** A backfill lands as one partition dated today, so the
  as-of model does not guard history here: the row filter does — `q` at date
  `t` reads only rows dated on or before `t` (tested). Dividing by the same
  dataset's as-traded close keeps the ratio on one share basis without
  reconciling against bronze OHLCV, whose closes are split-adjusted as of
  their fetch date.
* **Disclosed limits:** a cut lags up to `N` payments; Tiingo does not flag
  specials; a frequency change biases `q`
  both ways for up to a year (TSM 2019-20); a special distorts `q` in either
  direction -- a large one inflates `D`, a small one displaces a regular payment
  and deflates it -- and depending on where it falls can raise `N` itself (a
  semiannual payer with one special reads `N = 4` for weeks of the next year)
  until it ages out; and a continuous yield
  spreads an annual payer's dividend across every short option (FXI's delta
  error grew).

**Consumers.** `compute_put_backtest`, the ranking
(all three of its pricing calls), the portfolio, the metric screen, the sweep,
the roll schedule, the Surface (which falls back to its old assumed 1.9%,
labelled, when a name has no measured yield) and `scripts/greeks_check.py`.
Each result cites the snapshot (`dividend_snapshot` or `snapshot_ids`). **Not
yet:** the Book (`hedge_overlay`, `contribution_plan`, `model_plan`),
`index_replication` and `skew` still use the flat 1.9% `DEFAULT_DIVIDEND_YIELD`.

---

## 15. `options_expiry_<symbol>` — listed expiration dates

**Status: built 2026-08-30; populated by the daily refresh for the 24
chain-sweep symbols.** One row per `(symbol, expiration_date)` the underlying's
chain lists as of the ingest date (`contracts/options_expiry.py`), one dataset
per symbol like `ohlcv_<sym>`. Fetched from Cboe's delayed-quote chain with a
Yahoo fallback (`ingestion/options_expiry.py`, `make ingest-options-expiry
SYMBOL=…`). `transforms/options_expiry.classify_cadence` turns it into the
listing-cadence label `/api/putlab/cadence` serves; without a partition the
route falls back to the static catalogue in `contracts/options_calendar.py`
(`docs/DATA_FLOW.md` §3.1). This section was missing from this file until
2026-10-10, though the dataset predates it.
