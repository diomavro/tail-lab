// The Surface tab's Smile section: implied vol against log-forward moneyness
// k = ln(K/F), one session (docs/adr/0036). A slice that was not fitted reads
// as its refusal in words -- never a blank or a zero, which would pass for a
// measurement.
import type { ReactNode } from 'react'
import type { SmileSlice, SviParams, VolSurfaceResponse } from '../../../api/client'
import { dots, poly, tickText, type Label, type Pt } from '../chart'
import { ChartLabels } from '../ChartLabels'
import type { ResourceState } from '../PutLab'
import { Th } from '../Th'

/** Tenors (days) the chart draws, nearest available slice to each. */
const CHART_TENORS = [30, 90, 180]
const SERIES = [
  { line: 'pl-c-line', dot: 'pl-c-ink', key: 'pl-key-line' },
  { line: 'pl-c-loss', dot: 'pl-c-dot-mag', key: 'pl-key-loss' },
  { line: 'pl-c-bs', dot: 'pl-c-dot-muted', key: 'pl-key-dash' },
]

const days = (s: SmileSlice) => Math.round(s.t_years * 365.25)
const pct = (v: number | null, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`)
const num = (v: number | null, d = 2) => (v == null ? '—' : v.toFixed(d))

function sviVol(p: SviParams, k: number, t: number): number {
  const x = k - p.m
  const w = p.a + p.b * (p.rho * x + Math.sqrt(x * x + p.sigma * p.sigma))
  return Math.sqrt(Math.max(w, 0) / t)
}

function pickSlices(slices: SmileSlice[]): SmileSlice[] {
  const fitted = slices.filter((s) => s.fit !== null && s.points.length > 0)
  const out: SmileSlice[] = []
  for (const target of CHART_TENORS) {
    const best = fitted.reduce<SmileSlice | null>(
      (acc, s) => (acc === null || Math.abs(days(s) - target) < Math.abs(days(acc) - target) ? s : acc),
      null,
    )
    if (best && !out.includes(best)) out.push(best)
  }
  return out
}

function forwardWords(s: SmileSlice): string {
  if (s.forward_source === 'parity') return 'parity'
  return s.forward_source === 'carry_q0' ? 'carry, q unknown' : 'carry'
}

function checks(s: SmileSlice): ReactNode {
  if (s.refusal)
    return (
      <>
        <strong>REFUSED</strong>: {s.refusal}
      </>
    )
  if (s.fit === 'one_wing_quadratic') return 'one wing: shape only'
  const fly = s.butterfly_ok ? 'butterfly ok' : `butterfly FAILS (min g ${num(s.butterfly_min_g, 4)})`
  return `${fly} · ${s.lee_ok ? 'Lee ok' : 'Lee FAILS'}`
}

function SmileChart({ chosen, narrow }: { chosen: SmileSlice[]; narrow: boolean }) {
  const all = chosen.flatMap((s) => s.points)
  const kLo = Math.min(...all.map((p) => p.k))
  const kHi = Math.max(...all.map((p) => p.k))
  // The y-range holds the fitted curves too: an SVI that overshoots the
  // quotes between strikes must not draw outside the plot box.
  const curveVols = chosen.flatMap((s) => {
    const ks = s.points.map((p) => p.k)
    const [lo, hi] = [Math.min(...ks), Math.max(...ks)]
    return s.svi ? Array.from({ length: 80 }, (_, j) => sviVol(s.svi!, lo + ((hi - lo) * j) / 79, s.t_years)) : []
  })
  const vLo = Math.min(...all.map((p) => p.iv), ...curveVols)
  const vHi = Math.max(...all.map((p) => p.iv), ...curveVols)
  const W = narrow ? 400 : 860
  const H = 300
  const [X0, X1, Y0, Y1] = [56, W - 12, 14, H - 44]
  const X = (k: number) => X0 + ((k - kLo) / (kHi - kLo || 1)) * (X1 - X0)
  const Y = (v: number) => Y1 - ((v - vLo) / (vHi - vLo || 1)) * (Y1 - Y0)
  const kTicks = [kLo, kLo / 2, 0, kHi / 2, kHi].filter((k) => k >= kLo && k <= kHi)
  const vTicks = [vLo, (vLo + vHi) / 2, vHi]
  const labels: Label[] = [
    ...kTicks.map((k, i) => ({
      x: X(k),
      y: Y1 + 20,
      text: `k ${tickText(Number(k.toFixed(3)))}`,
      align: (i === 0 ? 'start' : i === kTicks.length - 1 ? 'end' : 'middle') as Label['align'],
    })),
    ...vTicks.map((v) => ({
      x: X0 - 6,
      y: Y(v) + 4,
      text: pct(v, 0),
      align: 'end' as const,
    })),
  ]
  const description = chosen
    .map((s) => `${days(s)} days: ATM ${pct(s.atm_vol)}, skew ${num(s.atm_skew)} per unit k`)
    .join('; ')
  return (
    <div style={{ maxWidth: 900 }}>
      <div className="pl-plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Implied vol by log-moneyness. ${description}.`}
          data-testid="smile-chart"
        >
          <path d={`M${X0},${Y1}H${X1}M${X(0)},${Y0}V${Y1}`} className="pl-c-axis" />
          {chosen.map((s, i) => {
            const series = SERIES[i]!
            const pts: Pt[] = s.points.map((p) => [X(p.k), Y(p.iv)])
            const lo = Math.min(...s.points.map((p) => p.k))
            const hi = Math.max(...s.points.map((p) => p.k))
            const curve: Pt[] = s.svi
              ? Array.from({ length: 80 }, (_, j) => {
                  const k = lo + ((hi - lo) * j) / 79
                  return [X(k), Y(sviVol(s.svi!, k, s.t_years))] as Pt
                })
              : []
            return (
              <g key={s.expiration} data-testid="smile-series">
                {curve.length > 0 && <path
                    d={poly(curve)}
                    className={series.line}
                    data-testid="smile-fit"
                    data-atm-vol={sviVol(s.svi!, 0, s.t_years).toFixed(4)}
                  />}
                <path d={dots(pts, 2.2)} className={series.dot} />
              </g>
            )
          })}
        </svg>
        <ChartLabels labels={labels} w={W} h={H} />
      </div>
      <div className="pl-keyrow">
        {chosen.map((s, i) => (
          <span key={s.expiration}>
            <i className={`pl-key ${SERIES[i]!.key}`} />
            {days(s)} days ({s.expiration}){s.fit === 'one_wing_quadratic' ? ', one wing' : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

export function SmileSection({ smile, narrow }: { smile: ResourceState<VolSurfaceResponse>; narrow: boolean }) {
  const head = (
    <div>
      <h3>Smile</h3>
      <p className="pl-chart-sub">
        Implied vol against log-forward moneyness k = ln(K/F): at the money is k = 0 at every tenor. Out-of-the-money
        quotes only, at the mid, with the SVI fit drawn through them.
      </p>
    </div>
  )
  if (smile.status !== 'ready') {
    const words =
      smile.status === 'loading'
        ? 'Reading the smiles…'
        : smile.status === 'error'
          ? `Could not read the smiles: ${smile.message}`
          : 'No option chain is known for this name yet.'
    return (
      <section aria-label="Smile" className="pl-surface-block">
        {head}
        <p className="pl-status" role={smile.status === 'error' ? 'alert' : 'status'}>
          {words}
        </p>
      </section>
    )
  }
  const v = smile.data
  if (v.refusal) {
    return (
      <section aria-label="Smile" className="pl-surface-block">
        {head}
        <p className="pl-status" role="status" data-testid="smile-refusal">
          REFUSED: {v.refusal}
        </p>
      </section>
    )
  }
  const chosen = pickSlices(v.slices)
  return (
    <section aria-label="Smile" className="pl-surface-block">
      {head}
      <p className="pl-view-note" data-testid="smile-session">
        Session {v.session} · {v.two_wing ? 'puts and calls' : `puts only (${v.one_wing_reason})`}
        {v.q_source === 'unknown' ? ' · dividend yield unknown, carry forwards at q = 0' : ''}
        {v.calendar_violations.length > 0
          ? ` · ${v.calendar_violations.length} calendar violation${v.calendar_violations.length > 1 ? 's' : ''} between adjacent expiries`
          : ''}
      </p>
      {chosen.length > 0 ? (
        <SmileChart chosen={chosen} narrow={narrow} />
      ) : (
        <p className="pl-status" role="status">
          No expiry could be fitted.
        </p>
      )}
      <details>
        <summary>Every expiry</summary>
        <div className="pl-scroll">
          <table className="pl-table" aria-label="Every expiry" data-testid="smile-table">
            <thead>
              <tr>
                <th scope="col" data-noexplain="row-label">
                  Expiry
                </th>
                <Th scope="col" concept="days_to_expiry">
                  Days
                </Th>
                <Th scope="col" concept="forward_price">
                  Forward
                </Th>
                <Th scope="col" concept="atm_vol">
                  ATM vol
                </Th>
                <Th scope="col" concept="smile_skew">
                  Skew dσ/dk
                </Th>
                <Th scope="col" concept="risk_reversal_25">
                  25Δ RR
                </Th>
                <Th scope="col" concept="smile_checks">
                  Checks
                </Th>
              </tr>
            </thead>
            <tbody>
              {v.slices.map((s) => (
                <tr key={s.expiration}>
                  <td>{s.expiration}</td>
                  <td>{days(s)}</td>
                  <td>
                    {s.forward.toFixed(2)} ({forwardWords(s)})
                  </td>
                  <td>{pct(s.atm_vol)}</td>
                  <td>{num(s.atm_skew)}</td>
                  <td>{s.risk_reversal_25 == null ? '—' : `${(s.risk_reversal_25 * 100).toFixed(1)} pts`}</td>
                  <td>{checks(s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}
