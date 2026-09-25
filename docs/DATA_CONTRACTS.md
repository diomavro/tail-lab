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
refresh). It refuses to write when the FOMC page yields no valid meetings or
more than half the earnings dates fail, since a half-calendar partition would
shadow the last complete one. The next source (BLS CPI) joins that write; it
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
is a rolling five-year Tiingo window (2021-08 .. 2026-08) and this panel ends
2023-12, so a consumer needing both has **~589 overlapping trading days** — about
a third of a default four-year window.

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
