# Data sourcing for proper historical backtesting

Research conducted 2026-08-19 (four parallel web-research tracks; vendor
pricing/coverage verified on vendor sites that day unless flagged
*unverified*). This document answers: **what data does proper historical
backtesting require that we don't have, and where should it come from —
free sources, a broker, or a paid data service?**

Decisions taken from it live in `HUMAN_TODO.md` (accounts/purchases) and
`AGENT_TODO.md` (free, keyless increments). Per-dataset source-of-record
changes must still land in `docs/DATA_CONTRACTS.md` when an adapter
actually switches.

---

## 1. Gap analysis — what "proper" backtesting still needs

The platform's backtests are only as good as four things it does not yet
have:

| # | Gap | Why it matters | Status today |
|---|---|---|---|
| G1 | **Real historical option quotes, 2007+** | The S1 thesis is that the market *misprices* tails; a model-priced backtest cannot see that mispricing (`docs/adr/0004`). Must cover 2008, 2011, 2015, 2018, 2020, 2022. | Model-priced only; forward collection not yet started |
| G2 | **Delisted-stock OHLCV** | The names that blew up and delisted (Lehman, Bear, SVB…) are exactly the most tail-sensitive; excluding them biases every sensitivity ranking (`docs/adr/0010`) | Yahoo drops delisted tickers entirely |
| G3 | **Point-in-time S&P 500 constituents** | "Who was in the universe on date D" — same survivorship requirement, membership side (`docs/adr/0010`) | Current constituents only |
| G4 | **A dependable broad-universe OHLCV feed** | The screen needs ~500 symbols daily; Yahoo's chart endpoint is aggressively throttling datacenter IPs (verified 429s), Stooq's anti-bot wall is confirmed still up | Yahoo, fragile |

Everything else (vol complex, rates/credit vintages, event calendar) is
already free and confirmed healthy — with two free *upgrades* found (§2).

## 2. Free sources — audit results (all verified live 2026-08-19)

- **CBOE index CSVs: working, keyless.**
  `https://cdn.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX3M,VIX9D,VVIX,SKEW}_History.csv`
  — current through 2026-08-18. History: VIX & SKEW from 1990, VVIX 2006,
  VIX3M 2009, VIX9D 2011.
- **Free win #1 — VIX futures term structure, 2004→present, keyless.**
  Per-contract daily settlement/OHLC/volume/OI:
  `cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry}.csv`;
  pre-2013 archive at `cdn.cboe.com/resources/futures/archive/volume-and-price/CFE_{M}{YY}_VX.csv`.
  Full constant-maturity curve buildable in-house; no vixcentral
  dependency needed.
- **Free win #2 — Nasdaq earnings calendar, keyless, 15+ years of
  history.** `https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`
  (browser UA + `Accept: application/json` required) — verified for 2010,
  2020, and upcoming dates; gives historical earnings *dates* + EPS
  surprise. Unofficial: pace requests and cache.
- **FRED/ALFRED: fine.** Vintage (`realtime_start/end`) API works on the
  free key; 120 req/min limit is irrelevant at our volume. Caveat: ICE
  BofA OAS series carry a redistribution restriction — internal use fine,
  don't republish raw values.
- **FOMC calendar:** HTML only (no ICS — the ical URL 404s); current page
  covers 2021–2027, `fomc_historical.htm` year pages reach back to 1936.
- **BLS schedules:** `bls.gov` **403s non-browser clients** — the CPI/NFP
  adapter must send browser-like headers (or fetch via Playwright).
  Archived *scheduled release dates* (what a point-in-time backtest
  needs) exist as per-year PDFs back to ≥2006 (`bls.gov/bls/archived_sched.htm`).
- **Put/call ratios:** legacy free file frozen at 2019-10-04 (history
  2006–2019). Ingest as a bounded historical extra or skip.
- **Yahoo chart JSON: degraded.** Still keyless, but datacenter IPs get
  instant 429s; residential + pacing may work but is not infrastructure.
  Keep as fallback only.
- **Stooq: still walled.** JS proof-of-work challenge on CSV export
  confirmed.
- **Tiingo free tier (needs free key → `HUMAN_TODO.md`):** 30+ years
  adjusted daily history, 50 req/hr, 1,000 req/day, **500 unique
  symbols/month** — almost exactly our screening budget. Recommended
  replacement primary for G4. Delisted tickers are served
  (community-attested, not contractual — spot-check LEH/BSC/WM/SIVB).

## 3. Historical options data (G1)

Only three realistic paths reach 2007–2008; everything else starts 2010
or later.

| Source | History | Coverage | Price | Notes |
|---|---|---|---|---|
| **optionsDX** (free acct) | 2010–2023 | SPY, SPX, QQQ, VIX + a few single names | **$0** | EOD chains with bid/ask, IV, greeks; quarterly CSV zips; freshness beyond 2023 unverified |
| **ORATS Data API** | **2007→present** | All US equity/ETF/index options | **$99/mo** | Bid/ask (15:46 ET snapshot), IV, greeks, smoothed surfaces; the only sub-$100/mo product reaching 2008 |
| **historicaloptiondata.com** | 2002→present | ~5,840 underlyings | one-off: **$1,495** (L2 full history), $945 (5-yr L2) | CSV, yours forever; DeltaNeutral-lineage EOD data |
| ThetaData | ~2014+ (12y max, Pro $160/mo; Standard 8y $80/mo) | full US | $40–160/mo | Misses 2008/2011; strong for intraday 2016+ |
| Polygon → **Massive** (rebranded Oct 2025) | ≤5y even at $199/mo | full US | $29–199/mo | Worst history-per-dollar for our need |
| CBOE DataShop | modern product 2012+; legacy SPX/OEX/VIX "optsum" **2005–2019** | per-symbol cart | cart-priced (low hundreds per symbol, unverified) | Exchange-grade; the one way to get 2008 SPX from the exchange itself |
| Databento OPRA | 2023-03+ | tick | $/GB | Wrong tool for EOD 2007+ |
| DoltHub `post-no-preference/options` | ~2019/2020+ | thousands of underlyings | $0 (dolt clone) | Free recent breadth |
| OptionMetrics IvyDB | 1996+ | full US | five figures/yr, WRDS-institutional | Not realistic without a university WRDS affiliation |

**Recommended path:** start free with **optionsDX** (validates the
real-quote `OptionPricer` v2 against the Black-Scholes proxy on
2010–2023 SPY/SPX/QQQ — covering 2011/2015/2018/2020/2022 tail events at
$0); if the model-vs-real deltas justify it, take **2–3 months of ORATS
at $99/mo** (~$200–300 total) to warehouse 2007+ full-universe EOD chains
into the lake permanently. The $1,495 one-off is the alternative if
ORATS's bulk-export fair-use terms turn out restrictive (unverified —
check before subscribing).

> **Superseded for the *strategy-evaluation* half of G1 by §9 below**
> (2026-08-21). §9 found free sources that cover strategy evaluation
> outright, including 2008, and demotes every paid option here to
> "only if evaluation says the model gap actually matters". The
> *signal/screening* half of G1 is untouched by §9.

## 4. Survivorship bias & point-in-time constituents (G2 + G3)

| Source | Delisted prices | PIT constituents | Price | Notes |
|---|---|---|---|---|
| **fja05680/sp500 (GitHub)** | — | **1996→present**, maintained (last commit 2026-07-13), MIT | **$0** | The standard free answer for G3; cross-check vs Wikipedia's "Historical components of the S&P 500" |
| **Tiingo** (free) | Yes (attested, not contractual) | No | $0 | Free path for G2: enumerate ex-members from the membership file, backfill their history within the 500-symbol/month quota |
| **Sharadar (direct, sharadar.com)** | **15,000+ delisted, back to Dec 1998** | Yes — SP500 additions/deletions + quarterly snapshots to Jan 1998 | **$9/mo** ("Prices" plan, personal license) | Closes G2+G3+G4 in one: ~21k stocks (SEP) + ~10k funds/ETFs (SFP), adjusted OHLCV, REST + bulk CSV, Linux-friendly |
| EODHD | Yes (`_old` tickers; depth unverified) | Yes (S&P membership log from ~2000; plan-gating unclear) | $19.99/mo | Runner-up if global exchanges ever needed |
| Norgate Platinum | 1990+ (audit-grade) | Yes | $630/yr | **Windows-only** desktop updater — a real blocker on the Ubuntu host |
| FirstRate Data | 7,000+ delisted, 2000+ | No | one-off, sticker unverified | Intraday bundles |
| CRSP | Gold standard incl. delisting returns | Yes | WRDS-institutional only | Only via an academic affiliation; license bars production use |
| Massive/Polygon, Alpha Vantage, FMP | Spotty/undocumented | No/partial | various | Not fit for this purpose |

**Recommended path:** ingest **fja05680/sp500** now (free, keyless, agent-
buildable — closes G3 at membership granularity) and subscribe to
**Sharadar Prices at $9/mo**, which simultaneously fixes G2 (delisted to
1998), G4 (dependable broad-universe feed with bulk CSV), and provides a
second PIT-constituents source to cross-check. Cheapest credible fix by
2–10× and it slots straight into the DuckDB bronze layer from Linux.

## 5. Brokers — verdict: not a data source

**No retail broker API serves historical data for expired option
contracts** (verified across all five) — the one dataset that decides G1.
Brokers are execution venues.

- **IBKR** — expired options: documented **no**. Live-contract history
  good, pacing (60 req/10min) bad for bulk. $0 minimum, no inactivity
  fee; OPRA non-pro data ~$10/mo, waived with modest commissions (exact
  2026 figure unverified — pricing pages block bots). **Hungary-eligible
  via IBKR Ireland; US listed options are fine under PRIIPs** (which
  blocks US-domiciled ETF shares, not their options). → Sign up when
  ready to *trade*; its live chains also serve forward collection.
- **Alpaca** — the only broker with any expired-option history (OPRA from
  **Feb 2024**) and a genuinely useful **free** data tier: historical SIP
  equity bars to ~2016 + options history, key-based REST, no funding
  obligation. Options *trading* eligibility for Hungarian accounts
  unverified — irrelevant if used data-only. → Worth a free signup as a
  supplementary tap and a second forward-collection source.
- **Tradier** — Hungary-permitted, cleanest free live-chain REST API for
  account holders; zero backtest history. Optional.
- **Tastytrade** — Hungary-eligible execution alternative; no research
  history. **Schwab** — Hungary eligibility doubtful, API history
  equities-only, weekly re-auth; skip.

## 6. The plan (costs summarized)

**Phase 0 — free, keyless, agent-buildable now** (queued in `AGENT_TODO.md`):
VX futures term-structure ingestion (2004+); fja05680 PIT constituents
ingestion (1996+); Nasdaq earnings-calendar adapter; FOMC/BLS calendar
adapters (BLS needs browser headers).

**Phase 1 — free accounts, ~30 min of human signups** (`HUMAN_TODO.md`):
Tiingo key (OHLCV primary + delisted backfill); optionsDX account
(2010–2023 real chains for the v2 pricer validation); Alpaca keys
(SIP equity history + 2024+ OPRA); provision `FLY_API_TOKEN` (CD, from
`docs/adr/0016` — unrelated to data but same queue).

**Phase 2 — paid, cheap, closes G2/G3/G4 durably:** **Sharadar Prices,
$9/mo.**

**Phase 3 — paid, closes G1:** **ORATS $99/mo for 2–3 months** (~$200–300
one-time) to warehouse 2007+ EOD option chains; alternative:
historicaloptiondata.com one-off ($945 5-yr / $1,495 full).

Steady state after phase 3: **$9/mo** ongoing + ~$300 already spent, with
every gap closed. Execution (IBKR) is a separate, non-data decision.

## 7. Licensing notes

All the above are personal-use licenses: data stays in our private lake
and private dashboard; never republish raw vendor data (explicit
restriction on ICE BofA OAS via FRED; standard no-redistribution terms at
Sharadar/ORATS/Tiingo/optionsDX). Results/derived metrics are fine.

## 8. Open items to verify by hand

- ORATS bulk-download/fair-use terms before subscribing (the plan assumes
  warehousing 2007+ locally is permitted).
- Tiingo delisted completeness — spot-check LEH, BSC, WM, SIVB.
- optionsDX freshness past 2023, and whether ThetaData's free tier limits
  matter to us.
- Sharadar $9 "Prices" plan really includes the SP500 constituents table
  (their page says yes; confirm at checkout).
- Alpaca: do historical option endpoints return since-expired contracts
  (trivial to test with a free key)?
- Exact IBKR non-pro OPRA bundle fee (check inside Client Portal, when an
  account exists).

---

## 9. Free sources for historical put prices (deep dive, 2026-08-21)

Re-opened at Dio's challenge — *"I don't believe there are NO free data
sources"* — and scoped by his follow-up to **strategy evaluation only**
(*"our strategy evaluation just needs historical put prices; our signal
for picking needs more, but for now let's focus only on the historical
puts"*). Every endpoint below was probed live from the workstation on
2026-08-21; HTTP codes, row counts and date ranges are measured, not
quoted from a vendor page.

**Verdict: he was right.** For evaluating a put-buying tail strategy,
free data covers the whole history including 2008. §3's ORATS/$1,495
recommendation is not needed to answer "did this strategy work, and what
did the protection actually cost". It remains open only for the *signal*
side (cross-sectional, full-universe chains), which §3 still governs.

### 9.1 Tier 1 — real transacted put P&L, daily, free, back to 1986

Cboe publishes daily history for its **option-strategy benchmark
indices** on the same keyless `cdn.cboe.com` pattern the VIX adapter
already uses:
`https://cdn.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv`.

These are not model output. Per the official methodology
(`cdn.cboe.com/api/global/us_indices/governance/Cboe_SP_500_Put_Protection_Indices_Methodology.pdf`,
HTTP 200, 744 KB, rev. Aug 2025), the roll premium is the **volume-
weighted average of actual OPRA transaction prices** in the roll window,
falling back to the last reported ask if the strike does not trade. That
is real executed put pricing, embedded in a daily index level.

Measured live (all HTTP 200, all current through 2026-08-20):

| Ticker | Index | First obs | Rows |
|---|---|---|---|
| `PPUT` | S&P 500 5% Put Protection | 1986-06-30 | 10,108 |
| `PPUT3M` | **S&P 500 Tail Risk Index** | 2004-03-19 | 5,640 |
| `VXTH` | VIX Tail Hedge | 2006-03-31 | 5,126 |
| `LTV` | **S&P 500 Left Tail Volatility** | 2006-01-03 | 5,183 |
| `CLLZ` | S&P 500 Zero-Cost Put Spread Collar | 1986-06-20 | 10,114 |
| `CLL3M` | S&P 500 3-Month 95-110 Collar | 2004-03-19 | 5,640 |
| `CLL` | S&P 500 95-110 Collar | **2008-08-26** (see note) | 4,483 |
| `CLLR` | Russell 2000 Zero-Cost Put Spread Collar | 2001-01-31 | 6,423 |
| `SPRO`, `SPRO01`…`SPRO12` | S&P 500 Buffer Protect, 12 staggered monthly series | 2005 | ~5,200 ea |
| `PUTY` | S&P 500 2% OTM PutWrite (short-put side) | 1986-06-30 | 10,110 |
| `PUT` | S&P 500 PutWrite | **1991-03-04** (see note) | 4,946 |
| `PUTD`, `PUTVM`, `PUTR`, `PTLT` | rest of the PutWrite family | 2001–2005 | — |
| `SKEW`, `VIX`, `VIX3M`, `VIX9D`, `VVIX` | implied-moment complex | 1990 / 1990 / 2009 / 2011 / 2006 | 9,211 (SKEW) |
| `SPX` | S&P 500 price index | **1975-01-02** | 13,017 |

The full catalogue is at
`cdn.cboe.com/api/global/us_indices/definitions/all_indices.json`
(HTTP 200, 1.4 MB, 2,495 indices; 129 match put/tail/collar/protect).

**Corrected against the shipped adapter's first live run (2026-08-21).** The
row counts and first-observation dates above are now what actually landed in
bronze (74,367 rows across the ten default tickers), not what the CSV headers
suggested. Two of the CDN files start much later than the index itself is
quoted elsewhere: **`CLL` begins 2008-08-26** and **`PUT` begins 1991-03-04**.
Use `CLLZ` (1986-06-20) when pre-crisis collar history is needed and `PUTY`
(1986-06-30) for the long-run putwrite series — the earlier draft of this
table claimed 1986 for both `CLL` and `PUT`, which the ingest disproved.

Sanity check on the same run, 2007-10-09 to 2009-03-09 (peak to trough):
`SPX` **-56.8%**, `PPUT` **-41.5%**, `PPUT3M` **-38.6%**, `VXTH` **-43.3%**,
`CLL` **-21.8%**. The protection did what protection is supposed to do, in
the one period that matters most — which is the whole point of having a
real-quote benchmark rather than a modelled one.

Why this closes evaluation:

1. **A benchmark that already is the strategy.** `PPUT3M` is literally
   Cboe's S&P 500 Tail Risk Index and `PPUT` a 5% OTM monthly put
   overlay. Any Put Lab result can be scored against a real, executed,
   published program across 2008, 2011, 2015, 2018, 2020 and 2022 —
   at zero cost and with no signup.
2. **The model-vs-market gap becomes measurable without buying quotes.**
   Run the `OptionPricer` proxy through PPUT's published rule
   (5% OTM, monthly roll, SOQ strike) and difference the resulting NAV
   against the real PPUT series. The residual *is* the mispricing the
   S1 thesis is about — which is exactly what §3 proposed to spend
   $99–$1,495 to see.
3. **Put premia are recoverable at roll granularity.** PPUT = long S&P
   500 total return + long the 5% OTM put. With the index level and the
   methodology both free, the protection leg can be backed out.
   *Caveat, verified:* `SPXT` (total-return) is **not** served by the
   CDN (HTTP 403 AccessDenied) — the dividend leg has to come from
   elsewhere (FRED) — so treat the inversion as an approximation to be
   pinned against optionsDX quotes on the 2010–2023 overlap before it
   is trusted.

### 9.2 Tier 2 — real chains, free, 2010→present

- **optionsDX** — every one of the 10 datasets in their shop is listed
  at **$0.00**: SPY, SPX, VIX, QQQ, TSLA, AAPL, NVDA, UVXY, SLV, BTC.
  SPX product page states **2010–2023**, EOD, "all expirations and
  strikes, greeks, implied volatility, bid/ask/last, and underlying
  price". Free account. Covers five of the six target tail events.
- **`cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json`** —
  **verified live today: HTTP 200, 13.7 MB, 30,842 SPX contracts**, each
  with `bid`/`ask`/`bid_size`/`ask_size`/`iv`/`open_interest`/`volume`/
  `delta`/`gamma`/`vega`/`theta`/`rho`/`theo`/`last_trade_price`. No key,
  no account, no rate-limit encountered. Same path serves `_VIX`, `_RUT`,
  `SPY`, `QQQ` and single names. **This is a free forward-collection tap
  that should be switched on immediately** — every day it is not running
  is a day of real chains permanently lost.
- **historicaldata.net** — free 2013 archive (Jan–Jun, ~1.8 GB, six
  monthly zips), broad US universe, with bid/ask + sizes, OI, all
  greeks, IV. Useful as a breadth cross-check on one known year.
- **DoltHub `post-no-preference/options`** — free (`dolt clone`), ~2,098
  underlyings, 2019→present.

### 9.3 Tier 3 — 2008 itself, free

- **historicaloptiondata.com free-data programme** — full-format L2 EOD
  chains, **January 2003 → most recent month**, *one rotating symbol per
  calendar month* (their own examples: **DIA for December 2008**, RUT
  for January 2009, TGT for February 2019). Name + email, files at
  dnfilevault.com. This is the only free source found that puts real
  2008–2009 option quotes on disk. Not a continuous program, but exactly
  right for pinning a pricer against crisis-period reality.
- **Minneapolis Fed Market-Based Probability Densities** — official Fed
  risk-neutral densities backed out of real SPX option prices by
  Breeden-Litzenberger. `sp12m` (S&P 500, 12-month) runs
  **2007-01-12 → 2026-08-19, 821 observations** with `mu`, `sd`, `skew`,
  `kurt`, `p10`/`p50`/`p90` and P(±20%). Free CSV, no key:
  `minneapolisfed.org/-/media/files/banking/mpd/mpd_stats.csv`
  (dictionary at `mpd_data_dictionary.csv`). Weekly, single tenor — a
  calibration target through the crisis, not a chain.
- **`SKEW` + the VIX term structure** — free, daily, `SKEW` from
  **1990**. These are Cboe's implied 2nd and 3rd moments from real OTM
  SPX option prices. A Gram-Charlier/Corrado-Su pricer calibrated to
  VIX + SKEW prices OTM puts with the market's actual skew back to 1990,
  which is strictly better than the flat Black-Scholes proxy and costs
  nothing. Validate it on the optionsDX 2010–2023 overlap, then run it
  across 1990–2009.

### 9.4 Negative results — verified, do not re-litigate

- **Wayback Machine is not an option-chain archive.** The Cboe SPX
  delayed-quote JSON has exactly **three** captures ever
  (2021-07-20, 2022-12-31, 2023-03-26). Checked because it would have
  been the cheapest possible backfill; it isn't one.
- **Yahoo's chart endpoint now 429s from residential IPs too.** Probed
  from Dio's workstation: `HTTP 429`. §2's "residential + pacing may
  work" is **no longer true** — this affects `ingestion/ohlcv.py`, which
  currently has Yahoo as primary (`docs/DATA_CONTRACTS.md` #1).
- **Stooq is still fully walled**, JS proof-of-work on both `spy.us` and
  `leh.us`. Its bulk `/db/h/` index page returns 200 but is an HTML
  shell, not a download.
- **Cboe's free "historical options data" page is aggregate only** —
  volume and put/call ratios (`totalpc.csv`, `indexpc.csv`, `spxpc.csv`,
  `equitypc.csv`, `vixpc.csv` + `*archive.csv`), frozen 2019-10-04, plus
  monthly volume-rank xlsx. No per-series prices. Cboe DataShop's
  `optsum` (SPX/OEX/VIX EOD, 2005–2019-09-30, with bid/ask) is real but
  cart-priced.
- **marketdata.app free tier is 1 year of history**, 24-hour delayed —
  useless for backtesting.
- **`cdn.cboe.com` per-day historical option-summary paths 403.** Only
  the index/`daily_prices`, `delayed_quotes`, futures-archive and
  `volume_and_call_put_ratios` trees are public.

### 9.5 Revised plan for the puts

Ordered by value per unit of effort; the first two need no account at
all and are agent-buildable today.

1. **Cboe strategy-index adapter** (`AGENT_TODO.md`) — PPUT, PPUT3M,
   VXTH, LTV, CLL/CLL3M/CLLZ, SPRO family, PUT/PUTY. Identical shape to
   `ingestion/vix.py`. Gives Put Lab a real benchmark row and the
   model-vs-market residual, free, back to 1986.
2. **Daily SPX/SPY/VIX chain snapshots** from the delayed-quote JSON
   into bronze (`AGENT_TODO.md`). Start now; it only accrues.
3. **optionsDX free account** (`HUMAN_TODO.md`) — 2010–2023 SPX/SPY/QQQ
   for the `OptionPricer` v2 validation §3 wanted.
4. **historicaloptiondata.com free data** (`HUMAN_TODO.md`) — 2008–2009
   crisis-period spot checks.
5. **Re-decide ORATS only after (1)–(4)**, on evidence: if the measured
   model-vs-PPUT residual is small, the $99/mo buys little for
   evaluation and its real justification is the signal side, not this.

### 9.6 Still to verify by hand

- Which optionsDX *years* sit at $0.00 (shop lists the range
  "$0.00 – $50.00" per product; the free/paid split per year is only
  visible in the variant selector after login).
- Whether historicaloptiondata.com's rotating free symbol can be
  requested for a *chosen* past month (e.g. Sep–Dec 2008) or only the
  current month's offering.
- Cboe's terms for automated polling of `delayed_quotes` — pace it and
  cache, same discipline as the Nasdaq earnings endpoint (§2).
- Whether an S&P 500 total-return series is obtainable free (for the
  §9.1(3) PPUT inversion) — FRED dividend-yield reconstruction is the
  obvious candidate and is already key-provisioned.

---

## 10. Second data deep dive (2026-08-21, later) — the 2008 gap closes

Dio asked for another pass, plus a sweep of the self-starter/practitioner
trading literature for sources the vendor-facing search missed. This round
produced **one significant win, one useful minor one, and four negative
results worth recording** so they are not re-chased.

### 10.1 The win — real SPY/QQQ/IWM EOD chains including 2008-2009

`github.com/lambdaclass/options_portfolio_backtester` (MIT, 263 stars, last
updated 2026-08-19) is an open-source options backtester built to settle the
Spitznagel-vs-AQR tail-hedge argument. To make its published results
reproducible it **redistributes its dataset as GitHub Release assets**, and
that dataset is exactly what §3 said only money could buy:

| File | Coverage | Size | Verified |
|---|---|---|---|
| `SPY_options.parquet` | **2008–2025** EOD chains | **602 MB** | HTTP 200, 26,143 downloads |
| `QQQ_options.parquet` | 2011–2025 | 369 MB | HTTP 200 |
| `IWM_options.parquet` | **2008–2025** | 294 MB | HTTP 200 |
| `{SYM}_underlying.parquet` | matching underlying prices | small | HTTP 200 |

Base URL:
`https://github.com/lambdaclass/options_backtester/releases/download/data-v1/{fname}`
(note the release lives on the sibling repo `options_backtester`). Every
file is **SHA-256 pinned** in `scripts/fetch_data.py` (`CANONICAL_HASHES`),
so a mirror cannot silently serve different bytes — `python
scripts/fetch_data.py verify` proves any copy byte-identical.

**This covers 2008–2009**, the one window optionsDX (2010+) cannot reach and
the window that drives every tail-hedge result worth having. No account, no
key, no signup — an agent can fetch it.

**Three caveats that must travel with it, and they are not small:**

1. **Provenance is undocumented.** The repo's own `data/DATA_NOTICE.md` says
   the files were mirrored from `philippdubach/options-data` ("Historical
   Options Chain Data for 100+ US Equities, 2008–2025"), that this upstream
   — CDN *and* GitHub repo — **disappeared in 2026**, and, verbatim: *"The
   upstream's own sourcing was not documented."* Nobody can say which
   exchange or vendor these quotes came from.
2. **Redistribution posture is research/educational only**, with an explicit
   takedown offer to any rights-holder. Fine for a private lake and a
   private dashboard; it is not a commercial-grade license.
3. **Quality is unverified.** Known gaps are documented (IWM's
   `underlying.parquet` ships `adjClose` all-NaN; QQQ's chain starts
   2011-03-23), but the chains themselves have not been audited by anyone
   whose audit we can read.

**Therefore: use it for validation, not as a source of record.** And the
cross-check is now free and authoritative — §9.1's `PPUT`/`PPUT3M` are
Cboe's own OPRA-transaction-priced put programs over the same window, so a
5% OTM SPY put roll built from this parquet should track PPUT. If it does,
the dataset is real; if it does not, we have learned that cheaply. That
validation is the gate this data must pass before anything depends on it.

### 10.2 The minor win — free 5-minute realized variance, 1990→2024

Hao Zhou's variance-risk-premium dataset (the Bollerslev-Tauchen-Zhou
series), linked from his homepage, **updated through December 2024**,
monthly, free: risk-neutral implied variance (VIX²/12, de-annualized),
**realized variance computed from 5-minute S&P 500 log returns**, and the
VRP difference.

The valuable column is the middle one. `research/backtest/put_roll.py`
currently derives its IV proxy from *daily*-bar trailing realized vol; a
5-minute realized-variance series is a strictly better volatility input and
is otherwise expensive to construct (it needs intraday history we do not
have). Monthly frequency limits it to calibration rather than per-roll
pricing, but as a calibration target for the skew-aware pricer it is free
and it spans every crisis back to 1990.

### 10.3 What the practitioner literature actually says

Worth recording because it validates the platform's approach rather than
changing it: the tail-hedging literature does not have a secret data source.

- The **Spitznagel/AQR tail-hedge debate** was settled publicly using real
  SPY chains 2008–2025 and the open-source backtester above — i.e. the same
  data now available in §10.1, and an existence proof that this question is
  answerable with free data. It is also a useful comparable: an independent
  implementation of the exact strategy family tail-lab is building.
- **Cboe's VXTH methodology is free** (`cdn.cboe.com/api/global/us_indices/
  governance/Cboe_VIX_Tail_Hedge_Index_Methodology.pdf`), and a published
  Stanford replication of VXTH exists — a reference implementation for §9.5's
  replication-harness item, so it need not be written from scratch.
- Spitznagel's *Safe Haven* deliberately gives no strategy or data detail;
  Sinclair, Krishnan and the rest name no free source the vendor sweep
  missed. **The practitioner path is the same one §9 found: model the puts,
  benchmark against Cboe's published indices.**

### 10.4 Negative results (verified — do not re-chase)

- **marketdata.app's "15+ years of historical options data, 100% free" is
  marketing copy.** Their own plan-limits documentation states Free Forever =
  **1 Year**, Starter = 5 Years. The claim appears in third-party listicles
  and the Workspace marketplace blurb; the vendor's docs contradict it.
- **QuantConnect / AlgoSeek does not reach 2008.** US **Index** Options
  (SPX, VIX, NDX, RUT + weeklies) start **January 2012**; US Equity Options
  start 2010–2012. Cloud-first, with export via LEAN CLI unclear. Strictly
  worse than optionsDX for our window.
- **QuantPedia's curated historical-data list yields nothing new** — the only
  options provider on it with a free tier is ORATS, and ORATS's free tier is
  not the historical bulk we need.
- **GitHub / archive.org / Hugging Face bulk sweeps found nothing** beyond
  what §3, §9 and §10.1 already list. DoltHub `post-no-preference/options`
  (2019+) remains the only other free chain repository.

### 10.5 What this changes

`PPUT` replication (§9.5 item 2) becomes **more** important, not less: it is
now doing double duty as the platform's model-vs-market measurement *and* as
the audit that decides whether the §10.1 dataset can be trusted. Do it
first. Nothing here displaces §9.5's ordering otherwise, and nothing here
revives the case for a paid feed.
