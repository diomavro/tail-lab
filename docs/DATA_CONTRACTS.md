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
put_roll.py`, `research/leaderboard.py`, `research/data_quality.py`) is
therefore basis-sensitive, and a Nasdaq partition must not be compared
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

1. **Cboe** — `cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`,
   `DATE,OPEN,HIGH,LOW,CLOSE` from **1990-01-02**. Cboe computes the VIX, so
   this is the index's publisher rather than a redistributor. Promoted to
   primary 2026-08-21; the first live run committed **9,255 rows covering
   1990-01-02 → 2026-08-20**, against the ~126 rows Yahoo's 6-month default
   had been supplying.
2. **Yahoo chart JSON** — fallback, for the 429 reason in dataset #1.

The equivalent CSVs for VIX3M, VIX9D, VVIX and SKEW live on the same host
and are confirmed live, but **only `VIX` is ingested today**, and only its
`CLOSE`: widening the committed shape to OHLC across the whole complex
would touch `transforms/vix.py`, `research/vix_stretch.py` and the
dashboard tile, so it is queued separately (`AGENT_TODO.md`). Realized vol
is **not fetched**; it's computed in `transforms/` from dataset #1.

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

**Source.** FRED (`fred.stlouisfed.org`) — Treasury curve
(`DGS1MO`...`DGS30`), SOFR (`SOFR`), fed funds (`DFF`). Free, keyed; the
`FRED_API_KEY` repo secret exists (`HUMAN_TODO.md`, done 2026-08-17).
`ingestion/rates.py` + `make ingest-rates` ship the adapter, requesting
FRED's ALFRED-style full vintage history so `vintage_date` below is real,
not a latest-value stand-in (`docs/DATA_SOURCING.md` §2 confirms the
vintage API works on the free key). **Not yet run against prod** — no
`rates` bronze partition exists yet; that live run, and wiring a
`research/` consumer, are follow-ups (`AGENT_TODO.md`).

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

**Source.** FRED (`fred.stlouisfed.org`) — HY OAS (`BAMLH0A0HYM2`), IG OAS
(`BAMLC0A0CM`). Free, keyed; same `FRED_API_KEY` repo secret as #3.
`ingestion/credit.py` + `make ingest-credit` (`SERIES=` override) ship the
adapter, requesting FRED's ALFRED-style full vintage history exactly as
`ingestion/rates.py` does. **Not yet run against prod** — no `credit`
bronze partition exists yet; that live run, and wiring a `research/`
consumer (starting with widening `research/regimes/timeline.py` beyond
VIX-complex-only), are follow-ups (`AGENT_TODO.md`).

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
  (`bls.gov`, public), both keyless. **The FOMC half ships**
  (`ingestion/fomc.py` + `make ingest-fomc`, done 2026-09-01) — HTML-only
  (the ICS feed 404s), parsing `fomccalendars.htm`'s 2021-2027 window.
  `announced_at` is set to the ingestion timestamp for every row rather than
  the true historical announcement date (conservative and point-in-time-safe,
  not a look-ahead risk, but under-informative for pre-ingest simulation
  dates — see the module docstring). BLS CPI is not yet built.
- *Manual*: a hand-maintained table (YAML/CSV under version control, not
  fetched) for unscheduled events — crises, surprise announcements,
  anything without a published future date. Not yet built.

**A second producer must not write its own bronze snapshot on a day the
first one already has.** Bronze immutability is keyed on `(dataset,
ingest_date)`, not on which producer wrote it (`docs/adr/0013`) — so if the
BLS or manual writer above ever calls `write_bronze("event_calendar",
today, ...)` independently on a day `ingestion/fomc.py` already committed,
that write is a silent no-op and its rows are lost, not merged. The next
producer must either combine all sources into one call before writing (the
`ingestion/cboe_strategy.py`/`ingestion/rates.py` family-in-one-write
pattern) or this shared-dataset design needs revisiting — not a footnote to
discover by losing a day of CPI events.

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
`https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json` —
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
collected and `stale_days`. Public, and read by the daily agent before it
picks any work.

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
`https://cdn.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv`
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
