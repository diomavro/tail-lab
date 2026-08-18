import { useEffect, useRef } from 'react'
import type { PutBacktestCycle } from '../../api/client'
import { fmtDollar, hideTooltip, showTooltip, svgEl } from './format'

interface CyclesBarsProps {
  cycles: PutBacktestCycle[]
  notional: number
}

// Per-cycle P&L bars -- port of the mock's renderCycles(). Losses get a 4px
// floor so a small bleed cycle stays visible.
export function CyclesBars({ cycles, notional }: CyclesBarsProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const ttRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    const tt = ttRef.current
    if (!svg || !tt) return
    svg.innerHTML = ''
    if (cycles.length === 0) return

    const W = 560
    const H = 260
    const P = { l: 46, r: 10, t: 12, b: 22 }
    const vmax = Math.max(notional || 1, ...cycles.map((c) => Math.abs(c.net)))
    const bw = (W - P.l - P.r) / cycles.length
    const Y = (v: number) => P.t + (1 - (v + vmax) / (2 * vmax)) * (H - P.t - P.b)

    svg.appendChild(
      svgEl('line', { x1: P.l, y1: Y(0), x2: W - P.r, y2: Y(0), stroke: 'var(--line-strong)', 'stroke-width': 1.2 }),
    )
    for (const v of [vmax, 0, -vmax]) {
      const label = svgEl('text', { x: P.l - 6, y: Y(v) + 3, 'text-anchor': 'end', class: 'axis-label' })
      label.textContent = fmtDollar(v)
      svg.appendChild(label)
    }

    cycles.forEach((c, k) => {
      const x = P.l + k * bw + bw * 0.12
      const w = Math.max(bw * 0.76, 1)
      const h0 = Math.abs(Y(c.net) - Y(0))
      const h = c.net >= 0 ? Math.max(h0, 0.6) : Math.max(h0, 4)
      const y = c.net >= 0 ? Y(c.net) : Y(0)
      const rect = svgEl('rect', {
        x,
        y,
        width: w,
        height: h,
        rx: Math.min(w / 2, 1.5),
        fill: c.net >= 0 ? 'var(--gain)' : 'var(--loss)',
        opacity: c.net >= 0 ? 0.95 : 0.8,
      })
      const onEnter = (ev: PointerEvent) => {
        const r = svg.getBoundingClientRect()
        showTooltip(
          tt,
          ev.clientX,
          r.top + (r.height * y) / H,
          `<div class="t-k">Cycle ${k + 1}</div><div class="t-v">paid ${fmtDollar(notional)} · back ${fmtDollar(c.payoff)}</div><div class="t-v" style="color:${c.net >= 0 ? 'var(--gain)' : 'var(--loss)'}">${fmtDollar(c.net)}</div>`,
        )
      }
      const onLeave = () => hideTooltip(tt)
      rect.addEventListener('pointerenter', onEnter)
      rect.addEventListener('pointerleave', onLeave)
      svg.appendChild(rect)
    })
  }, [cycles, notional])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Every roll, one bar</h2>
          <div className="hint">Each expiry cycle: what you paid vs. what it paid back.</div>
        </div>
      </div>
      <svg ref={svgRef} id="putlab-cycles" viewBox="0 0 560 260" role="img" aria-label="Per-cycle profit and loss bars" />
      <div className="legend" style={{ marginTop: 6 }}>
        <span className="sw">
          <span className="box" style={{ background: 'var(--loss)' }} /> premium bled
        </span>
        <span className="sw">
          <span className="box" style={{ background: 'var(--gain)' }} /> net payoff
        </span>
      </div>
      <div className="tt" ref={ttRef} role="status" aria-live="polite" />
    </>
  )
}
