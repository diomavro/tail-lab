import { useState } from 'react'
import type {
  SurfaceAnchorReading,
  SurfaceReading,
  SurfaceRealised,
  SurfaceResponse,
  SurfaceRung,
} from '../../../api/client'
import { fmtFixed } from '../format'
import type { ResourceState } from '../PutLab'
import type { PutLabControls } from '../types'

/* The Surface: the market's implied tail index beside the realised one.
 *
 * No control of its own -- the anchor IS the rail's OOM control, which is what
 * makes anchor-relativity physical. Every fit that can refuse renders the word
 * REFUSED with its reason: a blank, a dash or a zero would read as a measurement
 * (docs/adr/0026 §7). The caveat ships in the same view as the number.
 */

const W = 520
const H = 260
const PAD = 14

/** Rungs drawn: the Surface is one screen, so the table is capped. */
const MAX_RUNGS = 8

const refused = (reason: string | null | undefined) => (
  <span className="pl-refused" title={reason ?? undefined}>
    REFUSED{reason ? ` — ${reason}` : ''}
  </span>
)

const alphaOrRefused = (alpha: number | null | undefined, reason: string | null | undefined) =>
  alpha == null ? refused(reason) : <strong>{fmtFixed(alpha, 2)}</strong>

type LogPoint = { x: number; y: number }
type Series = { name: string; className: string; points: LogPoint[] }

const finitePositive = (x: number | null | undefined): x is number =>
  x != null && Number.isFinite(x) && x > 0

/** A log-log frame over every series. Points that are not strictly positive
 *  have no logarithm and are dropped -- on a survival curve that is the largest
 *  observation, whose share above it is exactly 0. */
function LogLogPlot({
  series,
  xLabel,
  yLabel,
  rule,
  ariaLabel,
}: {
  series: Series[]
  xLabel: string
  yLabel: string
  rule?: { x: number; label: string } | null
  ariaLabel: string
}) {
  const drawn = series.map((s) => ({
    ...s,
    points: s.points.filter((p) => finitePositive(p.x) && finitePositive(p.y)),
  }))
  const all = drawn.flatMap((s) => s.points)
  if (all.length < 2) return <p className="pl-status">Nothing to plot on log axes.</p>
  const lx = all.map((p) => Math.log10(p.x))
  const ly = all.map((p) => Math.log10(p.y))
  const [x0, x1] = [Math.min(...lx), Math.max(...lx, Math.min(...lx) + 1e-9)]
  const [y0, y1] = [Math.min(...ly), Math.max(...ly, Math.min(...ly) + 1e-9)]
  const X = (v: number) => PAD + ((Math.log10(v) - x0) / (x1 - x0)) * (W - 2 * PAD)
  const Y = (v: number) => H - PAD - ((Math.log10(v) - y0) / (y1 - y0)) * (H - 2 * PAD)
  const ruleX = rule && finitePositive(rule.x) ? X(rule.x) : null
  return (
    <figure className="pl-surface-plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        {drawn.map((s) => (
          <polyline
            key={s.name}
            className={s.className}
            fill="none"
            points={s.points.map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(' ')}
          />
        ))}
        {ruleX !== null && ruleX >= PAD && ruleX <= W - PAD && (
          <line className="pl-surface-onset" x1={ruleX} x2={ruleX} y1={PAD} y2={H - PAD} />
        )}
      </svg>
      <figcaption className="pl-micro">
        {drawn.map((s) => (
          <span key={s.name} className="pl-surface-key">
            <i className={`pl-surface-swatch ${s.className}`} />
            {s.name}
          </span>
        ))}
        {rule && <span className="pl-surface-key">| {rule.label}</span>}
        <br />x: {xLabel} · y: {yLabel} · both log₁₀
      </figcaption>
    </figure>
  )
}

function ZipfPlot({ surface }: { surface: SurfaceReading }) {
  const [view, setView] = useState<'survival' | 'price'>('survival')
  const realised = surface.realised
  const gross = realised?.survival_gross?.points ?? []
  const loss = realised?.survival_loss?.points ?? []
  const rungs = surface.ladder.slice(0, MAX_RUNGS)
  return (
    <section aria-label="Zipf plot" className="pl-surface-pane">
      <div className="pl-seg" role="radiogroup" aria-label="Plot view">
        {(['survival', 'price'] as const).map((v) => (
          <label className="pl-seg-opt" key={v}>
            <input type="radio" name="pl-surface-view" checked={view === v} onChange={() => setView(v)} />
            {v === 'survival' ? 'Survival' : 'Price vs strike'}
          </label>
        ))}
      </div>
      {view === 'survival' ? (
        gross.length + loss.length === 0 ? (
          <p className="pl-status">No price history read for this name, so there is no survival curve.</p>
        ) : (
          <LogLogPlot
            ariaLabel="Survival curves, gross move S and arithmetic loss r"
            xLabel="move size"
            yLabel="P(X > x)"
            rule={realised?.onset != null ? { x: realised.onset, label: 'Karamata onset' } : null}
            series={[
              { name: 'S (gross)', className: 'pl-surface-s', points: gross.map(([x, y]) => ({ x, y })) },
              { name: 'r (loss)', className: 'pl-surface-r', points: loss.map(([x, y]) => ({ x, y })) },
            ]}
          />
        )
      ) : (
        <LogLogPlot
          ariaLabel="Log put price against log strike, Black-Scholes overlaid"
          xLabel="strike"
          yLabel="put price"
          series={[
            {
              name: 'Market mid',
              className: 'pl-surface-r',
              points: rungs.map((r) => ({ x: r.strike, y: r.market_price ?? Number.NaN })),
            },
            {
              name: 'Paretan',
              className: 'pl-surface-s',
              points: rungs.map((r) => ({ x: r.strike, y: r.paretan_price })),
            },
            {
              name: 'Black–Scholes',
              className: 'pl-surface-bs',
              points: rungs.map((r) => ({ x: r.strike, y: r.black_scholes_price ?? Number.NaN })),
            },
          ]}
        />
      )}
    </section>
  )
}

const money = (x: number | null) => (x == null ? '—' : x.toFixed(2))

function TailLadder({ surface }: { surface: SurfaceReading }) {
  const rungs: SurfaceRung[] = surface.ladder.slice(0, MAX_RUNGS)
  return (
    <section aria-label="Tail ladder" className="pl-surface-pane">
      <div className="pl-pane-label">
        Tail ladder — Paretan price against the market, deeper than the anchor
      </div>
      {rungs.length === 0 ? (
        <p className="pl-status">{refused(surface.ladder_reason)}</p>
      ) : (
        <table className="pl-table" aria-label="Tail ladder rungs">
          <thead>
            <tr>
              <th className="num">Strike</th>
              <th className="num">Bid</th>
              <th className="num">Ask</th>
              <th className="num">Paretan</th>
              <th className="num">BS</th>
              <th className="num">IV ratio</th>
            </tr>
          </thead>
          <tbody>
            {rungs.map((r) => (
              <tr key={r.strike}>
                <td className="num">{r.strike.toFixed(2)}</td>
                <td className="num dim">{money(r.bid)}</td>
                <td className="num dim">{money(r.ask)}</td>
                <td className="num">{money(r.paretan_price)}</td>
                <td className="num dim">{money(r.black_scholes_price)}</td>
                <td className="num bold">{r.iv_ratio == null ? '—' : fmtFixed(r.iv_ratio, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function AnchorLine({ reading }: { reading: SurfaceAnchorReading }) {
  const alpha = reading.fit?.alpha ?? null
  const why = reading.fit?.refusal ?? reading.refusal
  const ceiling = reading.ceiling
  return (
    <span className="pl-surface-anchor" data-testid="implied-anchor">
      K {reading.strike.toFixed(0)}: {alphaOrRefused(alpha, why)}
      {' · ceiling '}
      {ceiling ? alphaOrRefused(ceiling.alpha, ceiling.refusal) : refused(reading.ceiling_reason)}
    </span>
  )
}

function AlphaStrip({ surface }: { surface: SurfaceReading }) {
  const { anchors, realised } = surface
  return (
    <div className="pl-surface-strip" role="status" aria-label="Alpha strip">
      <span>
        <span className="pl-kicker">Implied α</span>{' '}
        {anchors.readings.map((a) => (
          <AnchorLine key={a.strike} reading={a} />
        ))}
      </span>
      <span>
        <span className="pl-kicker">Dispersion</span>{' '}
        {anchors.dispersion == null ? refused(anchors.dispersion_reason) : <strong>{anchors.dispersion.toFixed(3)}</strong>}
      </span>
      <span>
        <span className="pl-kicker">Realised α</span>{' '}
        {realised ? alphaOrRefused(realised.alpha, realised.refusal) : refused(surface.realised_reason)}
      </span>
      <span>
        <span className="pl-kicker">Gap</span>{' '}
        {surface.alpha_gap == null ? refused(surface.alpha_gap_reason) : <strong>{surface.alpha_gap.toFixed(2)}</strong>}
      </span>
    </div>
  )
}

function RealisedCaveat({ realised }: { realised: SurfaceRealised | null }) {
  if (!realised) return null
  return (
    <p>
      Realised side: {realised.n_beyond} observations beyond the onset
      {realised.is_flat === false && ' — the stable plateau is NOT flat (stable: false), so no onset is claimed'}
      . Horizon {realised.horizon_days} calendar days. Log-basis α{' '}
      {realised.log_basis.alpha == null
        ? `refused (${realised.log_basis.refusal ?? 'no reason given'})`
        : fmtFixed(realised.log_basis.alpha, 2)}{' '}
      — {realised.log_basis.note}.
    </p>
  )
}

function SurfaceCaveat({ data }: { data: SurfaceResponse }) {
  const s = data.surface
  const spread = s.ladder
    .slice(0, MAX_RUNGS)
    .filter((r) => r.bid != null && r.ask != null)
    .map((r) => (r.ask as number) - (r.bid as number))
  const widest = spread.length ? Math.max(...spread) : null
  return (
    <details className="pl-surface-caveat">
      <summary>Read this before the number</summary>
      <p>
        <strong>A single implied alpha is not evidence of a power law.</strong> A genuine power law is
        anchor-invariant; α climbing with anchor depth is the signature of it failing. Dispersion across the three
        anchors is {s.anchors.dispersion == null ? 'unavailable' : s.anchors.dispersion.toFixed(3)}.
      </p>
      <p>
        Anchor-relative: every price on the ladder is relative to one quoted put, picked by fixed moneyness (
        {s.moneyness_pct}% OOM). {s.parameterisation}.
      </p>
      <p>
        σ√t guard: {s.lambda_guard_ok == null ? 'unknown (no anchor IV)' : s.lambda_guard_ok ? 'passes' : 'FAILS'}
        {s.anchor_iv != null && ` at anchor IV ${(s.anchor_iv * 100).toFixed(1)}%`}, {s.t_days} days to expiry.
        r = {(s.r * 100).toFixed(1)}%, q = {(s.q * 100).toFixed(1)}%. {s.rate_note}.
      </p>
      <p>
        Error bar: the widest bid–ask spread on the ladder is {widest == null ? 'unavailable' : `$${widest.toFixed(2)}`}.
        A rung whose IV ratio sits within that spread is not distinguishable from 1.
      </p>
      <RealisedCaveat realised={s.realised} />
    </details>
  )
}

export function SurfaceView({
  surface,
  controls,
}: {
  surface: ResourceState<SurfaceResponse>
  controls: PutLabControls
}) {
  if (surface.status === 'loading') {
    return (
      <p className="pl-status" role="status" aria-live="polite">
        Reading the Surface for {controls.asset.toUpperCase()}…
      </p>
    )
  }
  if (surface.status === 'no-data') {
    return (
      <p className="pl-status" role="status">
        No option chain is known for {controls.asset.toUpperCase()} yet, so there is no Surface to read.
      </p>
    )
  }
  if (surface.status === 'error') {
    return (
      <p className="pl-status pl-status-error" role="alert">
        Could not read the Surface: {surface.message}
      </p>
    )
  }
  const data = surface.data
  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>Surface</h2>
      <p className="pl-lede" style={{ marginBottom: 16 }}>
        {data.asset.toUpperCase()}, {data.surface.t_days} days to {data.surface.expiration}, anchored at{' '}
        {data.surface.moneyness_pct}% out of the money (the rail's OOM control).
      </p>
      <AlphaStrip surface={data.surface} />
      <div className="pl-surface-panes">
        <ZipfPlot surface={data.surface} />
        <TailLadder surface={data.surface} />
      </div>
      <SurfaceCaveat data={data} />
      <p className="pl-micro">
        Chain snapshot {data.chain_snapshot ?? 'none'} · prices snapshot {data.ohlcv_snapshot ?? 'none'} · code{' '}
        {data.code_sha} · as of {data.as_of}
      </p>
    </div>
  )
}
