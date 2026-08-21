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
import { Leaderboard } from './Leaderboard'
import './putlab.css'
import { QuestionBar } from './QuestionBar'
import { TabNav } from './TabNav'
import { PUTLAB_DEFAULT_CONTROLS, PUTLAB_OOM_PRESETS, PUTLAB_TENORS, type PutLabControls } from './types'
import { BacktestView } from './views/BacktestView'
import { LearnView } from './views/LearnView'
import { PortfolioView } from './views/PortfolioView'
import { RegimeView } from './views/RegimeView'
import { ScreenView } from './views/ScreenView'

// The five workspace tabs. Screen is the default landing view -- "what should
// I hedge?" -- so a user sees a result without picking anything and scrolling.
export type TabId = 'screen' | 'backtest' | 'portfolio' | 'regime' | 'learn'

// One Backtest-tab read (backtest / sweep / cadence / verdict / data-quality).
// Split per-resource so the tape+stats render the moment the backtest resolves
// even while the (slower) sweep is still loading.
export type ResourceState<T> =
  | { status: 'loading' }
  | { status: 'no-data' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T }

// A slider/number drag fires many onChange events per second -- wait for the
// controls to settle before hitting the network.
const DEBOUNCE_MS = 250
// After the primary read, warm the preset rails in the background so a rail
// click is instant. Deferred so it never competes with the primary fetch.
const PREFETCH_DELAY_MS = 350

// Per-resource, in-session client caches (key = JSON of the resource's real
// deps). Bronze is immutable for a given as_of=today, so a seen combo never
// needs re-fetching within a session -- revisiting it is instant, no network,
// no loading flicker. Module-level so they survive tab switches / remounts.
const CACHE_CAP = 50
const BACKTEST_CACHE = new Map<string, PutBacktestResponse>()
const SWEEP_CACHE = new Map<string, SweepResponse>()
const CADENCE_CACHE = new Map<string, CadenceResponse>()
const DATA_QUALITY_CACHE = new Map<string, DataQualityResponse>()
const REGIME_VERDICT_CACHE = new Map<string, RegimeVerdictResponse>()

function cachePut<T>(cache: Map<string, T>, key: string, value: T): void {
  // Bounded LRU-ish: evict the oldest inserted key past the cap.
  if (!cache.has(key) && cache.size >= CACHE_CAP) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(key, value)
}

// A cached fetch: keyed on ONLY its real deps (encoded in `key`), served
// synchronously from `cache` on a hit (no loading flash, no network), else
// fetched (debounced), cached, and stored. `key === null` disables the fetch
// entirely (used to gate to the Backtest tab). Re-runs only when `key` changes.
function useCachedResource<T>(
  cache: Map<string, T>,
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
  debounceMs: number,
): ResourceState<T> {
  const [state, setState] = useState<ResourceState<T>>(() => {
    if (key !== null) {
      const cached = cache.get(key)
      if (cached !== undefined) return { status: 'ready', data: cached }
    }
    return { status: 'loading' }
  })

  useEffect(() => {
    if (key === null) return
    const cached = cache.get(key)
    if (cached !== undefined) {
      setState({ status: 'ready', data: cached }) // instant: no loading state, no network
      return
    }
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      setState({ status: 'loading' })
      fetcher(controller.signal)
        .then((data) => {
          cachePut(cache, key, data)
          setState({ status: 'ready', data })
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return
          if (err instanceof ApiError && err.status === 404) {
            setState({ status: 'no-data' })
            return
          }
          setState({ status: 'error', message: err instanceof Error ? err.message : String(err) })
        })
    }, debounceMs)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
    // fetcher is re-created every render but closes over exactly the params
    // encoded in `key`, so keying on `key` alone is correct.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return state
}

// Warm the backtest+verdict caches for one OOM%/tenor combo (nothing else varies
// with those axes). Swallows errors -- prefetch is best-effort.
function prefetchCombo(
  asset: string,
  notional: number,
  years: number,
  moneyness_pct: number,
  tenor_weeks: number,
  signal: AbortSignal,
): void {
  const key = JSON.stringify([asset, notional, moneyness_pct, tenor_weeks, years])
  const params = { asset, notional, moneyness_pct, tenor_weeks, years }
  if (!BACKTEST_CACHE.has(key)) {
    fetchPutBacktest(params, signal)
      .then((d) => cachePut(BACKTEST_CACHE, key, d))
      .catch(() => {})
  }
  if (!REGIME_VERDICT_CACHE.has(key)) {
    fetchRegimeVerdict(params, signal)
      .then((d) => cachePut(REGIME_VERDICT_CACHE, key, d))
      .catch(() => {})
  }
}

// The Put Lab workspace shell: owns the shared controls, the screening
// universe, the active tab, and the (lazily fetched, per-resource cached)
// single-name Backtest reads. It renders a persistent header, a tab bar, the
// shared question builder (on the parameterized tabs only), the active view,
// and a footer. See docs/adr/0004 for the model-pricing caveat and
// docs/END_STATE.md §1.2/§1.5 for the endpoint contracts.
export function PutLab() {
  const [controls, setControls] = useState<PutLabControls>(PUTLAB_DEFAULT_CONTROLS)
  const [universe, setUniverse] = useState<UniverseMember[]>([])
  const [activeTab, setActiveTab] = useState<TabId>('screen')
  // The fragility ranking is pinned above the tabs (Dio, 2026-08-21): picking a
  // ticker from a dropdown means you already knew which ticker you wanted,
  // which defeats the point of a screen whose job is to tell you. Collapsible
  // because 35 rows permanently on top would bury the tab it feeds.
  const [rankingCollapsed, setRankingCollapsed] = useState(false)

  const updateControls = (patch: Partial<PutLabControls>) => setControls((c) => ({ ...c, ...patch }))

  // Picking a name from the ranking selects it AND jumps to the backtest --
  // that is the whole point of the shortcut. Safe to navigate on click only
  // because the ranking stays pinned above the tabs, so the next pick is
  // always one click away rather than a trip back to another tab.
  const selectAsset = (asset: string) => {
    updateControls({ asset })
    setActiveTab('backtest')
  }

  // The screening universe drives the dropdowns; fetched once. Failure just
  // leaves the QuestionBar on its built-in fallback list.
  useEffect(() => {
    const controller = new AbortController()
    fetchUniverse(controller.signal)
      .then(setUniverse)
      .catch(() => {})
    return () => controller.abort()
  }, [])

  // The Backtest reads are only shown on the Backtest tab, so gate every key to
  // null off-tab (no fetch). Each key lists ONLY the params that resource truly
  // depends on -- so changing OOM%/tenor refetches backtest+verdict but leaves
  // the sweep (grid is identical, only the highlighted cell moves client-side),
  // cadence, and data-quality untouched.
  const onBacktest = activeTab === 'backtest'
  const btKey = onBacktest
    ? JSON.stringify([
        controls.asset,
        controls.notional,
        controls.moneyness_pct,
        controls.tenor_weeks,
        controls.years,
      ])
    : null
  const sweepKey = onBacktest
    ? JSON.stringify([controls.asset, controls.notional, controls.years])
    : null
  const assetKey = onBacktest ? JSON.stringify([controls.asset]) : null

  const backtest = useCachedResource(
    BACKTEST_CACHE,
    btKey,
    (signal) =>
      fetchPutBacktest(
        {
          asset: controls.asset,
          notional: controls.notional,
          moneyness_pct: controls.moneyness_pct,
          tenor_weeks: controls.tenor_weeks,
          years: controls.years,
        },
        signal,
      ),
    DEBOUNCE_MS,
  )
  const regimeVerdict = useCachedResource(
    REGIME_VERDICT_CACHE,
    btKey,
    (signal) =>
      fetchRegimeVerdict(
        {
          asset: controls.asset,
          notional: controls.notional,
          moneyness_pct: controls.moneyness_pct,
          tenor_weeks: controls.tenor_weeks,
          years: controls.years,
        },
        signal,
      ),
    DEBOUNCE_MS,
  )
  const sweep = useCachedResource(
    SWEEP_CACHE,
    sweepKey,
    (signal) =>
      fetchSweep(
        { asset: controls.asset, notional: controls.notional, years: controls.years },
        signal,
      ),
    DEBOUNCE_MS,
  )
  const cadence = useCachedResource(
    CADENCE_CACHE,
    assetKey,
    (signal) => fetchCadence(controls.asset, signal),
    DEBOUNCE_MS,
  )
  const dataQuality = useCachedResource(
    DATA_QUALITY_CACHE,
    assetKey,
    (signal) => fetchDataQuality(controls.asset, signal),
    DEBOUNCE_MS,
  )

  // Prefetch the preset rails after the primary read so a rail click is instant.
  // Bounded: the OOM presets at the current tenor + the tenor presets at the
  // current OOM (~8 combos, cache-skipping repeats), not a full grid. Sweep is
  // deliberately not prefetched -- it doesn't vary with OOM%/tenor. Re-runs on
  // any axis/asset/years/notional change (cancels in-flight prefetches).
  useEffect(() => {
    if (!onBacktest) return
    const controller = new AbortController()
    const handle = window.setTimeout(() => {
      const combos: [number, number][] = [
        ...PUTLAB_OOM_PRESETS.map((m): [number, number] => [m, controls.tenor_weeks]),
        ...PUTLAB_TENORS.map((t): [number, number] => [controls.moneyness_pct, t.weeks]),
      ]
      for (const [m, t] of combos) {
        if (m === controls.moneyness_pct && t === controls.tenor_weeks) continue // the primary
        prefetchCombo(controls.asset, controls.notional, controls.years, m, t, controller.signal)
      }
    }, PREFETCH_DELAY_MS)
    return () => {
      window.clearTimeout(handle)
      controller.abort()
    }
  }, [
    onBacktest,
    controls.asset,
    controls.notional,
    controls.years,
    controls.moneyness_pct,
    controls.tenor_weeks,
  ])

  // The question builder is the single shared control surface on Screen and
  // Portfolio. The Backtest tab replaces it with the ChartCockpit (the four
  // params live on the edges of its hero chart), so the bar is hidden there to
  // avoid a redundant duplicate control surface.
  const showQuestionBar = activeTab === 'screen' || activeTab === 'portfolio'
  const dq = dataQuality.status === 'ready' ? dataQuality.data : null

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
            </div>
          </div>
          <div className="top-actions">
            {dq && (
              <span
                className={`pill ${dq.n_suspicious === 0 ? 'pill-ok' : 'pill-warn'}`}
                title={
                  dq.n_suspicious === 0
                    ? `No bad ticks or stale runs in ${dq.n_bars} bars of ${dq.asset.toUpperCase()} data.`
                    : dq.flags.map((f) => `${f.date}: ${f.kind} — ${f.detail}`).join('\n')
                }
              >
                <span className="dot" />
                {dq.n_suspicious === 0 ? 'Data clean' : `${dq.n_suspicious} flagged`}
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

        <Leaderboard
          controls={controls}
          currentAsset={controls.asset}
          autoRun
          onSelectAsset={selectAsset}
          collapsed={rankingCollapsed}
          onToggleCollapse={() => setRankingCollapsed((c) => !c)}
        />

        <TabNav activeTab={activeTab} onChange={setActiveTab} />

        {showQuestionBar && <QuestionBar controls={controls} onChange={updateControls} universe={universe} />}

        <div role="tabpanel" id={`panel-${activeTab}`} aria-labelledby={`tab-${activeTab}`} tabIndex={0}>
          {activeTab === 'screen' && <ScreenView controls={controls} />}
          {activeTab === 'backtest' && (
            <BacktestView
              controls={controls}
              backtest={backtest}
              sweep={sweep}
              cadence={cadence}
              regimeVerdict={regimeVerdict.status === 'ready' ? regimeVerdict.data : null}
              onChange={updateControls}
              universe={universe}
            />
          )}
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
