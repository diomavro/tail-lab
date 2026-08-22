import { useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  fetchAccuracy,
  fetchCadence,
  fetchDataQuality,
  fetchPutBacktest,
  fetchPutLabLeaderboard,
  fetchRegimes,
  fetchRegimeVerdict,
  fetchSweep,
  fetchUniverse,
  fetchVixStretch,
  type AccuracyResponse,
  type CadenceResponse,
  type DataQualityResponse,
  type PutBacktestResponse,
  type PutLabLeaderboardResponse,
  type RegimeTimelineView,
  type RegimeVerdictResponse,
  type SweepResponse,
  type UniverseMember,
  type VixStretchResponse,
} from '../../api/client'
import { FeedbackPanel } from '../FeedbackPanel'
import { fmtPrice } from './format'
import { ParamRail } from './ParamRail'
import './putlab.css'
import { TabNav } from './TabNav'
import { PUTLAB_DEFAULT_CONTROLS, PUTLAB_OOM_PRESETS, PUTLAB_TENORS, type PutLabControls } from './types'
import { BakeOffView } from './views/BakeOffView'
import { GlossaryView } from './views/GlossaryView'
import { PortfolioView } from './views/PortfolioView'
import { RecommendationsView } from './views/RecommendationsView'
import { RegimeView } from './views/RegimeView'
import { WorkspaceView } from './views/WorkspaceView'

/* The workspace shell.
 *
 * Two structural changes from the file this replaces:
 *
 *  1. Screen and Backtest are one tab. The old split meant picking a name on one
 *     tab and reading its result on another, with the ranking pinned above both
 *     to bridge the gap. Now the ranking is a one-line strip at the top of the
 *     Workspace that expands to the full table, and a click lands the result in
 *     the same view.
 *  2. One control surface. The old QuestionBar (Screen/Portfolio) and
 *     ChartCockpit (Backtest) were two competing ways to set the same four
 *     params, so a user had to learn where the controls lived per tab.
 *     ParamRail is the only one, and it is present on every tab.
 *
 * The page scrolls normally. The old fixed 100dvh shell with an inner scroller
 * pinned four bands above the result, which left the hero chart a few hundred
 * pixels on a laptop.
 */

export type TabId =
  | 'workspace'
  | 'recommendations'
  | 'portfolio'
  | 'bakeoff'
  | 'regime'
  | 'glossary'

/** Paper is the light sheet, plate the negative. Persisted, never inferred from
 *  the OS: which sheet a trading page prints on is the reader's call. */
export type Sheet = 'paper' | 'plate'

// One resource read (backtest / sweep / accuracy / ...). Split per-resource so
// the tape+stats render the moment the backtest resolves even while the
// (slower) sweep is still loading.
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
const ACCURACY_CACHE = new Map<string, AccuracyResponse>()
const RANKING_CACHE = new Map<string, PutLabLeaderboardResponse>()

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
// entirely (used to gate off-tab). Re-runs only when `key` changes.
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

const SHEET_KEY = 'putlab.sheet'

function readSheet(): Sheet {
  try {
    return localStorage.getItem(SHEET_KEY) === 'plate' ? 'plate' : 'paper'
  } catch {
    return 'paper' // private windows and blocked site data both throw here
  }
}

export function PutLab() {
  const [controls, setControls] = useState<PutLabControls>(PUTLAB_DEFAULT_CONTROLS)
  const [tab, setTab] = useState<TabId>('workspace')
  const [universe, setUniverse] = useState<UniverseMember[]>([])
  const [regimes, setRegimes] = useState<RegimeTimelineView | null>(null)
  const [vix, setVix] = useState<VixStretchResponse | null>(null)
  const [sheet, setSheet] = useState<Sheet>(readSheet)

  const update = (patch: Partial<PutLabControls>) => setControls((c) => ({ ...c, ...patch }))

  useEffect(() => {
    try {
      localStorage.setItem(SHEET_KEY, sheet)
    } catch {
      // A sheet that cannot be remembered still has to render.
    }
  }, [sheet])

  // Market-wide reads, fetched once: they key off nothing in `controls`.
  useEffect(() => {
    const c = new AbortController()
    fetchUniverse(c.signal).then(setUniverse).catch(() => {})
    fetchRegimes(c.signal).then(setRegimes).catch(() => {})
    fetchVixStretch(c.signal).then(setVix).catch(() => {})
    return () => c.abort()
  }, [])

  // The single-name reads are only shown on Workspace, so gate every key to
  // null off-tab (no fetch). Each key lists ONLY the params that resource truly
  // depends on -- so changing OOM%/tenor refetches backtest+verdict but leaves
  // the sweep (grid is identical, only the highlighted cell moves client-side),
  // cadence, and data-quality untouched.
  const onWorkspace = tab === 'workspace'
  const btKey = onWorkspace
    ? JSON.stringify([
        controls.asset,
        controls.notional,
        controls.moneyness_pct,
        controls.tenor_weeks,
        controls.years,
      ])
    : null
  const sweepKey = onWorkspace ? JSON.stringify([controls.asset, controls.notional, controls.years]) : null
  // The accuracy companion. Keyed on the same axes as the backtest minus
  // notional -- the model's error is a rate, so it does not depend on how much
  // was spent. Fetched independently so a slow residual never delays the tape,
  // and NOT gated to Workspace: the Regime view prints the same per-regime
  // residuals, and gating them off-tab is what made that panel read
  // "unmeasured" for a measured window.
  const accuracyKey = JSON.stringify([
    controls.asset,
    controls.moneyness_pct,
    controls.tenor_weeks,
    controls.years,
  ])
  // The rail's provenance block is on every tab, so these two are never gated.
  const assetKey = JSON.stringify([controls.asset])
  // The ranking feeds the Workspace's opening line and the whole
  // Recommendations table -- Portfolio's fragile-basket button screens on its
  // own fixed axes. It is by far the most expensive read in the app (a strike x
  // tenor sweep per name across the universe), so it is gated to the two tabs
  // that show it. Keyed on the screening axes only: which names are most
  // fragile does not depend on how much premium you would spend.
  const showsRanking = onWorkspace || tab === 'recommendations'
  const rankKey = showsRanking
    ? JSON.stringify([controls.moneyness_pct, controls.tenor_weeks, controls.years])
    : null

  const btParams = {
    asset: controls.asset,
    notional: controls.notional,
    moneyness_pct: controls.moneyness_pct,
    tenor_weeks: controls.tenor_weeks,
    years: controls.years,
  }

  const backtest = useCachedResource(BACKTEST_CACHE, btKey, (s) => fetchPutBacktest(btParams, s), DEBOUNCE_MS)
  const regimeVerdict = useCachedResource(
    REGIME_VERDICT_CACHE,
    btKey,
    (s) => fetchRegimeVerdict(btParams, s),
    DEBOUNCE_MS,
  )
  const accuracy = useCachedResource(
    ACCURACY_CACHE,
    accuracyKey,
    (s) =>
      fetchAccuracy(
        {
          asset: controls.asset,
          moneyness_pct: controls.moneyness_pct,
          tenor_weeks: controls.tenor_weeks,
          years: controls.years,
        },
        s,
      ),
    DEBOUNCE_MS,
  )
  const sweep = useCachedResource(
    SWEEP_CACHE,
    sweepKey,
    (s) => fetchSweep({ asset: controls.asset, notional: controls.notional, years: controls.years }, s),
    DEBOUNCE_MS,
  )
  const cadence = useCachedResource(CADENCE_CACHE, assetKey, (s) => fetchCadence(controls.asset, s), DEBOUNCE_MS)
  const dataQuality = useCachedResource(
    DATA_QUALITY_CACHE,
    assetKey,
    (s) => fetchDataQuality(controls.asset, s),
    DEBOUNCE_MS,
  )
  const ranking = useCachedResource(
    RANKING_CACHE,
    rankKey,
    (s) =>
      fetchPutLabLeaderboard(
        { moneyness_pct: controls.moneyness_pct, tenor_weeks: controls.tenor_weeks, years: controls.years },
        s,
      ),
    DEBOUNCE_MS,
  )

  // Prefetch the preset rails after the primary read so a rail click is instant.
  // Bounded: the OOM presets at the current tenor + the tenor presets at the
  // current OOM (~8 combos, cache-skipping repeats), not a full grid. Sweep is
  // deliberately not prefetched -- it doesn't vary with OOM%/tenor.
  useEffect(() => {
    if (!onWorkspace) return
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
    onWorkspace,
    controls.asset,
    controls.notional,
    controls.years,
    controls.moneyness_pct,
    controls.tenor_weeks,
  ])

  /** Picking a row lands on the cell that row advertised -- the ranking's
   *  headline is each name's best cell over its whole strike x tenor grid, so
   *  opening at the previously selected strike would show a different (usually
   *  worse) number than the row the reader just clicked. */
  const selectAsset = (asset: string, best?: { moneyness_pct: number; tenor_weeks: number }) => {
    update(best ? { asset, ...best } : { asset })
    setTab('workspace')
  }

  const bt = backtest.status === 'ready' ? backtest.data : null
  const dq = dataQuality.status === 'ready' ? dataQuality.data : null
  const cad = cadence.status === 'ready' ? cadence.data : null

  const dateline = useMemo(() => {
    const rows: { k: string; v: string }[] = []
    const member = universe.find((m) => m.symbol.toLowerCase() === controls.asset.toLowerCase())
    rows.push({ k: 'Name', v: `${controls.asset.toUpperCase()} ${member?.name ?? ''}`.trim() })
    if (bt) {
      rows.push({ k: 'Spot', v: fmtPrice(bt.spot) })
      rows.push({
        k: 'Strike',
        v: `${fmtPrice(bt.spot * (1 - controls.moneyness_pct / 100))} (${controls.moneyness_pct}% OOM)`,
      })
      rows.push({ k: 'r', v: `${(bt.rate * 100).toFixed(2)}%` })
    }
    if (regimes) rows.push({ k: 'Regime', v: regimes.current })
    if (vix) rows.push({ k: 'VIX', v: vix.close.toFixed(1) })
    return rows
  }, [universe, controls.asset, controls.moneyness_pct, bt, regimes, vix])

  return (
    <div className="putlab-root" data-theme={sheet}>
      <div className="pl-wrap">
        <div className="pl-rule-thick" />
        <header className="pl-masthead">
          <div className="pl-brand">
            <h1>Put Lab</h1>
            <span className="pl-brand-sub">Tail&nbsp;Risk Desk</span>
          </div>
          <div className="pl-masthead-right">
            {bt && <span className="pl-micro">{bt.as_of}</span>}
            <div className="pl-seg" role="radiogroup" aria-label="Sheet">
              {(['paper', 'plate'] as Sheet[]).map((s) => (
                <label className="pl-seg-opt" key={s}>
                  <input type="radio" name="pl-sheet" checked={sheet === s} onChange={() => setSheet(s)} />
                  {s === 'paper' ? 'Paper' : 'Plate'}
                </label>
              ))}
            </div>
          </div>
        </header>
        <div className="pl-rule-thin" />

        <dl className="pl-dateline">
          {dateline.map((r) => (
            <span key={r.k}>
              <dt>{r.k}</dt>
              <dd>{r.v}</dd>
            </span>
          ))}
          {dq && (
            <span className={`pl-tag ${dq.n_suspicious === 0 ? 'pl-tag-ok' : 'pl-tag-bad'}`}>
              {dq.n_suspicious === 0
                ? `Data clean · ${dq.n_bars} bars`
                : `${dq.n_suspicious} flagged of ${dq.n_bars}`}
            </span>
          )}
          <span className="pl-caveat pl-micro">Model-priced · Black&ndash;Scholes on trailing RV</span>
        </dl>
        <div className="pl-rule-hair" />

        <TabNav activeTab={tab} onChange={setTab} />
        <div className="pl-rule-hair" style={{ marginBottom: 24 }} />

        <div className="pl-shell">
          <ParamRail
            controls={controls}
            onChange={update}
            universe={universe}
            dataQuality={dq}
            cadence={cad}
            asOf={bt ? bt.as_of : null}
          />

          <main className="pl-main" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0}>
            {tab === 'workspace' && (
              <WorkspaceView
                controls={controls}
                onChange={update}
                backtest={backtest}
                sweep={sweep}
                accuracy={accuracy}
                ranking={ranking}
                regimeVerdict={regimeVerdict}
                regimes={regimes}
                onSelectAsset={selectAsset}
              />
            )}
            {tab === 'recommendations' && (
              <RecommendationsView
                ranking={ranking}
                controls={controls}
                onSelectAsset={selectAsset}
              />
            )}
            {tab === 'portfolio' && <PortfolioView universe={universe} controls={controls} />}
            {tab === 'bakeoff' && <BakeOffView controls={controls} />}
            {tab === 'regime' && <RegimeView regimes={regimes} vix={vix} accuracy={accuracy} />}
            {tab === 'glossary' && <GlossaryView />}
          </main>
        </div>

        <footer className="pl-footer">
          <p>
            <strong>Model-priced, not historical quotes.</strong> The underlying path is real daily OHLCV from
            the lake; every option premium is a Black&ndash;Scholes model price with trailing realized
            volatility standing in for implied (see adr/0004). tail-lab never trades &mdash; it hands you the
            read, you place the trade by hand.
          </p>
          <FeedbackPanel />
        </footer>
      </div>
    </div>
  )
}
