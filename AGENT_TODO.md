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
- [ ] Add the underlying OHLCV ingestion adapter, following the VIX
      adapter's pattern (`docs/DATA_CONTRACTS.md` #1). **In flight — PR #1
      open, unmerged as of 2026-08-18.** Do not re-attempt this until that
      PR is either merged (then check it off here) or closed; the source
      turned out to need the same Yahoo-chart-JSON deviation as VIX (Stooq
      is anti-bot-blocked for symbol downloads too, same as it was for
      VIX), already documented in that PR.
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
- [ ] Add the sensitivity-leaderboard gold mart (`transforms/marts/`) + API
      endpoint (`api/routes/leaderboard.py`) + dashboard tile. Now has a
      metric to rank by (`downside_beta`, above); still needs the OHLCV PR
      merged first for real return series to compute it over.
- [ ] Add the event-calendar ingestion adapter for FOMC dates
      (`federalreserve.gov`, keyless) with the `announced_at` point-in-time
      field required by `docs/DATA_CONTRACTS.md` #5.
- [ ] Add the CPI release-schedule adapter (BLS, keyless), same shape as
      the FOMC adapter above.
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
- [ ] Add a second sensitivity metric (co-skewness) and the first
      cross-metric backtest comparison (`research/backtest/compare.py`),
      answering research question 1 for two metrics.
- [ ] Add the regime-panel gold mart + API endpoint + dashboard tile, using
      the vol complex + credit spreads once dataset #4 exists.
- [ ] Storage-growth optimization (not urgent): `DeltaLakeStore.write_bronze`
      still stores each ingest's **full** history for that date, not a diff,
      matching the pre-Delta Parquet layout's semantics (`docs/adr/0013`).
      Once a dataset's ingest volume makes this costly, consider a
      diff/merge write for that dataset specifically — must not change the
      as-of/immutability contract or the point-in-time tests.
