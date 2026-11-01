import { useMemo, useState } from 'react'
import type { BeyondVolTest, SignalTestResult } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { beyondVolLine, signed3 } from './signalWording'

/* Fragility beyond trailing vol (docs/adr/0035): the one sentence under the
 * verdict, and the per-period slopes behind it. Everything printed is the API's
 * result. A confirmatory run (the pre-registered setting on the pre-registered
 * date) alone gets a p-value and a verdict; a later re-run (monitoring) and any
 * other setting (exploratory) read gamma_bar and its CI only
 * (signalWording.ts `beyondVolLine`), and then show no chart either.
 */

export function BeyondVolSentence({ result }: { result: Pick<SignalTestResult, 'beyond_vol' | 'beyond_vol_mode' | 'beyond_vol_unavailable_reason' | 'as_of'> }) {
  const line = beyondVolLine(result)
  return (
    <p className="pl-signal-beyond" data-testid="signal-beyond-vol" data-mode={line.mode}>
      {line.text}
      {line.caveat != null && (
        <>
          {' '}
          <span className="dim" data-testid="signal-beyond-vol-caveat">
            {line.caveat}
          </span>
        </>
      )}
      <ConceptInfo id="fama_macbeth" />
    </p>
  )
}

const W = 1000
const H = 160
const PAD = 8

/** The per-period slopes gamma_t, one bar each, with the mean and its 90% CI.
 *  Shown for a confirmatory run only. */
export function GammaChart({ bv }: { bv: BeyondVolTest }) {
  const [hover, setHover] = useState<number | null>(null)
  const periods = bv.per_period
  const model = useMemo(() => {
    if (periods.length === 0) return null
    const g = periods.map((p) => p.gamma)
    const ci = bv.ci90 == null ? [] : [bv.ci90.low, bv.ci90.high]
    const lo = Math.min(0, ...g, ...ci)
    const hi = Math.max(0, ...g, ...ci, lo + 0.05)
    const Y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (H - PAD * 2)
    return { lo, hi, Y, sw: (W - PAD * 2) / periods.length }
  }, [periods, bv.ci90])
  if (!model) return null
  const { Y, sw, lo, hi } = model
  const hovered = hover == null ? null : periods[hover]
  const mean = bv.gamma_bar
  return (
    <div data-testid="gamma-chart">
      <div className="pl-pane-label">
        Per-period slope of put outcome on fragility, trailing vol held fixed
        <ConceptInfo id="fama_macbeth" />
      </div>
      <div className="pl-chart">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Slope of the put-outcome rank on the composite rank in each of ${periods.length} periods, trailing vol held fixed${
            mean == null ? '' : `; the mean is ${signed3(mean)}`
          }${bv.ci90 == null ? '' : ` with a 90% interval from ${signed3(bv.ci90.low)} to ${signed3(bv.ci90.high)}`}`}
          onPointerLeave={() => setHover(null)}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const x = ((e.clientX - rect.left) / rect.width) * W
            setHover(Math.min(periods.length - 1, Math.max(0, Math.floor((x - PAD) / sw))))
          }}
        >
          {bv.ci90 != null && (
            <rect
              className="pl-sig-band"
              data-testid="gamma-ci"
              x={PAD}
              y={Y(bv.ci90.high)}
              width={W - PAD * 2}
              height={Math.max(1, Y(bv.ci90.low) - Y(bv.ci90.high))}
            />
          )}
          <line className="pl-tape-zero" x1={PAD} x2={W - PAD} y1={Y(0)} y2={Y(0)} />
          {periods.map((p, i) => (
            <rect
              key={p.date}
              className={`pl-sig-bar ${p.gamma >= 0 ? 'is-pos' : 'is-neg'}`}
              x={PAD + i * sw + sw * 0.2}
              y={Math.min(Y(0), Y(p.gamma))}
              width={sw * 0.6}
              height={Math.max(1, Math.abs(Y(p.gamma) - Y(0)))}
            />
          ))}
          {mean != null && <line className="pl-tape-hurdle" data-testid="gamma-mean" x1={PAD} x2={W - PAD} y1={Y(mean)} y2={Y(mean)} />}
        </svg>
        {[hi, 0, lo].map((v) => (
          <span className="pl-ytick" key={`y${v}`} style={{ top: `${(Y(v) / H) * 100}%` }}>
            {signed3(v)}
          </span>
        ))}
      </div>
      <div className="pl-xticks">
        <span>{periods[0]!.date}</span>
        <span aria-live="polite">{hovered ? `${hovered.date} · γ ${signed3(hovered.gamma)}` : ''}</span>
        <span>{periods[periods.length - 1]!.date}</span>
      </div>
      <div className="pl-legend" style={{ marginTop: 6 }}>
        <span>
          <span className="pl-sig-key pl-sig-key-bar" />
          One period&rsquo;s slope
        </span>
        <span>
          <span className="pl-sig-key pl-sig-key-band" />
          90% CI of the mean slope
        </span>
        <span>Dashed line: the mean slope</span>
      </div>
    </div>
  )
}
