# Discoveries

Things this project learned the hard way — findings that change how the
code should be written or how a result should be read. Each entry says what
was believed, what turned out to be true, and what changed as a result.

**This is not a changelog and not a source list.** `docs/DATA_FINDINGS.md`
tracks *which data sources work*; this file tracks *what we now know that we
didn't*. If a discovery is only "we built X", it doesn't belong here. If it
would make a future session stop and rethink, it does.

**Add an entry when a belief turns out to be wrong**, especially when the
wrong belief was invisible — no crash, no failing test, just quietly
different numbers. Those are the expensive ones.

---

## 1. Options must be priced off raw `close`, never `adj_close`

*2026-08-21*

**Believed:** `adj_close` is the "better" price series — splits and
dividends handled — so the backtester should read it. `put_roll.py` built
its entire price path from it.

**True:** an option is written on the price that actually printed.
Dividend back-adjustment lowers historical prices by every dividend paid
*since* that date, so a strike set off `adj_close` is set off a price that
never existed in the market — and the error grows the further back the
window reaches.

Measured on real SPY bronze:

| SPY 2021-08-23 | price used | 5% OTM strike |
|---|---|---|
| raw `close` (what traded) | 447.26 | **424.90** |
| `adj_close` | 418.02 | **397.12** |

A nominal "5% OTM" put was in truth an **~11% OTM** put — a 6.5% strike
error at the five-year mark, and larger further back. Deep-OOM puts are
exactly where convexity lives, so this is not a rounding concern: it
silently changed which strategy was being tested.

**The counter-argument, and why it loses.** Dividend adjustment exists to
remove the artificial price gap on ex-dividend dates, which otherwise adds
noise to volatility and beta estimates. That effect is real but tiny —
measured on the same data, switching to raw closes moves the trailing
realized-vol proxy by **0.3% of its mean** and downside beta by **~0.4%
relative** (QQQ 1.2194 vs 1.2245, TSLA 1.9477 vs 1.9551). Trading a 0.3%
volatility artefact to remove a 6.5% strike error is not a close call.

**Changed:** `put_roll.py`'s `_adj_close_series` became `_close_series` and
reads `close`. It is the single chokepoint — `portfolio`, `ranking`,
`metric_screen` and the API routes all read through `load_asof_series` — so
the basis is decided once for every backtest path.

**Still open:** total-return questions ("what would holding this have
earned") genuinely want `adj_close`, and should read it explicitly rather
than through the backtest chokepoint.

**Test-quality lesson attached to this one:** every existing bronze fixture
set `close == adj_close`, so *not one test could tell the two apart*. A
test that cannot fail when the behaviour changes is not testing that
behaviour. `test_backtest_prices_off_raw_close_not_adjusted_close` now
makes them differ deliberately.

---

## 2. `adj_close` means different things from different sources

*2026-08-21*

**Believed:** `adj_close` is a well-defined column.

**True:** it is a vendor convention, and vendors differ. Measured across
1,253 overlapping SPY days:

| | Yahoo | Nasdaq |
|---|---|---|
| `close` | — | agrees to **0.000029** (identical) |
| `adj_close` | dividend + split adjusted | **split-only** (no separate adjusted series exists) |
| total return over overlap | **+82.43%** | **+70.50%** |

The **11.93pp** gap is entirely SPY's dividend stream. Both feeds are
correct; they answer different questions.

Why this is dangerous here specifically: bronze is immutable and
`read_bronze_as_of` returns **one partition**, the latest — it never merges.
So re-ingesting a symbol from a different vendor swaps the basis under every
downstream consumer with no error and no warning. The numbers just become
different numbers.

**Changed:** `IngestResult.adjustment_basis` and the ingest run log record
which basis produced a partition. Prod OHLCV was deliberately *not*
re-ingested while consumers still read `adj_close`. Discovery 1 largely
defuses this: both vendors agree on `close` to 0.000029, so a backtest
reading raw closes is now vendor-agnostic.

---

## 3. A source that returns 200 with no rows is a failed source

*2026-08-21*

**Believed:** ingestion fails loudly when a source dies.

**True:** it failed loudly when a source *errored*, but a source answering
`200` with an empty body would sail through and commit an **empty bronze
partition** — which, because as-of resolution reads the latest partition,
would then shadow good data. Silent data loss dressed as a successful run.

This is the same shape as the earlier truncation incident, where a 2-year
fetch overwrote a 5-year partition and quietly cut production backtests.

**Changed:** `ingestion/sources.py` treats an empty result as a failed
source and moves to the next; an exhausted chain raises `AllSourcesFailed`
rather than returning empty.

---

## 4. Injecting one source's payload can still hit the network

*2026-08-21*

**Believed:** passing a test payload to an adapter makes the run hermetic.

**True:** with a multi-source chain it does not. Injecting the *second*
source's payload left the *first* source free to make a live HTTP call —
so a test that looked offline silently wasn't, breaking
`docs/STANDARDS.md`'s "tests never touch the network" without any visible
symptom beyond a slow suite.

**Changed:** injection is hermetic — supplying any payload restricts the
chain to injected sources only. Pinned by a regression test that
monkeypatches every fetcher to raise.

---

## 5. `make check` was a strict subset of CI

*2026-08-21*

**Believed:** `make check` runs every CI gate (as `CLAUDE.md` states).

**True:** CI ran `ruff format --check` as a separate step and `make check`
did not. A green local run therefore had no right to the confidence it
implied — and a commit that passed locally failed CI and never deployed.
Files written through an editor were auto-formatted by a hook; files
appended by a script were not, so the gap only showed for script-generated
code.

**Changed:** `make lint` now runs `ruff format --check` too. A local gate
that is a subset of CI is worse than no local gate, because it hands out a
green light it cannot back.

---

## 6. Read the data back; don't trust the header

*2026-08-21*

**Believed:** a vendor's documented coverage describes the file it serves.

**True:** Cboe quotes `CLL` and `PUT` back to 1986, but the CSVs their CDN
actually serves start **2008-08-26** and **1991-03-04**. Nasdaq's historical
endpoint silently caps at **~2,513 rows (~10 years)** no matter what
`fromdate` you request — asking for 2006 returns a window starting 2016,
with no error and no truncation flag.

Both were caught only by ingesting and reading back what landed.

**Changed:** `docs/DATA_FINDINGS.md` records a `Verified` date per source
and distinguishes "measured with a real request" from "read off a vendor
page". Use `CLLZ` (1986) and `PUTY` (1986) when long history matters.
