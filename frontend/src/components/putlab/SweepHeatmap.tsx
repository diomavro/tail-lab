import { useEffect, useRef } from 'react'
import type { SweepCell } from '../../api/client'
import { fmtPct, hideTooltip, makeRamp, normSigned, showTooltip, svgEl } from './format'

interface SweepHeatmapProps {
  cells: SweepCell[]
  moneynessPct: number
  tenorWeeks: number
}

const MONEYNESS_AXIS = [2, 4, 6, 8, 10, 12, 15, 18, 22]
const TENOR_AXIS = [1, 2, 4, 8, 12]

function tenorLabel(weeks: number): string {
  if (weeks === 4) return '1mo'
  if (weeks === 12) return '1qtr'
  return `${weeks}w`
}

// The strike x tenor sweep -- port of the mock's renderHeat(). Cells the
// backend didn't return (a tenor too long for the lookback window) render as
// a muted, dashed "no data" cell instead of fabricating a value.
export function SweepHeatmap({ cells, moneynessPct, tenorWeeks }: SweepHeatmapProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const legendRef = useRef<HTMLDivElement | null>(null)
  const ttRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    const legend = legendRef.current
    const tt = ttRef.current
    if (!svg || !legend || !tt) return
    svg.innerHTML = ''
    legend.innerHTML = ''

    const byKey = new Map<string, SweepCell>()
    for (const c of cells) byKey.set(`${c.moneyness_pct}_${c.tenor_weeks}`, c)
    const values = cells.map((c) => c.roi_on_premium)
    const lo = Math.min(0, ...values)
    const hi = Math.max(0, ...values)
    const ramp = makeRamp(svg)
    const nearestMoon = MONEYNESS_AXIS.reduce((p, c) =>
      Math.abs(c - moneynessPct) < Math.abs(p - moneynessPct) ? c : p,
    )

    const W = 1000
    const H = 262
    const P = { l: 70, r: 16, t: 14, b: 40 }
    const cw = (W - P.l - P.r) / MONEYNESS_AXIS.length
    const ch = (H - P.t - P.b) / TENOR_AXIS.length

    TENOR_AXIS.forEach((tw, ti) => {
      MONEYNESS_AXIS.forEach((mp, mi) => {
        const x = P.l + mi * cw
        const y = P.t + ti * ch
        const cell = byKey.get(`${mp}_${tw}`)
        if (!cell) {
          const rect = svgEl('rect', {
            x: x + 1.5,
            y: y + 1.5,
            width: cw - 3,
            height: ch - 3,
            rx: 5,
            fill: 'var(--surface-2)',
            stroke: 'var(--line)',
            'stroke-dasharray': '3,3',
          })
          svg.appendChild(rect)
          return
        }
        const f = normSigned(cell.roi_on_premium, lo, hi)
        const rect = svgEl('rect', {
          x: x + 1.5,
          y: y + 1.5,
          width: cw - 3,
          height: ch - 3,
          rx: 5,
          fill: ramp(f),
          class: 'heat-cell',
        })
        const onEnter = (ev: PointerEvent) => {
          const r = svg.getBoundingClientRect()
          showTooltip(
            tt,
            ev.clientX,
            r.top + (y / H) * r.height,
            `<div class="t-k">${mp}% OOM &middot; ${tenorLabel(tw)}</div><div class="t-v" style="color:${cell.roi_on_premium >= 0 ? 'var(--gain)' : 'var(--loss)'}">${fmtPct(cell.roi_on_premium)} on premium</div>`,
          )
        }
        const onLeave = () => hideTooltip(tt)
        rect.addEventListener('pointerenter', onEnter)
        rect.addEventListener('pointerleave', onLeave)
        svg.appendChild(rect)
        const label = svgEl('text', {
          x: x + cw / 2,
          y: y + ch / 2 + 4,
          'text-anchor': 'middle',
          class: 'mono',
          style: `font-size:12px;font-family:ui-monospace,Menlo,monospace;fill:${f > 0.62 || f < 0.3 ? '#0b0b0b' : 'var(--text)'}`,
        })
        label.textContent = `${cell.roi_on_premium >= 0 ? '+' : ''}${Math.round(cell.roi_on_premium * 100)}`
        svg.appendChild(label)
        if (mp === nearestMoon && tw === tenorWeeks) {
          svg.appendChild(svgEl('rect', { x: x + 1.5, y: y + 1.5, width: cw - 3, height: ch - 3, rx: 5, class: 'heat-cur' }))
        }
      })
    })

    // axes
    MONEYNESS_AXIS.forEach((m, mi) => {
      const label = svgEl('text', { x: P.l + mi * cw + cw / 2, y: H - P.b + 20, 'text-anchor': 'middle', class: 'axis-label' })
      label.textContent = `${m}%`
      svg.appendChild(label)
    })
    const xlab = svgEl('text', { x: (P.l + W - P.r) / 2, y: H - 6, 'text-anchor': 'middle', class: 'regime-label' })
    xlab.textContent = 'strike distance out-of-the-money →'
    svg.appendChild(xlab)
    TENOR_AXIS.forEach((tw, ti) => {
      const label = svgEl('text', { x: P.l - 10, y: P.t + ti * ch + ch / 2 + 4, 'text-anchor': 'end', class: 'axis-label' })
      label.textContent = tenorLabel(tw)
      svg.appendChild(label)
    })

    // legend
    for (const [text, f] of [
      ['bleeds', 0.1],
      ['breakeven', 0.5],
      ['pays off', 0.92],
    ] as const) {
      const sw = document.createElement('span')
      sw.className = 'sw'
      const box = document.createElement('span')
      box.className = 'box'
      box.style.background = ramp(f)
      sw.appendChild(box)
      sw.append(` ${text}`)
      legend.appendChild(sw)
    }
  }, [cells, moneynessPct, tenorWeeks])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>The sweep &mdash; strike &times; tenor</h2>
          <div className="hint">Total return on premium for every combination. Your current pick is outlined.</div>
        </div>
        <div className="legend" ref={legendRef} />
      </div>
      <svg
        ref={svgRef}
        id="putlab-heat"
        viewBox="0 0 1000 262"
        role="img"
        aria-label="Heatmap of total return by moneyness and tenor"
      />
      <div className="tt" ref={ttRef} role="status" aria-live="polite" />
    </>
  )
}
