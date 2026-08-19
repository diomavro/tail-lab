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
