import { useState } from 'react'
import { ApiError, fetchPutLabLeaderboard, type RankedAsset } from '../../api/client'
import { fmtMult, fmtPct, fmtPrice } from './format'
import type { PutLabControls } from './types'

// The universe ranking: "which names' OOM puts got the best results at this
// strike/tenor." It runs ~35 model backtests server-side (~15-25s), so it's an
// explicit action (a button), not part of the debounced auto-fetch. Results
// come back sorted by ROI; the table re-sorts client-side on header click.

type SortKey = keyof Pick<
  RankedAsset,
  'roi_on_premium' | 'hit_rate' | 'biggest_payoff_mult' | 'n_cycles' | 'name' | 'spot'
>

type State =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: RankedAsset[] }

const VERDICT_LABEL: Record<RankedAsset['verdict'], string> = {
  confirmed: 'confirmed',
  regime_only: 'regime only',
  failed: 'failed',
  untested: 'untested',
}

export function Leaderboard({
  controls,
  currentAsset,
}: {
  controls: PutLabControls
  currentAsset: string
}) {
  const [state, setState] = useState<State>({ status: 'idle' })
  const [sortKey, setSortKey] = useState<SortKey>('roi_on_premium')
  const [asc, setAsc] = useState(false)

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
          const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number)
          return asc ? cmp : -cmp
        })
      : []

  const arrow = (key: SortKey) => (key === sortKey ? (asc ? ' ▲' : ' ▼') : '')

  return (
    <section className="panel" style={{ marginTop: 22 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">The leaderboard</span>
          <h2 style={{ marginTop: 6 }}>Which names paid best?</h2>
          <div className="hint">
            Rank every name in the universe by return on premium at{' '}
            <span className="mono">
              {controls.moneyness_pct}% OOM &middot; {controls.tenor_weeks}w
            </span>
            , each tagged with its cross-regime verdict. A <em>regime-only</em> winner only paid in one regime &mdash;
            treat a big number with a <span className="badge regime_only">regime only</span> tag as a bet on that
            regime repeating, not a standalone edge.
          </div>
        </div>
        <button className="lb-run" onClick={run} disabled={state.status === 'loading'}>
          {state.status === 'loading' ? 'Backtesting 35 names…' : 'Rank the universe'}
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
                <th className="lb-sort lb-num" onClick={() => sortBy('spot')}>
                  Spot{arrow('spot')}
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('roi_on_premium')}>
                  Return{arrow('roi_on_premium')}
                </th>
                <th>Verdict</th>
                <th className="lb-sort lb-num" onClick={() => sortBy('hit_rate')}>
                  Hit{arrow('hit_rate')}
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('biggest_payoff_mult')}>
                  Best{arrow('biggest_payoff_mult')}
                </th>
                <th className="lb-sort lb-num" onClick={() => sortBy('n_cycles')}>
                  Rolls{arrow('n_cycles')}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.asset} className={r.asset === currentAsset ? 'lb-current' : undefined}>
                  <td className="lb-num lb-rank">{i + 1}</td>
                  <td className="lb-name">{r.name}</td>
                  <td className="lb-num">{fmtPrice(r.spot)}</td>
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
                  <td className="lb-num">{fmtMult(r.biggest_payoff_mult)}</td>
                  <td className="lb-num">{r.n_cycles}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
