# Data verdicts

Referee reports on datasets whose provenance or quality could not be taken on
trust. One section per dataset. A verdict is not an opinion — it is a
measurement against an authoritative free benchmark, with the numbers shown
so a future session can disagree with the reasoning rather than re-run the
work.

`docs/DATA_FINDINGS.md` says *which sources work*; `docs/DISCOVERIES.md` says
*what we learned*; this file says *whether a specific dataset may be believed,
and for which columns*.

---

## lambdaclass `data-v1` — SPY option chains, 2008–2025

*Verdict written 2026-08-21. Referee: Cboe `PPUT`.*

**TRUSTED for quotes and contract terms. NOT TRUSTED for the derived
columns (`mark`, `implied_volatility`, greeks), especially before 2011.**

### What was tested

`docs/DATA_SOURCING.md` §10.1 found free EOD SPY chains covering the
2008–2009 window no free source otherwise reaches, but with undocumented
provenance — so `AGENT_TODO.md` gated it behind a replication test before
anything could depend on it.

`SPY_options.parquet` (631,738,559 bytes, SHA-256 `a7152991…41f0a`,
**24,681,665 rows, 2008-01-02 → 2025-12-12) and `SPY_underlying.parquet`
(SHA-256 `847e60a4…c79db`, 6,570 rows from 1999-11-01) were downloaded and
hash-verified against upstream `CANONICAL_HASHES`.

From the chains alone we rebuilt PPUT's strategy — hold the underlying, buy a
~5% OTM one-month put, roll at each monthly expiration — and compared
roll-to-roll returns against the real `PPUT` series in bronze. PPUT is priced
at OPRA transaction prices by Cboe itself, so it is an authoritative and free
referee.

**207 monthly rolls, 2008-01-18 → 2025-11-21 (17.8 years). Zero rolls were
unpriceable** — every single monthly expiration over eighteen years had a
live two-sided quote at the target strike. Mean selected strike: **−4.96% OTM**
against a −5% target. Mean put cost: **0.790% of spot per month**.

### Result

| Settlement convention | corr (monthly) | Tracking error | Drift vs PPUT | Worst roll |
|---|---|---|---|---|
| PM close (naive) | 0.9847 | 2.30 %/yr | −0.31 %/yr | 4.00% |
| **AM open (SPX-like)** | **0.9927** | **1.63 %/yr** | −0.47 %/yr | 3.74% |
| AM open, less 3 worst rolls | 0.9959 | 1.13 %/yr | −0.44 %/yr | 1.60% |

Cumulative over 17.8 years: replication **4.11–4.24×** vs PPUT **4.54×**
(+8.25%/yr and +8.44%/yr vs +8.85%/yr).

**The decisive test is the second row.** SPX monthly options settle AM against
the opening SOQ; our first pass settled against the PM close. Changing *only
that convention* — not one byte of data — lifted correlation from 0.9847 to
0.9927 and cut tracking error by 29%. The three worst rolls are all violent
intraday-reversal expirations where open and close diverge hugely
(2010-05-21: open −2.9% below close; 2020-03-20: open **+6.0%** above close).
The residual error is *our* methodology, not the vendor's data.

The remaining −0.4%/yr drift is fully accounted for by known differences:
SPY's 0.0945% expense ratio, SPY-vs-SPX basis, and buying at the EOD mid
where Cboe buys at the 11:30–12:00 ET VWAP.

A dataset of fabricated or mis-stamped quotes cannot track an
OPRA-transaction-priced index at ρ=0.9927 across the GFC, 2020, and 2022.
**These are real quotes.**

### Provenance — resolved

No longer undocumented. The schema is a **field-for-field match with Alpha
Vantage's `HISTORICAL_OPTIONS` endpoint**, verified against a live demo
response (`IBM`, 2017-11-15): `contractID, symbol, expiration, strike, type,
last, mark, bid, bid_size, ask, ask_size, volume, open_interest, date,
implied_volatility, delta, gamma, theta, vega, rho` — same fields, same
order, including idiosyncratic ones like `mark` and `bid_size`. The parquet
adds only a derived `in_the_money`. `SPY_underlying.parquet` likewise matches
`TIME_SERIES_DAILY_ADJUSTED` exactly (`adjusted_close`, `dividend_amount`,
`split_coefficient`) and begins 1999-11-01, Alpha Vantage's SPY history.
Alpha Vantage's options history begins 2008; the parquet's first row is
2008-01-02.

So this is a commercial vendor's premium endpoint with published methodology,
mirrored twice — which improves the *quality* story and slightly worsens the
*redistribution* story. See `HUMAN_TODO.md`.

### Column-level trust

| Column | Verdict | Evidence |
|---|---|---|
| `bid`, `ask` | **Trust** | crossed quotes 0.000–0.016% of rows (worst year 2021 at 0.093%); median relative spread 1.8–4.5%; zero-bid share declines 10.6% → 5.3% as liquidity grows |
| `strike`, `expiration`, `type`, `contract_id` | **Trust** | 0 duplicate `(date, contract_id)` groups in 24.7M rows; 207/207 rolls priceable; Saturday expirations pre-Feb-2015 correctly reflect the OCC convention of the era |
| `volume`, `open_interest` | **Trust** (unverified but internally consistent) | no independent check run |
| `mark` | **Reject pre-2011** | **87.2% / 91.2% / 29.2%** of 2008 / 2009 / 2010 rows sit at ≤ $0.011 — a sentinel, not a price. Mean gap vs `(bid+ask)/2`: 85.9% / 89.8% / 28.9%, falling to ~1.4% from 2011 |
| `implied_volatility` | **Reject pre-2011, treat with suspicion after** | the value **0.01488** is a floor sentinel occupying **60.0%** of 2008 and **52.5%** of 2009 puts at 20–45 DTE (median IV in 2008 reads 0.0149 — during a VIX-80 crisis). Falls to <7% from 2011, <0.5% by 2023. Values are heavily quantized throughout: ~0.1% distinct values per row |
| `delta`, `gamma`, `theta`, `vega`, `rho` | **Reject pre-2011** | median put delta at 20–45 DTE reads **−1.0000** in 2008 and −0.9999 in 2009 — nonsense for near-money puts, and the same sentinel contamination as IV. Sign convention is correct throughout (no positive put deltas) |

### How to use it

1. **Price off `(bid+ask)/2`. Never off `mark`, never off `last`.** The
   replication above uses mid and lands at ρ=0.9927; `mark` would have
   destroyed 2008–2010 outright.
2. **Compute your own IV and greeks** from mid, spot, and a rate curve. The
   shipped ones are a lookup table with a floor.
3. **Settle monthly puts against the settlement-day open**, not the close, if
   you are comparing to any SPX-settled benchmark.
4. **Validation and research only** — this is not a source of record, and the
   licence posture (`HUMAN_TODO.md`) is unresolved. Its job is to referee the
   model pricer over 2008–2009, where nothing free competes.

### Reproducing this

`SPY_options.parquet` is 632 MB. DuckDB's `httpfs` range-reads the parquet
footer, so schema, row count, and per-column statistics can be checked
against the live release **without downloading anything** — a 1-second
`SELECT count(*)` over HTTPS returns 24,681,665. Download only when you need
a filtered scan.
