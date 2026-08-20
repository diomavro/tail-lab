import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchCadence,
  fetchPutBacktest,
  fetchDataQuality,
  fetchRegimeVerdict,
  fetchSweep,
  type DataQualityResponse,
  fetchUniverse,
  type CadenceResponse,
  type PutBacktestResponse,
  type RegimeVerdictResponse,
  type SweepResponse,
  type UniverseMember,
} from '../../api/client'
import { FeedbackPanel } from '../FeedbackPanel'
import { ConceptInfo } from './ConceptInfo'
import './putlab.css'
import { QuestionBar } from './QuestionBar'
import { TabNav } from './TabNav'
import { PUTLAB_DEFAULT_CONTROLS, type PutLabControls } from './types'
import { BacktestView } from './views/BacktestView'
import { LearnView } from './views/LearnView'
import { PortfolioView } from './views/PortfolioView'
import { RegimeView } from './views/RegimeView'
import { ScreenView } from './views/ScreenView'

// The five workspace tabs. Screen is the default landing view -- "what should
// I hedge?" -- so a user sees a result without picking anything and scrolling.
export type TabId = 'screen' | 'backtest' | 'portfolio' | 'regime' | 'learn'

// The single-name backtest bundle, fetched lazily only for the Backtest tab.
export type BacktestState =
  | { status: 'loading' }
  | { status: 'no-data' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      backtest: PutBacktestResponse
      sweep: SweepResponse
      cadence: CadenceResponse
      regimeVerdict: RegimeVerdictResponse | null
      dataQuality: DataQualityResponse | null
    }

// A slider/number drag fires many onChange events per second -- wait for the
// controls to settle before hitting the network.
const DEBOUNCE_MS = 250

// The Put Lab workspace shell: owns the shared controls, the screening
// universe, the active tab, and the (lazily fetched) single-name backtest
// bundle. It renders a persistent header, a tab bar, the shared question
// builder (on the parameterized tabs only), the active view, and a footer.
// See docs/adr/0004 for the model-pricing caveat and docs/END_STATE.md
// §1.2/§1.5 for the endpoint contracts.
export function PutLab() {
  const [controls, setControls] = useState<PutLabControls>(PUTLAB_DEFAULT_CONTROLS)
  const [universe, setUniverse] = useState<UniverseMember[]>([])
  const [activeTab, setActiveTab] = useState<TabId>('screen')
  const [state, setState] = useState<BacktestState>({ status: 'loading' })

  const updateControls = (patch: Partial<PutLabControls>) => setControls((c) => ({ ...c, ...patch }))

  // The screening universe drives the dropdowns; fetched once. Failure just
  // leaves the QuestionBar on its built-in fallback list.
  useEffect(() => {
    const controller = new AbortController()
    fetchUniverse(controller.signal)
      .then(setUniverse)
      .catch(() => {})
    return () => controller.abort()
  }, [])

  // The single-name backtest bundle is only shown on the Backtest tab, so only
  // fetch it there -- never while the user is on Screen/Portfolio/Regime/Learn.
  useEffect(() => {
    if (activeTab !== 'backtest') return
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      setState({ status: 'loading' })
      Promise.all([
        Promise.all([
          fetchPutBacktest(controls, controller.signal),
          fetchSweep({ asset: controls.asset, notional: controls.notional, years: controls.years }, controller.signal),
          fetchCadence(controls.asset, controller.signal),
        ]),
        // The regime verdict needs VIX history too; if it's unavailable, degrade
        // gracefully (teaser hides) rather than blanking the whole view.
        fetchRegimeVerdict(controls, controller.signal).catch(() => null),
        fetchDataQuality(controls.asset, controller.signal).catch(() => null),
      ])
        .then(([[backtest, sweep, cadence], regimeVerdict, dataQuality]) =>
          setState({ status: 'ready', backtest, sweep, cadence, regimeVerdict, dataQuality }),
        )
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
  }, [activeTab, controls.asset, controls.notional, controls.moneyness_pct, controls.tenor_weeks, controls.years])

  // The question builder is the single shared control surface; it's irrelevant
  // on Regime/Learn, so it only shows on the three parameterized tabs.
  const showQuestionBar = activeTab === 'screen' || activeTab === 'backtest' || activeTab === 'portfolio'

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
            {state.status === 'ready' && state.dataQuality && (
              <span
                className={`pill ${state.dataQuality.n_suspicious === 0 ? 'pill-ok' : 'pill-warn'}`}
                title={
                  state.dataQuality.n_suspicious === 0
                    ? `No bad ticks or stale runs in ${state.dataQuality.n_bars} bars of ${state.dataQuality.asset.toUpperCase()} data.`
                    : state.dataQuality.flags
                        .map((f) => `${f.date}: ${f.kind} — ${f.detail}`)
                        .join('\n')
                }
              >
                <span className="dot" />
                {state.dataQuality.n_suspicious === 0
                  ? 'Data clean'
                  : `${state.dataQuality.n_suspicious} flagged`}
              </span>
            )}
            <span
              className="pill caveat"
              title="Option premiums are Black-Scholes model prices using trailing realized volatility as an IV proxy, not real historical quotes."
            >
              <span className="dot" /> Model-priced
              <ConceptInfo id="model_priced" />
            </span>
          </div>
        </header>

        <TabNav activeTab={activeTab} onChange={setActiveTab} />

        {showQuestionBar && <QuestionBar controls={controls} onChange={updateControls} universe={universe} />}

        <div role="tabpanel" id={`panel-${activeTab}`} aria-labelledby={`tab-${activeTab}`} tabIndex={0}>
          {activeTab === 'screen' && <ScreenView controls={controls} currentAsset={controls.asset} />}
          {activeTab === 'backtest' && <BacktestView controls={controls} state={state} />}
          {activeTab === 'portfolio' && <PortfolioView universe={universe} />}
          {activeTab === 'regime' && <RegimeView />}
          {activeTab === 'learn' && <LearnView />}
        </div>

        <footer>
          <p>
            <strong>Model-priced, not historical quotes.</strong> The underlying path is real daily OHLCV already in
            the lake; every option premium is a Black-Scholes model price with trailing realized volatility as the IV
            proxy (<span className="mono">docs/adr/0004</span>). tail-lab never trades &mdash; it hands you the read,
            you place the trade by hand.
          </p>
          <FeedbackPanel />
        </footer>
      </div>
    </div>
  )
}
