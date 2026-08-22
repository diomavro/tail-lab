import { useMemo } from 'react'
import type {
  AnnualizedPoint,
  EquityPoint,
  PricePoint,
  PutBacktestCycle,
  RegimeSegment,
} from '../../api/client'
import { fmtDollar, fmtPct } from './format'

/* The strategy tape: what actually happened, on one date axis.
 *
 * Three changes from the file this replaces.
 *
 *  1. Markers are on the same basis as the stat card above them. They used to
 *     filter on `payoff > 0` -- any intrinsic value -- while `hit_rate` counts
 *     `payoff > notional`. The dots and the number disagreed. Both now mean
 *     "the payoff cleared the premium budget", and the dot is SIZED on
 *     `payoff / notional`, which is what `biggest_payoff_mult` reports.
 *  2. The price pane is on a LOG y-axis. A four-year single-name path is
 *     exponential; against a linear axis it reads as a flat line with one late
 *     kink, and the strike ladder's constant %-below-spot offset stops looking
 *     constant.
 *  3. Axis labels are HTML positioned in a gutter beside the SVG rather than
 *     SVG <text>. SVG text cannot inherit the theme's font stack reliably, does
 *     not pick up `tabular-nums`, and cannot wrap.
 *
 * Rendered declaratively rather than by imperative DOM building in an effect:
 * the same data always produces the same markup, which is what makes the marker
 * basis assertable at all.
 */

/** Drop any tick that lands within `minGap` px of one already kept. */
function thinTicks(values: number[], y: (v: number) => number, minGap = 13): number[] {
  const kept: number[] = []
  for (const v of values) {
    if (kept.every((k) => Math.abs(y(k) - y(v)) >= minGap)) kept.push(v)
  }
  return kept
}

const W = 1000
const PRICE_H = 292
const PNL_H = 120
const PAD_L = 8
const PAD_R = 8

interface Props {
  pricePath: PricePoint[]
  mtmCurve: EquityPoint[]
  cycles: PutBacktestCycle[]
  annualizedSoFar: AnnualizedPoint[]
  benchmarkAnnualized: number | null
  notional: number
  /** VIX-level regime bands (calm < 17 · elevated 17-28 · crisis >= 28) painted
   *  behind the price pane. Calm is the ground and prints as nothing. */
  regimes: RegimeSegment[]
}

export function StrategyTape({
  pricePath,
  mtmCurve,
  cycles,
  annualizedSoFar,
  benchmarkAnnualized,
  notional,
  regimes,
}: Props) {
  const model = useMemo(() => {
    if (pricePath.length === 0 || mtmCurve.length === 0) return null

    const px = pricePath.map((p) => ({ t: Date.parse(p.date), v: p.price }))
    const eq = mtmCurve.map((p) => ({ t: Date.parse(p.date), v: p.cum_pnl }))
    const t0 = px[0]!.t
    const t1 = Math.max(px[px.length - 1]!.t, t0 + 1)
    const X = (t: number) => PAD_L + ((t - t0) / (t1 - t0)) * (W - PAD_L - PAD_R)

    // --- price pane, log scale -------------------------------------------
    const lo = Math.min(...px.map((p) => p.v), ...cycles.map((c) => c.strike))
    const hi = Math.max(...px.map((p) => p.v), ...cycles.map((c) => c.strike))
    const logLo = Math.log(Math.max(lo, 0.01))
    const logHi = Math.log(Math.max(hi, lo * 1.0001))
    const PY = (v: number) =>
      8 + (1 - (Math.log(Math.max(v, 0.01)) - logLo) / (logHi - logLo)) * (PRICE_H - 16)
    // Geometric ticks, so the gutter reads as the axis it actually is.
    const priceTicks = [0, 1, 2, 3].map((i) => Math.exp(logLo + ((logHi - logLo) * i) / 3))

    // --- P&L pane ---------------------------------------------------------
    const vLo = Math.min(0, ...eq.map((p) => p.v))
    const vHi = Math.max(1, ...eq.map((p) => p.v))
    const QY = (v: number) => 8 + (1 - (v - vLo) / (vHi - vLo)) * (PNL_H - 16)

    // --- annualized-so-far, riding the P&L pane on its own scale ----------
    // Its own pane would be a third band for one thin line; instead it shares
    // the P&L pane, with its scale in the LEFT gutter and dollars in the right.
    //
    // The domain has to be anchored rather than fitted. The earliest points
    // annualize a sub-year ROI and routinely reach thousands of percent, so a
    // fitted scale pins 0, the hurdle and the final rate into one pixel row.
    // Keep those three anchors in view, cap the magnitude around them, and let
    // the early spike clip to the edge -- it is an artefact of the horizon, not
    // a reading.
    const ann = annualizedSoFar.map((p) => ({ t: Date.parse(p.date), v: p.annualized }))
    const lastAnn = ann.length > 0 ? ann[ann.length - 1]!.v : 0
    const anchors = [0, lastAnn, ...(benchmarkAnnualized != null ? [benchmarkAnnualized] : [])]
    const cap = Math.max(0.3, ...anchors.map(Math.abs)) * 2.5
    let aHi = Math.max(...anchors, Math.min(Math.max(...ann.map((a) => a.v), 0), cap))
    let aLo = Math.min(...anchors, Math.max(Math.min(...ann.map((a) => a.v), 0), -cap))
    const aPad = (aHi - aLo) * 0.14 || 0.1
    aHi += aPad
    aLo -= aPad
    const clampAnn = (v: number) => Math.max(aLo, Math.min(aHi, v))
    const AY = (v: number) => 8 + (1 - (clampAnn(v) - aLo) / (aHi - aLo || 1)) * (PNL_H - 16)

    const line = (pts: { t: number; v: number }[], y: (v: number) => number) =>
      pts.map((p, i) => `${i ? 'L' : 'M'} ${X(p.t).toFixed(2)} ${y(p.v).toFixed(2)}`).join(' ')

    return {
      X,
      PY,
      QY,
      AY,
      t0,
      t1,
      priceTicks,
      // Max, zero and min, minus any that would print on top of one another.
      // A curve that never goes positive puts max and zero on the same row; one
      // dominated by a single crisis spike puts zero and min there. Two labels
      // stacked are unreadable and read as one number.
      pnlTicks: thinTicks([vHi, 0, vLo], QY),
      pricePathD: line(px, PY),
      pnlD: line(eq, QY),
      annD: ann.length > 1 ? line(ann, AY) : null,
      hurdleY: benchmarkAnnualized == null ? null : AY(benchmarkAnnualized),
      rateTicks: [aHi - aPad, aLo + aPad],
      zeroY: QY(0),
      firstDate: pricePath[0]!.date,
      lastDate: pricePath[pricePath.length - 1]!.date,
    }
  }, [pricePath, mtmCurve, cycles, annualizedSoFar, benchmarkAnnualized])

  if (!model) {
    return (
      <p className="pl-status" role="status">
        Not enough price history to draw the tape.
      </p>
    )
  }

  const { X, PY, QY, AY } = model
  const clampX = (t: number) => Math.max(PAD_L, Math.min(W - PAD_R, X(t)))

  return (
    <>
      <div className="pl-pane-label">Underlying, log scale · strike bought each roll</div>
      <div className="pl-chart pl-chart-price">
        <svg viewBox={`0 0 ${W} ${PRICE_H}`} role="img" aria-label="Underlying price with the strike bought at each roll">
          {/* Regime backdrop first, so every ink prints over it. */}
          {regimes
            .filter((s) => s.regime !== 'calm')
            .map((s) => {
              const x1 = clampX(Date.parse(s.start))
              const x2 = clampX(Date.parse(s.end))
              if (x2 <= x1) return null
              return (
                <rect
                  key={`${s.regime}-${s.start}`}
                  className={`pl-tape-regime is-${s.regime}`}
                  x={x1}
                  y={0}
                  width={x2 - x1}
                  height={PRICE_H}
                />
              )
            })}
          {model.priceTicks.map((v) => (
            <line
              key={`g${v}`}
              className="pl-tape-grid"
              x1={PAD_L}
              x2={W - PAD_R}
              y1={PY(v)}
              y2={PY(v)}
            />
          ))}
          <path className="pl-tape-price" d={model.pricePathD} />
          {cycles.map((c) => {
            const x1 = clampX(Date.parse(c.entry_date))
            const x2 = clampX(Date.parse(c.expiry_date))
            const y = PY(c.strike)
            // The same basis as hit_rate and biggest_payoff_mult: the payoff has
            // to clear the premium budget, not merely finish in the money.
            const mult = c.payoff / notional
            const paid = mult > 1
            return (
              <g key={c.entry_date}>
                <line
                  className={`pl-tape-strike${paid ? ' is-paid' : ''}`}
                  x1={x1}
                  x2={x2}
                  y1={y}
                  y2={y}
                />
                {paid && (
                  <circle
                    className="pl-tape-marker"
                    cx={x2}
                    cy={y}
                    // Sized on payoff / budget, so a 3x roll reads bigger than a
                    // 1.1x one instead of both being one dot.
                    r={Math.min(9, 3 + Math.sqrt(mult))}
                  />
                )}
              </g>
            )
          })}
        </svg>
        {model.priceTicks.map((v) => (
          <span className="pl-ytick" key={`t${v}`} style={{ top: `${(PY(v) / PRICE_H) * 100}%` }}>
            {fmtDollar(v)}
          </span>
        ))}
      </div>

      <div className="pl-pane-label" style={{ marginTop: 14 }}>
        Cumulative P&amp;L on premium · annualized rate so far
      </div>
      <div className="pl-chart pl-chart-pnl pl-chart-two-gutter">
        <svg viewBox={`0 0 ${W} ${PNL_H}`} role="img" aria-label="Cumulative profit and loss, and the annualized rate earned so far">
          <line className="pl-tape-zero" x1={PAD_L} x2={W - PAD_R} y1={model.zeroY} y2={model.zeroY} />
          {model.hurdleY != null && (
            <line
              className="pl-tape-hurdle"
              x1={PAD_L}
              x2={W - PAD_R}
              y1={model.hurdleY}
              y2={model.hurdleY}
            />
          )}
          <path className="pl-tape-pnl" d={model.pnlD} />
          {model.annD && <path className="pl-tape-ann" d={model.annD} />}
        </svg>
        {model.pnlTicks.map((v) => (
          <span className="pl-ytick" key={`q${v}`} style={{ top: `${(QY(v) / PNL_H) * 100}%` }}>
            {fmtDollar(v)}
          </span>
        ))}
        {model.rateTicks.map((v) => (
          <span className="pl-ytick pl-ytick-rate" key={`r${v}`} style={{ top: `${(AY(v) / PNL_H) * 100}%` }}>
            {fmtPct(v)}/yr
          </span>
        ))}
        {benchmarkAnnualized != null && (
          <span
            className="pl-ytick pl-ytick-rate pl-ytick-hurdle"
            style={{ top: `${(AY(benchmarkAnnualized) / PNL_H) * 100}%` }}
          >
            {fmtPct(benchmarkAnnualized)}/yr
          </span>
        )}
      </div>

      <div className="pl-xticks">
        <span>{model.firstDate}</span>
        <span>{model.lastDate}</span>
      </div>
    </>
  )
}
