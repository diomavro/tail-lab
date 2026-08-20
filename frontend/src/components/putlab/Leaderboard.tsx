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
}: {
  controls: PutLabControls
  currentAsset: string
  autoRun?: boolean
}) {
  const [state, setState] = useState<State>({ status: 'idle' })
  const [sortKey, setSortKey] = useState<SortKey>('fragility_score')
  const [asc, setAsc] = useState(false)
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
    <section className="panel" style={{ marginTop: 22 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">
            The fragility screen
            <ConceptInfo id="fragility_thesis" />
          </span>
          <h2 style={{ marginTop: 6 }}>Which names are most fragile?</h2>
          <div className="hint">
            Rank the universe by <strong>fragility</strong> vs the market &mdash; downside beta, co-skewness,
            co-kurtosis, tail beta and downside capture combined &mdash; then hold puts on the most fragile names.
            The edge is timing-free: the payoff
            comes from the fragility, not a forecast of <em>when</em>. The put columns show the model-priced result of{' '}
            <span className="mono">
              {controls.moneyness_pct}% OOM &middot; {controls.tenor_weeks}-week
            </span>{' '}
            puts, so you can spot <em>cheap fragility</em>: fragile names whose puts still paid.
          </div>
        </div>
        <button className="lb-run" onClick={run} disabled={state.status === 'loading'}>
          {state.status === 'loading' ? 'Screening 35 names…' : 'Screen the universe'}
        </button>
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

      {state.status === 'ready' && (
        <div className="lb-scroll">
          <table className="lb-table mono">
            <thead>
              <tr>
                <th className="lb-num">#</th>
                <th className="lb-sort" onClick={() => sortBy('name')}>
                  Asset{arrow('name')}
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('fragility_score')} title="Composite fragility (0–100): equal-weight blend of downside beta, co-skewness, tail beta & downside capture, most fragile first. Co-kurtosis is shown but excluded — it rewards co-movement with the market's own tails, so it flags broad indices.">
                  Fragility{arrow('fragility_score')}
                  <ConceptInfo id="fragility_score" />
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('downside_beta')} title="Downside beta vs SPY">
                  β&minus;{arrow('downside_beta')}
                  <ConceptInfo id="downside_beta" />
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('co_skewness')} title="Co-skewness (more negative = more crash-prone)">
                  Skew{arrow('co_skewness')}
                  <ConceptInfo id="co_skewness" />
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('co_kurtosis')} title="Co-kurtosis (tail amplification) — shown for reference but EXCLUDED from the composite: it rewards co-movement with the market's own tails, so it flags broad indices, not fragile single names.">
                  Kurt{arrow('co_kurtosis')}
                  <ConceptInfo id="co_kurtosis" />
                </th>
                <th
                  className="lb-sort lb-num"
                  onClick={() => sortBy('tail_beta')}
                  title="Extreme-tail beta vs SPY, worst 10% of market days"
                >
                  Tail &beta;{arrow('tail_beta')}
                  <ConceptInfo id="tail_beta" />
                </th>
                <th
                  className="lb-sort lb-num"
                  onClick={() => sortBy('downside_capture')}
                  title="Downside capture ratio vs SPY (>1 = amplifies losses)"
                >
                  Capt{arrow('downside_capture')}
                  <ConceptInfo id="downside_capture" />
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('roi_on_premium')} title="Model-priced put return on premium">
                  Put ret{arrow('roi_on_premium')}
                  <ConceptInfo id="roi_on_premium" />
                </th>
                <th>
                  Verdict
                  <ConceptInfo id="verdict" />
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('hit_rate')}>
                  Hit{arrow('hit_rate')}
                  <ConceptInfo id="hit_rate" />
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.asset} className={r.asset === currentAsset ? 'lb-current' : undefined}>
                  <td className="lb-num lb-rank">{i + 1}</td>
                  <td className="lb-name">{r.name}</td>
                  <td className="lb-num" style={{ fontWeight: 700 }}>
                    {r.fragility_score === null ? '—' : Math.round(r.fragility_score * 100)}
                  </td>
                  <td className="lb-num">{num(r.downside_beta, 2)}</td>
                  <td className="lb-num">{num(r.co_skewness, 2)}</td>
                  <td className="lb-num">{num(r.co_kurtosis, 1)}</td>
                  <td className="lb-num">{num(r.tail_beta, 2)}</td>
                  <td className="lb-num">{num(r.downside_capture, 2)}</td>
                  <td
                    className="lb-num"
                    style={{ color: r.roi_on_premium >= 0 ? 'var(--gain)' : 'var(--loss)', fontWeight: 700 }}
                  >
                    {fmtPct(r.roi_on_premium)}
                  </td>
                  <td>
                    <span className={`badge ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span>
                  </td>
                  <td className="lb-num">{Math.round(r.hit_rate * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
