import { useEffect, useState } from 'react'
import { fetchVixStretch, type VixStretchResponse } from '../api/client'

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: VixStretchResponse }

export function VixStretchTile() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetchVixStretch(controller.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setState({ status: 'error', message: err instanceof Error ? err.message : String(err) })
      })
    return () => controller.abort()
  }, [])

  return (
    <div className="tile" role="status" aria-live="polite">
      <h2>VIX stretch</h2>
      {state.status === 'loading' && <p>Loading…</p>}
      {state.status === 'error' && <p className="tile-error">{state.message}</p>}
      {state.status === 'ready' && (
        <>
          <p className="tile-zscore">{state.data.z_score.toFixed(2)}σ</p>
          <dl>
            <dt>Date</dt>
            <dd>{state.data.date}</dd>
            <dt>Close</dt>
            <dd>{state.data.close.toFixed(2)}</dd>
            <dt>20d mean</dt>
            <dd>{state.data.rolling_mean_20d.toFixed(2)}</dd>
            <dt>20d std</dt>
            <dd>{state.data.rolling_std_20d.toFixed(2)}</dd>
          </dl>
        </>
      )}
    </div>
  )
}
