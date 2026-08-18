import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchCadence,
  fetchPutBacktest,
  fetchSweep,
  type CadenceResponse,
  type PutBacktestResponse,
  type SweepResponse,
} from '../../api/client'
import { CadencePanel } from './CadencePanel'
import { CyclesBars } from './CyclesBars'
import { EquityCurve } from './EquityCurve'
import { MemoryTeaser } from './MemoryTeaser'
import './putlab.css'
import { QuestionBar } from './QuestionBar'
import { StatBand } from './StatBand'
import { SweepHeatmap } from './SweepHeatmap'
import { PUTLAB_DEFAULT_CONTROLS, type PutLabControls } from './types'

type LoadState =
  | { status: 'loading' }
  | { status: 'no-data' }
  | { status: 'error'; message: string }
  | { status: 'ready'; backtest: PutBacktestResponse; sweep: SweepResponse; cadence: CadenceResponse }

// A slider/number drag fires many onChange events per second -- wait for the
// controls to settle before hitting the network.
const DEBOUNCE_MS = 250

// The Put Lab container: owns the question-builder state, fetches the three
// live endpoints (debounced, abortable), and lays out the mock's sections
// top-to-bottom. See tail-lab's docs/adr/0004 for the model-pricing caveat
// and docs/END_STATE.md §1.2/§1.5 for the endpoint contracts.
export function PutLab() {
  const [controls, setControls] = useState<PutLabControls>(PUTLAB_DEFAULT_CONTROLS)
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  const updateControls = (patch: Partial<PutLabControls>) => setControls((c) => ({ ...c, ...patch }))

  useEffect(() => {
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      setState({ status: 'loading' })
      Promise.all([
        fetchPutBacktest(controls, controller.signal),
        fetchSweep({ asset: controls.asset, notional: controls.notional, years: controls.years }, controller.signal),
        fetchCadence(controls.asset, controller.signal),
      ])
        .then(([backtest, sweep, cadence]) => setState({ status: 'ready', backtest, sweep, cadence }))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return
          if (err instanceof ApiError && err.status === 404) {
            setState({ status: 'no-data' })
            return
          }
          setState({ status: 'error', message: err instanceof Error ? err.message : String(err) })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- controls fields listed individually so the effect re-runs on value change, not identity
  }, [controls.asset, controls.notional, controls.moneyness_pct, controls.tenor_weeks, controls.years])

  return (
    <div className="putlab-root">
      <div className="wrap">
        <header className="top">
          <div className="brand">
            <div className="glyph" aria-hidden="true">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ color: 'var(--accent)' }}
              >
                <path d="M12 3v2M12 21v-3" />
                <path d="M3 11c0-4 4-6 9-6s9 2 9 6c-2-1.5-4-2-4 0 0-2-2-2-2 0 0-2-2-2-3 0 0-2-1.4-2-3 0-2-.5-4 0-4 2Z" />
              </svg>
            </div>
            <div>
              <h2>Put Lab</h2>
              <div className="sub">
                Ask what a tail-hedge would have done &mdash; invest in out-of-the-money puts, roll them, and watch
                the bleed and the payoffs across market regimes.
              </div>
            </div>
          </div>
          <div className="top-actions">
            <span
              className="pill caveat"
              title="Option premiums are Black-Scholes model prices using trailing realized volatility as an IV proxy, not real historical quotes."
            >
              <span className="dot" /> Model-priced
            </span>
          </div>
        </header>

        <QuestionBar controls={controls} onChange={updateControls} />

        {state.status === 'loading' && (
          <p className="putlab-status" role="status" aria-live="polite">
            Running the backtest...
          </p>
        )}
        {state.status === 'error' && (
          <p className="putlab-status putlab-status-error" role="alert">
            {state.message}
          </p>
        )}
        {state.status === 'no-data' && (
          <p className="putlab-status" role="status" aria-live="polite">
            No data yet for this asset and window &mdash; try a shorter lookback or a different asset.
          </p>
        )}

        {state.status === 'ready' && (
          <>
            <StatBand backtest={state.backtest} />

            <section className="panel">
              <EquityCurve equityCurve={state.backtest.equity_curve} cycles={state.backtest.cycles} />
            </section>

            <div className="grid2">
              <section className="panel" style={{ marginBottom: 0 }}>
                <CyclesBars cycles={state.backtest.cycles} notional={controls.notional} />
              </section>
              <section className="panel" style={{ marginBottom: 0 }}>
                <CadencePanel cadence={state.cadence} />
              </section>
            </div>

            <section className="panel" style={{ marginTop: 22 }}>
              <SweepHeatmap
                cells={state.sweep.cells}
                moneynessPct={controls.moneyness_pct}
                tenorWeeks={controls.tenor_weeks}
              />
            </section>
          </>
        )}

        <MemoryTeaser />

        <footer>
          <strong>Model-priced, not historical quotes.</strong> The underlying path is real daily OHLCV already in
          the lake; every option premium is a Black-Scholes model price with trailing realized volatility as the IV
          proxy (<span className="mono">docs/adr/0004</span>). tail-lab never trades &mdash; it hands you the read,
          you place the trade by hand.
        </footer>
      </div>
    </div>
  )
}
