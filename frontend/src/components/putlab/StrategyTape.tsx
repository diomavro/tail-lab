import { useEffect, useRef } from 'react'
import type { AnnualizedPoint, EquityPoint, PricePoint, PutBacktestCycle } from '../../api/client'
import { fmtDollar, fmtPct, svgEl } from './format'

interface StrategyTapeProps {
  pricePath: PricePoint[]
  mtmCurve: EquityPoint[]
  cycles: PutBacktestCycle[]
  // The running "annualized return so far" curve + the S&P buy-and-hold hurdle,
  // shown in their OWN pane (not overlaid on the $ P&L) so the two comparisons
  // never blur together.
  annualizedSoFar: AnnualizedPoint[]
  benchmarkAnnualized: number | null
}

// The strategy tape: three stacked panes sharing one date axis.
//   1. underlying price + each roll's OOM strike bar (where the put sat vs spot),
//   2. cumulative $ P&L (does the P&L jump when the stock dives under a strike?),
//   3. annualized-return-so-far vs the S&P hurdle (at what HORIZON did it beat
//      just holding the market — the strategy line above the dashed hurdle).
// Each pane is one comparison in one colour, so nothing collides.
export function StrategyTape({
  pricePath,
  mtmCurve,
  cycles,
  annualizedSoFar,
  benchmarkAnnualized,
}: StrategyTapeProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    svg.innerHTML = ''
    if (pricePath.length === 0 || mtmCurve.length === 0) return

    const W = 1000
    const P = { l: 58, r: 58 }
    // Three stacked panes sharing the x-axis.
    const price = { t: 16, h: 132 }
    const pnl = { t: 182, h: 84 }
    const annp = { t: 300, h: 84 }

    const px = pricePath.map((p) => ({ t: Date.parse(p.date), v: p.price }))
    const eq = mtmCurve.map((p) => ({ t: Date.parse(p.date), v: p.cum_pnl }))
    const xs = px[0]!.t
    const xe = Math.max(px[px.length - 1]!.t, xs + 1)
    const X = (t: number) => P.l + ((t - xs) / (xe - xs)) * (W - P.l - P.r)

    const add = (tag: keyof SVGElementTagNameMap, attrs: Record<string, string | number>, text?: string) => {
      const e = svgEl(tag, attrs)
      if (text !== undefined) e.textContent = text
      svg.appendChild(e)
    }
    const path = (pts: { t: number; v: number }[], y: (v: number) => number, attrs: Record<string, string | number>) => {
      let d = ''
      pts.forEach((p, k) => {
        d += (k ? ' L ' : 'M ') + X(p.t) + ' ' + y(p.v)
      })
      add('path', { d, fill: 'none', ...attrs })
    }

    // ---------- pane 1: underlying price + strike bars ----------
    const pricesOnly = px.map((p) => p.v)
    const strikes = cycles.map((c) => c.strike)
    const pmin = Math.min(...pricesOnly, ...strikes)
    const pmax = Math.max(...pricesOnly, ...strikes)
    const PY = (v: number) => price.t + (1 - (v - pmin) / (pmax - pmin || 1)) * price.h
    for (let g = 0; g <= 3; g++) {
      const v = pmin + ((pmax - pmin) * g) / 3
      add('line', { x1: P.l, y1: PY(v), x2: W - P.r, y2: PY(v), stroke: 'var(--grid)', 'stroke-width': 1 })
      add('text', { x: P.l - 8, y: PY(v) + 3, 'text-anchor': 'end', class: 'axis-label' }, fmtDollar(v))
    }
    add('text', { x: P.l, y: price.t - 5, class: 'regime-label' }, 'underlying + strike bought')
    path(px, PY, { stroke: 'var(--muted)', 'stroke-width': 1.6 })
    for (const c of cycles) {
      const x1 = X(Date.parse(c.entry_date))
      const x2 = X(Date.parse(c.expiry_date))
      const y = PY(c.strike)
      const paid = c.net > 0
      const stroke = paid ? 'var(--gain)' : 'var(--accent)'
      add('line', { x1, y1: y, x2, y2: y, stroke, 'stroke-width': paid ? 2.6 : 1.6, opacity: paid ? 1 : 0.7 })
      add('line', { x1, y1: y - 3, x2: x1, y2: y + 3, stroke, 'stroke-width': 1 })
      if (paid) add('circle', { cx: x2, cy: y, r: 3, fill: 'var(--gain)' })
    }

    // ---------- pane 2: cumulative $ P&L ----------
    const vmin = Math.min(0, ...eq.map((p) => p.v))
    const vmax = Math.max(1, ...eq.map((p) => p.v))
    const QY = (v: number) => pnl.t + (1 - (v - vmin) / (vmax - vmin)) * pnl.h
    add('line', { x1: P.l, y1: QY(0), x2: W - P.r, y2: QY(0), stroke: 'var(--line-strong)', 'stroke-width': 1.2 })
    add('text', { x: P.l - 8, y: QY(vmax) + 3, 'text-anchor': 'end', class: 'axis-label' }, fmtDollar(vmax))
    add('text', { x: P.l - 8, y: QY(vmin) + 3, 'text-anchor': 'end', class: 'axis-label' }, fmtDollar(vmin))
    add('text', { x: P.l, y: pnl.t - 5, class: 'regime-label' }, 'cumulative P&L ($)')
    path(eq, QY, { stroke: 'var(--accent)', 'stroke-width': 2 })
    const lastEq = eq[eq.length - 1]!
    add('circle', { cx: X(lastEq.t), cy: QY(lastEq.v), r: 4, fill: 'var(--accent)' })

    // ---------- pane 3: annualized return so far vs the S&P hurdle ----------
    add('text', { x: P.l, y: annp.t - 5, class: 'regime-label' }, 'annualized return so far vs S&P')
    const ann = annualizedSoFar.map((p) => ({ t: Date.parse(p.date), v: p.annualized }))
    if (ann.length > 0) {
      // Robust % domain: the earliest points annualize a sub-year ROI and can hit
      // thousands of percent; keep 0, the hurdle, and the final rate in view, cap
      // the magnitude around those anchors, and clip early spikes to the edge.
      const lastV = ann[ann.length - 1]!.v
      const hurdle = benchmarkAnnualized
      const mustShow = [0, lastV, ...(hurdle != null ? [hurdle] : [])]
      const anchorMag = Math.max(0.3, ...mustShow.map((v) => Math.abs(v)))
      const cap = anchorMag * 2.5
      let amax = Math.max(...mustShow, Math.min(Math.max(...ann.map((a) => a.v)), cap))
      let amin = Math.min(...mustShow, Math.max(Math.min(...ann.map((a) => a.v)), -cap))
      const apad = (amax - amin) * 0.14 || 0.1
      amin -= apad
      amax += apad
      const clamp = (v: number) => Math.max(amin, Math.min(amax, v))
      const AY = (v: number) => annp.t + (1 - (clamp(v) - amin) / (amax - amin || 1)) * annp.h

      // left % axis extents
      add('text', { x: P.l - 8, y: AY(amax) + 8, 'text-anchor': 'end', class: 'axis-label' }, fmtPct(amax))
      add('text', { x: P.l - 8, y: AY(amin) - 2, 'text-anchor': 'end', class: 'axis-label' }, fmtPct(amin))
      // 0% baseline (subtle solid)
      add('line', { x1: P.l, y1: AY(0), x2: W - P.r, y2: AY(0), stroke: 'var(--line-strong)', 'stroke-width': 1 })
      // S&P hurdle: a GREY DASHED reference line, labelled on the right so it can
      // never be mistaken for the orange strike bars or the teal strategy line.
      if (hurdle != null) {
        add('line', {
          x1: P.l,
          y1: AY(hurdle),
          x2: W - P.r,
          y2: AY(hurdle),
          stroke: 'var(--faint)',
          'stroke-width': 1.4,
          'stroke-dasharray': '6 4',
        })
        add(
          'text',
          { x: W - P.r + 6, y: AY(hurdle) + 3, class: 'axis-label', fill: 'var(--faint)' },
          `S&P ${fmtPct(hurdle)}`,
        )
      }
      // the strategy's annualized-so-far line (teal), endpoint green/red vs hurdle
      path(ann, AY, { stroke: 'var(--cool)', 'stroke-width': 1.8 })
      const lastAnn = ann[ann.length - 1]!
      const beat = hurdle != null && lastAnn.v >= hurdle
      add('circle', {
        cx: X(lastAnn.t),
        cy: AY(lastAnn.v),
        r: 3.5,
        fill: beat ? 'var(--gain)' : 'var(--loss)',
      })
    } else {
      add(
        'text',
        { x: (P.l + W - P.r) / 2, y: annp.t + annp.h / 2, 'text-anchor': 'middle', class: 'axis-label' },
        'not enough horizon yet',
      )
    }
  }, [pricePath, mtmCurve, cycles, annualizedSoFar, benchmarkAnnualized])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Strategy tape</h2>
          <div className="hint">
            Three views on one time axis: the <strong>underlying</strong> with each roll&rsquo;s bought{' '}
            <strong>strike</strong>, the cumulative <strong>$ P&amp;L</strong>, and the strategy&rsquo;s{' '}
            <strong>annualized return so far</strong> against the <strong>S&amp;P hurdle</strong> &mdash; wherever the
            teal line sits above the grey dashed line, that holding-horizon beat just owning the market.
          </div>
        </div>
        <div className="legend">
          <span className="sw">
            <span className="ln" style={{ background: 'var(--muted)' }} /> underlying
          </span>
          <span className="sw">
            <span className="bar" style={{ background: 'var(--accent)' }} /> strike bought
          </span>
          <span className="sw">
            <span className="bar" style={{ background: 'var(--gain)' }} /> paid off
          </span>
          <span className="sw">
            <span className="ln" style={{ background: 'var(--accent)' }} /> $ P&amp;L
          </span>
          <span className="sw">
            <span className="ln" style={{ background: 'var(--cool)' }} /> annualized so far
          </span>
          <span className="sw">
            <span className="ln-dash" /> S&amp;P hurdle
          </span>
        </div>
      </div>
      <svg
        ref={svgRef}
        viewBox="0 0 1000 384"
        role="img"
        aria-label="Underlying price with strikes, cumulative P&L, and annualized return so far vs the S&P"
      />
    </>
  )
}
