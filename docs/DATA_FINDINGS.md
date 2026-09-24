# Data findings ledger

**What this file is.** A one-page status board for every data source
tail-lab has actually probed: what it gives, whether it works, when that was
last verified. It is an **index, not an explanation** — the reasoning,
methodology and full detail live in `docs/DATA_SOURCING.md` (§ references in
the last column) and the schemas in `docs/DATA_CONTRACTS.md`. Read this file
to decide *what to reach for*; read those to understand *why*.

**Why it exists.** Two deep dives (2026-08-19 and 2026-08-21) probed several
dozen sources. The results are scattered across four dated sections of a
500-line document, which means the next person — or the daily agent — has to
read all of it to learn that Stooq is walled or that Yahoo started failing.
The negative half is the more valuable half: **a verified dead end is a
result, and re-chasing it is pure waste.**

**Sibling files.** `docs/DATA_FLOW.md` maps each source onto the page it
feeds and the blast radius if it dies — read it to answer "what breaks if
Cboe goes down"; read this one to answer "does Cboe work today". `docs/DISCOVERIES.md` records what we *learned* — beliefs
that turned out wrong and what changed as a result (e.g. why the backtester
prices off raw `close`, not `adj_close`). `docs/DATA_VERDICTS.md` holds the
referee reports for datasets that had to be measured against a benchmark
before they could be believed, column by column. This file is *which sources
work*; those are *what we now know* and *what may be trusted*.

**Maintenance rule.** When you probe a source, add or update a row here in
the same PR, with the date you probed it. A row without a `Verified` date is
a rumour. Sources marked ✅ were measured with a real request, not read off
a vendor's marketing page — the difference has already mattered twice
(see `marketdata.app` and `CLL`/`PUT` below).

---

## Positive findings — sources that work

### Live and already wired in

| Source | What it gives | Status | Verified | Detail |
|---|---|---|---|---|
| **Cboe strategy indices** `cdn-api.cboe.com/api/global/us_indices/daily_prices/{T}_History.csv` (moved from `cdn.cboe.com`, which froze 2026-09-23) | Real OPRA-transaction-priced put/collar/putwrite program NAVs. `PPUT` 1986→, `PPUT3M` (Tail Risk) 2004→, `VXTH` 2006→, `LTV` 2006→, `CLLZ` 1986→, `SPX` 1975→ | ✅ **Ingested** — 74,367 rows, dataset #7 | 2026-08-21 | §9.1 |
| **FRED / ALFRED** | Rates, credit, vintages (`realtime_start/end`) | ✅ Live, free key held | 2026-08-19 | §2 |
| **Cboe VX futures archive** | Per-contract VIX futures settlement, 2004→ | ✅ Live, keyless | 2026-08-19 | §2 |
| **Nasdaq earnings calendar** `api.nasdaq.com/api/calendar/earnings` | Historical earnings dates + EPS surprise, 2010→ | ✅ Live, keyless (browser UA required) | 2026-08-19 | §2 |
| **Nasdaq historical** `api.nasdaq.com/api/quote/{SYM}/historical` | Daily OHLCV, **~2,513 rows (~10 yr) hard cap**, split-adjusted only. **Primary for dataset #1** | ✅ **Wired** — keyless, browser UA + JSON Accept; `assetclass` must be `etf` or `stocks` (400s on the wrong one) | 2026-08-21 | contracts #1 |

### Live, free, not yet used

| Source | What it gives | Status | Verified | Detail |
|---|---|---|---|---|
| **Cboe delayed quotes** `.../delayed_quotes/options/_SPX.json` | Full SPX chain — **30,842 contracts**, bid/ask/sizes, IV, OI, all five greeks, theo | ✅ Live, keyless, 13.7 MB. **Time-sensitive: forward collection only** | 2026-08-21 | §9.2 |
| **Cboe vol complex CSVs** | `VIX`/`VIX3M`/`VIX9D`/`VVIX`/`SKEW`, OHLC. `SKEW` from **1990** | ✅ Live, keyless — *and the fix for the Yahoo failure below* | 2026-08-21 | §2, §9.3 |
| **Minneapolis Fed MPD** `.../banking/mpd/mpd_stats.csv` | Official Breeden-Litzenberger risk-neutral density stats for S&P 500 (`sp12m`): mu/sd/skew/kurt, p10/50/90, P(±20%). **2007-01-12 → 2026-08-19, 821 weekly obs** | ✅ Live, keyless | 2026-08-21 | §9.3 |
| **Hao Zhou VRP dataset** | Monthly implied variance, **realized variance from 5-minute S&P returns**, VRP. **1990 → Dec 2024** | ✅ Live, free (Google Drive — mirror it) | 2026-08-21 | §10.2 |
| **fja05680/sp500** | Point-in-time S&P 500 constituents, 1996→ | ✅ Live, MIT | 2026-08-19 | §4 |
| **Cboe methodology PDFs** (PPUT, VXTH) | The exact index rules — needed to replicate them | ✅ Live, free | 2026-08-21 | §9.1, §10.3 |
| **DoltHub `post-no-preference/options`** | Free chains, ~2,098 underlyings, 2019→ | ✅ Free (`dolt clone`) | 2026-08-19 | §3 |

### Gated — free, but needs a human step

| Source | What it gives | Gate | Detail |
|---|---|---|---|
| **lambdaclass `data-v1`** GitHub Release | **SPY 2008–2025** (632 MB, 24.7M rows), QQQ 2011–2025, IWM 2008–2025 EOD chains, SHA-256 pinned | ✅ **Validated 2026-08-21** — tracks Cboe `PPUT` at **ρ=0.9927, TE 1.63%/yr** over 207 monthly rolls. Quotes trustworthy; `mark`/IV/greeks are sentinel-filled pre-2011. Provenance resolved: **Alpha Vantage `HISTORICAL_OPTIONS`**. Remaining gate is the **licence call** in `HUMAN_TODO.md` | `docs/DATA_VERDICTS.md`, §10.1 |
| **optionsDX** | SPX/SPY/QQQ/VIX EOD chains **2010–2023**, bid/ask + IV + greeks | Free account | §9.2 |
| **historicaloptiondata.com free data** | Full L2 EOD chains, **Jan 2003 →**, one rotating symbol/month (DIA Dec 2008, RUT Jan 2009) | Name + email | §9.3 |
| **historicaldata.net** | Free 2013 archive (Jan–Jun, 1.8 GB), broad universe, L2 fields | Download | §9.2 |
| **Tiingo** | 30+ yr adjusted daily, 500 symbols/month | Free key | §2 |

---

## Negative findings — verified dead ends, do not re-chase

| Source / idea | What we found | Verified |
|---|---|---|
| **Yahoo chart endpoint** | **429s from residential IPs too**, not just datacenter. Previously believed "residential may work". ✅ **Resolved 2026-08-21** — all three adapters (`vix.py`, `ohlcv.py`, `options_expiry.py`) now resolve an ordered source chain and Yahoo is the *fallback* in each, no longer the primary | 2026-08-21 |
| **Wayback Machine as a chain archive** | Exactly **3 captures ever** of the Cboe SPX chain JSON (2021-07-20, 2022-12-31, 2023-03-26). Cannot backfill forward-collection | 2026-08-21 |
| **Stooq** | JS proof-of-work wall on symbol CSV (`spy.us`, `leh.us`) *and* the bulk `/db/h/` page is an HTML shell | 2026-08-21 |
| **marketdata.app "15+ years free"** | Marketing copy. Their own plan-limits doc: **Free Forever = 1 year**, Starter = 5 years. Claim appears only in third-party listicles | 2026-08-21 |
| **QuantConnect / AlgoSeek** | US **Index** options start **January 2012**; equity options 2010–2012. Worse than optionsDX for our window, and cloud-first | 2026-08-21 |
| **`SPXT` (S&P total return) on the Cboe CDN** | **HTTP 403 AccessDenied.** Blocks exact PPUT inversion — dividend leg must be reconstructed (FRED) | 2026-08-21 |
| **Cboe per-day option summary CDN paths** | **403.** Only `daily_prices`, `delayed_quotes`, futures archive and `volume_and_call_put_ratios` trees are public | 2026-08-21 |
| **Cboe's free "historical options data" page** | Aggregate **volume and put/call ratios only**, frozen 2019-10-04. No per-series prices | 2026-08-21 |
| **QuantPedia curated data list** | Nothing free for options except ORATS, whose free tier is not historical bulk | 2026-08-21 |
| **GitHub / archive.org / Hugging Face bulk sweeps** | Nothing beyond what is already listed here | 2026-08-21 |
| **Practitioner/self-starter trading books** | **No secret source.** Spitznagel gives no data or strategy detail; Sinclair and Krishnan name nothing new. The public tail-hedge debate was settled with the free data above | 2026-08-21 |
| **Vendor headers vs reality** | `CLL`'s CDN file starts **2008-08-26** and `PUT`'s **1991-03-04**, not 1986 as the index is quoted elsewhere. Use `CLLZ`/`PUTY` for long history. *Caught only by ingesting and reading back* | 2026-08-21 |

---

## Paid options — all now off the critical path

Recorded so the decision is not re-opened without new information.

| Vendor | Price | Verdict |
|---|---|---|
| **ORATS** | $99/mo | **Not needed for evaluation.** Its case is the *signal* side (full-universe cross-sectional chains), not put backtesting. Re-decide only after the model-vs-PPUT residual is measured |
| **historicaloptiondata.com** | $945 / $1,495 one-off | Superseded — the free rotating-symbol programme reaches 2003, and §10.1 covers 2008 |
| **Cboe DataShop `optsum`** | cart-priced | Real (SPX/OEX/VIX EOD, 2005–2019-09-30, with bid/ask) but paid; exchange-grade fallback if provenance ever becomes critical |
| **Sharadar Prices** | $9/mo | **Still live as a decision** — but it addresses survivorship/PIT constituents/broad-universe OHLCV (G2/G3/G4), *not* puts. Unaffected by §9–§10 |

---

## Cross-validation results

Independent agreement between two sources is the only cheap evidence we can
get that either is telling the truth. Recorded here when measured.

| Pair | Window | Result |
|---|---|---|
| **Nasdaq vs Yahoo**, SPY `close` | 1,253 overlapping days to 2026-08-20 | **max absolute difference 0.000029** — effectively identical. The raw price series is trustworthy from either feed |
| **Nasdaq vs Yahoo**, SPY `adj_close` | same | max **$29.62**, mean **$14.00**; total return over the overlap **+82.43%** (Yahoo, dividend-adjusted) vs **+70.50%** (Nasdaq, split-only) — a **11.93pp** gap that is entirely the dividend stream. Do not compare partitions across sources |
| **Cboe `PPUT` vs a model-priced put roll** | 2004→ | *Not yet run* — queued in `AGENT_TODO.md`; also the referee for §10.1's unknown-provenance chains |

---

## The one-line summary

For **evaluating** a put-buying tail strategy, the free path is complete:
Cboe's own transaction-priced indices are the benchmark (1986→, ingested),
optionsDX covers 2010–2023 real chains, and §10.1 covers 2008–2009 pending
validation. The Yahoo dependency is closed — every adapter now falls back
rather than fails. **The binding constraint is now a research question, not
a plumbing one: nobody has yet measured the model-priced backtest against
PPUT.**
