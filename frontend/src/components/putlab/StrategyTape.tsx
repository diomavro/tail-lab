import { useEffect, useRef } from 'react'
import type { AnnualizedPoint, EquityPoint, PricePoint, PutBacktestCycle } from '../../api/client'
import { fmtDollar, fmtPct, svgEl } from './format'

interface StrategyTapeProps {
  pricePath: PricePoint[]
  mtmCurve: EquityPoint[]
  cycles: PutBacktestCycle[]
  // The running "annualized return so far" curve + the S&P buy-and-hold hurdle
  // over the same window, overlaid on the P&L pane against a right % axis.
  annualizedSoFar: AnnualizedPoint[]
  benchmarkAnnualized: number | null
}

// The strategy tape: the real underlying price with each roll's OOM strike
// drawn as a bar over its [entry, expiry] window (so you see which put the sim
// bought and where the strike sat vs the stock), over a PnL pane on the SAME
// date axis -- so a PnL jump lines up visually with the stock diving under a
// strike. Answers "does the PnL move like it should relative to the stock?".
// The PnL pane plots the daily mark-to-model curve (mtmCurve), which aligns
// 1:1 with pricePath's dates, instead of the sparser realized equity_curve.
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
    // Wider right gutter than the base tape: the annualized-so-far overlay hangs
    // its % axis there.
    const P = { l: 56, r: 46 }
    // Two stacked panes sharing the x-axis: price (top), PnL (bottom).
    const price = { t: 12, h: 140 }
    const pnl = { t: 182, h: 104 }

    const px = pricePath.map((p) => ({ t: Date.parse(p.date), v: p.price }))
    const eq = mtmCurve.map((p) => ({ t: Date.parse(p.date), v: p.cum_pnl }))
    const xs = px[0]!.t
    const xe = Math.max(px[px.length - 1]!.t, xs + 1)
    const X = (t: number) => P.l + ((t - xs) / (xe - xs)) * (W - P.l - P.r)

    // price + strike range
    const pricesOnly = px.map((p) => p.v)
    const strikes = cycles.map((c) => c.strike)
    const pmin = Math.min(...pricesOnly, ...strikes)
    const pmax = Math.max(...pricesOnly, ...strikes)
    const PY = (v: number) => price.t + (1 - (v - pmin) / (pmax - pmin || 1)) * price.h

    const vmin = Math.min(0, ...eq.map((p) => p.v))
    const vmax = Math.max(1, ...eq.map((p) => p.v))
    const QY = (v: number) => pnl.t + (1 - (v - vmin) / (vmax - vmin)) * pnl.h

    const add = (tag: keyof SVGElementTagNameMap, attrs: Record<string, string | number>, text?: string) => {
      const e = svgEl(tag, attrs)
      if (text !== undefined) e.textContent = text
      svg.appendChild(e)
    }

    // --- price pane gridlines + $ labels ---
    for (let g = 0; g <= 3; g++) {
      const v = pmin + ((pmax - pmin) * g) / 3
      const y = PY(v)
      add('line', { x1: P.l, y1: y, x2: W - P.r, y2: y, stroke: 'var(--grid)', 'stroke-width': 1 })
      add('text', { x: P.l - 8, y: y + 3, 'text-anchor': 'end', class: 'axis-label' }, fmtDollar(v))
    }
    add('text', { x: P.l, y: price.t - 4, class: 'regime-label' }, 'underlying + strikes bought')

    // --- stock price line ---
    let d = ''
    px.forEach((p, k) => {
      d += (k ? ' L ' : 'M ') + X(p.t) + ' ' + PY(p.v)
    })
    add('path', { d, fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1.6 })

    // --- each roll's OOM strike as a bar over [entry, expiry]; payoff cycles hot ---
    for (const c of cycles) {
      const x1 = X(Date.parse(c.entry_date))
      const x2 = X(Date.parse(c.expiry_date))
      const y = PY(c.strike)
      const paid = c.payoff > c.payoff - c.net // payoff > notional? net>0
      const stroke = paid ? 'var(--gain)' : 'var(--accent)'
      add('line', { x1, y1: y, x2, y2: y, stroke, 'stroke-width': paid ? 2.6 : 1.6, opacity: paid ? 1 : 0.7 })
      // tick at entry so overlapping strikes read as distinct rolls
      add('line', { x1, y1: y - 3, x2: x1, y2: y + 3, stroke, 'stroke-width': 1 })
      if (paid) add('circle', { cx: x2, cy: y, r: 3, fill: 'var(--gain)' })
    }

    // --- PnL pane ---
    add('line', { x1: P.l, y1: QY(0), x2: W - P.r, y2: QY(0), stroke: 'var(--line-strong)', 'stroke-width': 1.2 })
    add('text', { x: P.l - 8, y: QY(vmax) + 3, 'text-anchor': 'end', class: 'axis-label' }, fmtDollar(vmax))
    add('text', { x: P.l - 8, y: QY(vmin) + 3, 'text-anchor': 'end', class: 'axis-label' }, fmtDollar(vmin))
    add('text', { x: P.l, y: pnl.t - 4, class: 'regime-label' }, 'cumulative P&L')
    let dq = ''
    eq.forEach((p, k) => {
      dq += (k ? ' L ' : 'M ') + X(p.t) + ' ' + QY(p.v)
    })
    add('path', { d: dq, fill: 'none', stroke: 'var(--accent)', 'stroke-width': 2 })
    const lastEq = eq[eq.length - 1]!
    add('circle', { cx: X(lastEq.t), cy: QY(lastEq.v), r: 4, fill: 'var(--accent)' })

    // --- annualized-return-so-far overlay on the PnL pane (right % axis) ---
    // A thin secondary line showing, at each roll's expiry, the yearly rate the
    // strategy had earned from the start -- so you can see under what HORIZON it
    // would have been good, and where it crossed the S&P buy-and-hold hurdle.
    const ann = annualizedSoFar.map((p) => ({ t: Date.parse(p.date), v: p.annualized }))
    if (ann.length > 0) {
      // Robust right-axis domain. The earliest points annualize a sub-year ROI
      // and can hit thousands of percent, which would squish the meaningful
      // region; so we always keep 0, the S&P hurdle, and the final rate in view,
      // cap the magnitude around those anchors, and clip the early spikes to the
      // pane's top/bottom edge instead of letting them set the scale.
      const lastV = ann[ann.length - 1]!.v
      const mustShow = [0, lastV, ...(benchmarkAnnualized != null ? [benchmarkAnnualized] : [])]
      const anchorMag = Math.max(0.3, ...mustShow.map((v) => Math.abs(v)))
      const cap = anchorMag * 2.5
      const seriesMax = Math.max(...ann.map((a) => a.v))
      const seriesMin = Math.min(...ann.map((a) => a.v))
      let amax = Math.max(...mustShow, Math.min(seriesMax, cap))
      let amin = Math.min(...mustShow, Math.max(seriesMin, -cap))
      const apad = (amax - amin) * 0.12 || 0.1
      amin -= apad
      amax += apad
      const clamp = (v: number) => Math.max(amin, Math.min(amax, v))
      const AY = (v: number) => pnl.t + (1 - (clamp(v) - amin) / (amax - amin || 1)) * pnl.h
      const rx = W - P.r + 5

      // 0% reference (dashed, subtle) + its right-axis tick.
      add('line', {
        x1: P.l,
        y1: AY(0),
        x2: W - P.r,
        y2: AY(0),
        stroke: 'var(--grid)',
        'stroke-width': 1,
        'stroke-dasharray': '3 3',
      })
      // S&P hurdle (dashed, amber) -- the horizon-crossing the viewer looks for.
      if (benchmarkAnnualized != null) {
        add('line', {
          x1: P.l,
          y1: AY(benchmarkAnnualized),
          x2: W - P.r,
          y2: AY(benchmarkAnnualized),
          stroke: 'var(--warm)',
          'stroke-width': 1.2,
          'stroke-dasharray': '5 4',
        })
        add(
          'text',
          { x: rx, y: AY(benchmarkAnnualized) + 3, class: 'axis-label', fill: 'var(--warm)' },
          fmtPct(benchmarkAnnualized),
        )
      }
      // The annualized-so-far line (thin, cool) + endpoint dot.
      let da = ''
      ann.forEach((a, k) => {
        da += (k ? ' L ' : 'M ') + X(a.t) + ' ' + AY(a.v)
      })
      add('path', { d: da, fill: 'none', stroke: 'var(--cool)', 'stroke-width': 1.4 })
      const lastAnn = ann[ann.length - 1]!
      add('circle', { cx: X(lastAnn.t), cy: AY(lastAnn.v), r: 3, fill: 'var(--cool)' })
      // Right-axis extent labels (percent) in the cool tone.
      add('text', { x: rx, y: AY(amax) + 8, class: 'axis-label', fill: 'var(--cool)' }, fmtPct(amax))
      add('text', { x: rx, y: AY(amin) - 2, class: 'axis-label', fill: 'var(--cool)' }, fmtPct(amin))
      add(
        'text',
        { x: W - P.r, y: pnl.t - 4, 'text-anchor': 'end', class: 'regime-label', fill: 'var(--cool)' },
        'annualized so far',
      )
    }
  }, [pricePath, mtmCurve, cycles, annualizedSoFar, benchmarkAnnualized])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Strategy tape</h2>
          <div className="hint">
            The real underlying with each roll&rsquo;s OOM strike drawn as a bar over its life &mdash; P&amp;L below on
            the same dates, so a payoff lines up with the stock diving under a strike. The cool line is the
            strategy&rsquo;s <strong>annualized return so far</strong> (right axis); where it sits above the dashed
            S&amp;P hurdle is a horizon over which the hedge beat the market.
          </div>
        </div>
        <div className="legend">
          <span className="sw">
            <span className="box" style={{ background: 'var(--muted)' }} /> underlying
          </span>
          <span className="sw">
            <span className="box" style={{ background: 'var(--accent)' }} /> strike bought
          </span>
          <span className="sw">
            <span className="box" style={{ background: 'var(--gain)' }} /> paid off
          </span>
          <span className="sw">
            <span className="box" style={{ background: 'var(--cool)' }} /> annualized so far
          </span>
          <span className="sw">
            <span className="box" style={{ background: 'var(--warm)' }} /> S&amp;P hurdle
          </span>
        </div>
      </div>
      <svg
        ref={svgRef}
        viewBox="0 0 1000 300"
        role="img"
        aria-label="Underlying price with strikes bought, over cumulative P&L"
      />
    </>
  )
}
