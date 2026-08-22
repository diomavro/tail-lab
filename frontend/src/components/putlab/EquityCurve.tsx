import { useMemo, useState } from 'react'
import type { EquityPoint, PutBacktestCycle } from '../../api/client'
import { fmtDollar } from './format'

/* Cumulative P&L of a basket, on one date axis.
 *
 * Rendered declaratively rather than by imperative DOM building in an effect
 * (which is how the tape works too, for the same reason): the same data always
 * produces the same markup, so what is on screen can be asserted rather than
 * inferred. Axis labels are HTML in the gutter beside the SVG, not SVG <text>.
 */

const W = 1000
const H = 200
const PAD = 8

interface Props {
  equityCurve: EquityPoint[]
  /** Daily mark-to-model curve for a single-name backtest. Optional: the
   *  Portfolio combined view has no per-day MTM (only a realized curve over the
   *  union of leg expiries) and falls back to equityCurve. */
  mtmCurve?: EquityPoint[]
  cycles: PutBacktestCycle[]
}

export function EquityCurve({ equityCurve, mtmCurve, cycles }: Props) {
  const [hover, setHover] = useState<number | null>(null)
  const isDaily = mtmCurve !== undefined && mtmCurve.length > 0
  const curve = isDaily ? mtmCurve! : equityCurve

  const model = useMemo(() => {
    if (curve.length === 0) return null
    const pts = curve.map((p) => ({ t: Date.parse(p.date), v: p.cum_pnl, date: p.date }))
    const t0 = pts[0]!.t
    const t1 = Math.max(pts[pts.length - 1]!.t, t0 + 1)
    const lo = Math.min(0, ...pts.map((p) => p.v))
    const hi = Math.max(1, ...pts.map((p) => p.v))
    const X = (t: number) => PAD + ((t - t0) / (t1 - t0)) * (W - PAD * 2)
    const Y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (H - PAD * 2)
    const line = pts.map((p, i) => `${i ? 'L' : 'M'} ${X(p.t).toFixed(2)} ${Y(p.v).toFixed(2)}`).join(' ')
    return {
      pts,
      X,
      Y,
      line,
      area: `M ${X(t0).toFixed(2)} ${Y(0).toFixed(2)} ${line.slice(1)} L ${X(t1).toFixed(2)} ${Y(0).toFixed(2)} Z`,
      ticks: [hi, (hi + lo) / 2, lo],
      zeroY: Y(0),
      first: pts[0]!.date,
      last: pts[pts.length - 1]!.date,
    }
  }, [curve])

  if (!model) return null
  const { X, Y } = model
  const marked = new Set(cycles.filter((c) => c.net > 0).map((c) => c.expiry_date))
  const hovered = hover == null ? null : model.pts[hover]

  return (
    <>
      <div className="pl-pane-label">
        {isDaily
          ? 'Marked to model daily — the open put fluctuates and settles at each expiry'
          : 'Running net P&L — premium paid out, payoffs collected at each expiry'}
      </div>
      <div className="pl-chart pl-chart-equity">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Cumulative profit and loss over time"
          onPointerLeave={() => setHover(null)}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const x = ((e.clientX - rect.left) / rect.width) * W
            let best = 0
            for (let i = 1; i < model.pts.length; i++) {
              if (Math.abs(X(model.pts[i]!.t) - x) < Math.abs(X(model.pts[best]!.t) - x)) best = i
            }
            setHover(best)
          }}
        >
          {model.ticks.map((v) => (
            <line key={v} className="pl-tape-grid" x1={PAD} x2={W - PAD} y1={Y(v)} y2={Y(v)} />
          ))}
          <path className="pl-eq-area" d={model.area} />
          <line className="pl-tape-zero" x1={PAD} x2={W - PAD} y1={model.zeroY} y2={model.zeroY} />
          <path className="pl-tape-pnl" d={model.line} />
          {model.pts
            .filter((p) => marked.has(p.date))
            .map((p) => (
              <circle key={p.date} className="pl-eq-settle" cx={X(p.t)} cy={Y(p.v)} r={4} />
            ))}
          {hovered && (
            <line className="pl-eq-cursor" x1={X(hovered.t)} x2={X(hovered.t)} y1={PAD} y2={H - PAD} />
          )}
        </svg>
        {model.ticks.map((v) => (
          <span className="pl-ytick" key={`y${v}`} style={{ top: `${(Y(v) / H) * 100}%` }}>
            {fmtDollar(v)}
          </span>
        ))}
      </div>
      <div className="pl-xticks">
        <span>{model.first}</span>
        <span aria-live="polite">
          {hovered ? `${hovered.date} · ${fmtDollar(hovered.v)}` : ''}
        </span>
        <span>{model.last}</span>
      </div>
    </>
  )
}
