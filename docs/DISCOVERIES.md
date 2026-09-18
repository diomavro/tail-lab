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

---

## 7. Trust is per-column, not per-dataset

*2026-08-21*

**Believed:** a dataset is either good enough to use or it isn't. The
lambdaclass validation was framed as one question — are these quotes real?

**True:** the answer was *yes for the quotes and no for everything derived
from them*, in the same file, for the same rows. Its `bid`/`ask`/`strike`
reproduce Cboe's `PPUT` at **ρ=0.9927** across 17.8 years, while in the same
2008–2009 rows:

- `mark` is **$0.01 in 87–91%** of rows — a sentinel wearing a price's name
- `implied_volatility` sits on a **0.01488 floor in 60%** of 2008 puts,
  during a VIX-80 crisis
- median put delta at 20–45 DTE reads **−1.0000**

A blanket "validated ✅" would have licensed exactly the wrong use. The most
dangerous fields were the *convenient* ones — the pre-computed columns you
reach for to skip work.

**What makes sentinels worse than nulls:** a null fails loudly at the first
arithmetic. `mark = 0.01` and `iv = 0.01488` propagate silently into a
backtest and produce a plausible, wrong number. Note that the sentinel is
neither round nor obviously fake; it was found by asking *how many rows share
the minimum value*, not by looking at any single row.

**Changed:** `docs/DATA_VERDICTS.md` records verdicts as a **column-level
trust table**, never a single verdict per dataset. When probing a new source,
check the derived columns separately from the raw ones, and check the *share
of rows at the extreme value* rather than eyeballing a sample.

---

## 8. A schema is a fingerprint — provenance can be recovered from it

*2026-08-21*

**Believed:** the lambdaclass dataset's provenance was permanently
unknowable. The upstream vanished in 2026 and, verbatim, *"the upstream's own
sourcing was not documented"*. That unknown was blocking a human licence
decision.

**True:** field names and field *order* are a vendor's fingerprint. The
parquet's twenty columns are a field-for-field match with Alpha Vantage's
`HISTORICAL_OPTIONS` — including idiosyncratic choices no two vendors would
converge on independently (`mark` alongside `bid`/`ask`; `bid_size` and
`ask_size` as separate columns; that exact ordering). The underlying file
matches `TIME_SERIES_DAILY_ADJUSTED` just as exactly, and the coverage start
dates line up on both. Cost: one demo API call.

**Changed:** when a dataset's origin is unknown, **fingerprint the schema
against candidate vendors' documented responses before recording "provenance
unknown"**. It is cheap, and it converts an unanswerable question into a
licence question — which at least has an answer.

**The corollary is uncomfortable:** knowing the vendor made the *quality*
story better and the *redistribution* story worse. Resolving an unknown does
not always resolve it in your favour, and that is still better than not
knowing.

---

## 9. The model's pricing error flips sign in a crisis

*2026-08-22*

**Believed:** the model-vs-market gap (`docs/adr/0004`'s admitted limitation)
was a *level* problem — our Black-Scholes premium is probably a bit off, and
once measured it could be corrected with a calibration factor.

**True:** measured against Cboe's PPUT over **438 monthly rolls and 36.5
years**, the residual is **+1.34%/yr overall but −1.46%/yr in crisis**
(`docs/MODEL_RESIDUAL.md`). The model underpays for puts in calm and elevated
markets and **overpays in exactly the regime the platform exists to study**.

A single multiplier would have fixed the common case and made the important
case worse — and, because calm rolls outnumber crisis rolls 5:1, a fitted
constant would have been dominated by the regime that matters least.

The mechanism is skew: `sigma` is the VIX, a ~30-day ATM vol, but the option
is 5% OTM. Steep skew in calm markets means VIX understates the OTM put's
vol; a crisis flattens the surface and VIX overshoots it.

**The strike evidence, added after the correction in §10, is stronger than the
regime evidence.** PPUT3M is struck at **10%** OTM, and its residual is
**+2.71%/yr — double PPUT's** — even though its longer quarterly tenor should
push the other way (a 10% strike three months out is fewer standard deviations
from the money than a 5% strike one month out). Strike depth dominates tenor,
which is exactly what a missing volatility skew looks like. It also refines the
flip: PPUT3M's residual stays positive *through crises*, so the sign change is
a near-the-money phenomenon, not a crisis phenomenon.

**Changed:** the residual is now reported **per regime, never as one number**
(`make residual`); the Put Lab's accuracy panel measures each run against
whichever published program sits closer to its strike and tenor, rather than
quoting the 5%-OTM figure at a 20%-OTM tail hedge; and the skew-aware pricer in
`AGENT_TODO.md` has a falsifiable acceptance test instead of a vague one. It
must shrink **PPUT3M's residual by more than PPUT's** *and* close the 5%-OTM
crisis flip. Fixing only part of that means the skew story is wrong.

**Method note worth keeping:** the harness reports a **dividend-yield
sensitivity beside every residual**, because the equity leg's dividends are an
assumption (`SPXT` is paywalled), not a measurement. It earns its keep
immediately — PPUT's residual holds its sign across the plausible yield range
and PPUT3M's does not. A residual quoted without that sensitivity would have
been a number pretending to be a measurement.


---

## 10. Cboe's index *names* do not describe their strikes

*2026-08-22*

**Believed:** `PPUT3M` is the three-month sibling of `PPUT`, so it runs the
same 5% OTM strike over a longer tenor. The name and the shared 2004 launch
date both point that way, and `CLL3M` really is the 3-month `CLL`.

**True:** Cboe calls `PPUT3M` the **"S&P 500 Tail Risk Index"**, and it buys a
**10% OTM** quarterly put. The replication harness shipped with 5% hard-coded
and published a residual of **+0.43%/yr** for a strategy that does not exist.

The error was invisible from the outputs: a 5%-struck replication still
correlated 0.9812 with the real index, because both are dominated by the same
equity leg. **A high correlation with an index is not evidence you replicated
that index** — it is mostly evidence you held the same underlying. The tell
only appears on correction: the right strike moved correlation to **0.9972**
and tracking error from 2.89%/yr to **0.98%/yr**, better than PPUT's own fit.

**Changed:** the strike is fixed, `PROGRAMS` carries a comment saying to read
the methodology rather than the ticker, and the corrected number turned out to
be the most useful measurement in the file — see §9's strike evidence.

**The general lesson, and it is the same shape as §6 (read the data back, do
not trust the header):** a vendor's *label* is not a specification. Cboe
publishes methodology PDFs for every index in this catalogue; the definitions
endpoint `cdn.cboe.com/api/global/us_indices/definitions/all_indices.json`
gives each ticker's official description keylessly and is the cheapest first
check. Before replicating any published rule, confirm the rule from the
publisher — not from the ticker, and not from a sibling index's naming
pattern.

---

## 11. A flat-vol model reports deep-tail puts as free

*2026-08-22*

**Believed:** the model-vs-market gap was a *level* error that got somewhat
worse at deeper strikes. §9 measured +1.34%/yr at 5% OTM and +2.71%/yr at 10%
and read that as skew, by argument.

**True:** with real quotes in the lake the argument became a measurement
(`make skew`, 210 roll dates of SPY quotes 2008–2025), and the deep tail is
not a worse version of the same error — it is a different kind of error:

| Strike | VIX | Market IV | Median market/model premium |
|---|---|---|---|
| 5% OTM | 19.8% | 22.0% | **1.42×** |
| 10% OTM | 19.8% | 27.0% | 7.38× |
| 20% OTM | 19.8% | **38.0%** | **21,663×** |

At 20% OTM the Black-Scholes-at-VIX price has rounded to approximately
nothing while the market charges real money. The model does not misprice the
deep tail; **it reports that the option is free.** That is precisely the
region the S1 thesis is about, and precisely where a model-priced backtest is
least trustworthy — the opposite of the intuition that a cheap option is a
small error.

**The counter-intuitive part:** the *annualized cost* of the error peaks at
10% OTM (+2.05%/yr) and **falls** to +1.01%/yr at 20%. The ratio explodes
while the dollars shrink. So the most expensive place to be wrong is moderate
OTM, and the most embarrassing place is the deep tail. Reporting either
number alone would mislead.

**Changed:** at 5% OTM skew accounts for **+1.57%/yr against a +1.34%/yr
residual — the whole thing**, so `docs/MODEL_RESIDUAL.md` no longer argues
that skew is the cause, it measures it. The skew-aware pricer in
`AGENT_TODO.md` stops being a hypothesis test and becomes a calibration with
a stated target: +2.2 vol points at 5% OTM, +7.2 at 10%, +18.2 at 20%.

**Method note:** implied vol is re-inverted from `(bid+ask)/2` by bisection,
never read from the vendor's `implied_volatility` column, which is
sentinel-filled before 2011 (§7). Bisection rather than Newton because a put
price is monotone in vol and cannot diverge, while Newton can — exactly on the
near-zero-vega deep strikes that produced the finding above.

---

## 12. Five years of daily returns cannot establish a Karamata region

*2026-09-18*

**Believed:** the tail index is the hard part of Paretan pricing, and the
Karamata constant — the point beyond which the strong Pareto law takes over —
is a detail you estimate along the way. `docs/adr/0026` treats it as a
precondition, and the first version of `research/surface/karamata.py` returned
an onset unconditionally.

**Turned out:** on bronze OHLCV's rolling five-year window (1,254 closes,
~580 down-moves) **no name in the universe has a measurable Karamata region.**
Measured 2026-09-18 with `make tail-alpha`, at the relative-flatness tolerance
that "L has converged to a constant" actually means. At the **default**
tolerance this holds for all 70 universe symbols; the sweep below was run on
these five:

| | down-moves | best flatness | first Hill plateau |
|---|---|---|---|
| SPY | 577 | 0.61 | alpha 2.75 at a **1.71 %** move |
| QQQ | 574 | 0.69 | alpha 3.27 at a 2.49 % move |
| TSLA | 602 | 0.96 | alpha 3.41 at a 6.64 % move |
| NVDA | 584 | 3.00 | alpha 3.10 at a 4.46 % move |
| HYG | 618 | 1.44 | alpha 2.75 at a **0.74 %** move |

Against a tolerance of 0.05. And it is not one knob away: for these five names, sweeping tolerance over
{0.05, 0.10, 0.25, 0.50} × min_beyond over {20, 30, 50} returns `is_flat=False`
in all 60 cells. (Across all 70 universe symbols the same sweep finds 6 flat
cells out of 840 — `dia`, `xbi`, `pltr` and `aapl`, all at tolerance 0.50, which
is ten times the default and not a tolerance anyone should read as "flat".) Only at tolerance 1.00 — where "flat" means
`L` varying by 100 % of its own mean, which is not flatness — does a region
appear, and its onset then sits deep in the body (SPY 0.64–0.80 %, HYG 0.62 %,
QQQ 1.06–1.31 %; TSLA 4.24 %, NVDA never).

**Why it is the expensive kind of finding.** The plateau `stable_k` reports for
SPY is **alpha 2.75**, exactly the figure Taleb et al. use for SPX. A tool that
printed it would have produced a number that agrees with the literature and looks
like a replication.

It is not even the only plateau: at the default window and tolerance SPY has
**21 disjoint plateau regions**, and `stable_k` returns the first because that
is the one at the deepest threshold. The last of the 21 reports alpha **0.61**
at a 0.12 % move, which is by itself enough to show that "a Hill plateau exists"
carries no information.

The reported one rests on 55 order statistics at a 1.71 % threshold — the top
9.7 % of down-days, or 4.5 % of all returns. **Whether that is tail or body is precisely
what a percentile cannot settle**, and it is why the Karamata test rather than a
depth heuristic is the gate: `L` is not flat there (relative spread 0.609 against
a 0.05 tolerance), so the strong Pareto law is not established at that threshold
and the plateau is not a tail index — whatever percentile it sits at. HYG makes
the point harder to dismiss: the same 2.75, at a 0.74 % move.

**What changed.**

1. `KaramataFit` gained **`is_flat`**, and consumers may not use `onset` without
   it. Previously, when no stretch met the tolerance, the search returned its own
   `min_beyond` floor and there was no way to tell that apart from a measurement.
2. `hill.stable_k` takes **`min_threshold`**, and `scripts/tail_alpha.py` gates
   on the onset — refusing outright when there is no onset to gate on, rather
   than falling back to the ungated plateau. Falling back would be backwards:
   the absence of an onset is *less* reason to trust the plateau, not more.
3. **The realised-alpha leg is not available from bronze OHLCV.** Anything
   needing a realised tail index must use the optionsDX panel's own `spot`
   column (~3,520 trading days, 2010-2023, `docs/DATA_CONTRACTS.md` #12), which
   is also the basis the implied side is fitted on — so the two legs come from
   one column of one file rather than two sources with different split
   conventions.

**What it does not mean.** Not that returns are thin-tailed — `L` converging to
a constant is an asymptotic statement, and the finding is that ~580 observations
cannot establish it, not that it is false. The right reading is that the default
tolerance is calibrated for what the phrase means, and that this window cannot
meet it. Loosening the tolerance until a number appears is the specific failure
this entry exists to prevent.
