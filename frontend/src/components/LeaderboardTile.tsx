import { useEffect, useState } from 'react'
import { fetchLeaderboard, type LeaderboardResponse } from '../api/client'

type LoadState =
  | { status: 'loading' }
  | { status: 'no-data' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: LeaderboardResponse }

const METRIC_LABELS: Record<string, string> = {
  downside_beta: 'Downside beta',
}

// The put-buying cockpit's entry point (docs/END_STATE.md §1.1): the
// screening universe ranked by a sensitivity metric, most-sensitive first,
// so Dio can see put candidates ranked daily (standing directive).
export function LeaderboardTile() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetchLeaderboard(controller.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        if (err instanceof Error && err.message.includes('404')) {
          setState({ status: 'no-data' })
          return
        }
        setState({ status: 'error', message: err instanceof Error ? err.message : String(err) })
      })
    return () => controller.abort()
  }, [])

  return (
    <div className="tile leaderboard-tile" role="status" aria-live="polite">
      <h2>Sensitivity leaderboard</h2>
      {state.status === 'loading' && <p>Loading…</p>}
      {state.status === 'error' && <p className="tile-error">{state.message}</p>}
      {state.status === 'no-data' && <p>No scorable assets yet.</p>}
      {state.status === 'ready' && (
        <>
          <p className="tile-asof">
            as of {state.data.as_of} &middot; vs {state.data.benchmark}
          </p>
          <table className="leaderboard-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Symbol</th>
                <th>{METRIC_LABELS[state.data.metric] ?? state.data.metric}</th>
              </tr>
            </thead>
            <tbody>
              {state.data.rows.map((row) => (
                <tr key={row.symbol}>
                  <td>{row.rank}</td>
                  <td>{row.symbol}</td>
                  <td>{row.score.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
