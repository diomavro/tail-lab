# Agent backlog

This is the daily agent's queue — the agent owns this file: it checks off
what it ships, adds what it discovers, and re-orders items as
`docs/END_STATE.md` §5 priorities shift. Every item here must be buildable
with free/keyless resources by the agent alone; anything needing an
account, an API key, or money goes to `HUMAN_TODO.md` instead
(`docs/adr/0002`).

**Before picking an item, apply the value bar in `docs/AGENT_MISSION.md`:**
it must advance a documented research question (`docs/END_STATE.md` §4) or
make the cockpit measurably sharper. It is fine, and expected, to ship
nothing on a given day rather than pad this list with busywork.

Ordered roughly by `docs/END_STATE.md` §5's milestone sequence; the top of
the list is not a strict queue — a smaller, sharper item out of order beats
a large one strictly in order.

## Next increments

- [x] Stand up the walking skeleton: VIX ingestion adapter (`ingestion/vix.py`)
      → bronze → silver → gold (`research/vix_stretch.py` orchestrating
      `transforms/vix.py`) → one dashboard tile reading it, deployed to Fly
      (`docs/adr/0011`). **Done** — live at https://tail-lab.fly.dev
      (`HUMAN_TODO.md`); this item was already shipped but still listed
      unchecked, fixed for bookkeeping accuracy.
- [x] Add the underlying OHLCV ingestion adapter, following the VIX
      adapter's pattern (`docs/DATA_CONTRACTS.md` #1). **Done 2026-08-18
      (PR #1 merged.)** Built against Yahoo Finance's chart JSON (keyless),
      not Stooq — Stooq now serves an anti-bot challenge to plain HTTP
      clients for symbol downloads too (same as VIX); Yahoo is README's
      named keyless fallback. Adapter + tests updated to the Delta store
      (`docs/adr/0013`) at merge time.
- [x] Add a downside-beta sensitivity metric in `research/metrics/`, pinned
      by a test against a synthetic price path with a known analytic value.
      **Done 2026-08-18** (`research/metrics/downside_beta.py`) — a pure
      function (return series in, float out), so it didn't need to wait on
      the OHLCV adapter above: pinned against a hand-computable panel where
      the downside-only answer is provably different from whole-sample
      beta on the same data (proving the conditioning does real work), plus
      Hypothesis properties (linearity in the asset series, self-beta = 1).
      Not yet wired into a gold mart or the leaderboard — that's the next
      two items, and needs live OHLCV/benchmark return series to run on.
- [x] **Put Lab** — the interactive model-priced OOM-put backtester (Dio's
      redirect, 2026-08-19; `docs/END_STATE.md` §1.2/§1.5). **Done, live at
      https://tail-lab.fly.dev**: `research/backtest/put_roll.py` (engine, PR
      #4), `api/putlab_routes.py` backtest/sweep/cadence + `contracts/
      options_calendar.py` (PR #5), and the React `components/putlab/`
      dashboard (PR #6). ~5y OHLCV for spy/qqq/iwm/tsla/gld/eem ingested to
      prod. Follow-ups split out below.
- [x] **Memory layer, phase 2** — persist every Put Lab backtest as a
      verdict keyed by `(rule_hash, regime)`, `regime_only` != `confirmed`
      (`docs/adr/0015`). **Done**: `memory/store.py` (JSON-on-Tigris +
      DuckDB), the record + prior-art endpoints (`api/putlab_memory_routes.py`),
      the live regime verdict in the cockpit, and — the last piece —
      `.github/workflows/daily-verdict-sweep.yml`, a least-privilege 06:30 sweep
      that records verdicts across the universe so `run_count`/coverage
      accumulate daily. Follow-ups: DuckDB `coverage()` / `open_questions()`
      endpoints; AST/embedding "similar rule" retrieval; prereg + lineage.
- [~] Live options-expiry **cadence adapter** — replace the static
      `contracts/options_calendar.py` table with a keyless read of Yahoo's
      `/v7/finance/options` expiration dates → derived avg gap + weekly/
      monthly classification, following the ingestion adapter shape.
      **Ingestion + derivation done 2026-08-21**: `ingestion/options_expiry.py`
      (fetch/parse/validate/commit, `contracts/options_expiry.py`'s new
      per-symbol bronze dataset) + `transforms/options_expiry.py`
      (`classify_cadence` — pure, pinned + Hypothesis-tested). Sourcing note:
      the plain keyless GET this item assumed no longer works — Yahoo's
      `/v7/finance/options` now 401s ("Invalid Crumb") without a session
      cookie + crumb token, discovered live 2026-08-21 (same anti-bot
      evolution already hit and documented for Stooq). Worked around with the
      documented (and `yfinance`-proven) three-request cookie+crumb flow,
      still keyless — no account/API key — just no longer a single bare GET;
      see the module docstring. **Not done, left as a follow-up**: no
      `research/` orchestrator reads this bronze dataset yet, and
      `api/putlab_routes.py`'s `cadence_for` still reads the static table —
      swapping it for the live-derived value needs at least one accumulated
      daily snapshot first (same reasoning `downside_beta.py` followed:
      ship the pure function, wire it in once it has real data to read).
- [x] Extend the OHLCV adapter's default fetch range beyond `2y` (it fetched
      `5y` here only via an explicit `range_`), so the Put Lab's "last 4
      years" spans real history without a manual override. **Already done**
      (PR #34, "make ingest default to 5y history") — `fetch_ohlcv_raw`'s
      `range_` default is `5y`; found unchecked while picking today's item,
      fixed for bookkeeping accuracy.
- [x] Add the sensitivity-leaderboard gold mart (`transforms/marts/`) + API
      endpoint + dashboard tile — becomes the Put Lab's asset-picker entry
      point. **Done 2026-08-19** (Dio's standing directive, in-app feedback:
      "prioritize the sensitivity leaderboard so I can see put candidates
      ranked daily"). `transforms/marts/sensitivity_leaderboard.py` (pure
      rank-a-scores-table function) + `research/leaderboard.py`
      (orchestrator: reads point-in-time OHLCV for the benchmark + universe,
      computes downside beta per symbol, calls the mart) + `api/
      leaderboard_routes.py` (`GET /api/leaderboard`, flat like
      `putlab_routes.py`) + `frontend/src/components/LeaderboardTile.tsx`,
      placed above Put Lab in `App.tsx`. Note: the previously-referenced
      local `agent/sensitivity-leaderboard` branch didn't exist in this
      checkout (no drafted code found), so this was built fresh from the
      existing VIX/Put Lab patterns. One deliberate deviation from the
      literal plan above: the mart takes *already-computed* scores
      (symbol, score -> ranked table) rather than calling
      `research/metrics/downside_beta.py` itself, because `transforms/` sits
      below `research/` in the machine-checked layering
      (`pyproject.toml`'s import-linter contract) and may never import it —
      metric computation had to live in the `research/` orchestrator.
      Universe is the six names with OHLCV already ingested
      (spy/qqq/iwm/tsla/gld/eem, matching the Put Lab asset picker); a
      symbol that can't be scored yet is skipped, not fatal. Only one metric
      (downside beta) is wired in — a second sensitivity metric (item below)
      is what makes the leaderboard's per-metric tabs (`docs/END_STATE.md`
      §1.1) real.
- [x] Add the event-calendar ingestion adapter for FOMC dates
      (`federalreserve.gov`, keyless) with the `announced_at` point-in-time
      field required by `docs/DATA_CONTRACTS.md` #5. **Done 2026-09-01**:
      `contracts/event_calendar.py` (the shared `EventRow` schema every
      producer — this adapter, a future CPI/earnings adapter, and the
      manual unscheduled table — writes into) + `ingestion/fomc.py` +
      `make ingest-fomc`. Parses `fomccalendars.htm`'s HTML with a small
      regex over the page's consistent `fomc-meeting__month`/
      `fomc-meeting__date` markup (no new HTML-parsing dependency — bs4/lxml
      aren't installed and the structure is regular enough that adding one
      wasn't worth it); pinned against a real fetched panel
      (`tests/fixtures/fomc_calendar_sample.html`, the full 2024 year: one
      plain two-day meeting, four Summary-of-Economic-Projections meetings,
      and the one month-spanning case, `Apr/May` `30-1`, that a hand-written
      fixture would be tempted to skip). `event_date` is the meeting's
      *last* day (the decision/statement day, matching the Fed's own
      `monetary<YYYYMMDD>a.htm` URLs), not the first.
      **Known limitation, stated in the module docstring, not hidden**: the
      adapter has only ever scraped the page once, so it cannot recover the
      real historical announcement date for a meeting already on the page
      (the Fed typically publishes a year's calendar a year ahead). It sets
      `announced_at` to the ingestion timestamp for every row, past or
      future — conservative and point-in-time-safe (never claims earlier
      knowledge than provable, so no as-of read can leak), but it means a
      backtest simulating a date before this adapter's first live run will
      see zero FOMC events rather than the ones genuinely public by then.
      A positive-control test (`test_ingest_announced_at_never_predates_the_actual_scrape`)
      pins exactly this behavior so it can't regress silently.
      **Not yet run against prod** (no live-run credential in this
      workflow — `docs/AGENT_MISSION.md`'s "data changes" rule) and **no
      `research/`/`api/` consumer wired yet** — same ship-the-adapter-first
      precedent `rates.py`/`credit.py`/`options_expiry.py` followed: ship
      the pure adapter, wire it into the event calendar's proximity flags
      (`docs/END_STATE.md` §1.4) once a live partition exists to read.
      **Scoped narrower than the full item**: only the FOMC half of dataset
      #5; the BLS CPI adapter (needs browser-like headers per
      `docs/DATA_SOURCING.md` §2) and the manual unscheduled-event table are
      still open, and the historical `fomc_historical.htm` archive (reaches
      back to 1936) is a follow-up once the announcement-date backfill
      question above is worth solving.
- [ ] Add the CPI release-schedule adapter (BLS, keyless), same shape as
      the FOMC adapter above. Sourcing note (2026-08-19): `bls.gov` 403s
      non-browser clients — send browser-like headers; archived
      *scheduled-release-date* PDFs go back to ≥2006
      (`bls.gov/bls/archived_sched.htm`), exactly what the point-in-time
      `announced_at` rule needs.
      **Tried and blocked, 2026-09-02**: sending a full browser `User-Agent` +
      `Accept`/`Accept-Language` headers still gets a **403 Akamai bot-block on
      every path tried**, including the bare `bls.gov/` root — not just the
      schedule page. This is IP-reputation/fingerprint blocking, not a missing
      header, so it doesn't yield to the fix `docs/DATA_SOURCING.md` §2
      anticipated. Confirms that note's parenthetical: this needs Playwright (a
      real headless browser) or another rendering approach, which is a new
      dependency decision for a human, not a same-day adapter increment. Left
      unbuilt; also note the second producer landing in `event_calendar` (this
      adapter, once unblocked) must combine its write with `ingestion/fomc.py`'s
      rather than write independently — same-day bronze writes to a dataset
      already written that day silently no-op (`lake/store.py:write_bronze`),
      so two independent producers would lose whichever wrote second. See
      `ingestion/fomc.py`'s module docstring and `contracts/event_calendar.py`'s.

## Data-sourcing increments (2026-08-19 — free/keyless; see `docs/DATA_SOURCING.md`)

- [~] **Structured run logging (`docs/STANDARDS.md` §f, `docs/adr/0016`)**
      — Dio's standing directive: extremely detailed logging of everything
      automated. **Foundation done**: one `logging` configuration in the API
      composition root (`observability.configure_logging`, called from
      `api/main.py`) + `log_event` key=value helper, and the three Put Lab
      backtest endpoints (`/backtest`, `/sweep`, `/regime-verdict`) now emit a
      structured run line (asset, as-of, bronze snapshot id(s), `code_sha`,
      params, headline outputs). **Remaining**: wire the same
      `configure_logging` into CLI/ingestion entry points and have every
      ingestion run log its full `IngestResult` surface (dataset, source,
      window, fetched/valid/quarantined, snapshot id or no-op, duration) —
      new adapters should adopt `observability.log_event` from the start.
- [ ] In-cockpit **activity log** surface (end state in `docs/STANDARDS.md`
      §f): persist run records (ops blob or small Delta table — mind
      `docs/adr/0014`'s BlobStore precedent) + an API route + a dashboard
      tile listing recent automated actions (ingests with row counts,
      backtests, deploys), so "what happened while I was away" is one
      glance.
- [~] **VIX futures term-structure ingestion** (keyless, big free win):
      per-contract daily settlement CSVs
      `cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry}.csv`
      + pre-2013 archive `.../resources/futures/archive/volume-and-price/CFE_{M}{YY}_VX.csv`
      → full VX history 2004→present; build the constant-maturity curve
      in `transforms/`. New contract in `contracts/`, adapter follows the
      VIX shape.
      **Modern-path adapter done 2026-09-02**: `contracts/vix_futures.py`
      (dataset #11) + `ingestion/vix_futures.py` + `make ingest-vix-futures`.
      Scoped to the URL pattern above only, verified live to serve contracts
      expiring on or after **2013-01-16** (earlier dates 403/`AccessDenied`);
      `compute_vx_expiry` (third Friday of the following month, minus 30
      days) pinned against four real contracts fetched live 2026-09-02
      spanning 2013-2026. `default_expiries` picks a conservative 6 months
      forward — CBOE has listed at least that many consecutive months
      throughout the product's history — so the default `make` target never
      guesses past what's actually listed. OHLC's Cboe zero-fill (a no-trade
      day) is mapped to null before validation, mirroring
      `ingestion/option_chain.py`'s greeks convention; `settle` is never
      zero-filled and stays mandatory. **Not done, deliberately deferred**:
      the pre-2013 archive path is a **different URL/filename convention**
      with an apparent **10x price-scaling difference** from the modern
      series (a probed 2007 contract settled ~150-200, versus spot VIX
      trading ~15-20 the same era) — confirmed reachable but not ingested,
      since gluing two differently-scaled sources into one dataset without
      validating the scaling first would be worse than shipping half of it
      honestly. Also not done: the expiry rule has **no holiday adjustment**
      (Cboe moves a holiday-Wednesday expiry to the preceding business day;
      this adapter doesn't), and the `transforms/` constant-maturity curve —
      same ship-the-adapter-first precedent every other dataset here has
      followed. No `research/` consumer wired yet and not yet run against
      prod (no live-run credential in this workflow).
- [x] **The `transforms/` constant-maturity curve, done 2026-09-05** —
      `transforms/vix_futures.py` (`bronze_to_silver` + `silver_to_gold`).
      Interpolates the listed VX curve to one constant-30-calendar-day
      settlement per `trade_date`, bracketing the target maturity between
      the nearest listed contract expiring on/before it and the nearest
      expiring on/after it (linear interpolation on `settle` by
      days-to-expiry — the standard constant-maturity convention, not the
      variance-interpolation formula the VIX index itself uses, since this
      curve is over futures prices, not implied vols). A `trade_date` where
      every listed contract falls on the same side of the target is
      dropped rather than extrapolated — same "don't guess past what's
      honestly known" stance the adapter's own pre-2013-archive deferral
      takes. Also carries `front_settle`/`front_days_to_expiry` (the
      single nearest contract) alongside the interpolated value, since
      "how far the curve had to reach" is exactly the context a bare
      number hides — this is the first piece of the contango/backwardation
      read the module docstring and `docs/DATA_CONTRACTS.md` #11 promise.
      Pinned by hand-computable interpolation cases (including an
      exact-match case that must not divide by zero) in
      `tests/test_transforms_vix_futures.py`; pure function, no
      network/filesystem, mirrors `transforms/vix.py`'s shape exactly.
      **Not yet wired**: no `research/` orchestrator reads it (nothing has
      live VX futures bronze data yet — ingestion still needs a live run,
      which this workflow has no credential for) and it isn't yet fed into
      the regime panel (`docs/END_STATE.md` §1.3) as a term-structure
      signal alongside the VIX/credit classifier `research/regimes/
      timeline.py` already widened (`combine_regime_labels`) — a
      contango/backwardation-based escalation is the natural next
      increment once real bronze data exists to validate thresholds
      against, same as credit's `CREDIT_CALM_MAX`/`CREDIT_ELEVATED_MAX`
      were picked from real history.
- [x] **Point-in-time S&P 500 constituents ingestion** from
      `github.com/fja05680/sp500` (MIT, maintained, 1996→present; raw CSV
      over HTTPS, keyless) — closes `docs/adr/0010` at membership
      granularity. **Adapter done 2026-08-29**: `contracts/
      sp500_constituents.py` (dataset #10 in `docs/DATA_CONTRACTS.md`) +
      `ingestion/sp500_constituents.py` + `make ingest-sp500-constituents`,
      following `cboe_strategy.py`'s "refetch full history every time"
      shape so a partial fetch can never truncate a good partition. Live
      fetch on 2026-08-29 verified 2,718 observation dates, 1996-01-02 to
      2026-06-30, 487→503 tickers per row over that span — in the plausible
      range for "S&P 500" once multi-class listings (GOOG/GOOGL,
      NWS/NWSA, FOX/FOXA) are counted, so the row shape is sane by
      inspection; the row-count cross-check against Wikipedia's "Historical
      components of the S&P 500" this item asked for is **not done** and
      is the natural next step before trusting the series for research.
      One deliberate deviation from a literal per-`(date, ticker)` table:
      each row keeps the source's own full comma-joined membership list
      rather than exploding to long format (~1.3M rows for no consumer yet
      to justify it) — see the contract module's docstring for the
      "resolve latest `obs_date` <= target, then split" read pattern a
      future consumer follows. **Not yet wired**: no `research/` consumer
      reads it, and the screening universe (`contracts/options_calendar.py`)
      is still current-constituents-only — wiring the survivorship caveat
      into any result that uses it is a separate, later increment, same
      ship-the-adapter-first precedent `rates.py`/`credit.py` followed.
- [x] **Earnings-calendar adapter** via Nasdaq's keyless endpoint. **Done
      2026-09-06**: `ingestion/earnings.py` + `make ingest-earnings`,
      `EARNINGS` rows into the shared `event_calendar` dataset (#5),
      mirroring `ingestion/fomc.py`'s shape (pure parse / network fetch /
      orchestrate split, same conservative `announced_at` = ingestion
      timestamp treatment, tests pinned against a real fixture fetched live
      2026-09-06 — `tests/fixtures/nasdaq_earnings_sample.json`, 29 rows
      spanning all three `time` codes, plus a real weekend
      `rows: null` response). **Scoped narrower than the item as written**:
      the endpoint answers one calendar date per call, so this adapter
      sweeps a rolling 30-day-forward window per run (`DEFAULT_LOOKAHEAD_DAYS`)
      rather than the source's full 2010+ history — a deep historical
      backfill for research questions 3/5 is a separate follow-up (still
      open, see below), not built here; the forward window is what the
      cockpit's proximity flags (`docs/END_STATE.md` §1.4) need day-to-day.
      A single date's fetch failure is caught and skipped, not fatal
      ("retry tomorrow", per the sourcing note). One deliberate addition
      beyond the item's literal scope: `event_calendar` bronze is a shared
      dataset immutable per `(dataset, ingest_date)`, and `ingestion/fomc.py`'s
      docstring had already flagged that a second producer writing
      independently on a day the first already ran would silently no-op and
      lose its own rows — since this ships as that second producer for real,
      `ingest_earnings_calendar` now refuses (raises `RuntimeError`) rather
      than risk that silently; see the module docstring and
      `docs/DATA_CONTRACTS.md` #5. Also, following the note left on
      `contracts/event_calendar.py`'s `EVENT_TYPES` docstring ("the first
      EARNINGS adapter owns making this true"): `EventRowSchema` now has a
      `dataframe_check` enforcing that every `EARNINGS` row carries a
      `symbol`, closing a gap that was previously only documented, not
      checked (design review, PR #63). **Not yet done**: historical
      backfill (2010+, for research questions 3/5's pattern-library use);
      no `research/`/`api` consumer wired yet — same ship-the-adapter-first
      precedent every other event-calendar producer here has followed; the
      BLS CPI adapter should carry the same same-day-collision guard once
      it ships (still blocked on Akamai, see above).
- [x] **`transforms/vix_futures.py`'s empty-output branch has an
      untyped/object-dtype frame** — design-review advisory on PR #84
      (2026-09-05). **Done 2026-09-08**: `silver_to_gold`'s empty path now
      calls a new `_empty_gold_frame()` helper that builds the zero-row frame
      with the same explicit dtypes (`datetime64[ns]`/`float64`/`float64`/
      `int64`) the non-empty path produces, mirroring `ingestion/fomc.py`'s/
      `ingestion/earnings.py`'s `_empty_frame()` pattern. Pinned by a new
      `test_silver_to_gold_empty_and_non_empty_paths_share_dtypes` (asserts
      the two paths' `.dtypes` are identical, not just the column names) plus
      a dtype assertion added to the existing drop-unbracketable-dates test.
- [ ] **Switch OHLCV primary to Tiingo** — the key now exists and is
      verified (`HUMAN_TODO.md`, 2026-09-03). New adapter (500 unique
      symbols/month budget — plan symbol rotation), demote Yahoo to
      fallback, update `docs/DATA_CONTRACTS.md` #1's source section in the
      same PR. Measured: SPY 1993-01-29 → 2026-09-02 with `adjClose` and
      `divCash`, so 33 years of adjusted daily history. Auth is
      `Authorization: Token <key>`; put the key in `config.py` as
      `tiingo_api_key` mirroring `fred_api_key` (plain env name, not
      `TAIL_LAB_`-prefixed).

      **DO NOT also attempt the delisted backfill on Tiingo.** The gate this
      item used to carry — "spot-check LEH/BSC/WM/SIVB actually return data
      first" — was run on 2026-09-03 and Tiingo FAILS it. Do not re-litigate
      this; the measurements are:

      | Ticker | Result |
      |---|---|
      | LEH | HTTP **200 with zero rows** |
      | BSC | HTTP **200 with zero rows** |
      | SIVB, FRC | 404 |
      | WM | 200, but it returns **Waste Management**, not Washington Mutual |
      | SBNY | 881 rows through 2026 at $0.32 — OTC continuation, not the listed bank |

      Two of those are worse than a plain absence, which is why this needs
      saying rather than just "no":

      * **LEH/BSC return 200 with an empty list.** An adapter would record
        "no data in this period" rather than "this source lacks this name",
        so a backfill whose entire purpose is removing survivorship bias
        would silently reintroduce it — the exact failure `docs/adr/0010`
        exists to prevent.
      * **WM returns plausible prices for the wrong company.** Waste
        Management traded ~$34 through the week Washington Mutual collapsed.
        A backfill keyed on ticker alone splices one company's history into
        the other's position and nothing looks wrong. Any future delisted
        adapter must key on a permanent identifier (PERMNO/CUSIP/FIGI), not
        on a ticker, and must treat an empty-but-200 response as a hard
        error rather than as "no trading that period".

      The delisted source itself is now a purchasing decision, tracked in
      `HUMAN_TODO.md`.
- [ ] **optionsDX ingestion + real-quote pricer validation** once the
      zips are staged (`HUMAN_TODO.md` phase 1): bronze-ingest the
      2010–2023 SPY/SPX/QQQ EOD chains, add the second `OptionPricer`
      implementation (`docs/adr/0004` — this is the moment to split
      `research/option_pricer.py` into the `research/pricing/` shape), and
      publish a model-vs-real comparison so the ORATS/one-off buy decision
      (`HUMAN_TODO.md` phase 3) is made on evidence.
- [ ] (Optional, bounded) ingest CBOE's frozen equity put/call-ratio
      history 2006–2019 (`totalpc.csv`) as a regime-panel extra.
- [x] Add the FRED rates adapter (`docs/DATA_CONTRACTS.md` #3) — the FRED
      API key now exists as the `FRED_API_KEY` repo secret (`HUMAN_TODO.md`,
      done 2026-08-17), so this is unblocked. Must request the
      vintage/ALFRED-style `realtime_start`/`realtime_end` parameters and
      validate a required `vintage_date` column, per the point-in-time rule
      in `docs/DATA_CONTRACTS.md` #3 — a latest-value-only pull is a
      look-ahead bug, not a simplification.
      **Done 2026-08-22**: `contracts/rates.py` + `ingestion/rates.py` +
      `make ingest-rates` (`SERIES=` override), `Settings.fred_api_key`
      (plain `FRED_API_KEY` env, mirrors the `AWS_*` pattern). Requests
      FRED's full ALFRED vintage history so `vintage_date` is a real,
      validated per-row column, not a latest-value stand-in — confirmed the
      free key supports this (`docs/DATA_SOURCING.md` §2). One dataset,
      long-format, keyed by `(series_id, obs_date, vintage_date)`, following
      `cboe_strategy`'s multi-series shape rather than `ohlcv`'s
      one-dataset-per-symbol one, since the whole family is fetched and read
      together. **Not yet run against prod** (no live key in this
      workflow — `AGENT_MISSION.md`'s "data changes" rule) and **no
      `research/` consumer wired yet** — same reasoning `downside_beta.py`
      and `options_expiry.py` followed: ship the pure adapter, wire it into
      the regime panel once real vintage-aware rows exist in the lake. That
      wiring (`docs/END_STATE.md` §1.3, once credit also lands) is the next
      step in this dataset's life, not this PR's.
- [x] Add the FRED credit adapter (`docs/DATA_CONTRACTS.md` #4), same key
      and same vintage requirement as rates above. **Done 2026-08-26**:
      `contracts/credit.py` + `ingestion/credit.py` + `make ingest-credit`
      (`SERIES=` override), mirroring `ingestion/rates.py`'s structure
      exactly (same ALFRED-vintage fetch, same three-function split) but
      as its own module rather than shared code, matching this package's
      existing one-module-per-dataset convention (`vix.py`/`cboe_strategy.py`
      are equally close in shape and equally separate). Ingests HY OAS
      (`BAMLH0A0HYM2`) and IG OAS (`BAMLC0A0CM`); schema floor is 0.0 (an
      OAS is non-negative by construction, unlike a Treasury yield) with a
      50.0 ceiling (well above the 2008 HY OAS peak of ~19.9%) to catch
      unit errors. **Not yet run against prod** (no live key in this
      workflow) and **no `research/` consumer wired yet** — same
      ship-the-adapter-first precedent `rates.py` followed. The next step
      in this dataset's life is the item below (widening the regime
      classifier), once this adapter has a live partition to read.
- [x] Widen the regime classifier (`research/regimes/timeline.py`) from
      VIX-complex-only to also weigh credit spreads, once the FRED credit
      adapter above exists — closes the scope gap noted on the "regime-panel
      gold mart" item below. **Done 2026-09-03**: `contracts/regime.py` gained
      a second, independent threshold set for HY OAS (calm < 5.0%, elevated
      5.0-8.0%, crisis >= 8.0%, documented against the series' own history —
      2016 oil selloff / 2018 Q4 in the high-single-digits, 2020 COVID ~10.9%,
      2008 peak ~19.9%) sharing the existing VIX hysteresis engine (refactored
      to take thresholds as parameters, so the two classifiers are one engine,
      not two copies), plus `combine_regime_labels` (most-severe-wins — credit
      stress can only escalate the VIX view, never talk it down).
      `research/regimes/timeline.py:load_credit_oas` is the platform's first
      research-layer consumer of an ALFRED vintage dataset: for each
      `obs_date` it resolves to the latest `vintage_date`, which is safe
      without extra as-of filtering because every vintage in the resolved
      bronze partition already predates that partition's own `ingest_date`
      (pinned by a positive-control point-in-time test mirroring the VIX
      timeline's). `compute_regime_timeline` combines VIX and (ffill-aligned,
      causal) credit labels per day, and **falls back to the VIX-only label
      whenever no `credit` bronze partition exists** — same
      ship-the-adapter-first / graceful-fallback precedent `research/cadence.py`
      set for `options_expiry` — so production behavior is unchanged today (no
      environment has ever run `make ingest-credit`) and becomes credit-aware
      with no further code change the moment it does. `docs/DATA_FLOW.md` §3.2
      and `docs/DATA_CONTRACTS.md` #4 updated in the same PR, mirroring §3.1's
      write-up for the same not-yet-ingested state.
- [x] Write the first adversarial point-in-time test for the as-of read
      path — construct a scenario where leaking a later bronze snapshot
      (a restated value, or a later-ingested date) would change what an
      as-of read returns, and assert it doesn't (`docs/adr/0009`). **Done**
      — `tests/test_lake_store.py::test_no_look_ahead` and
      `::test_restated_value_does_not_leak_into_earlier_asof_read` cover
      this against `LakeStore.read_bronze_as_of`, the current (pre-backtest
      -engine) location of the as-of primitive `docs/STANDARDS.md` notes as
      the stand-in for the not-yet-built `lake/asof.py`. Re-verify this
      item once an actual backtest engine reads through a dedicated
      `lake/asof.py`, per the `docs/STANDARDS.md` note on end-state paths.
- [x] Add the Black-Scholes put pricer behind the `OptionPricer` protocol,
      pinned against the closed-form BS put formula for a hand-computable
      (S, K, T, r, σ). **Done** — `research/option_pricer.py`
      (`OptionPricer` ABC + `BlackScholesPricer`), tested in
      `tests/test_research_option_pricer.py` (pinned case) and
      `tests/test_research_option_pricer_properties.py` (Hypothesis
      properties). Note: `ARCHITECTURE.md`'s target layout names this
      `research/pricing/black_scholes.py` behind
      `research/pricing/interface.py`; today it's one flat module. Worth
      splitting into that shape when a second `OptionPricer` implementation
      (e.g. real quotes, `docs/adr/0004`) actually arrives — not before,
      since a one-file interface+impl pair doesn't yet need the subpackage.
- [x] Wire up `import-linter` in CI to machine-check the module dependency
      direction in `ARCHITECTURE.md`, including the `api` ↛ `ingestion`
      carve-out. **Done** — see the `[tool.importlinter]` contracts in
      `pyproject.toml` and the `import-linter` step in
      `.github/workflows/ci.yml`.
- [x] Add a second sensitivity metric (co-skewness). **Done 2026-08-20**
      (Dio's standing directive, in-app feedback: "prioritize the
      sensitivity leaderboard so I can see put candidates ranked daily") --
      `research/metrics/co_skewness.py` (Harvey & Siddique co-skewness with
      the market, pinned against a hand-computable self-skewness identity +
      Hypothesis properties), wired into `research/leaderboard.py` behind a
      new `metric` parameter (`Literal["downside_beta", "co_skewness"]`) and
      `GET /api/leaderboard?metric=...`, with a metric-picker tab added to
      `LeaderboardTile.tsx` -- makes the leaderboard's per-metric tabs
      (`docs/END_STATE.md` §1.1) real for the first time. One deliberate
      convention: co-skewness's leaderboard *score* is the *negated* raw
      statistic (more negative raw co-skewness = more crash-prone = more
      tail-sensitive), so "higher score = more sensitive" stays consistent
      with downside beta's convention -- documented in `_score()`.
      **Done differently, see below**: the screening leaderboard itself was
      later reframed as a five-metric composite fragility screen
      (`research/backtest/ranking.py`, PRs #24/#25/#28) superseding this
      two-metric `research/leaderboard.py` version's role as the *live*
      leaderboard surface — `research/leaderboard.py` / `api/leaderboard_routes.py`
      / `frontend/src/components/LeaderboardTile.tsx` still exist and are
      still tested, but `LeaderboardTile.tsx` is no longer rendered by
      `App.tsx` (superseded by the Put Lab's `Leaderboard.tsx` fragility
      screen tab). Found while picking today's item (2026-08-21) — flagging
      here rather than deleting the orphaned files unprompted; a future
      increment should either wire `LeaderboardTile.tsx` back in or remove
      the now-dead `research/leaderboard.py` stack deliberately.
- [x] **Delete the orphaned `research/leaderboard.py` stack** (the decision
      flagged directly above). **Done 2026-09-06** by the weekly cleanup
      agent, exactly as audited below. Delete
      `src/tail_lab/research/leaderboard.py`,
      `src/tail_lab/api/leaderboard_routes.py`,
      `src/tail_lab/transforms/marts/sensitivity_leaderboard.py`,
      `frontend/src/components/LeaderboardTile.tsx`, and their three test
      files (`tests/test_research_leaderboard.py`,
      `tests/test_api_leaderboard.py`,
      `tests/test_transforms_marts_sensitivity_leaderboard.py`); remove the
      `leaderboard_routes` import + `app.include_router(leaderboard_router)`
      from `api/main.py`; remove `fetchLeaderboard`/`LeaderboardRow`/
      `LeaderboardResponse`/`LeaderboardMetric` from `frontend/src/api/
      client.ts` and the now-orphaned `.leaderboard-tile`/
      `.leaderboard-metric-tabs`/`.leaderboard-table` rules from
      `frontend/src/App.css`. Nothing else in `src/` or the frontend
      imports any of it (`api/putlab_routes.py`'s `GET
      /api/putlab/leaderboard` is unrelated, backed by
      `research/backtest/ranking.py`); no e2e fixture mocks `/api/leaderboard`;
      `docs/adr/0017` (lines 105-108) already documents the route as dead
      and defers only the literal deletion. `README.md`/`ARCHITECTURE.md`/
      `docs/END_STATE.md` need no edit (§1.1's "sensitivity leaderboard" is
      the aspirational concept, still served — better — by
      `research/backtest/ranking.py` + `RankingStrip.tsx`); do update
      `docs/DATA_FLOW.md` (drop `/api/leaderboard` from the routes diagram
      and the lines 95-98 "orphaned" callout), `docs/DATA_CONTRACTS.md`
      line 60's `research/leaderboard.py` mention, and `CLAUDE.md`'s
      `research/` file list, none of which are constitution-guarded. One
      accepted, deliberate capability loss: the old `?metric=...` param let
      a caller sort the whole list by one raw metric alone; the new
      composite `fragility_score` always sorts by the blend (each raw
      metric is still visible per-row in `RankingStrip`'s expanded table,
      just not independently sortable) — this is `ranking.py`'s intended
      reframing, not a regression to work around.
- [x] The first cross-metric backtest comparison answering research question 1
      (`docs/END_STATE.md` §1.5, §4 Q1) -- **done 2026-08-20** as
      `research/backtest/metric_screen.py` (PR #27, "the metric bake-off"),
      not the originally-sketched `research/backtest/compare.py` path: for
      each of the five raw fragility metrics + the composite, backtests every
      name once and compares blended put return / hit rate / bleed / regime
      breakdown / Spearman rank correlation to realized payoff. Surfaced as
      `GET /api/putlab/metric-screen` + the `MetricScreen.tsx` panel. Found
      unchecked while picking today's item; fixed for bookkeeping accuracy.
- [x] Add the regime-panel gold mart + API endpoint + dashboard tile.
      **Done 2026-08-20** as a VIX-complex-only classifier (PR #23,
      `research/regimes/timeline.py` + `RegimePanel.tsx`) — narrower than
      this item's original "vol complex + credit spreads" scope, since
      dataset #4 (credit) still doesn't exist; re-open a follow-up item to
      widen the classifier once FRED credit is ingested. Found unchecked
      while picking today's item; fixed for bookkeeping accuracy.
- [ ] Storage-growth optimization (not urgent): `DeltaLakeStore.write_bronze`
      still stores each ingest's **full** history for that date, not a diff,
      matching the pre-Delta Parquet layout's semantics (`docs/adr/0013`).
      Once a dataset's ingest volume makes this costly, consider a
      diff/merge write for that dataset specifically — must not change the
      as-of/immutability contract or the point-in-time tests.

## Free historical-put increments (2026-08-21 — see `docs/DATA_SOURCING.md` §9)

Both items are keyless, need no account, and were probed live on
2026-08-21. They exist because Dio pushed back on §3's "you must pay for
real option quotes" conclusion, and he was right for the evaluation half
of the problem. Take them in order — item 1 is the higher-value one, but
item 2 is time-sensitive in a way nothing else in this file is.

- [x] **Cboe option-strategy benchmark index adapter.** **Done 2026-08-21.**
      `contracts/cboe_strategy.py` + `ingestion/cboe_strategy.py` +
      `make ingest-cboe-strategy` (`TICKERS=` override), dataset #7 in
      `docs/DATA_CONTRACTS.md`, 24 new tests, parser pinned to a committed
      fixture of real PPUT rows (1986 inception + the Sep-Oct 2008 window).
      First live run committed **74,367 rows across 10 indices,
      1975-01-02 to 2026-08-20, 0 quarantined**. Sanity check over
      2007-10-09 to 2009-03-09: SPX -56.8% vs PPUT -41.5%, PPUT3M -38.6%,
      VXTH -43.3%, CLL -21.8%. Two source quirks found and documented:
      `CLL`'s CDN file starts 2008-08-26 and `PUT`'s 1991-03-04, not 1986.
      **Still open:** wire the series into the Put Lab as a benchmark row
      and then as the model-vs-market residual — the original description
      below is retained for that remaining half.
- [ ] ~~**Cboe option-strategy benchmark index adapter.**~~ Same shape and
      same host as `ingestion/vix.py`:
      `https://cdn.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv`,
      `DATE,<TICKER>` two-column CSV, keyless, all HTTP 200 and current
      through 2026-08-20. Ingest at minimum `PPUT` (5% put protection,
      from 1986-06-30, 10,109 rows), `PPUT3M` (Cboe **S&P 500 Tail Risk
      Index**, 2004-03-19), `VXTH` (VIX tail hedge, 2006-03-31), `LTV`
      (**S&P 500 Left Tail Volatility**, 2006-01-03) and `SPX` (price
      index, from 1975); `CLL`/`CLL3M`/`CLLZ`/`CLLR`, the `SPRO01..12`
      buffer series and the `PUT`/`PUTY` putwrite family are the same
      adapter with a longer ticker list. These indices price their puts
      at **actual OPRA volume-weighted transaction prices** (Cboe
      methodology PDF, §9.1) — so they are a real-quote benchmark for the
      Put Lab, covering 2008/2011/2015/2018/2020/2022, for $0. Wire the
      result into the Put Lab as a benchmark series and, once that lands,
      as the model-vs-market residual (`docs/END_STATE.md` §4 research
      question on `docs/adr/0004`'s model-priced limitation).
- [x] **Daily Cboe delayed-quote chain snapshots into bronze.** **Done
      2026-08-26** (`docs/adr/0020`) — and it is worth recording that this
      item sat here, correctly labelled time-sensitive, for five days while
      the daily agent shipped five other things. Nothing was malfunctioning:
      the value bar asks what "measurably sharpens the cockpit", and an
      increment whose entire payoff is in 2029 loses that comparison every
      single day. `docs/AGENT_MISSION.md` now carries an irreversibility
      clause so it cannot lose it again. Shipped narrower than described
      here: 24 names not 70, put wing only, one write per day.
      Original note follows.
      `https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json`
      verified live: HTTP 200, 13.7 MB, **30,842 SPX contracts**, each
      carrying `bid`/`ask`/`bid_size`/`ask_size`/`iv`/`open_interest`/
      `volume`/`delta`/`gamma`/`vega`/`theta`/`rho`/`theo`/
      `last_trade_price`/`prev_day_close`, plus a top-level `timestamp`
      and the underlying's `current_price`/`iv30`. Same path serves
      `_VIX`, `_RUT`, `SPY`, `QQQ`. **Time-sensitive:** this is forward
      collection — every day the snapshot does not run is a day of real
      chains permanently lost, and the Wayback Machine cannot backfill it
      (exactly three captures ever exist — §9.4). Pace and cache politely,
      as with the Nasdaq earnings endpoint. Needs a new dataset entry in
      `docs/DATA_CONTRACTS.md` before the adapter lands.
- [ ] **Skew-aware `OptionPricer` calibrated to VIX + SKEW.** `SKEW` is
      now ingestable (`ingestion/vix_complex.py`, done 2026-09-07, see
      the vol-complex item above) — this item was previously blocked purely
      on having a source for it and no longer is, though a live run and a
      `research/` consumer are still open. `SKEW` is
      free and daily from **1990** and is Cboe's implied third moment from
      real OTM SPX put prices; a Gram-Charlier/Corrado-Su expansion on
      VIX (2nd moment) + SKEW (3rd) prices OTM puts with the market's
      actual skew, instead of the flat Black-Scholes proxy
      (`docs/adr/0004`). Validate against optionsDX quotes on the
      2010–2023 overlap first (needs the `HUMAN_TODO.md` account), then
      extend back to 1990. Do not ship it as the default pricer until the
      overlap validation is in a test.
      **The calibration target is now measured, not guessed** (`make skew`,
      `docs/MODEL_RESIDUAL.md`): real SPY quotes over 210 roll dates put the
      market's implied vol **+2.2 vol points above VIX at 5% OTM, +7.2 at
      10%, +18.2 at 20%**. Success is `make residual` moving toward zero once
      those gaps are priced in.
      **It now has a falsifiable acceptance test that needs no account.**
      Re-run `make residual` before and after (`docs/MODEL_RESIDUAL.md`).
      The skew story predicts the new pricer shrinks the calm/elevated
      residual (**+1.6%/yr**) **and** the crisis flip (**-1.5%/yr**)
      *together*. If it fixes only one, skew is not the mechanism and the
      write-up must say so rather than shipping the half that worked.
- [x] **Migrate the vol complex off Yahoo onto the Cboe CDN, and finish it.**
      **Source migration done 2026-08-21** — `ingestion/vix.py` now resolves
      an ordered chain (Cboe primary, Yahoo fallback) via the new
      `ingestion/sources.py`, and the first live run committed **9,255 rows
      covering 1990-01-02 → 2026-08-20**, against the ~126 rows Yahoo's
      6-month default had been giving. `ingestion/ohlcv.py` (Nasdaq primary)
      and `ingestion/options_expiry.py` (Cboe chain primary) moved in the
      same change, so **no adapter has Yahoo as its primary any more**.
      **The remaining four series shipped 2026-09-07**:
      `ingestion/vix_complex.py` + `contracts/vix_complex.py` fetch VIX3M,
      VIX9D, VVIX and SKEW from the same CDN host into a new, independent
      `vix_complex` bronze dataset (`make ingest-vix-complex`) — deliberately
      NOT merged into the existing `vix` dataset, so every current consumer
      of spot VIX (`transforms/vix.py`, `research/vix_stretch.py`, the
      regime timeline, the dashboard tile) is untouched. Confirmed live
      2026-09-07 that VIX3M/VIX9D serve full OHLC like spot VIX but
      VVIX/SKEW serve a bare `DATE,<TICKER>` (no OHLC at all) — the parser
      handles both shapes, `open`/`high`/`low` land null where the source
      never had them, and the schema validates `close` against a *per-series*
      bound (VIX3M/VIX9D 0–200, VVIX 0–300, SKEW 50–250) since one shared
      range can't honour a SKEW print (~100–170) and a VIX3M print an order
      of magnitude smaller. **SKEW — the hard blocker on the skew-aware
      pricer below — is now ingestable.** Also factored `find_header_line`
      (the "locate the header row past a CDN file's preamble" helper) out of
      `vix.py`/`cboe_strategy.py`/`mpd.py`'s three identical copies into
      `ingestion/sources.py`, since this adapter would otherwise have been a
      fourth. **Still open, and why the vol-complex item stays split from
      this one:** merging `vix_complex` into `vix` and widening spot VIX
      itself to full OHLC still touches `transforms/vix.py`,
      `research/vix_stretch.py` and the dashboard tile — unchanged scope
      note, now tracked as its own item directly below. **Not yet run
      against prod** (no live-run credential in this workflow) and **no
      `research/` consumer wired yet** — same ship-the-adapter-first
      precedent every other dataset here has followed.
      *Original description follows.*
- [ ] ~~**Migrate the vol complex off Yahoo onto the Cboe CDN.**~~
      Highest-value item in this section: `docs/DATA_CONTRACTS.md` #2
      specifies Cboe as the source for `VIX`, `VIX3M`, `VIX9D`, `VVIX` and
      `SKEW`, but `ingestion/vix.py` ingests only `VIX`, and it does so from
      **Yahoo's chart endpoint, which now returns HTTP 429 from residential
      IPs as well as datacenter ones** (probed 2026-08-21,
      `docs/DATA_SOURCING.md` §9.4) — so the platform's most-used dataset is
      on a source that is actively failing. The shipped
      `ingestion/cboe_strategy.py` proves the replacement path works:
      `https://cdn.cboe.com/api/global/us_indices/daily_prices/{TICKER}_History.csv`,
      keyless, HTTP 200, full history every fetch. These files serve
      `DATE,OPEN,HIGH,LOW,CLOSE` (unlike the strategy indices' two-column
      shape), which `parse_index_history_csv` already handles — prefer
      reusing/generalising that parser over writing a second one.
      **Acceptance:** all five series ingested; contract #2's OHLC schema
      honoured (not just close); `source_id` becomes `"cboe"`; the Yahoo
      path removed or explicitly demoted to fallback in both the code and
      contract #2's source section, in the same PR. This also unblocks the
      skew-aware pricer above, which cannot run without `SKEW` in the lake.
- [ ] **Merge `vix_complex` into `vix` and widen spot VIX to full OHLC**,
      once a `research/` consumer actually needs the two read together
      (e.g. the skew-aware pricer, or a term-structure view alongside
      `transforms/vix_futures.py`'s constant-maturity curve). Today spot
      VIX (`vix`, close-only) and the rest of the complex (`vix_complex`,
      OHLC-where-available) are two independent bronze datasets on purpose
      (`AGENT_TODO.md`'s "Migrate the vol complex..." item above) — unifying
      them means widening `vix`'s committed shape, which touches
      `transforms/vix.py`, `research/vix_stretch.py` and the dashboard tile,
      so it stays deferred until a consumer earns the migration risk.
- [x] **PPUT replication harness — done 2026-08-22.**
      `research/backtest/index_replication.py`, `make residual`, results in
      `docs/MODEL_RESIDUAL.md`. Residual vs `PPUT` is **+1.34%/yr** over 438
      monthly rolls (36.5y, correlation 0.9913) and **+2.71%/yr** vs `PPUT3M`
      over 89 quarterly rolls (correlation 0.9972). **It flips sign by
      regime** at 5% OTM — +1.55%/yr calm, −1.46%/yr crisis — and **doubles
      with strike depth**, which is the strongest skew evidence in the project
      (`docs/DISCOVERIES.md` §9, §10). Runs on free Cboe data only; needs no
      option chains.

- [x] **Surface the accuracy context on every result — now a constitutional
      requirement**, not a nice-to-have (README, "Accuracy is surfaced, not
      filed"). **Done 2026-08-22** (`research/accuracy.py`,
      `GET /api/putlab/accuracy`, `AccuracyPanel.tsx`, rendered directly under
      every backtest's headline numbers, never behind "more detail"). Found
      unchecked while picking today's item — fixed for bookkeeping accuracy.
      A backtest figure shown without the known size of its error is
      the one failure mode this platform cannot afford. Four things exist
      already and are all currently invisible in the UI:
      1. **The model-vs-market residual.** `make residual` /
         `docs/MODEL_RESIDUAL.md` says a model-priced put roll runs
         **+1.34%/yr optimistic** against PPUT, and **flips to -1.46%/yr in
         crisis**. A Put Lab result should say so *for the regime mix of the
         window it just ran*, not quote the global number.
      2. **The published benchmark.** The honest question for a put program
         is not "did it make money" but "did it beat the published put
         program you could have bought instead". Reuse the existing benchmark
         plumbing (the S&P hurdle on the sweep heatmap), do not add a second.
         Measured reference points, first live ingest (2007-10-09 to
         2009-03-09): SPX -56.8%, PPUT -41.5%, PPUT3M -38.6%, VXTH -43.3%,
         CLL -21.8%.
      3. **Data-quality flags on the inputs.** `research/data_quality.py`
         already scans for bad ticks and stale runs and nothing shows it.
      4. **The assumptions and their leverage.** Flat 4% rate, VIX as the IV
         proxy, the dividend yield. Where a sensitivity is computed, show it
         next to the number, as the residual report does.
      **Acceptance:** a backtest result in the UI cannot be read without also
      reading how wrong it might be. If a piece of context is not yet known
      for a given asset or window, the surface says *that* rather than
      staying silent.

- [x] **`options_expiry` is a dangling branch — wire it or retire it.**
      **Wired, 2026-08-30.** Chose "wire it": `research/cadence.py`
      (`resolve_cadence`) reads the `options_expiry_{symbol}` bronze
      partition as-of a date, mirroring `research/data_quality.py`'s
      point-in-time read shape, and classifies it with the existing
      `transforms/options_expiry.classify_cadence`; `/api/putlab/cadence`
      now calls it (with `as_of`/`store` params matching every other Put Lab
      route) instead of reading `contracts/options_calendar.cadence_for`
      directly. New `make ingest-options-expiry SYMBOL=...` target, mirroring
      `ingest-ohlcv`'s shape. **Falls back to the static table whenever the
      lake has no snapshot for a symbol, is empty, or has too few near-term
      expirations to classify** — so behavior in prod is unchanged today
      (no `options_expiry_*` partition exists there yet, same
      ship-the-pure-function-first precedent `rates.py`/`credit.py` followed)
      and becomes live the moment Dio runs the ingest target for a symbol, with
      no further code change. A live-derived record is labelled
      `"Weeklies (live)"`/`"Monthlies (live)"` so the two provenances are
      never visually confused with the static table's `"(assumed)"` flag.
      `docs/DATA_FLOW.md` §3.1 was updated in the same PR, so it already
      matches (the draft note claiming otherwise was stale on arrival — caught
      by the adversarial reviewer on #61 and, characteristically, merged
      unaddressed before this fix).

- [ ] **Free-source health canary.** Yahoo degraded from "works" to "429s
      everywhere" between 2026-08-19 and 2026-08-21 and nothing in the
      platform noticed — the failure surfaced only because a human went
      looking. Add a small scheduled check that probes each keyless source
      the platform depends on (Cboe index CDN, Cboe delayed quotes, Nasdaq
      earnings, FRED, Yahoo) and records status + latency through
      `observability.log_event`, feeding the activity-log surface queued
      above. **Acceptance:** a red canary is visible in the cockpit without
      anyone reading logs. Keep it cheap — one HEAD/small-GET per source per
      day, not a crawl.
- [x] **Minneapolis Fed MPD adapter** (free, keyless, official). **Done
      2026-08-31.** `contracts/mpd.py` (`docs/DATA_CONTRACTS.md` #9) +
      `ingestion/mpd.py` + `make ingest-mpd`, following the
      `ingestion/cboe_strategy.py` shape (one fetch, one long-format panel,
      no per-ticker loop needed since the source is a single file covering
      the whole market family). Parser pinned against a real fixture
      (`tests/fixtures/mpd_stats_sample.csv`, live-fetched 2026-08-31):
      the Sep-Oct 2008 crisis window for `sp12m`, plus `bac` and `infl1y`
      rows and a real blank-`maturity_target` row, so the null-handling and
      the inflation markets' different "large move" threshold (source's own
      preamble note) are both pinned, not assumed. **The live file no
      longer carries most of the per-firm densities** the original item
      described (aig/gs/jpm/ms/wfc/... are absent from the current file;
      only `bac`/`citi` remain alongside `sp12m`/`sp6m` and several
      commodity/FX/rate/inflation markets) — ingesting the whole file rather
      than filtering to `sp12m` costs nothing extra (one fetch either way)
      and keeps whatever the source still carries. **Not yet run against
      prod** and **no `research/` consumer wired yet** — same
      ship-the-adapter-first precedent `rates.py`/`credit.py` followed; the
      calibration/validation use (`docs/END_STATE.md` §4 Q2, the skew-aware
      pricer) is a follow-up once a live `mpd` partition exists.

### Working rules for this section

- **Do not run `make ingest-*`.** Ingestion targets write to the *production*
  Tigris lake when `.env` is present, and a wrong `range_` has already
  silently shadowed a good partition and truncated prod backtests once
  (see the OHLCV truncation incident). Build adapters against committed
  fixtures, let CI prove them, and leave the live run to Dio. The
  `cboe_strategy` adapter is safe by construction here — it refetches full
  history every time, so it cannot truncate — but the rule stands.
- **Tests never touch the network** (`docs/STANDARDS.md`). Every adapter
  above splits pure-parse from fetch and commits a real fixture, exactly as
  `ingestion/cboe_strategy.py` does.
- **Add the dataset contract in the same PR as the adapter**, never after.
- **One item per PR.** These are deliberately independent; a PR that does
  two of them is harder to review and to revert.

## Second-deep-dive increments (2026-08-21 — see `docs/DATA_SOURCING.md` §10)

- [x] **Validate the lambdaclass `data-v1` chains against PPUT — done
      2026-08-21.** Verdict in `docs/DATA_VERDICTS.md`: the chains reproduce
      `PPUT` at **ρ=0.9927, TE 1.63%/yr** across 207 monthly rolls
      (2008-01→2025-11), zero unpriceable rolls; provenance traced to **Alpha
      Vantage `HISTORICAL_OPTIONS`**. Quotes trusted; `mark`, IV and greeks
      are sentinel-filled before 2011 and must not be used. **Still no
      adapter** — the licence call in `HUMAN_TODO.md` is unanswered.

- [ ] **Hao Zhou 5-minute realized-variance series as a pricer input.**
      Free, monthly, **1990 → December 2024**, from the Bollerslev-Tauchen-
      Zhou variance-risk-premium dataset (link via
      `sites.google.com/site/haozhouspersonalhomepage`): risk-neutral implied
      variance (VIX²/12), realized variance from **5-minute** S&P 500 log
      returns, and their difference. `research/backtest/put_roll.py` derives
      its IV proxy from *daily*-bar trailing realized vol; the 5-minute
      series is a strictly better volatility measure and needs intraday
      history we otherwise do not have. Monthly frequency makes this a
      **calibration target**, not a per-roll input — wire it in as a
      benchmark the daily-bar proxy is scored against, and quantify the bias
      the daily proxy carries. Note the file is served from Google Drive, so
      mirror it into the lake rather than fetching it on every run.

- [x] **Decide what `adj_close` should mean now that Nasdaq is the OHLCV
      primary.** **Decided 2026-08-21 (Dio): option (c).** The backtester now
      prices off raw `close` — an option is written on the price that
      actually printed, and pricing off `adj_close` was setting strikes off
      prices that never traded (6.5% strike error at the 5-year mark, so a
      nominal 5% OTM put was really ~11% OTM). Cost of the switch, measured:
      0.3% of mean on the realized-vol proxy, ~0.4% relative on downside
      beta. Written up as `docs/DISCOVERIES.md` §1–2.
      This also defuses the vendor question: Nasdaq and Yahoo agree on
      `close` to 0.000029, so the backtest is now vendor-agnostic and prod
      OHLCV can safely be re-ingested from either.
      **Follow-up:** `leaderboard.py` and `data_quality.py` still read
      `adj_close` directly. For a total-return metric that is arguably
      correct — but it should be a decision with a comment on it, not an
      inheritance. Check each and annotate.
- [ ] ~~**Decide what `adj_close` should mean now that Nasdaq is the OHLCV
      primary.**~~ Measured on SPY over 1,253 overlapping days (2026-08-21):
      the two sources agree on `close` to **0.000029** — effectively
      identical, a strong cross-validation — but their `adj_close` differs
      by up to **$29.62** (mean $14.00), because Yahoo back-adjusts for
      dividends and Nasdaq does not. Over that overlap the total return is
      **+82.43% (Yahoo) vs +70.50% (Nasdaq)** — an **11.93pp** gap that is
      entirely the dividend stream. Every consumer of `adj_close`
      (`put_roll.py`, `leaderboard.py`, `data_quality.py`) is therefore
      basis-sensitive.
      Three options, and this needs a decision before prod OHLCV is
      re-ingested — a Nasdaq partition would shadow the Yahoo ones under
      as-of resolution and silently shift every backtest by ~12pp:
      (a) accept split-only and document it everywhere;
      (b) reconstruct a dividend-adjusted series from a free dividend source;
      (c) **argue `put_roll.py` should use raw `close` anyway** — options
      settle on actual prices, not dividend-adjusted ones, so the current
      use of `adj_close` for option payoffs may be the real bug and this
      migration merely exposed it. (c) is the most likely right answer and
      the cheapest to test.
      **Until this is decided, do not re-ingest prod OHLCV.** The existing
      Yahoo partitions are internally consistent and still readable; nothing
      is broken today.

## Design-review advisories not yet acted on (found 2026-09-07)

Two non-blocking findings from PR #86's round-2 review (the Nasdaq
earnings-calendar adapter) that were never logged anywhere — exactly the
gap `docs/adr/0023`'s step 2b exists to close. Neither was small enough to
fold into today's unrelated PR, so they're queued here instead of being
lost a second time.

- [ ] **`_refuse_if_already_written_today` (`ingestion/earnings.py`)
      string-parses `LakeStore.bronze_snapshot_id`'s output** to recover a
      partition's resolved date, even though that method's docstring
      (`lake/store.py:94-99`) describes it as an opaque citation string, not
      a documented, parseable format — it happens to work only because
      `DeltaLakeStore` builds it as `f"{dataset}@{date.isoformat()}#{digest}"`.
      Add a real accessor to the `LakeStore` protocol (e.g.
      `has_bronze_partition(dataset, date)` or `bronze_partition_date(...)`)
      the next time a same-day-collision guard is needed on a shared
      dataset (the BLS CPI adapter, still blocked on Akamai, will need
      exactly this) — fix it there rather than adding a third string-parse.
- [ ] **`lookahead_days` (`ingestion/earnings.py`) is never exercised with a
      non-default value** — not by `make ingest-earnings`, not by any test.
      Either add a test that calls `ingest_earnings_calendar` with a
      non-default value, or trim the parameter if nothing is meant to use it
      yet.
- [x] **`ingest_vix_complex`'s `raw` mapping is looked up with the already-
      uppercased `resolved` series name** — advisory from PR #87's review
      (2026-09-07). **Done 2026-09-11**: `ingest_vix_complex` now
      uppercase-normalizes `raw`'s keys once at the top
      (`ingestion/vix_complex.py`) before the lookup, so a caller passing
      lowercase keys is matched instead of silently falling through to a live
      `fetch_vix_complex_raw` call. Pinned by
      `test_ingest_matches_lowercase_raw_keys_against_uppercased_series`,
      which monkeypatches `fetch_vix_complex_raw` to raise if called at all —
      proving the injected payload was used, not a network path that tests
      happen not to exercise.

## Operational (2026-09-01)

- [ ] **Surface `agent/*` PRs blocked by design review for more than a day.**
      A BLOCK leaves the PR red, and the agent that wrote it is a one-shot
      daily run that never comes back — so absent a human it rots (five PRs,
      four days, 2026-08-29..09-01). The advisory half of this leak is closed
      (the daily agent now reads review comments, `docs/adr/0023`); the block
      half still needs a nudge. Cheapest is a scheduled check listing open
      `agent/*` PRs whose `agent-review` is failing, in the same place the
      chain-freshness check reports.
- [ ] **Detect agent PRs that have gone silently CI-less.** GitHub runs
      `pull_request` workflows on the merge commit, so a branch that conflicts
      with `main` reports **zero** check runs — which is indistinguishable from
      "queued" and from a dropped event, and is therefore invisible. This is
      not hypothetical: five agent PRs stalled 2026-08-29..09-01, and as
      siblings merged they all conflicted on `Makefile` (each adds an
      `ingest-*` target at the same place) and stopped getting CI entirely.
      Cheapest fix is a scheduled check that lists open `agent/*` PRs with
      `mergeable == false` or no check runs, and says so loudly. A better fix
      also removes the cause: the per-adapter `Makefile` targets are a
      guaranteed collision point, so consider a single generic
      `make ingest DATASET=<name>` dispatching on the adapter, which would make
      new adapters conflict-free by construction.

- [ ] **Chunked bronze write, so SPX can be ingested at all.**
      `ingest_optionsdx` materialises a whole symbol before writing: it holds
      `combined` and `valid` (near-identical, ~681 MB each on SPX) plus the
      Arrow conversion the Delta write adds on top. SPX (168 months, ~7.6M
      rows) is OOM-killed at ~3.9 GB on a 7.4 GB machine and is the ONE
      symbol of the six still missing from the lake; the other five ingested
      without complaint, because the failure scales with the largest symbol.
      Four fixes were tried and none of them worked, so do not re-try these:
      a dict-per-row parser rewrite, batched `pd.concat`, a hand-rolled line
      parser, and freeing `combined` before the write. Profiling showed
      accumulation was never the problem (7.65M rows = 681 MB of frames,
      RSS 1,031 MB; peak through concat+dedup 2.2 GB) — the symbol being held
      *whole* is. The fix belongs in the lake layer: append each month to the
      Delta table as it is parsed, so no step ever holds the full symbol.
      Note `write_bronze` is currently a no-op if the `ingest_date` partition
      exists (immutable bronze), so a chunked writer needs an explicit
      append-within-one-ingest mode rather than repeated `write_bronze` calls.
- [x] **Retry a failed chain FETCH, not just the failed POST.** The POST now
      retries (2026-09-04, a transient 500 cost a session). `sweep_to_records`
      still does not: a per-symbol Cboe blip is caught, logged and skipped, and
      the sweep continues. That is partly deliberate — per-symbol fault
      tolerance, with `MIN_PLAUSIBLE_ROWS` catching a mass failure — but by the
      same argument that motivated the POST retry, a silently-dropped chain
      costs that symbol's session permanently and nobody sells it back. The
      floor only catches a wholesale failure; losing 1 of 24 names passes it.
      Wanted: retry each symbol a couple of times, and make the count of
      symbols that ended up missing a loud output rather than a `::warning::`
      nobody reads.
      **Done 2026-09-12.** `ingestion/option_chain.py` gained
      `_fetch_with_retry`, used by both `ingest_option_chain`'s and
      `sweep_to_records`'s per-symbol loops: up to 3 attempts, 3s apart,
      retrying only a fault a retry could plausibly fix (`_retryable_fetch_error`
      mirrors `scripts/chain_snapshot.py`'s `_retryable` for the POST leg — a
      5xx/429/connection fault retries, a firm 4xx or a non-`requests`
      exception from an injected test fetcher does not, so existing
      one-dead-chain tests are unaffected). Pinned: a transient-then-success
      fetch is retried and lands in `symbols_ok`; a persistent 5xx exhausts the
      attempt budget; a 404 and a non-network exception are each tried exactly
      once (`tests/test_ingestion_option_chain.py`).
      For the "loud output" half, chose the cockpit over the CI log: a missing
      symbol is exactly the "accuracy surfaced, not filed" case, and
      `GET /api/ingest/option-chain/status` is the freshness check the daily
      agent (and Dio) already reads every day, unlike a scheduled workflow's
      `::warning::` line. `OptionChainSnapshotStatus` gained `missing_symbols`
      — anyone in `DEFAULT_SNAPSHOT_SYMBOLS` absent from the last partition —
      computed in `api/ingest_routes.option_chain_snapshot_status` and pinned
      in `tests/test_api_ingest.py`, including the case `stale_days` cannot see
      (23 of 24 chains landed, so the day is fresh but not complete).
      `docs/DATA_CONTRACTS.md` #6 updated in the same PR. The scheduled
      script's `::warning::` for a missing symbol is left in place — still
      useful for someone actually watching a run — the status field is the new
      always-on signal, not a replacement for it.

- [x] **Done 2026-09-10.** A bounded cache for quote sources —
      `research/backtest/quote_cache.py`. Byte-budgeted (400 MB against the
      1024 MB machine), LRU by BYTES not entries, and one build per key even
      under threads. Measured: four concurrent SPY requests produce ONE build
      and peak at 702 MB, against ~1066 MB measured for two unguarded ones
      (an earlier note said 1318 MB; a second measurement put it at 1066 MB,
      still over the 1024 MB cap, so the conclusion holds and the number was
      quoted too confidently).

- [ ] **Wire a route THROUGH the cache — the cache alone does not protect
      anything.** `OptionsDxQuoteSource.from_store` is still the only public
      constructor and is still unguarded, so a route can bypass the cache
      entirely by calling it. Needs a single seam that a route must go through,
      a documented cache key, and a TTL or `as_of` contract: keying on
      `(symbol, as_of)` with `as_of` defaulting to today is stale-forever,
      where `LakeStore._resolve_cached` refreshes in 45 s. Given `docs/adr/0009`
      is the #1 invariant, a cache sitting in front of as-of reads with no key
      contract is the specific risk to close before turning any of this on.
      `OptionsDxQuoteSource.from_store` is measured at 13-38 s for SPY — always
      past the 5 s health-check timeout — and two simultaneous constructions
      peaked at ~1066 MB against a 1024 MB machine (see the revised
      measurement above). Every putlab route is a sync `def`, so Starlette
      runs up to 40 in a threadpool: per-request construction is unservable
      and concurrent construction OOMs. A per-`(symbol, as_of)` cache is not
      enough on its own either — measured steady state for five optionsDX
      assets plus `option_quotes` is 682 MB resident, on top of the app's own
      ~252 MB. Wanted: an explicit cache with a BYTE budget and eviction, not
      the count-capped `_frame_cache`. `quote_fills.index_nbytes` (a
      `memory_usage(deep=True)` sum with a measured RSS correction factor,
      tested directly in `test_research_backtest_quote_fills.py`) is the
      byte-reporter this cache's `build` callable needs — still not called
      from `OptionsDxQuoteSource.from_store` / `OptionQuotesSource.from_store`,
      which is the other half of this wiring step.

- [x] **Done 2026-09-10.** `bronze_snapshot_id` reads the Delta LOG rather
      than the data — per-file path, size, row count and per-column stats,
      which the log already carries. Measured 186 MB peak across three
      datasets including the 3.28M-row SPY panel, where the old path would
      have read all of it and pinned it in `_frame_cache` forever.
      `docs/STANDARDS.md` §f requires every backtest to log its input
      snapshots, and `putlab_routes._log_run(..., extra_snapshots=...)` routes
      through `store.bronze_snapshot_id` → `_cached_partition` → a FULL
      partition read plus a sha256 of it. The obvious next commit after wiring
      the market path — adding `("quotes_snapshot", "optionsdx_quotes_spy")` —
      therefore reads the whole 3.28M-row panel and dies, undoing the
      projection entirely. Needs a content id that can be computed from a
      projection, or an explicit refusal for quote datasets.

## Position sizing / optimal leverage (2026-09-01 — Dio; `docs/END_STATE.md` §4 Q8)

Dio's ask, in his words: a control where "the investor can either keep
investing in the same way they do now, or invest a portion of their wealth",
plus "analytics on what the optimal leverage is with this strategy, it might be
higher than normal". He is right that it might be, and right that it is not the
textbook number — but the reason matters, and getting the framing wrong
produces a confident wrong answer. **Read §4 Q8 before starting.** Ordered so
that nothing depends on a number the platform cannot yet compute honestly.

- [x] **Sizing mode: fixed cash OR a fraction of wealth.** **Backend seam done
      2026-09-09.** `research/backtest/sizing.py` — `SizingMode` ABC mirroring
      `OptionPricer`'s pluggability (`docs/adr/0004`) exactly: `resolve(*,
      n_legs) -> float`, with `FixedPremium(amount)` (today's, unchanged) and
      `WealthFraction(alpha, wealth)` (`alpha * wealth / n_legs`, `alpha`
      constrained to `(0, 1]` — sizing above 100% of stated wealth is a
      leverage decision this seam deliberately leaves to the `g(alpha)` sweep
      below). Threaded through `compute_put_backtest`, `run_portfolio`,
      `rank_universe` and `build_roll_schedule` as a new optional
      `sizing_mode: SizingMode | None = None` parameter *alongside* the
      existing `notional: float`, mirroring how those same functions already
      take `pricer: OptionPricer | None` — when given, it resolves the budget
      and `notional` is ignored; when `None` (every existing caller, unchanged)
      `notional` is used exactly as before. Chosen over renaming/replacing
      `notional` because `run_put_roll` (the pure engine one layer down) sits
      exactly at the `max-args=13` ratchet pinned to its own name in
      `pyproject.toml` — an additive optional parameter on the four
      orchestration functions costs nothing there since none of them are
      ratchet-pinned, whereas replacing `notional` would have forced updating
      every one of the ~30 existing test call sites across
      `tests/test_research_backtest_{put_roll,portfolio,ranking,roll_schedule}.py`
      for zero behavioral gain. Per-call-site `n_legs` choice, each documented
      in its function's docstring: `compute_put_backtest` and `run_portfolio`
      use `n_legs=1` (a single asset is one leg; portfolio's own
      `leg.weight / total_weight` split already divides the pool across legs,
      so dividing again here would double-count); `rank_universe` uses
      `n_legs=len(symbols)` (every screened name is priced independently, so a
      wealth fraction splits evenly across the universe); `build_roll_schedule`
      uses `n_legs=min(top_k, len(scorable))` — the schedule's *actual* leg
      count, not the requested `top_k`, since a thin universe can produce fewer
      scorable legs (pinned by
      `test_build_roll_schedule_sizing_mode_uses_actual_leg_count_not_top_k`) —
      and resolves the budget before building any `RollLeg`/`RollSchedule`,
      satisfying the executor-path note below: the artifact still carries only
      a concrete `float`, never a live formula. Tested per `docs/STANDARDS.md`:
      pinned hand-computable cases + Hypothesis properties (linearity in
      wealth, monotone-decreasing in `n_legs`, never exceeds `alpha * wealth`)
      in `tests/test_research_backtest_sizing{,_properties}.py`, plus one
      override test per orchestration function proving `sizing_mode` actually
      drives the result (not just default-constructible — no prior seam in
      this codebase, including `OptionPricer` itself, had that test). **Not
      done, deliberately deferred**: the API surface (`api/putlab_routes.py`)
      and the Put Lab control-bar toggle — same ship-the-pure-seam-first
      precedent `downside_beta.py`/`options_expiry.py`/every dataset adapter in
      this file has followed. Every existing route still passes a flat
      `notional` query param and gets identical behavior; wiring `alpha`/
      `wealth` query params through to `sizing_mode=WealthFraction(...)` and a
      Fixed/Wealth-fraction toggle in `ParamRail.tsx`'s "Position" section
      (mirroring its existing radiogroup pattern) is the natural next
      increment, and unblocks the `g(alpha)` sweep two items below, which needs
      a live `alpha` control to sweep over.
- [x] **Report time-average growth, not just ROI.** Every headline the platform
      shows — `roi_on_premium`, `hit_rate`, `annualized`,
      `biggest_payoff_mult` — describes a put in isolation, and **none of them
      can say how much to hold**. Add `g = (1/T) * log(W_T / W_0)` computed on
      the *combined* portfolio path (benchmark + hedge at fraction alpha),
      alongside the arithmetic figures, and label the difference. For a
      right-skewed payoff the two diverge sharply, and the gap is the whole
      point (`docs/END_STATE.md` §4 Q8).
      **Done 2026-09-10**: `research/backtest/growth.py`
      (`time_average_growth`), a pure function deliberately decoupled from
      `PutBacktestResult`'s `PricePoint`/`EquityPoint` models (plain
      `(date, value)` tuples for `price_path` instead) so it stays a leaf
      module any benchmark price series can feed later, not just a single
      leg's `price_path`. Only the two endpoints of the benchmark path matter
      — `g = (1/T) * log(W_T/W_0)` with `W_T = wealth * (S_T/S_0) +
      hedge_final` — since a time-average growth rate is defined by a
      trajectory's start and end, not its interior; `hedge_final` is a single
      realized-P&L figure (the hedge's last `mtm_curve` point), not a curve,
      because nothing in this function ever reads an interior hedge point
      (round 2 of review caught the unused-generality version of this
      signature). Wired into `compute_put_backtest`: when `sizing_mode` is
      specifically a `WealthFraction` (not `FixedPremium`/`None`, which have
      no `wealth` figure to compound against), the result's new
      `PutBacktestResult.time_average_growth` field is populated by calling
      the pure function on the run's own `price_path` and `mtm_curve[-1]`;
      otherwise it stays `None`. No API/UI change — same ship-the-pure-function-first
      precedent the `SizingMode` seam itself just followed (`AGENT_TODO.md`'s
      "Sizing mode" item above), and `sizing_mode` still isn't threaded
      through `api/putlab_routes.py`, so this has no effect on production
      output today. Pinned hand-computable cases + Hypothesis properties
      (zero-hedge collapses to benchmark-only growth exactly, monotone in the
      hedge's realized P&L, invariant to uniformly scaling wealth and hedge
      P&L by the same constant) in `tests/test_research_backtest_growth{,
      _properties}.py`; `research/backtest/growth.py` itself is at 100%
      coverage.
      **One modeling decision surfaced, not hidden, in the docstring**: this
      treats the *entire* stated `wealth` as continuously held in the
      benchmark, with the hedge funded as a separate additive cash flow sized
      by `alpha * wealth` — not `(1-alpha)*wealth` in the benchmark and
      `alpha*wealth` held back in cash. Because `WealthFraction` resolves
      `alpha * wealth` as a **per-roll** budget (re-spent every cycle, not a
      one-time draw), a strategy that rolls enough times can spend more than
      the stated `wealth` in cumulative premium — found while testing:
      `alpha=0.5, wealth=4000` over a 2-cycle SPY window spent ~4000 in
      premium against a benchmark that barely moved, so combined wealth went
      non-positive and `time_average_growth` correctly returned `None`
      (`log` of a non-positive number is undefined, handled as a `None`
      return alongside every other undefined case, same convention
      `annualized_sharpe` already uses — not a bug to raise on, since a
      leveraged sweep hitting ruin at some `alpha` is a real scenario, not an
      error). **Not done, deliberately deferred**: the `g(alpha)` sweep two
      items below is the natural next step and needs this ruin case handled
      exactly as it is here (a sweep must see `g` go to `None`/very negative
      near ruin, not crash) — no further plumbing needed to consume it.
- [ ] **The leverage analytic Dio asked for: a `g(alpha)` sweep.** Sweep alpha
      across a grid, plot the time-average growth of the combined portfolio,
      and report three numbers: the argmax `alpha*`, the growth at `alpha = 0`
      (hold no hedge), and the `alpha` at which `g` returns to zero — the
      Peters analogue of leverage `2 l*`, where over-allocation stops merely
      costing growth and starts destroying it. Desk vocabulary
      (`docs/adr/0021`): this belongs under **Carry**, next to the bleed it
      trades against. Compute `alpha*` **numerically on the empirical payoff
      distribution** — the closed form `l* = (mu - r)/sigma^2` assumes
      lognormal symmetric returns and a long put is neither.
- [ ] **Default to fractional Kelly, and say so on the surface.** Full Kelly is
      notoriously sensitive to parameter error, and this platform's parameters
      are *known* to be wrong by measured amounts: a +1.34%/yr model residual
      (`docs/MODEL_RESIDUAL.md`) and a model premium off by up to 600x on a
      real recommended leg (`docs/PRIOR_ART.md`). Half-Kelly or less by
      default, with the full-Kelly number shown beside it and the reason for
      the haircut named where the number appears — "accuracy is surfaced, not
      filed" (README).
- [ ] **Then, and only then, answer whether it really is higher than normal.**
      The hypothesis: a variance penalty calibrated on symmetric outcomes
      over-penalises a payoff whose loss is bounded at the premium and whose
      variance is mostly upside, so `alpha*` may exceed what a naive Kelly
      reading suggests. Test it, do not assert it — and report the standalone
      result next to the portfolio one, because standalone the growth-optimal
      allocation to a negative-EV bet is **zero**, and a reader who sees only
      the portfolio number will not understand why the position is held at all.

## Execution-path increments (2026-08-28 — the schedule is going to be executed)

Dio confirmed the direction: tail-lab dumps a recommended strategy as a JSON
file and a **separate** daily process picks it up and executes it
(`docs/adr/0019`'s shape; 0019 stays **Proposed** for now, deliberately — see
its Status). Real quotes therefore stop being an upgrade and become the
critical path.

The first marked run, 2026-08-28, produced **0 of 10 placeable legs**. That is
the number to move.

- [ ] **Drive the chain collection off the screen's own output.** 7 of the top
      10 legs came back `not_collected`: the 24 names in
      `DEFAULT_SNAPSHOT_SYMBOLS` were chosen for options liquidity and thesis
      span, and the screen ranks over all 70 — so the set we collect and the
      set we recommend are misaligned by construction. Collect at least the
      union of (a) today's top-K legs and (b) a stable core, or widen toward
      the full 70. Measured cost of the current 24 is 4.68 MB / 25s, and the 46
      additions are mostly thin chains (`docs/adr/0020`).
- [ ] **The screen optimises toward untradeable contracts, and this is the
      deep one.** All 3 collected legs came back `illiquid` — KRE bid 0.00 / OI
      0, XLE bid 0.01 ask 0.20 (a 190%-of-mid spread), XLF bid 0.00. That is
      not bad luck. `rank_universe` picks the strike x tenor cell maximising
      `best_annualized`, every such figure divides by the **model** premium,
      and the model premium collapses toward zero exactly where real markets
      thin out — so the optimiser is systematically steered into contracts
      nobody trades. `MODEL_PRICED_MAX_MONEYNESS_PCT` (`docs/adr/0018`) was
      meant to bound this and does not: KRE was at 8%, inside the bound, and
      still 600x mispriced. Candidate fixes, in order of honesty: rank on
      *market* premium where a quote exists; failing that, add a liquidity
      prior to the objective; failing that, bound by the arbitrage check queued
      under the portfolio-repo increments rather than by a moneyness constant.
      **This likely changes every published ranking**, so it needs an ADR and a
      before/after.
- [ ] **Write the marked schedule to the lake as a dated artifact**, not only
      as an endpoint. The executor should read a durable, auditable file whose
      history survives the app being down; `schedule_id` already makes it
      dedupable. JSON, schema-validated, data-never-code.
- [ ] **Decide how fills flow back** — `docs/adr/0019` left it open and it is
      the loop that makes the system measure itself. A fill is a free
      measurement of the model-vs-market gap on a contract we actually chose,
      which is strictly better evidence than `make greeks-check` on the whole
      chain.

## Prior-art increments (2026-08-27 — see `docs/PRIOR_ART.md`)

From reading nautilus_trader, hftbacktest and kalshimarketmaker. Ordered by
severity: the first is a possible correctness problem in results already
published, the rest are capability.

- [ ] **Delta-based strike selection, alongside moneyness** (`docs/PRIOR_ART.md`
      §1 — read it before starting; the numbers are the argument). Priced at
      our own regime bands, a "10% OOM 4-week put" is a **0.05-delta** contract
      in calm (VIX 12) and a **17.6-delta** contract in crisis (VIX 45) — a
      350x spread, premium 0.00% vs 1.26% of notional. Every cross-regime
      comparison we make therefore partly measures whether the strike was
      reachable at all, which is mechanical. Add a `StrikeSelection` seam to
      `research/backtest/put_roll.py` with two implementations — `ByMoneyness`
      (today's, unchanged and still the default) and `ByDelta(target,
      tolerance)` — mirroring the `OptionPricer` pluggability of
      `docs/adr/0004`. **Use the stored Cboe `delta` (`docs/adr/0020`), not a
      model delta** — `docs/PRIOR_ART.md` §6: our pricer is flat-vol, so a model
      delta is a sticky-strike delta on a smile that does not exist, and would
      import the same flat-vol error this item exists to remove. Cboe's is
      computed off the real smile. Only the pre-collection backtest needs a
      model delta, and it must state the sticky-strike assumption where its
      results appear. Then re-run the Bake-off and the
      regime verdicts under both and **report whether the conclusions move** —
      that comparison is the deliverable, not the feature. If they move,
      `docs/adr/0015`'s `regime_only` vs `confirmed` needs an ADR, because a
      rule_hash carrying `moneyness_pct` has been treating two very different
      contracts as one rule.
- [x] **Greeks on the pricer.** **Done 2026-08-27.** `PutGreeks` (delta,
      gamma, vega, theta, rho, itm_prob) with nautilus's conventions — vega per
      **vol point**, theta per **calendar day**. Validated three ways: against
      central finite differences of `price_put` across six parameter regimes,
      against the put-call delta-parity identity `delta_call - delta_put =
      e^{-qT}` (which catches sign and discount-factor errors a single
      hand-computed case cannot), and against **the exchange's own greeks** on
      7,618 liquid contracts from the stored chain snapshot — median absolute
      delta error **0.005**, with the residual tracking dividend yield exactly
      as theory predicts (TSLA, a non-payer, 0.0006; SPY 0.0043). A numbered
      assumptions register now heads the module. Original note follows.
- [ ] ~~Greeks on the pricer (original)~~ `research/option_pricer.py` has only
      `price_put` — it prices but does not differentiate, which is why the
      North Star's last clause ("how much would the position bleed if nothing
      happens") has never been answerable. Add `delta`, `gamma`, `vega`,
      `theta` and `itm_prob` from the same closed form already there. Copy
      nautilus's conventions verbatim so the numbers are legible to anyone off
      a desk: **vega scaled 0.01** (per 1 vol point), **theta scaled 1/365.25**
      (per calendar day). Pin each against a hand-computable case and against
      the stored Cboe greeks on a liquid SPY strike — an independent second
      opinion we now have for free (`docs/adr/0020`).
- [ ] **The Carry Budget** (`docs/adr/0021`'s missing Risk function), once
      greeks exist. Portfolio theta over the recommended basket IS the
      annualised bleed. Report it per candidate and for the whole set, against
      a stated annual budget, and show what fraction is consumed. Limits are
      declarative config, never agent-tunable (`docs/adr/0021` §5). Pairs with
      **shock scenarios** — `spot_shock` / `vol_shock` / `time_to_expiry_shock`
      as arguments, not a separate page: carry is what the hedge costs, shock
      is what it buys, and we currently surface neither.
- [ ] **Beta-weighted greeks and time-weighted vega.** Beta-weighting expresses
      delta/gamma in index terms — the professional form of what the Screen
      already does by hand, since everything here is ranked *versus SPY*, and
      the missing bridge from a sensitivity rank to a book-level exposure
      number. Time-weighted vega normalises to a 30-day base so the sweep's
      1-12 week tenors are actually comparable; today they are compared
      without it.
- [ ] **Make the cost model pluggable, like the pricer.** `OptionPricer` is a
      seam (`docs/adr/0004`); `research/backtest/brokerage.py`'s half-spread is
      one hardcoded assumption. hftbacktest names its unobservables
      (`QueueModel`, `LatencyModel`, with `RiskAdverseQueueModel` documented as
      the conservative default) and nautilus names its fill models. Give the
      fill assumption the same treatment with a conservative default, so the
      Bake-off can rank cost assumptions alongside metrics and the
      model-vs-market residual (`docs/MODEL_RESIDUAL.md`) becomes attributable
      to pricing versus execution.

## Ratchet reduction (2026-08-28 — the weekly cleanup agent's queue, `docs/adr/0023`)

`pyproject.toml`'s limits sit at today's worst offenders. Each item below is
"refactor this function, then lower the number in the same PR". They may only
ever go down.

- [x] **`research/backtest/ranking.py:rank_universe`** — cyclomatic complexity
      **14**, the value `max-complexity` is currently pinned to. 182 lines.
      **Done (weekly cleanup, 2026-08-30).** The 14 was mostly closures: ruff's
      mccabe folds a nested `def`'s own complexity into its enclosing function,
      so `_fragility`/`_vol_beta_for`/`_rank_one` being defined *inside*
      `rank_universe` added their combined complexity (11) on top of its own
      branching. Un-nested all three to module level (closure variables bundled
      into a `_RankContext` dataclass passed explicitly, `_rank_one` invoked via
      `functools.partial` in the thread pool), and split the composite-score +
      sort tail into `_score_and_sort`. `rank_universe` itself is now complexity
      **1** with no logic change — `tests/test_research_backtest_ranking.py`
      passes unchanged. New repo-wide worst offender is 11
      (`put_roll.run_put_roll` / `metric_screen.compare_metric_screens`, both
      below), so `max-complexity` moved **14 -> 11** in the same PR.
- [ ] **`research/backtest/put_roll.py:run_put_roll`** — still **13 keyword
      arguments**, pinning `max-args`. **Its statement count is no longer the
      `max-statements` pin** — a prior refactor (not logged here when it
      landed; found while doing the complexity item below) already split the
      roll loop into `_roll_model_cycles`/`_roll_market_cycles`/
      `_CycleAccumulation`, leaving `run_put_roll` at 48 statements against
      the 75 the ratchet still names it for. The actual `max-statements`
      worst offender today is `portfolio.py:run_portfolio` at 51 — lowering
      that ratchet **75 -> 51** needs no work on `run_put_roll` at all, just
      a PR confirming the number and updating the comment. `max-args=13` is
      still genuinely pinned by `run_put_roll`'s own signature, unreduced:
      bundling the strategy-spec keywords (`moneyness_pct`, `tenor_weeks`,
      `lookback_years`, `rate`, `commission_per_contract`, `spread_scale`)
      into a parameter object is still the honest fix, but note first that
      ~30 existing test call sites across
      `tests/test_research_backtest_{put_roll,portfolio,ranking,sweep}.py`
      and `test_research_backtest_quote_priced_roll.py` call it by keyword —
      changing its signature rewrites all of them, in tension with the
      "tests stay unchanged" rule this agent runs under. Consider a
      backward-compatible `RollSpec` overload, or accept the test-file
      rewrite explicitly and say so in the PR, rather than silently
      breaking the rule.
- [x] **`research/backtest/metric_screen.py:compare_metric_screens`** (complexity
      11). **Done (weekly cleanup, 2026-09-13).** Same nested-closure cause as
      `rank_universe` above: `_fragility` and its nested `_safe` were defined
      *inside* `compare_metric_screens`, folding their complexity into it.
      Un-nested both to module level (`_fragility` now takes `bench_ret`/
      `vol_changes`/`lookback_days` explicitly instead of closing over them;
      `_safe` renamed `_safe_metric`, also module-level) — no logic change,
      `tests/test_research_backtest_metric_screen.py` passes unchanged.
      `compare_metric_screens` itself is now complexity 7. In the same PR,
      also found and fixed `put_roll.py:_mark_to_market_curve` at complexity
      11 (the actual, undocumented co-holder of the old `max-complexity=11`
      pin — `run_put_roll` itself hadn't been the offender since whatever
      refactor is described in the item above, and the pyproject comment
      was never updated to say so): extracted the open-leg mark computation
      (model vs. real-quote, with the carried-forward-bid state machine)
      into `_mark_open_leg`, leaving `_mark_to_market_curve` at complexity 5.
      New repo-wide worst offenders are `ingestion/optionsdx.py:
      ingest_optionsdx` and `portfolio.py:run_portfolio`, both complexity
      10, so `max-complexity` moved **11 -> 10** in the same PR.
      **`index_replication.py:run_index_replication`** (121 lines, 11 args)
      is unrelated to this item (it pins nothing at today's `max-args=13`,
      `_roll_model_cycles`/`run_put_roll` both already sit at 13) and is
      left open below.
- [ ] **`index_replication.py:run_index_replication`** (121 lines, 11
      positional-eligible args) — split out from the item above since it
      doesn't pin any current ratchet value (`max-args=13`, `run_put_roll`
      and `_roll_model_cycles` sit there instead); still worth a parameter-
      object pass on its own merits if a future PR touches that module.
- [ ] **`api/putlab_routes.py`** is 669 lines, the largest module in the repo.
      Check whether it is still one thing before it becomes a god module; the
      flat `api/` layout is a documented deviation (`CLAUDE.md`) and splitting
      it needs `ARCHITECTURE.md` to agree, so this one may need an ADR.

## Found by validation (2026-08-27)

- [ ] **Pass a dividend yield through the roll backtest.** Discovered by
      `make greeks-check`, the new scoring of our greeks against the
      exchange's own (`docs/adr/0020` chains). The delta error orders itself
      by distribution yield exactly as theory demands — TSLA (no payout)
      0.0006, SPY 0.0043, **TLT 0.0601, HYG 0.1967** — because
      `research/backtest/put_roll.py` prices every name at `q = 0`.
      `index_replication` already passes `q`; the roll backtest does not. A
      0.20 delta error on HYG means we are pricing a different option than we
      think, and HYG and TLT sit in the universe *specifically* as the credit
      and rates hedges — the names the tail thesis most wants to be right
      about. Needs a per-symbol yield source (a keyless one: the distribution
      history is derivable from the OHLCV adapter's `close` vs `adj_close`
      divergence, which is already ingested). Until it lands, put prices and
      greeks on income names are biased cheap; say so where they are shown.

## Portfolio-repo increments (2026-08-27 — see `docs/PRIOR_ART.md` §6-§10)

From `AshJha0/quant-portfolio`. The first two are cheap and fix things that are
demonstrably wrong today; the rest are upgrades.

- [x] **Hysteresis on the regime classifier.** **Done 2026-08-27.**
      `HYSTERESIS_BAND = 1.0` VIX point, `classify_vix_series` in
      `contracts/regime.py`, wired through `label_vix_series`. Measured on the
      full 1990-2026 VIX history (9,255 days): regime transitions fall
      **753 -> 338 (-55%)** while only **6.5% of day-labels change** and the
      label mix barely moves (calm 4292->4137, elevated 3955->4100, crisis
      1008->1018) — it removed churn, not signal. `band=0.0` reproduces the old
      thresholds exactly, so the change stays auditable. **Not yet done: the
      re-count of how many existing `confirmed` verdicts survive** — that needs
      a sweep against the stored memory and is its own increment.
      Original note follows. `contracts/regime.py` uses hard
      VIX thresholds (calm < 17, elevated < 28), so a VIX oscillating
      16.9 -> 17.1 -> 16.8 flips regime three times in three days. Two
      thresholds with the current state as tiebreak fixes it in a few lines and
      needs no new model; the reference implementation reports 67-82% less
      turnover from exactly this change. **This is not cosmetic**:
      `docs/adr/0015` keys verdicts on `(rule_hash, regime)` and calls a rule
      `confirmed` once it paid in >= 2 regimes, so threshold chatter lets a rule
      collect its second regime from a boundary wobble. Pin it with a series
      that crosses the boundary repeatedly and assert the label changes once.
      Then re-count how many existing `confirmed` verdicts survive, and say so.
- [~] **Give every no-lookahead test a positive control** (`docs/PRIOR_ART.md`
      §9). **Partly done 2026-08-27, and the premise needed correcting**: the
      lake layer already had this — `test_lake_store.py` builds its restatement
      so it WOULD change the answer if it leaked, and `test_point_in_time_clock.py`
      proves the naive local clock would have disagreed. The real gap was one
      layer up, in **derived series**, where a "harmless denoising step"
      imports the future without touching the store at all. Closed for the
      regime timeline (`tests/test_contracts_regime_hysteresis.py` pairs the
      causal assertion with a centred-window control that must move).
      **Remaining**: audit the other derived series the same way — the fragility
      metrics' rolling windows, `research/regimes/timeline.py`'s callers, and
      anything in `research/backtest/` that z-scores or smooths over time.
- [ ] **Replace `MODEL_PRICED_MAX_MONEYNESS_PCT` with an arbitrage check.**
      That constant (10.0, `research/backtest/sweep.py`) is one hardcoded number
      standing in for "past here our premium is a rounding artefact"
      (`docs/adr/0018`) — same cutoff for every name, regime and tenor. The
      honest form is the **Durrleman condition** (`g >= 0`, positive implied
      density) plus a calendar check (total variance non-decreasing in T): the
      model is trustworthy exactly where the density it implies stays positive,
      and that boundary moves with vol and tenor. Supersedes 0018's constant
      with a measurement; needs an ADR since 0018 is constitution.
- [ ] **A model-governance tripwire for the pricer.** `docs/MODEL_RESIDUAL.md`
      measures the model-vs-market gap (+1.34%/yr, sign-flipping in crisis) but
      nothing states *at what point the model-priced backtest stops being
      trusted*. Borrow the VaR-backtesting discipline: a stated threshold and a
      consequence (the reference retires a method failing Kupiec or
      Christoffersen for two consecutive quarters). Now that the forward
      collection is running (`docs/adr/0020`), the residual can be recomputed
      continuously instead of once on 210 historical dates — so the tripwire has
      something live to trip on.
- [ ] **A numbered assumptions register per research module.** The constitution
      already requires that "the assumptions a number rests on and how much they
      move it" be visible where the result is shown; there is no artifact for
      it. Adopt the reference's form: a numbered list, each entry with **what
      breaks if it is violated**. Start with `option_pricer.py` (flat vol, no
      smile, European exercise, sticky-strike greeks) since that is where the
      known error lives.

## Strategy-family increments (2026-08-26 — from Kakushadze & Serur, *151 Trading Strategies*, SSRN 3247865)

Four increments from a read of the options (§2) and volatility (§7) chapters.
Ordered by how directly each attacks a known weakness rather than by how
interesting it is. Q6-Q8 in `docs/END_STATE.md` §4 are the questions these
serve; `docs/adr/0021` is why the risk-shaped one comes first.

- [ ] **Multiple-testing correction on the Bake-off (correctness, not a
      feature).** `research/backtest/metric_screen.py` ranks six-plus
      sensitivity metrics across strikes, tenors and regimes and reports a
      winner. That is a large grid, and the conventional t > 2.0 hurdle is
      exactly what Harvey, Liu & Zhu (2016) showed is wrong once a
      literature has tested hundreds of factors — they argue for ~t > 3.0.
      Report the number of comparisons alongside every ranking, apply a
      correction (Benjamini-Hochberg is the honest default; a Bonferroni
      bound is the conservative one), and surface both the raw and the
      adjusted verdict. Pin it with a test that feeds pure noise through the
      grid and asserts the corrected ranking declares no winner — the
      uncorrected one will happily name one, which is the whole point.
      **This changes existing published numbers**, so say so on the surface.
- [ ] **Measure the volatility risk premium the screen pays** (§4 Q6). VRP
      = implied minus subsequently-realized vol, per name, per roll. It is
      the headwind every S1 roll fights and the platform has never once
      measured it. Needs only what is already ingested (OHLCV realized vol
      + the vol complex; per-name IV arrives with `docs/adr/0020`'s
      collection). Then the payoff: a **VRP-adjusted sensitivity metric** —
      rank by sensitivity *per unit of premium paid over realized* rather
      than by raw sensitivity. That is the precise form of the README's
      "cheapness-adjusted variants", and it is a new column in the Screen,
      not a new page.
- [ ] **Put ratio backspread as a second structure** (§2.37, §4 Q8). Short
      one near-ATM put, long two further-OTM, often at zero or negative net
      debit — convexity kept, carry financed. It is the textbook answer to
      the single biggest practical objection to permanent put buying, and
      the engine already prices every leg it needs
      (`research/backtest/put_roll.py` + `option_pricer.py`). Add it behind
      the same pluggable structure interface as the naked put so the
      Bake-off can rank *structures* the way it ranks metrics, and report
      its carry against the naked put's in the Carry Budget. Respect
      `docs/adr/0018` — do not let the short leg wander past what the
      pricer can honestly price.
- [ ] **The dispersion test: cheap tails, or expensive beta?** (§4 Q7,
      §6.3.) Backtest the screened single-name basket against the SPY put
      that costs the *same premium*, like for like. Index IV carries a
      correlation risk premium that single names do not, so the basket
      should be cheaper per unit of tail — but correlations go to one in a
      crash, which is precisely what the index put is paid for. If the
      basket does not beat the equal-premium index put, S1's edge is a
      correlation short in a sensitivity screen's clothing. Cheap to run
      (both sides are model-priced today), and it is the single most
      decisive test of the thesis currently available.

## Metric-library increments (2026-08-24)

- [x] **Add a sixth sensitivity metric: vol beta.** Dio's standing directive
      (in-app feedback, still open: "prioritize the sensitivity leaderboard
      so I can see put candidates ranked daily") calls for growing the metric
      library (`docs/END_STATE.md` §5 milestone 3); the five metrics already
      wired into `research/backtest/ranking.py` (downside beta, co-skewness,
      co-kurtosis, tail beta, downside capture) are all co-movement-with-the-
      benchmark's-own-returns metrics. README's list also names **factor
      sensitivities** as a category, which nothing built so far covers.
      `research/metrics/vol_beta.py` fills that gap: beta of an asset's
      returns against VIX pct-changes (the volatility factor) instead of the
      benchmark's own returns -- differentiates names that share a downside
      beta but react differently to a pure vol-of-vol shock. VIX is already a
      live dataset (`ingestion/vix.py`), so no new source is needed. Pinned
      by a hand-computable exact-recovery case, a case proving it provably
      differs from ordinary beta-against-the-benchmark on the same asset
      series, and Hypothesis properties (linearity in the asset series,
      self-beta = 1), matching `downside_beta.py`'s test shape exactly.
      **Shipped standalone, not yet wired into `ranking.py`'s composite** --
      same "ship the pure function, wire it in once it has real data to run
      against" precedent `downside_beta.py` and `options_expiry.py` followed
      (see their entries above): wiring touches `RankedAsset`, the API
      schema, `RankingStrip.tsx` and the e2e fixtures, which is a second,
      separable increment. Sign convention documented in the module
      docstring: raw vol beta is typically negative for equities (they fall
      as VIX rises), so a composite wiring must negate it first, exactly as
      `ranking.py` already negates co-skewness.
- [x] **Wire `vol_beta` into `research/backtest/ranking.py`'s composite and
      `metric_screen.py`'s bake-off**, once ready to touch `RankedAsset` /
      the API schema / `RankingStrip.tsx` / e2e fixtures together -- the
      follow-up to the item above. **Done 2026-08-25.** `COMPOSITE_METRICS`
      now has five equal-weighted members (vol beta joins downside beta,
      co-skewness, tail beta, downside capture; co-kurtosis stays display-only
      per its existing exclusion). `research/regimes/timeline.py` gained
      `load_vix_close` (the raw close series `compute_regime_timeline` already
      built internally, now reusable) so both `ranking.py` and
      `metric_screen.py` can regress vol beta against VIX changes without a
      second VIX read helper. One deliberate deviation from the other four
      metrics: vol beta does NOT gate on the SPY benchmark being present in
      `rank_universe` (`_vol_beta_for` is unconditional, unlike `_fragility`)
      -- it regresses against VIX, not SPY, so it is estimable even when the
      benchmark is missing, and the "no benchmark" test now pins that the
      composite still gets one metric to average instead of coming back
      `None`. `metric_screen.py`'s bake-off is a seventh screen now (six raw
      metrics + composite); `_METRIC_FUNCS` intentionally still excludes vol
      beta (it needs a different regressor than the other five, which all
      share the SPY-aligned pair) -- `_ALL_METRIC_NAMES` is the iteration set
      everywhere a screen list is needed. Also added the `vol_beta` glossary
      entry (`frontend/src/content/concepts.ts`) and updated `fragility_score`'s
      formula string to name all five composite members, since it was already
      wrong after the vol-beta metric itself shipped standalone.
