import { useEffect, useRef } from 'react'
import type { EquityPoint, PutBacktestCycle } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtDollar, hideTooltip, showTooltip, svgEl } from './format'

interface EquityCurveProps {
  equityCurve: EquityPoint[]
  // Daily mark-to-model curve for the single-name backtest. Optional because
  // the Portfolio combined view has no per-day MTM (only a realized curve
  // over the union of leg expiries) and falls back to equityCurve below.
  mtmCurve?: EquityPoint[]
  cycles: PutBacktestCycle[]
}

// Cumulative P&L of the hedge -- port of the mock's renderEquity(), minus
// the regime bands (no regime classifier exists yet; see PutLab.tsx header).
// Plots the daily mark-to-model curve (mtmCurve) when given, rather than the
// sparser realized equity_curve, so day-to-day fluctuation of the open put
// shows up instead of straight lines between expiries.
export function EquityCurve({ equityCurve, mtmCurve, cycles }: EquityCurveProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const ttRef = useRef<HTMLDivElement | null>(null)
  const isDaily = mtmCurve !== undefined && mtmCurve.length > 0
  const curve = mtmCurve !== undefined && mtmCurve.length > 0 ? mtmCurve : equityCurve

  useEffect(() => {
    const svg = svgRef.current
    const tt = ttRef.current
    if (!svg || !tt) return
    svg.innerHTML = ''
    if (curve.length === 0) return

    const W = 1000
    const H = 340
    const P = { l: 64, r: 16, t: 20, b: 26 }
    const eq = curve.map((p) => ({ t: Date.parse(p.date), v: p.cum_pnl }))
    const first = eq[0]
    const last = eq[eq.length - 1]
    if (!first || !last) return
    const xs = first.t
    const xe = Math.max(last.t, xs + 1)
    const vmin = Math.min(0, ...eq.map((p) => p.v))
    const vmax = Math.max(1, ...eq.map((p) => p.v))
    const X = (t: number) => P.l + ((t - xs) / (xe - xs)) * (W - P.l - P.r)
    const Y = (v: number) => P.t + (1 - (v - vmin) / (vmax - vmin)) * (H - P.t - P.b)

    // gridlines + $ labels
    const ticks = 4
    for (let g = 0; g <= ticks; g++) {
      const v = vmin + ((vmax - vmin) * g) / ticks
      const y = Y(v)
      svg.appendChild(svgEl('line', { x1: P.l, y1: y, x2: W - P.r, y2: y, stroke: 'var(--grid)', 'stroke-width': 1 }))
      const label = svgEl('text', { x: P.l - 8, y: y + 3, 'text-anchor': 'end', class: 'axis-label' })
      label.textContent = fmtDollar(v)
      svg.appendChild(label)
    }
    svg.appendChild(
      svgEl('line', { x1: P.l, y1: Y(0), x2: W - P.r, y2: Y(0), stroke: 'var(--line-strong)', 'stroke-width': 1.5 }),
    )

    // area + line
    let dArea = `M ${X(first.t)} ${Y(0)}`
    let dLine = ''
    eq.forEach((p, k) => {
      dArea += ` L ${X(p.t)} ${Y(p.v)}`
      dLine += `${k ? ' L ' : 'M '}${X(p.t)} ${Y(p.v)}`
    })
    dArea += ` L ${X(last.t)} ${Y(0)} Z`
    const gid = 'putlab-eqfill'
    const defs = svgEl('defs')
    const lg = svgEl('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 })
    lg.appendChild(svgEl('stop', { offset: '0%', 'stop-color': 'var(--accent)', 'stop-opacity': 0.32 }))
    lg.appendChild(svgEl('stop', { offset: '100%', 'stop-color': 'var(--accent)', 'stop-opacity': 0.02 }))
    defs.appendChild(lg)
    svg.appendChild(defs)
    svg.appendChild(svgEl('path', { d: dArea, fill: `url(#${gid})` }))
    svg.appendChild(
      svgEl('path', {
        d: dLine,
        fill: 'none',
        stroke: 'var(--accent)',
        'stroke-width': 2.4,
        'stroke-linejoin': 'round',
      }),
    )

    // settlement markers: find the daily mtm point landing on each cycle's
    // expiry date (mtmCurve marks to model exactly onto the realized value
    // there, so this is where a positive-net cycle "pays off" on the curve).
    const byDate = new Map(curve.map((p) => [p.date, p]))
    cycles.forEach((c) => {
      if (c.net <= 0) return
      const p = byDate.get(c.expiry_date)
      if (!p) return
      svg.appendChild(
        svgEl('circle', {
          cx: X(Date.parse(p.date)),
          cy: Y(p.cum_pnl),
          r: 4,
          fill: 'var(--surface)',
          stroke: 'var(--gain)',
          'stroke-width': 2,
        }),
      )
    })

    // end marker
    svg.appendChild(svgEl('circle', { cx: X(last.t), cy: Y(last.v), r: 4.5, fill: 'var(--accent)' }))

    // hover
    const hit = svgEl('rect', { x: P.l, y: P.t, width: W - P.l - P.r, height: H - P.t - P.b, fill: 'transparent' })
    svg.appendChild(hit)
    const vline = svgEl('line', { y1: P.t, y2: H - P.b, stroke: 'var(--line-strong)', 'stroke-width': 1, opacity: 0 })
    svg.appendChild(vline)
    const onMove = (ev: PointerEvent) => {
      const r = svg.getBoundingClientRect()
      const sx = ((ev.clientX - r.left) / r.width) * W
      const t = xs + ((sx - P.l) / (W - P.l - P.r)) * (xe - xs)
      let best = eq[0]
      if (!best) return
      for (const p of eq) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p
      vline.setAttribute('x1', String(X(best.t)))
      vline.setAttribute('x2', String(X(best.t)))
      vline.setAttribute('opacity', '1')
      const dateLabel = new Date(best.t).toISOString().slice(0, 10)
      showTooltip(
        tt,
        ev.clientX,
        r.top + Y(best.v),
        `<div class="t-k">${dateLabel}</div><div class="t-v" style="color:${best.v >= 0 ? 'var(--gain)' : 'var(--loss)'}">${fmtDollar(best.v)}</div>`,
      )
    }
    const onLeave = () => {
      hideTooltip(tt)
      vline.setAttribute('opacity', '0')
    }
    hit.addEventListener('pointermove', onMove)
    hit.addEventListener('pointerleave', onLeave)
    return () => {
      hit.removeEventListener('pointermove', onMove)
      hit.removeEventListener('pointerleave', onLeave)
    }
  }, [curve, cycles])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>
            Cumulative P&L of the hedge
            <ConceptInfo id="mark_to_market" />
          </h2>
          <div className="hint">
            {isDaily
              ? "Marked to model every day — the open put's value fluctuates daily and settles at each expiry."
              : 'Running net profit & loss — premiums paid out, payoffs collected at each expiry.'}
          </div>
        </div>
        <div className="legend">
          <span className="sw">
            <span className="box" style={{ background: 'var(--accent)' }} /> equity
          </span>
          <span className="sw">
            <span className="box" style={{ border: '1.5px solid var(--gain)', background: 'transparent' }} /> payoff
            cycle
          </span>
        </div>
      </div>
      <svg
        ref={svgRef}
        id="putlab-equity"
        viewBox="0 0 1000 340"
        role="img"
        aria-label="Cumulative profit and loss of the put-buying strategy over time"
      />
      <div className="tt" ref={ttRef} role="status" aria-live="polite" />
    </>
  )
}
