import { useEffect, useRef } from 'react'
import type { EquityPoint, PricePoint, PutBacktestCycle } from '../../api/client'
import { fmtDollar, svgEl } from './format'

interface StrategyTapeProps {
  pricePath: PricePoint[]
  mtmCurve: EquityPoint[]
  cycles: PutBacktestCycle[]
}

// The strategy tape: the real underlying price with each roll's OOM strike
// drawn as a bar over its [entry, expiry] window (so you see which put the sim
// bought and where the strike sat vs the stock), over a PnL pane on the SAME
// date axis -- so a PnL jump lines up visually with the stock diving under a
// strike. Answers "does the PnL move like it should relative to the stock?".
// The PnL pane plots the daily mark-to-model curve (mtmCurve), which aligns
// 1:1 with pricePath's dates, instead of the sparser realized equity_curve.
export function StrategyTape({ pricePath, mtmCurve, cycles }: StrategyTapeProps) {
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    svg.innerHTML = ''
    if (pricePath.length === 0 || mtmCurve.length === 0) return

    const W = 1000
    const P = { l: 56, r: 16 }
    // Two stacked panes sharing the x-axis: price (top), PnL (bottom).
    const price = { t: 16, h: 210 }
    const pnl = { t: 262, h: 150 }

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
  }, [pricePath, mtmCurve, cycles])

  return (
    <>
      <div className="panel-head">
        <div>
          <h2>Strategy tape</h2>
          <div className="hint">
            The real underlying with each roll&rsquo;s OOM strike drawn as a bar over its life &mdash; P&amp;L below on
            the same dates, so a payoff lines up with the stock diving under a strike.
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
        </div>
      </div>
      <svg
        ref={svgRef}
        viewBox="0 0 1000 438"
        role="img"
        aria-label="Underlying price with strikes bought, over cumulative P&L"
      />
    </>
  )
}
