import { useEffect, useRef } from 'react'
import type { CadenceResponse } from '../../api/client'
import { svgEl } from './format'

interface CadencePanelProps {
  cadence: CadenceResponse
}

// Expiry cadence stat chips + upcoming-expiry timeline -- port of the mock's
// renderCadence(). The mock iterated a per-asset list of chains; the real
// endpoint returns one cadence per asset, so the row collapses to one line.
export function CadencePanel({ cadence }: CadencePanelProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    svg.innerHTML = ''
    const W = 500
    const y = 44
    svg.appendChild(svgEl('line', { x1: 20, y1: y, x2: W - 20, y2: y, stroke: 'var(--line-strong)', 'stroke-width': 1.5 }))
    const gap = cadence.avg_gap_days
    const monthly = cadence.cadence === 'monthly'
    let day = Math.round(gap * 0.4)
    for (let k = 0; k < 8; k++) {
      const dx = 20 + (day / 56) * (W - 40)
      if (dx > W - 20) break
      const big = monthly || k % 4 === 0
      svg.appendChild(
        svgEl('line', {
          x1: dx,
          y1: y - (big ? 16 : 9),
          x2: dx,
          y2: y + (big ? 16 : 9),
          stroke: big ? 'var(--accent)' : 'var(--faint)',
          'stroke-width': big ? 2.4 : 1.4,
        }),
      )
      if (big) {
        const label = svgEl('text', { x: dx, y: y + 30, 'text-anchor': 'middle', class: 'axis-label' })
        label.textContent = `${day}d`
        svg.appendChild(label)
      }
      day += Math.round(gap)
    }
    const todayLabel = svgEl('text', { x: 20, y: 20, class: 'regime-label' })
    todayLabel.textContent = 'today'
    svg.appendChild(todayLabel)
    svg.appendChild(svgEl('circle', { cx: 20, cy: y, r: 4, fill: 'var(--accent)' }))
  }, [cadence])

  const nextExpiry = Math.max(Math.round(cadence.avg_gap_days * 0.4), 1)
  const perYear = Math.round(252 / cadence.avg_gap_days)

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Expiry cadence</h2>
          <div className="hint">
            {cadence.symbol} lists {cadence.cadence} expiries &mdash; avg {cadence.avg_gap_days.toFixed(1)} days apart.
          </div>
        </div>
      </div>
      <div className="cadence">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 4 }}>
          {[
            ['Next expiry', `${nextExpiry}d`],
            ['Avg gap', `${cadence.avg_gap_days.toFixed(1)}d`],
            ['Per year', `~${perYear}`],
          ].map(([k, v]) => (
            <div
              key={k}
              style={{ border: '1px solid var(--line)', borderRadius: 10, padding: '10px 12px', background: 'var(--surface-2)' }}
            >
              <div
                style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.1em', color: 'var(--faint)' }}
              >
                {k}
              </div>
              <div className="mono" style={{ fontSize: 20, fontWeight: 700, marginTop: 3 }}>
                {v}
              </div>
            </div>
          ))}
        </div>
        <div className="cad-row">
          <span className="cad-name mono">{cadence.symbol}</span>
          <span className="cad-tag">{cadence.label}</span>
          <span className="hint" style={{ fontSize: 12, color: 'var(--muted)' }}>
            {cadence.detail}
          </span>
        </div>
      </div>
      <svg
        ref={svgRef}
        id="putlab-cad-timeline"
        viewBox="0 0 500 90"
        role="img"
        aria-label="Upcoming expiry timeline"
        style={{ marginTop: 14 }}
      />
    </>
  )
}
