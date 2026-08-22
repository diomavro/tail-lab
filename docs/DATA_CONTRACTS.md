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
