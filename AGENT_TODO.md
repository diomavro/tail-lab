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
- [ ] Add the event-calendar ingestion adapter for FOMC dates
      (`federalreserve.gov`, keyless) with the `announced_at` point-in-time
      field required by `docs/DATA_CONTRACTS.md` #5. Sourcing note
      (2026-08-19, `docs/DATA_SOURCING.md` §2): HTML only — the ICS feed
      404s; current page covers 2021–2027, `fomc_historical.htm` year
      pages reach 1936.
- [ ] Add the CPI release-schedule adapter (BLS, keyless), same shape as
      the FOMC adapter above. Sourcing note (2026-08-19): `bls.gov` 403s
      non-browser clients — send browser-like headers; archived
      *scheduled-release-date* PDFs go back to ≥2006
      (`bls.gov/bls/archived_sched.htm`), exactly what the point-in-time
      `announced_at` rule needs.

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
- [ ] **VIX futures term-structure ingestion** (keyless, big free win):
      per-contract daily settlement CSVs
      `cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX/VX_{expiry}.csv`
      + pre-2013 archive `.../resources/futures/archive/volume-and-price/CFE_{M}{YY}_VX.csv`
      → full VX history 2004→present; build the constant-maturity curve
      in `transforms/`. New contract in `contracts/`, adapter follows the
      VIX shape.
- [ ] **Point-in-time S&P 500 constituents ingestion** from
      `github.com/fja05680/sp500` (MIT, maintained, 1996→present; raw CSV
      over HTTPS, keyless) — closes `docs/adr/0010` at membership
      granularity. Cross-check row counts against Wikipedia's "Historical
      components of the S&P 500". Wire the survivorship caveat into any
      result that still uses current-constituents.
- [ ] **Earnings-calendar adapter** via Nasdaq's keyless endpoint
      (`api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`, browser UA
      + JSON Accept header; verified back to 2010) → `EARNINGS` rows in
      dataset #5. Unofficial endpoint: throttle, cache, and treat errors
      as "retry tomorrow", not hard failures.
- [ ] **Switch OHLCV primary to Tiingo** once the key exists
      (`HUMAN_TODO.md` phase 1): new adapter (500 unique symbols/month
      budget — plan symbol rotation), demote Yahoo to fallback, update
      `docs/DATA_CONTRACTS.md` #1's source section in the same PR. Then
      backfill delisted ex-constituents enumerated from the PIT membership
      file (spot-check LEH/BSC/WM/SIVB actually return data first).
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
- [ ] Widen the regime classifier (`research/regimes/timeline.py`) from
      VIX-complex-only to also weigh credit spreads, once the FRED credit
      adapter above exists — closes the scope gap noted on the "regime-panel
      gold mart" item below.
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
- [~] **Migrate the vol complex off Yahoo onto the Cboe CDN, and finish it.**
      **Source migration done 2026-08-21** — `ingestion/vix.py` now resolves
      an ordered chain (Cboe primary, Yahoo fallback) via the new
      `ingestion/sources.py`, and the first live run committed **9,255 rows
      covering 1990-01-02 → 2026-08-20**, against the ~126 rows Yahoo's
      6-month default had been giving. `ingestion/ohlcv.py` (Nasdaq primary)
      and `ingestion/options_expiry.py` (Cboe chain primary) moved in the
      same change, so **no adapter has Yahoo as its primary any more**.
      **Still open, and the reason this is `[~]` not `[x]`:** only `VIX` is
      ingested, and only its `CLOSE`. `VIX3M`/`VIX9D`/`VVIX`/**`SKEW`** are
      confirmed live on the same host and still unwired — and `SKEW` is the
      hard blocker on the skew-aware pricer below. Widening the committed
      shape to OHLC touches `transforms/vix.py`, `research/vix_stretch.py`
      and the dashboard tile, so treat that as the real work here.
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

- [ ] **`options_expiry` is a dangling branch — wire it or retire it.**
      Surfaced by writing `docs/DATA_FLOW.md` §3.1. The adapter
      (`ingestion/options_expiry.py`, with a Cboe-primary fallback chain) and
      the transform (`transforms/options_expiry.py`) both exist and are
      tested, but there is **no `make ingest-options-expiry` target**, **no
      `options_expiry_*` partition in production bronze** (verified
      2026-08-22), and `/api/putlab/cadence` answers from the static catalogue
      in `contracts/options_calendar.py` instead. The code path is real and
      the data path is inert — the shape that looks wired on a dependency
      graph and does nothing in production.
      **Decide, do not drift:** either add the ingest target and point the
      cadence endpoint at the lake (real, per-symbol expiration cadence
      instead of a hand-maintained list), or delete the adapter and transform
      and say in `docs/DATA_CONTRACTS.md` that cadence is deliberately static.
      Both are defensible; leaving tested code that nothing runs is not.
      **Acceptance:** either way, `docs/DATA_FLOW.md` §3.1 stops describing a
      dangling branch.

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
- [ ] **Minneapolis Fed MPD adapter** (free, keyless, official). Risk-neutral
      density statistics for the S&P 500 backed out of real option prices by
      Breeden-Litzenberger:
      `https://www.minneapolisfed.org/-/media/files/banking/mpd/mpd_stats.csv`
      (dictionary at `mpd_data_dictionary.csv`). The `sp12m` market runs
      **2007-01-12 to 2026-08-19, 821 weekly observations**, carrying `mu`,
      `sd`, `skew`, `kurt`, `p10`/`p50`/`p90` and the probability of a ±20%
      move. Weekly and single-tenor, so it is a *calibration and validation
      target* for the skew-aware pricer through the 2008 crisis — not a
      chain and not a substitute for one. Ingest the `sp12m` rows at
      minimum; the file also carries per-firm densities (aig, citi, bac, gs,
      ms...) that the fragility screen may want later.

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
