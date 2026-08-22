import { useEffect, useRef, useState } from 'react'
import { ApiError, fetchPutLabLeaderboard, type RankedAsset } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtPct } from './format'
import type { PutLabControls } from './types'

// The fragility screen: rank the universe by how FRAGILE each name is (vs the
// market — downside beta, co-skewness, co-kurtosis, tail beta, downside
// capture, combined), and show the
// model-priced put payoff alongside. The thesis is timing-free: hold puts on
// the most fragile names; the payoff comes from the fragility, not a forecast.
// Runs ~35 backtests server-side, so it's an explicit action, not auto-fetch.

type SortKey = keyof Pick<
  RankedAsset,
  | 'fragility_score'
  | 'downside_beta'
  | 'co_skewness'
  | 'co_kurtosis'
  | 'tail_beta'
  | 'downside_capture'
  | 'roi_on_premium'
  | 'hit_rate'
  | 'name'
  | 'best_annualized'
>

type State =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: RankedAsset[] }

const num = (v: number | null, d: number): string => (v === null ? '—' : v.toFixed(d))

const VERDICT_LABEL: Record<RankedAsset['verdict'], string> = {
  confirmed: 'confirmed',
  regime_only: 'regime only',
  failed: 'failed',
  untested: 'untested',
}

export function Leaderboard({
  controls,
  currentAsset,
  autoRun = false,
  onSelectAsset,
  collapsed = false,
  onToggleCollapse,
}: {
  controls: PutLabControls
  currentAsset: string
  autoRun?: boolean
  // Picking a row is the primary way to choose what to backtest -- the old
  // dropdown made you already know the ticker you wanted, which defeats the
  // point of a screen whose whole job is telling you which ticker to want.
  // Picking carries the row's *best* parameters, not just its ticker, so the
  // backtest opens on exactly the cell whose number the row advertised.
  onSelectAsset?: (asset: string, best?: { moneyness_pct: number; tenor_weeks: number }) => void
  collapsed?: boolean
  onToggleCollapse?: () => void
}) {
  const [state, setState] = useState<State>({ status: 'idle' })
  const [sortKey, setSortKey] = useState<SortKey>('fragility_score')
  const [asc, setAsc] = useState(false)
  // Compact by default: rank, name, and the one number that decides anything.
  // The other eight columns are the evidence behind the ranking, not the
  // ranking, and having them all on screen pushed the actual result below the
  // fold -- which is what the workspace exists to keep in view.
  const [showAllMetrics, setShowAllMetrics] = useState(false)
  const didAutoRun = useRef(false)

  const run = () => {
    setState({ status: 'loading' })
    fetchPutLabLeaderboard({
      moneyness_pct: controls.moneyness_pct,
      tenor_weeks: controls.tenor_weeks,
      years: controls.years,
    })
      .then((resp) => setState({ status: 'ready', rows: resp.ranked }))
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? `Ranking failed (${err.status})`
            : err instanceof Error
              ? err.message
              : String(err)
        setState({ status: 'error', message })
      })
  }

  // Fire the (expensive) screen once on mount when the view asks for it, so the
  // Screen tab lands on results instead of an empty "click to run" panel. Guard
  // so it only fires once and only from the idle state.
  useEffect(() => {
    if (autoRun && !didAutoRun.current && state.status === 'idle') {
      didAutoRun.current = true
      run()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run-once-on-mount guard
  }, [autoRun])

  const sortBy = (key: SortKey) => {
    if (key === sortKey) setAsc((a) => !a)
    else {
      setSortKey(key)
      setAsc(key === 'name') // names default A→Z, numbers default high→low
    }
  }

  const rows =
    state.status === 'ready'
      ? [...state.rows].sort((a, b) => {
          const av = a[sortKey]
          const bv = b[sortKey]
          if (typeof av === 'string') return asc ? av.localeCompare(bv as string) : (bv as string).localeCompare(av)
          // nulls (unestimable fragility) always sort last, regardless of direction
          const an = (av as number | null) ?? Number.NEGATIVE_INFINITY
          const bn = (bv as number | null) ?? Number.NEGATIVE_INFINITY
          const cmp = an - bn
          return asc ? cmp : -cmp
        })
      : []

  const arrow = (key: SortKey) => (key === sortKey ? (asc ? ' ▲' : ' ▼') : '')

  return (
    <section className="panel lb-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">
            The fragility screen
            <ConceptInfo id="fragility_thesis" />
          </span>
          <h2 style={{ marginTop: 6 }}>Which names are most fragile?</h2>
          {!collapsed && (
            <div className="hint">
              Most fragile first. <strong>Best/yr</strong> is the highest annualized return each
              name reached anywhere on its strike &times; tenor grid;{' '}
              {onSelectAsset && (
                <>
                  <strong>click any row</strong> to open the backtest on exactly that cell.{' '}
                </>
              )}
              {showAllMetrics && (
                <>
                  Fragility is downside beta, co-skewness, co-kurtosis, tail beta and downside
                  capture combined &mdash; the edge is timing-free, so the payoff comes from the
                  fragility, not a forecast of <em>when</em>. <span className="mono">Put P&amp;L</span>{' '}
                  is the model-priced result at the{' '}
                  <span className="mono">
                    {controls.moneyness_pct}% OOM &middot; {controls.tenor_weeks}-week
                  </span>{' '}
                  parameters set below, so you can spot <em>cheap fragility</em>: fragile names
                  whose puts still paid.
                </>
              )}
            </div>
          )}
        </div>
        <div className="lb-head-actions">
          {onSelectAsset && (
            <span className="lb-selected" title="The name every tab is currently showing">
              showing <strong>{currentAsset.toUpperCase()}</strong>
            </span>
          )}
          {state.status === 'ready' && !collapsed && (
            <button
              className="lb-collapse"
              onClick={() => setShowAllMetrics((v) => !v)}
              aria-expanded={showAllMetrics}
              title={
                showAllMetrics
                  ? 'Show only the best annualized return'
                  : 'Show the fragility metrics behind the ranking'
              }
            >
              {showAllMetrics ? 'Fewer columns' : 'All metrics'}
            </button>
          )}
          <button className="lb-run" onClick={run} disabled={state.status === 'loading'}>
            {state.status === 'loading' ? 'Screening 35 names…' : 'Screen the universe'}
          </button>
          {onToggleCollapse && (
            <button
              className="lb-collapse"
              onClick={onToggleCollapse}
              aria-expanded={!collapsed}
              title={collapsed ? 'Show the full ranking' : 'Collapse the ranking'}
            >
              {collapsed ? 'Expand ▾' : 'Collapse ▴'}
            </button>
          )}
        </div>
      </div>

      {state.status === 'loading' && (
        <p className="putlab-status" role="status" aria-live="polite">
          Running a model backtest on every name &mdash; this takes ~20 seconds.
        </p>
      )}
      {state.status === 'error' && (
        <p className="putlab-status putlab-status-error" role="alert">
          {state.message}
        </p>
      )}

      {state.status === 'ready' && !collapsed && (
        <div className="lb-scroll">
          <table className="lb-table mono">
            <thead>
              <tr>
                <th className="lb-num">#</th>
                <th className="lb-sort" onClick={() => sortBy('name')}>
                  Name{arrow('name')}
                </th>
                <th
                  className="lb-sort lb-num lb-best"
                  onClick={() => sortBy('best_annualized')}
                  title="The best annualized return this name reached anywhere on its strike x tenor grid, and the cell that got there. Clicking the row opens the backtest on exactly that cell."
                >
                  Best/yr{arrow('best_annualized')}
                </th>
                {showAllMetrics && (
                  <>
                    <th className="lb-sort lb-num" onClick={() => sortBy('fragility_score')} title="Composite fragility (0–100): equal-weight blend of downside beta, co-skewness, tail beta & downside capture, most fragile first. Co-kurtosis is shown but excluded — it rewards co-movement with the market's own tails, so it flags broad indices.">
                      Frag{arrow('fragility_score')}
                    </th>
                    <th className="lb-sort lb-num" onClick={() => sortBy('downside_beta')} title="Downside beta vs SPY">
                      Dβ⁻{arrow('downside_beta')}
                    </th>
                    <th className="lb-sort lb-num" onClick={() => sortBy('co_skewness')} title="Co-skewness (more negative = more crash-prone)">
                      CoSkew{arrow('co_skewness')}
                    </th>
                    <th className="lb-sort lb-num" onClick={() => sortBy('co_kurtosis')} title="Co-kurtosis (tail amplification) — shown for reference but EXCLUDED from the composite: it rewards co-movement with the market's own tails, so it flags broad indices, not fragile single names.">
                      CoKurt{arrow('co_kurtosis')}
                    </th>
                    <th className="lb-sort lb-num" onClick={() => sortBy('roi_on_premium')} title="Return on premium at the screened strike/tenor">
                      Put P&L{arrow('roi_on_premium')}
                    </th>
                    <th>Verdict</th>
                    <th className="lb-sort lb-num" onClick={() => sortBy('hit_rate')}>
                      Hit{arrow('hit_rate')}
                    </th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const selected = r.asset === currentAsset
                const pick = () =>
                  onSelectAsset?.(
                    r.asset,
                    r.best_moneyness_pct !== null && r.best_tenor_weeks !== null
                      ? { moneyness_pct: r.best_moneyness_pct, tenor_weeks: r.best_tenor_weeks }
                      : undefined,
                  )
                return (
                <tr
                  key={r.asset}
                  className={[selected ? 'lb-current' : '', onSelectAsset ? 'lb-pick' : '']
                    .filter(Boolean)
                    .join(' ')}
                  onClick={onSelectAsset ? pick : undefined}
                  // Rows carry a real action, so they need real affordances:
                  // focusable, Enter/Space activated, and announced as selected.
                  tabIndex={onSelectAsset ? 0 : undefined}
                  role={onSelectAsset ? 'button' : undefined}
                  aria-pressed={onSelectAsset ? selected : undefined}
                  title={
                    onSelectAsset
                      ? r.best_annualized === null
                        ? `Backtest ${r.name}`
                        : `Backtest ${r.name} at its best cell: ${r.best_moneyness_pct}% OOM, ${r.best_tenor_weeks}-week`
                      : undefined
                  }
                  onKeyDown={
                    onSelectAsset
                      ? (e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            pick()
                          }
                        }
                      : undefined
                  }
                >
                  <td className="lb-num lb-rank">{i + 1}</td>
                  <td className="lb-name">{r.name}</td>
                  <td className="lb-num lb-best">
                    {r.best_annualized === null ? (
                      <span className="lb-nobest" title="Too little history to score any cell">
                        &mdash;
                      </span>
                    ) : (
                      <>
                        <div
                          style={{
                            color: r.best_annualized >= 0 ? 'var(--gain)' : 'var(--loss)',
                            fontWeight: 700,
                          }}
                        >
                          {fmtPct(r.best_annualized)}
                        </div>
                        <div className="lb-bestcell">
                          {r.best_moneyness_pct}% &middot; {r.best_tenor_weeks}w
                        </div>
                      </>
                    )}
                  </td>
                  {showAllMetrics && (
                    <>
                      <td className="lb-num" style={{ fontWeight: 700 }}>
                        {r.fragility_score === null ? '—' : Math.round(r.fragility_score * 100)}
                      </td>
                      <td className="lb-num">{num(r.downside_beta, 2)}</td>
                      <td className="lb-num">{num(r.co_skewness, 2)}</td>
                      <td className="lb-num">{num(r.co_kurtosis, 1)}</td>
                      <td className="lb-num">
                        <div style={{ color: r.roi_on_premium >= 0 ? 'var(--gain)' : 'var(--loss)', fontWeight: 700 }}>
                          {fmtPct(r.roi_on_premium)}
                        </div>
                        <div style={{ opacity: 0.6, fontSize: '0.85em' }}>{fmtPct(r.annualized_return)}/yr</div>
                      </td>
                      <td>
                        <span className={`badge ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span>
                      </td>
                      <td className="lb-num">{Math.round(r.hit_rate * 100)}%</td>
                    </>
                  )}
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
