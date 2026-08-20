import { useEffect, useRef } from 'react'
import type { PutBacktestCycle } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtPrice, hideTooltip, showTooltip, svgEl } from './format'

interface CostOverTimeProps {
  cycles: PutBacktestCycle[]
}

// What the puts cost over time: each roll's model premium per put (left
// axis, --accent) against the implied-vol proxy that priced it (right axis,
// --cool), both indexed by entry_date. Same SVG pattern as EquityCurve.
export function CostOverTime({ cycles }: CostOverTimeProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const ttRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    const tt = ttRef.current
    if (!svg || !tt) return
    svg.innerHTML = ''
    if (cycles.length === 0) return

    const W = 1000
    const H = 238
    const P = { l: 64, r: 56, t: 20, b: 26 }
    const pts = cycles.map((c) => ({ t: Date.parse(c.entry_date), premium: c.premium, sigma: c.sigma }))
    const first = pts[0]
    const last = pts[pts.length - 1]
    if (!first || !last) return
    const xs = first.t
    const xe = Math.max(last.t, xs + 1)
    const X = (t: number) => P.l + ((t - xs) / (xe - xs)) * (W - P.l - P.r)

    const pmax = Math.max(1, ...pts.map((p) => p.premium))
    const YP = (v: number) => P.t + (1 - v / pmax) * (H - P.t - P.b)
    const smax = Math.max(0.01, ...pts.map((p) => p.sigma))
    const YS = (v: number) => P.t + (1 - v / smax) * (H - P.t - P.b)

    // gridlines + left ($ premium) / right (% IV) axis labels
    const ticks = 4
    for (let g = 0; g <= ticks; g++) {
      const v = (pmax * g) / ticks
      const y = YP(v)
      svg.appendChild(svgEl('line', { x1: P.l, y1: y, x2: W - P.r, y2: y, stroke: 'var(--grid)', 'stroke-width': 1 }))
      const label = svgEl('text', { x: P.l - 8, y: y + 3, 'text-anchor': 'end', class: 'axis-label' })
      label.textContent = fmtPrice(v)
      svg.appendChild(label)
    }
    for (let g = 0; g <= ticks; g++) {
      const v = (smax * g) / ticks
      const label = svgEl('text', { x: W - P.r + 8, y: YS(v) + 3, 'text-anchor': 'start', class: 'axis-label' })
      label.textContent = `${(v * 100).toFixed(0)}%`
      svg.appendChild(label)
    }

    // IV proxy line (secondary axis, --cool, dashed so it reads as context)
    let dIv = ''
    pts.forEach((p, k) => {
      dIv += `${k ? ' L ' : 'M '}${X(p.t)} ${YS(p.sigma)}`
    })
    svg.appendChild(
      svgEl('path', { d: dIv, fill: 'none', stroke: 'var(--cool)', 'stroke-width': 1.8, 'stroke-dasharray': '4 3' }),
    )

    // premium-per-put line (primary axis, --accent) + fill
    let dArea = `M ${X(first.t)} ${YP(0)}`
    let dLine = ''
    pts.forEach((p, k) => {
      dArea += ` L ${X(p.t)} ${YP(p.premium)}`
      dLine += `${k ? ' L ' : 'M '}${X(p.t)} ${YP(p.premium)}`
    })
    dArea += ` L ${X(last.t)} ${YP(0)} Z`
    const gid = 'putlab-costfill'
    const defs = svgEl('defs')
    const lg = svgEl('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 })
    lg.appendChild(svgEl('stop', { offset: '0%', 'stop-color': 'var(--accent)', 'stop-opacity': 0.28 }))
    lg.appendChild(svgEl('stop', { offset: '100%', 'stop-color': 'var(--accent)', 'stop-opacity': 0.02 }))
    defs.appendChild(lg)
    svg.appendChild(defs)
    svg.appendChild(svgEl('path', { d: dArea, fill: `url(#${gid})` }))
    svg.appendChild(
      svgEl('path', { d: dLine, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2.2, 'stroke-linejoin': 'round' }),
    )
    pts.forEach((p) => {
      svg.appendChild(svgEl('circle', { cx: X(p.t), cy: YP(p.premium), r: 3, fill: 'var(--accent)' }))
    })

    // hover
    const hit = svgEl('rect', { x: P.l, y: P.t, width: W - P.l - P.r, height: H - P.t - P.b, fill: 'transparent' })
    svg.appendChild(hit)
    const vline = svgEl('line', { y1: P.t, y2: H - P.b, stroke: 'var(--line-strong)', 'stroke-width': 1, opacity: 0 })
    svg.appendChild(vline)
    const onMove = (ev: PointerEvent) => {
      const r = svg.getBoundingClientRect()
      const sx = ((ev.clientX - r.left) / r.width) * W
      const t = xs + ((sx - P.l) / (W - P.l - P.r)) * (xe - xs)
      let best = pts[0]
      if (!best) return
      for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p
      vline.setAttribute('x1', String(X(best.t)))
      vline.setAttribute('x2', String(X(best.t)))
      vline.setAttribute('opacity', '1')
      const dateLabel = new Date(best.t).toISOString().slice(0, 10)
      showTooltip(
        tt,
        ev.clientX,
        r.top + YP(best.premium),
        `<div class="t-k">${dateLabel}</div><div class="t-v" style="color:var(--accent)">${fmtPrice(best.premium)}/put</div><div class="t-v" style="color:var(--cool)">${(best.sigma * 100).toFixed(0)}% IV</div>`,
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
  }, [cycles])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>
            What the puts cost over time
            <ConceptInfo id="mark_to_market" />
          </h2>
          <div className="hint">
            Each roll&rsquo;s model premium per put and the implied-vol proxy
            <ConceptInfo id="model_priced" /> that priced it &mdash; cheap insurance gets expensive exactly when vol
            spikes.
          </div>
        </div>
        {cycles.length > 0 && (
          <div className="legend">
            <span className="sw">
              <span className="box" style={{ background: 'var(--accent)' }} /> premium / put
            </span>
            <span className="sw">
              <span className="box" style={{ background: 'var(--cool)' }} /> implied vol
            </span>
          </div>
        )}
      </div>
      {cycles.length === 0 ? (
        <p className="hint">No rolls in this window yet.</p>
      ) : (
        <svg
          ref={svgRef}
          viewBox="0 0 1000 238"
          role="img"
          aria-label="Model premium per put and implied volatility proxy at each roll's entry date"
        />
      )}
      <div className="tt" ref={ttRef} role="status" aria-live="polite" />
    </>
  )
}
