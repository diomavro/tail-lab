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

- [ ] Stand up the walking skeleton: VIX ingestion adapter
      (`ingestion/vix_complex.py`) → bronze → silver (validated against
      `contracts/datasets.py::VolComplexRow`) → one gold mart → one
      dashboard tile reading it, deployed to Fly (`docs/adr/0011`).
- [ ] Add the underlying OHLCV ingestion adapter (Stooq, keyless), following
      the VIX adapter's pattern (`docs/DATA_CONTRACTS.md` #1).
- [ ] Add a downside-beta sensitivity metric in `research/metrics/`, pinned
      by a test against a synthetic price path with a known analytic value.
- [ ] Add the sensitivity-leaderboard gold mart (`transforms/marts/`) + API
      endpoint (`api/routes/leaderboard.py`) + dashboard tile.
- [ ] Add the event-calendar ingestion adapter for FOMC dates
      (`federalreserve.gov`, keyless) with the `announced_at` point-in-time
      field required by `docs/DATA_CONTRACTS.md` #5.
- [ ] Add the CPI release-schedule adapter (BLS, keyless), same shape as
      the FOMC adapter above.
- [ ] Write the first adversarial point-in-time test for
      `lake/asof.py` — construct a scenario where leaking tomorrow's OHLCV
      bar would change a backtest result, and assert it doesn't
      (`docs/adr/0009`).
- [ ] Add the Black-Scholes put pricer (`research/pricing/black_scholes.py`)
      behind the `OptionPricer` protocol, pinned against the closed-form BS
      put formula for a hand-computable (S, K, T, r, σ).
- [ ] Wire up `import-linter` in CI to machine-check the module dependency
      direction in `ARCHITECTURE.md`, including the `api` ↛ `ingestion`
      carve-out.
- [ ] Add a second sensitivity metric (co-skewness) and the first
      cross-metric backtest comparison (`research/backtest/compare.py`),
      answering research question 1 for two metrics.
- [ ] Add the regime-panel gold mart + API endpoint + dashboard tile, using
      the vol complex + credit spreads once dataset #4 exists.
