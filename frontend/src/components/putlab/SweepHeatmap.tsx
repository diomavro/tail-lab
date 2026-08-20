import { useEffect, useRef } from 'react'
import type { SweepCell } from '../../api/client'
import { fmtPct, hideTooltip, showTooltip, svgEl } from './format'

interface SweepHeatmapProps {
  cells: SweepCell[]
  moneynessPct: number
  tenorWeeks: number
  benchmarkSymbol: string
  benchmarkAnnualized: number | null
  onSelect?: (moneynessPct: number, tenorWeeks: number) => void
}

const MONEYNESS_AXIS = [2, 4, 6, 8, 10, 12, 15, 18, 22]
const TENOR_AXIS = [1, 2, 4, 8, 12]

function tenorLabel(weeks: number): string {
  if (weeks === 4) return '1mo'
  if (weeks === 12) return '1qtr'
  return `${weeks}w`
}

// The three categorical buckets, keyed to the two thresholds 0 and the S&P
// hurdle. When the benchmark is unavailable it collapses to a two-way split at
// 0 (loses / makes money) -- a positive return can't be "below the S&P" if we
// don't know the S&P.
type Bucket = 'loss' | 'below' | 'beats'
function bucketFor(ret: number, benchmark: number | null): Bucket {
  if (ret < 0) return 'loss'
  if (benchmark !== null && ret < benchmark) return 'below'
  return 'beats'
}

// The strike x tenor sweep. Cells are coloured in THREE categorical buckets by
// annualized return on premium (not a continuous ramp): red loses money, amber
// makes money but trails the S&P, green beats it. Cells the backend didn't
// return (a tenor too long for the lookback) render as a muted, dashed "no
// data" cell instead of fabricating a value.
export function SweepHeatmap({
  cells,
  moneynessPct,
  tenorWeeks,
  benchmarkSymbol,
  benchmarkAnnualized,
  onSelect,
}: SweepHeatmapProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const legendRef = useRef<HTMLDivElement | null>(null)
  const ttRef = useRef<HTMLDivElement | null>(null)
  // Held in a ref so a fresh inline onSelect each render doesn't re-run the
  // (SVG-rebuilding) effect -- the handlers read the latest via .current.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  useEffect(() => {
    const svg = svgRef.current
    const legend = legendRef.current
    const tt = ttRef.current
    if (!svg || !legend || !tt) return
    svg.innerHTML = ''
    legend.innerHTML = ''

    const byKey = new Map<string, SweepCell>()
    for (const c of cells) byKey.set(`${c.moneyness_pct}_${c.tenor_weeks}`, c)

    // Solid category fills off the scoped tokens (custom properties inherit).
    const style = getComputedStyle(svg)
    const fillFor: Record<Bucket, string> = {
      loss: style.getPropertyValue('--loss').trim(),
      below: style.getPropertyValue('--warm').trim(),
      beats: style.getPropertyValue('--gain').trim(),
    }
    // Label colour picked for contrast on each solid fill: near-black reads on
    // the light amber, white on the saturated red/green.
    const textFor: Record<Bucket, string> = {
      loss: '#ffffff',
      below: '#0b0b0b',
      beats: '#ffffff',
    }

    const nearestMoon = MONEYNESS_AXIS.reduce((p, c) =>
      Math.abs(c - moneynessPct) < Math.abs(p - moneynessPct) ? c : p,
    )

    const W = 1000
    const H = 420
    const P = { l: 82, r: 18, t: 16, b: 50 }
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
            rx: 6,
            fill: 'var(--surface-2)',
            stroke: 'var(--line)',
            'stroke-dasharray': '3,3',
          })
          svg.appendChild(rect)
          return
        }
        const ret = cell.annualized_return
        const bucket = bucketFor(ret, benchmarkAnnualized)
        const rect = svgEl('rect', {
          x: x + 1.5,
          y: y + 1.5,
          width: cw - 3,
          height: ch - 3,
          rx: 6,
          fill: fillFor[bucket],
          class: 'heat-cell',
        })
        const onEnter = (ev: PointerEvent) => {
          const r = svg.getBoundingClientRect()
          showTooltip(
            tt,
            ev.clientX,
            r.top + (y / H) * r.height,
            `<div class="t-k">${mp}% OOM &middot; ${tenorLabel(tw)}</div>` +
              `<div class="t-v" style="color:${fillFor[bucket]}">${fmtPct(ret)}/yr annualized</div>` +
              `<div class="t-sub">total ${fmtPct(cell.roi_on_premium)} on premium &middot; ${cell.n_cycles} rolls</div>`,
          )
        }
        const onLeave = () => hideTooltip(tt)
        rect.addEventListener('pointerenter', onEnter)
        rect.addEventListener('pointerleave', onLeave)
        if (onSelectRef.current) {
          rect.style.cursor = 'pointer'
          rect.setAttribute('role', 'button')
          rect.setAttribute('tabindex', '0')
          rect.setAttribute('aria-label', `Set ${mp}% out-of-the-money at ${tenorLabel(tw)} tenor`)
          const pick = () => onSelectRef.current?.(mp, tw)
          rect.addEventListener('click', pick)
          rect.addEventListener('keydown', (ev) => {
            if (ev.key === 'Enter' || ev.key === ' ') {
              ev.preventDefault()
              pick()
            }
          })
        }
        svg.appendChild(rect)
        const label = svgEl('text', {
          x: x + cw / 2,
          y: y + ch / 2 + 6,
          'text-anchor': 'middle',
          class: 'mono',
          style: `font-size:19px;font-weight:600;font-family:ui-monospace,Menlo,monospace;fill:${textFor[bucket]}`,
        })
        label.textContent = `${ret >= 0 ? '+' : ''}${Math.round(ret * 100)}`
        svg.appendChild(label)
        if (mp === nearestMoon && tw === tenorWeeks) {
          svg.appendChild(
            svgEl('rect', { x: x + 1.5, y: y + 1.5, width: cw - 3, height: ch - 3, rx: 6, class: 'heat-cur' }),
          )
        }
      })
    })

    // axes
    MONEYNESS_AXIS.forEach((m, mi) => {
      const label = svgEl('text', {
        x: P.l + mi * cw + cw / 2,
        y: H - P.b + 24,
        'text-anchor': 'middle',
        class: 'axis-label',
      })
      label.textContent = `${m}%`
      svg.appendChild(label)
    })
    const xlab = svgEl('text', { x: (P.l + W - P.r) / 2, y: H - 8, 'text-anchor': 'middle', class: 'regime-label' })
    xlab.textContent = 'strike distance out-of-the-money →'
    svg.appendChild(xlab)
    TENOR_AXIS.forEach((tw, ti) => {
      const label = svgEl('text', {
        x: P.l - 12,
        y: P.t + ti * ch + ch / 2 + 5,
        'text-anchor': 'end',
        class: 'axis-label',
      })
      label.textContent = tenorLabel(tw)
      svg.appendChild(label)
    })

    // legend -- the named categories, or the two-way fallback when the S&P
    // hurdle is unavailable.
    const entries: ReadonlyArray<readonly [string, Bucket]> =
      benchmarkAnnualized !== null
        ? [
            ['loses money', 'loss'],
            ['below S&P', 'below'],
            ['beats S&P', 'beats'],
          ]
        : [
            ['loses money', 'loss'],
            ['makes money', 'beats'],
          ]
    for (const [text, bucket] of entries) {
      const sw = document.createElement('span')
      sw.className = 'sw'
      const box = document.createElement('span')
      box.className = 'box'
      box.style.background = fillFor[bucket]
      sw.appendChild(box)
      sw.append(` ${text}`)
      legend.appendChild(sw)
    }
  }, [cells, moneynessPct, tenorWeeks, benchmarkAnnualized])

  const hurdle =
    benchmarkAnnualized !== null
      ? `${benchmarkSymbol.toUpperCase()} ${fmtPct(benchmarkAnnualized)}/yr`
      : null

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>The sweep &mdash; strike &times; tenor</h2>
          <div className="hint">
            Colour by <strong>annualized</strong> return on premium vs the S&amp;P 500
            {hurdle ? (
              <>
                {' '}
                (<strong>{hurdle}</strong> buy-and-hold over this window) &mdash; red loses money, amber makes
                money but trails the S&amp;P, green beats it.
              </>
            ) : (
              <> &mdash; benchmark unavailable for this window, so red loses money and green makes money.</>
            )}{' '}
            Cell figures are return on the small put premium, net of brokerage; the S&amp;P figure is buy-and-hold on capital &mdash;
            different bases, so a rough hurdle, not a like-for-like. Your current pick is outlined.
            {onSelect ? ' Click any cell to set that strike + tenor.' : ''}
          </div>
        </div>
        <div className="legend" ref={legendRef} />
      </div>
      <svg
        ref={svgRef}
        id="putlab-heat"
        viewBox="0 0 1000 420"
        role="img"
        aria-label="Heatmap of annualized return on premium by moneyness and tenor, coloured against the S&P 500"
      />
      <div className="tt" ref={ttRef} role="status" aria-live="polite" />
    </>
  )
}
