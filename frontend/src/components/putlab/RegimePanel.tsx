import { useEffect, useState } from 'react'
import { fetchRegimes, type RegimeLabel, type RegimeTimelineView } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'

// The market-regime history that underpins every verdict: a VIX-based
// calm/elevated/crisis band strip over the whole window, with the current
// regime called out. It's market-wide (not per-asset), so fetched once.

const REGIME_VAR: Record<RegimeLabel, string> = {
  calm: 'var(--cool)',
  elevated: 'var(--warm)',
  crisis: 'var(--hot)',
}
const REGIME_LABEL: Record<RegimeLabel, string> = { calm: 'calm', elevated: 'elevated', crisis: 'crisis' }

export function RegimePanel() {
  const [view, setView] = useState<RegimeTimelineView | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchRegimes(controller.signal)
      .then(setView)
      .catch(() => {})
    return () => controller.abort()
  }, [])

  if (!view || view.segments.length === 0) return null

  const total = Object.values(view.day_counts).reduce((a, b) => a + b, 0) || 1
  const first = view.segments[0]!.start
  const last = view.segments[view.segments.length - 1]!.end

  return (
    <section className="panel" style={{ marginTop: 22 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">Market regime</span>
          <h2 style={{ marginTop: 6 }}>
            Right now:{' '}
            <span className="regime-now" style={{ color: REGIME_VAR[view.current] }}>
              {REGIME_LABEL[view.current]}
            </span>
            <ConceptInfo id="regime" />
          </h2>
          <div className="hint">
            VIX-based regime over the window &mdash; the same calm / elevated / crisis labels that decide whether a
            strategy reads <em>confirmed</em> or <em>regime-only</em>.
          </div>
        </div>
        <div className="legend">
          {(['calm', 'elevated', 'crisis'] as RegimeLabel[]).map((r) => (
            <span className="sw" key={r}>
              <span className="box" style={{ background: REGIME_VAR[r] }} /> {r} (
              {Math.round(((view.day_counts[r] ?? 0) / total) * 100)}%)
            </span>
          ))}
        </div>
      </div>

      <div
        className="regime-strip"
        role="img"
        aria-label={`Regime history; currently ${view.current}`}
      >
        {view.segments.map((s, i) => (
          <div
            key={i}
            className="regime-band"
            style={{ flexGrow: s.n_days, background: REGIME_VAR[s.regime] }}
            title={`${s.regime}: ${s.start} → ${s.end} (${s.n_days} trading days)`}
          />
        ))}
      </div>
      <div className="regime-axis mono">
        <span>{first}</span>
        <span>{last}</span>
      </div>
    </section>
  )
}
