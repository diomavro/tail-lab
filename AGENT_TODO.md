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
- [ ] **Memory layer, phase 2** — persist every Put Lab backtest as a
      verdict keyed by `(rule_hash, regime)`, so repeats are skipped and a
      strategy that only paid in one crash is flagged `regime_only`, never
      `confirmed` (Dio's hypothesis-memory spec, adapted to tail-lab's wall —
      no live/broker stages). Storage: DuckDB + JSON/parquet on Tigris (no new
      service). NEEDS AN ADR before enacting (new persistence subsystem).
      Makes the currently-static memory teaser in the Put Lab real.
- [ ] Live options-expiry **cadence adapter** — replace the static
      `contracts/options_calendar.py` table with a keyless read of Yahoo's
      `/v7/finance/options` expiration dates → derived avg gap + weekly/
      monthly classification, following the ingestion adapter shape.
- [ ] Extend the OHLCV adapter's default fetch range beyond `2y` (it fetched
      `5y` here only via an explicit `range_`), so the Put Lab's "last 4
      years" spans real history without a manual override.
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
- [ ] Add the FRED rates adapter (`docs/DATA_CONTRACTS.md` #3) — the FRED
      API key now exists as the `FRED_API_KEY` repo secret (`HUMAN_TODO.md`,
      done 2026-08-17), so this is unblocked. Must request the
      vintage/ALFRED-style `realtime_start`/`realtime_end` parameters and
      validate a required `vintage_date` column, per the point-in-time rule
      in `docs/DATA_CONTRACTS.md` #3 — a latest-value-only pull is a
      look-ahead bug, not a simplification.
- [ ] Add the FRED credit adapter (`docs/DATA_CONTRACTS.md` #4), same key
      and same vintage requirement as rates above.
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
      **Not done** (split out, still open below): the first cross-metric
      backtest comparison (`research/backtest/compare.py`) answering
      research question 1 for two metrics -- that's a separate, larger
      increment than wiring the second metric into the screening
      leaderboard was.
- [ ] The first cross-metric backtest comparison
      (`research/backtest/compare.py`), running the Put Lab backtest engine
      (`research/backtest/put_roll.py`) once per sensitivity metric's
      top-ranked candidates and comparing hit rate / payoff / bleed by
      regime (`docs/END_STATE.md` §1.5, §4 research question 1) -- now that
      downside beta and co-skewness (above) are both wired into the
      leaderboard, this is the natural next increment.
- [ ] Add the regime-panel gold mart + API endpoint + dashboard tile, using
      the vol complex + credit spreads once dataset #4 exists.
- [ ] Storage-growth optimization (not urgent): `DeltaLakeStore.write_bronze`
      still stores each ingest's **full** history for that date, not a diff,
      matching the pre-Delta Parquet layout's semantics (`docs/adr/0013`).
      Once a dataset's ingest volume makes this costly, consider a
      diff/merge write for that dataset specifically — must not change the
      as-of/immutability contract or the point-in-time tests.
