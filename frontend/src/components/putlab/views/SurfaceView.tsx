import { useState, type ReactNode } from 'react'
import type {
  SurfaceAnchorReading,
  SurfaceReading,
  SurfaceRealised,
  SurfaceResponse,
  SurfaceRung,
} from '../../../api/client'
import { dots, logTicks, poly, tickText, type Label, type Pt } from '../chart'
import { ChartLabels } from '../ChartLabels'
import { fmtFixed } from '../format'
import type { ResourceState } from '../PutLab'
import type { PutLabControls } from '../types'

/* The Surface: the market's implied tail index beside the realised one.
 *
 * No control of its own -- the anchor IS the rail's strike control, which is
 * what makes anchor-relativity physical. Every fit that can refuse renders the
 * word REFUSED with its reason: a blank, a dash or a zero would read as a
 * measurement (docs/adr/0026 §7). The caveat ships in the same view as the
 * number.
 *
 * Every chart is scaled from the payload, never from a fixed range: an alpha
 * of 1.6 or a $40 put must land on the plot, not off it.
 */

/** Rungs drawn: the Surface is one screen, so the ladder is capped. */
const MAX_RUNGS = 8

const refused = (reason: string | null | undefined) => (
  <span className="pl-refused" title={reason ?? undefined}>
    REFUSED{reason ? ` — ${reason}` : ''}
  </span>
)

const finitePositive = (x: number | null | undefined): x is number =>
  x != null && Number.isFinite(x) && x > 0

/** The anchor's distance below spot, as the rail states it. */
const oomOf = (strike: number, spot: number) => Math.round((1 - strike / spot) * 100)

/** The reason an anchor has no implied alpha, from the deepest place it says. */
const anchorWhy = (a: SurfaceAnchorReading | undefined, fallback: string) =>
  a?.fit?.refusal ?? a?.refusal ?? fallback

// ------------------------------------------------------------ alpha figures --

function Figure({ k, children, note }: { k: string; children: ReactNode; note: string }) {
  return (
    <div className="pl-fig" data-testid="alpha-figure">
      <div className="pl-fig-k">{k}</div>
      <div className="pl-fig-v">{children}</div>
      <div className="pl-fig-note">{note}</div>
    </div>
  )
}

function gapNote(gap: number): string {
  if (gap > 0) return 'Implied minus realised: the market prices a thinner tail than history shows'
  if (gap < 0) return 'Implied minus realised: the market prices a fatter tail than history shows'
  return 'Implied minus realised: the market prices the tail history shows'
}

function AlphaFigures({ s }: { s: SurfaceReading }) {
  const first = s.anchors.readings[0]
  const implied = first?.fit?.alpha ?? null
  const realised = s.realised
  const n = s.anchors.readings.length
  return (
    <div className="pl-figs" role="group" aria-label="Alpha figures">
      <Figure
        k="Implied α"
        note={
          first
            ? `At the anchor, K ${first.strike.toFixed(0)} · ${oomOf(first.strike, s.spot)}% OOM; the gap and the ladder use it`
            : 'No anchor strike below spot on this expiry'
        }
      >
        {implied == null ? refused(anchorWhy(first, 'no anchor strike below spot')) : fmtFixed(implied, 2)}
      </Figure>
      <Figure k="Dispersion" note={`Across the ${n} anchors; near zero is what a power law shows`}>
        {s.anchors.dispersion == null ? refused(s.anchors.dispersion_reason) : s.anchors.dispersion.toFixed(3)}
      </Figure>
      <Figure
        k="Realised α"
        note={realised ? `From ${realised.horizon_days}-day losses past the Karamata onset` : 'No price history read'}
      >
        {realised == null
          ? refused(s.realised_reason)
          : realised.alpha == null
            ? refused(realised.refusal)
            : fmtFixed(realised.alpha, 2)}
      </Figure>
      <Figure k="Gap" note={s.alpha_gap == null ? 'Implied minus realised' : gapNote(s.alpha_gap)}>
        {s.alpha_gap == null ? refused(s.alpha_gap_reason) : s.alpha_gap.toFixed(2)}
      </Figure>
    </div>
  )
}

// -------------------------------------------------------------- alpha chart --

function AlphaChart({ s, narrow }: { s: SurfaceReading; narrow: boolean }) {
  const rows = s.anchors.readings
  if (rows.length === 0) return null
  const realised = s.realised?.alpha ?? null
  const implied = rows[0]?.fit?.alpha ?? null
  const values = [
    ...rows.flatMap((r) => [r.fit?.alpha, r.ceiling?.alpha]),
    realised,
  ].filter((v): v is number => v != null && Number.isFinite(v))
  // At least 2.0 to 4.5, widened to the half-unit that holds every value.
  const lo = Math.floor(Math.min(2, ...values) * 2) / 2
  const hi = Math.ceil(Math.max(4.5, ...values) * 2) / 2

  const W = narrow ? 400 : 860
  const X0 = narrow ? 10 : 150
  const XW = narrow ? 380 : 560
  const rowY = (i: number) => (narrow ? 46 + 48 * i : 24 + 36 * i)
  const top = narrow ? 24 : 6
  const bottom = rowY(rows.length - 1) + (narrow ? 10 : 16)
  const axisY = bottom + (narrow ? 20 : 22)
  const H = axisY + (narrow ? 28 : 31)
  const AX = (a: number) => X0 + ((a - lo) / (hi - lo)) * XW

  const tickStep = narrow ? 1 : 0.5
  const ticks: number[] = []
  for (let a = Math.ceil(lo / tickStep) * tickStep; a <= hi + 1e-9; a += tickStep) ticks.push(a)

  const labels: Label[] = []
  const links: string[] = []
  const filled: Pt[] = []
  const open: Pt[] = []
  rows.forEach((r, i) => {
    const y = rowY(i)
    const a = r.fit?.alpha ?? null
    const c = r.ceiling?.alpha ?? null
    const name = `K ${r.strike.toFixed(0)} · ${oomOf(r.strike, s.spot)}% OOM`
    if (a != null) filled.push([AX(a), y])
    if (c != null) open.push([AX(c), y])
    if (a != null && c != null) links.push(`M${AX(Math.min(a, c))},${y}H${AX(Math.max(a, c))}`)
    const aText = a == null ? `α REFUSED — ${anchorWhy(r, 'no fit')}` : `α ${a.toFixed(2)}`
    const cText =
      c == null ? `ceiling REFUSED — ${r.ceiling?.refusal ?? r.ceiling_reason ?? 'no ceiling'}` : `ceiling ${c.toFixed(2)}`
    if (narrow) {
      labels.push({ x: X0, y: y - 17, text: name, align: 'start', strong: true })
      labels.push({ x: X0 + XW, y: y - 17, text: `${aText} · ${cText}`, align: 'end' })
    } else {
      labels.push({ x: 0, y, text: name, align: 'start', strong: true })
      const right = Math.max(...[a, c].filter((v): v is number => v != null).map(AX), X0)
      labels.push({ x: right + 12, y, text: `${aText} · ${cText}`, align: 'start', strong: true })
    }
  })

  let band = ''
  if (realised != null && implied != null) {
    const [x1, x2] = [AX(Math.min(realised, implied)), AX(Math.max(realised, implied))]
    band = `M${x1},${top + 2}H${x2}V${bottom}H${x1}Z`
    if (s.alpha_gap != null) {
      labels.push({ x: (x1 + x2) / 2, y: bottom + (narrow ? 10 : 9), text: `gap ${s.alpha_gap.toFixed(2)}` })
    }
  }
  if (realised != null) {
    labels.push(
      narrow
        ? { x: AX(realised) + 6, y: 12, text: `realised ${realised.toFixed(2)}`, align: 'start', strong: true }
        : { x: AX(realised) - 6, y: 6, text: `realised ${realised.toFixed(2)}`, align: 'end', strong: true },
    )
  }
  for (const t of ticks) labels.push({ x: AX(t), y: axisY + 18, text: `α ${t.toFixed(1)}` })

  return (
    <div className="pl-plot" style={{ maxWidth: 900 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Implied alpha at ${rows.length} anchors beside the realised alpha`}
        data-testid="alpha-chart"
      >
        {band && <path d={band} className="pl-c-band" />}
        <path
          d={`M${X0},${axisY}H${X0 + XW}` + ticks.map((t) => `M${AX(t)},${axisY - 4}V${axisY + 4}`).join('')}
          className="pl-c-axis"
        />
        <path d={links.join('')} className="pl-c-link" />
        {realised != null && <path d={`M${AX(realised)},${top}V${bottom}`} className="pl-c-realised" />}
        <path d={dots(open, 4.5)} className="pl-c-ceiling" data-testid="alpha-ceilings" />
        <path d={dots(filled, 5.5)} className="pl-c-ink" data-testid="alpha-dots" />
      </svg>
      <ChartLabels labels={labels} w={W} h={H} />
    </div>
  )
}

// ------------------------------------------------------------- ladder chart --

const LW = 560
const LH = 280
const PX0 = 52
const PX1 = 544
const PY0 = 14
const PY1 = 240

/** x for each rung by strike, descending left to right (deeper is right), with
 *  half a step of air at each end; and the column each rung owns for hover. */
function strikeAxis(rungs: SurfaceRung[]) {
  const ks = rungs.map((r) => r.strike)
  const kmax = Math.max(...ks)
  const kmin = Math.min(...ks)
  const pad = rungs.length > 1 ? (kmax - kmin) / (2 * (rungs.length - 1)) : 1
  const X = (k: number) => PX0 + ((kmax + pad - k) / (kmax - kmin + 2 * pad)) * (PX1 - PX0)
  const xs = ks.map(X)
  const cols = xs.map((x, i) => {
    const left = i === 0 ? PX0 : (xs[i - 1]! + x) / 2
    const right = i === xs.length - 1 ? PX1 : (x + xs[i + 1]!) / 2
    return { x: left, w: right - left }
  })
  return { X, xs, cols }
}

/** A rung whose Paretan price sits inside the market's own bid-ask cannot be
 *  told apart from the market: its IV ratio is not distinguishable from 1. */
const insideSpread = (r: SurfaceRung) =>
  r.bid != null && r.ask != null && r.paretan_price >= r.bid && r.paretan_price <= r.ask

const money = (x: number | null) => (x == null ? 'none' : x.toFixed(2))

function LadderChart({ rungs }: { rungs: SurfaceRung[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const { X, xs, cols } = strikeAxis(rungs)
  const prices = rungs
    .flatMap((r) => [r.bid, r.ask, r.paretan_price, r.black_scholes_price])
    .filter(finitePositive)
  const lo = Math.min(...prices) * 0.9
  const hi = Math.max(...prices) * 1.1
  const PY = (p: number) => PY0 + (1 - (Math.log(p) - Math.log(lo)) / (Math.log(hi) - Math.log(lo))) * (PY1 - PY0)
  const yt = logTicks(lo, hi, 5)

  const spreads = rungs
    .filter((r) => finitePositive(r.bid) && finitePositive(r.ask))
    .map((r) => `M${X(r.strike)},${PY(r.bid as number).toFixed(1)}V${PY(r.ask as number).toFixed(1)}`)
    .join('')
  const mids = rungs
    .filter((r) => finitePositive(r.market_price))
    .map((r) => {
      const y = PY(r.market_price as number)
      return `M${X(r.strike) - 6},${y.toFixed(1)}H${X(r.strike) + 6}`
    })
    .join('')
  const par: Pt[] = rungs.filter((r) => finitePositive(r.paretan_price)).map((r) => [X(r.strike), PY(r.paretan_price)])
  // A rung with no BS price breaks the dashed line rather than bridging it.
  const bs = rungs
    .map((r) => (finitePositive(r.black_scholes_price) ? ([X(r.strike), PY(r.black_scholes_price)] as Pt) : null))
    .reduce<Pt[][]>((runs, p) => {
      if (p == null) runs.push([])
      else runs[runs.length - 1]!.push(p)
      return runs
    }, [[]])
    .filter((run) => run.length > 1)
    .map(poly)
    .join('')

  const labels: Label[] = [
    ...yt.map((p) => ({ x: PX0 - 8, y: PY(p), text: `$${tickText(p)}`, align: 'end' as const })),
    ...rungs.map((r, i) => ({ x: xs[i]!, y: 256, text: r.strike.toFixed(0) })),
    { x: (PX0 + PX1) / 2, y: 274, text: 'strike → deeper' },
  ]
  const shown = hover ?? 0
  const r = rungs[shown]!

  return (
    <>
      <div className="pl-plot">
        <svg
          viewBox={`0 0 ${LW} ${LH}`}
          role="img"
          aria-label="Tail ladder: put price by strike, Paretan and Black-Scholes against the market's bid-ask"
          onPointerLeave={() => setHover(null)}
        >
          <path d={yt.map((p) => `M${PX0},${PY(p).toFixed(1)}H${PX1}`).join('') + `M${PX0},${PY1}H${PX1}`} className="pl-c-grid" />
          {hover != null && (
            <rect x={cols[hover]!.x} y={PY0} width={cols[hover]!.w} height={PY1 - PY0} className="pl-c-hi" />
          )}
          <path d={spreads} className="pl-c-spread" data-testid="ladder-spreads" />
          <path d={mids} className="pl-c-mid" />
          <path d={bs} className="pl-c-bs" />
          <path d={poly(par)} className="pl-c-line" />
          <path d={dots(par, 2.5)} className="pl-c-ink" />
          {cols.map((c, i) => (
            <rect
              key={rungs[i]!.strike}
              x={c.x}
              y={PY0}
              width={c.w}
              height={PY1 - PY0}
              className="pl-c-hit"
              data-testid="ladder-rung"
              onPointerEnter={() => setHover(i)}
              onClick={() => setHover(i)}
            />
          ))}
        </svg>
        <ChartLabels labels={labels} w={LW} h={LH} />
      </div>
      <div className="pl-readout" data-testid="ladder-readout" aria-live="polite">
        <span>
          <span className="pl-readout-k">Strike</span> <strong>{r.strike.toFixed(0)}</strong>
        </span>
        <span>
          <span className="pl-readout-k">Bid–ask</span>{' '}
          <strong>{r.bid == null || r.ask == null ? 'no quote' : `${r.bid.toFixed(2)}–${r.ask.toFixed(2)}`}</strong>
        </span>
        <span>
          <span className="pl-readout-k">Paretan</span> <strong>{r.paretan_price.toFixed(2)}</strong>
        </span>
        <span>
          <span className="pl-readout-k">BS</span> <strong>{money(r.black_scholes_price)}</strong>
        </span>
        <span>
          <span className="pl-readout-k">IV ratio</span>{' '}
          <strong>{r.iv_ratio == null ? 'no IV' : r.iv_ratio.toFixed(3)}</strong>
        </span>
      </div>
    </>
  )
}

const IW = 560
const IH = 110

function IvRatioStrip({ rungs }: { rungs: SurfaceRung[] }) {
  const { X } = strikeAxis(rungs)
  const vals = rungs.map((r) => r.iv_ratio).filter((v): v is number => v != null && Number.isFinite(v))
  const span = Math.max(0.01, ...vals.map((v) => Math.abs(v - 1))) * 1.25
  const [lo, hi] = [1 - span, 1 + span]
  const IY = (v: number) => 10 + (1 - (v - lo) / (hi - lo)) * 80
  const withIv = rungs.filter((r) => r.iv_ratio != null && Number.isFinite(r.iv_ratio))
  const inside = withIv.filter(insideSpread)
  const outside = withIv.filter((r) => !insideSpread(r))
  const spreads = rungs
    .filter((r) => r.bid != null && r.ask != null)
    .map((r) => (r.ask as number) - (r.bid as number))
  const widest = spreads.length ? Math.max(...spreads) : null
  const labels: Label[] = [
    { x: PX0 - 8, y: IY(1), text: '1.00', align: 'end' },
    { x: PX0 - 8, y: IY(hi), text: hi.toFixed(3), align: 'end' },
    { x: PX0 - 8, y: IY(lo), text: lo.toFixed(3), align: 'end' },
    ...outside.map((r) => ({ x: X(r.strike), y: IY(r.iv_ratio as number) - 12, text: (r.iv_ratio as number).toFixed(3), strong: true })),
    ...rungs.filter((r) => r.iv_ratio == null).map((r) => ({ x: X(r.strike), y: IY(1) - 12, text: 'no IV' })),
  ]
  const pt = (r: SurfaceRung): Pt => [X(r.strike), IY(r.iv_ratio as number)]
  return (
    <div>
      <div className="pl-kicker" style={{ margin: '10px 0 5px' }}>
        IV ratio, Paretan over market
      </div>
      <div className="pl-plot">
        <svg viewBox={`0 0 ${IW} ${IH}`} role="img" aria-label="Implied-vol ratio, Paretan over market, at each rung">
          <path d={`M${PX0},${IY(1)}H${PX1}`} className="pl-c-one" />
          <path d={withIv.map((r) => `M${X(r.strike)},${IY(1)}V${IY(r.iv_ratio as number).toFixed(1)}`).join('')} className="pl-c-link" />
          <path d={dots(inside.map(pt), 4)} className="pl-c-ceiling" data-testid="iv-inside" />
          <path d={dots(outside.map(pt), 4.5)} className="pl-c-ink" data-testid="iv-outside" />
        </svg>
        <ChartLabels labels={labels} w={IW} h={IH} />
      </div>
      <p className="pl-chart-caption">
        An open circle is a rung whose Paretan price falls inside the market&rsquo;s own bid&ndash;ask: its ratio
        is not distinguishable from 1. A filled dot falls outside it.{' '}
        {widest == null ? 'No rung has a two-sided quote.' : `The widest spread on the ladder is $${widest.toFixed(2)}.`}
      </p>
    </div>
  )
}

function TailLadder({ s }: { s: SurfaceReading }) {
  const rungs: SurfaceRung[] = s.ladder.slice(0, MAX_RUNGS)
  return (
    <section aria-label="Tail ladder" className="pl-surface-pane">
      <div>
        <h3>Tail ladder</h3>
        <p className="pl-chart-sub">Put price by strike, deeper than the anchor. Hover or tap a rung.</p>
      </div>
      {rungs.length === 0 ? (
        <p className="pl-status">{refused(s.ladder_reason)}</p>
      ) : (
        <>
          <div className="pl-legend">
            <span><i className="pl-key pl-key-line" />Paretan</span>
            <span><i className="pl-key pl-key-dash" />Black&ndash;Scholes</span>
            <span><i className="pl-key pl-key-spread" />Market bid&ndash;ask</span>
          </div>
          <LadderChart rungs={rungs} />
          <IvRatioStrip rungs={rungs} />
        </>
      )}
    </section>
  )
}

// ---------------------------------------------------------- survival chart --

/** Survival of the loss curve at ``x``, read off the stepped empirical curve. */
function survivalAt(points: [number, number][], x: number): number | null {
  const sorted = [...points].sort((a, b) => a[0] - b[0])
  let y: number | null = null
  for (const [px, py] of sorted) {
    if (px > x) break
    y = py
  }
  return y
}

function SurvivalChart({ s }: { s: SurfaceReading }) {
  const realised = s.realised
  const gross = (realised?.survival_gross?.points ?? []).filter(([x, y]) => finitePositive(x) && finitePositive(y))
  const loss = (realised?.survival_loss?.points ?? []).filter(([x, y]) => finitePositive(x) && finitePositive(y))
  const all = [...gross, ...loss]
  if (realised == null || all.length < 2) {
    return (
      <p className="pl-status">
        No price history read for this name, so there is no survival curve
        {realised == null && s.realised_reason ? ` (${s.realised_reason})` : ''}.
      </p>
    )
  }
  const lx = all.map(([x]) => Math.log10(x))
  const ly = all.map(([, y]) => Math.log10(y))
  const [x0, x1] = [Math.min(...lx), Math.max(...lx, Math.min(...lx) + 1e-9)]
  const [y0, y1] = [Math.min(...ly), Math.max(0, ...ly)]
  const ZX = (x: number) => PX0 + ((Math.log10(x) - x0) / (x1 - x0)) * (PX1 - PX0)
  const ZY = (y: number) => PY0 + ((y1 - Math.log10(y)) / (y1 - y0 || 1)) * (PY1 - PY0)
  const curve = (pts: [number, number][]) => poly(pts.map(([x, y]) => [ZX(x), ZY(y)]))
  const xt = logTicks(10 ** x0, 10 ** x1, 6)
  const yt = logTicks(10 ** y0, 10 ** y1, 5)

  const onset = realised.onset
  const onsetX = finitePositive(onset) && onset >= 10 ** x0 && onset <= 10 ** x1 ? ZX(onset) : null
  // The fitted tail: slope -alpha on both log axes, from the loss curve at the
  // onset out to the largest loss. Only drawn when both were measured.
  let fit = ''
  const labels: Label[] = [
    ...yt.map((y) => ({ x: PX0 - 8, y: ZY(y), text: tickText(y), align: 'end' as const })),
    ...xt.map((x) => ({ x: ZX(x), y: 256, text: `${tickText(x * 100)}%` })),
    { x: (PX0 + PX1) / 2, y: 274, text: 'move size x' },
  ]
  if (onsetX != null && realised.alpha != null && loss.length) {
    const y0s = survivalAt(loss, onset as number)
    const xEnd = Math.max(...loss.map(([x]) => x))
    if (y0s != null && y0s > 0 && xEnd > (onset as number)) {
      const yEnd = y0s * (xEnd / (onset as number)) ** -realised.alpha
      const yClamped = Math.max(yEnd, 10 ** y0)
      const xClamped = (onset as number) * (yClamped / y0s) ** (-1 / realised.alpha)
      fit = `M${ZX(onset as number).toFixed(1)},${ZY(y0s).toFixed(1)}L${ZX(xClamped).toFixed(1)},${ZY(yClamped).toFixed(1)}`
      const mx = Math.sqrt((onset as number) * xClamped)
      labels.push({
        x: ZX(mx) + 6,
        y: ZY(y0s * (mx / (onset as number)) ** -realised.alpha) - 16,
        text: `slope −${realised.alpha.toFixed(2)}`,
        align: 'start',
        strong: true,
      })
    }
  }
  if (onsetX != null) labels.push({ x: onsetX + 6, y: 24, text: `onset ${tickText((onset as number) * 100)}%`, align: 'start' })

  return (
    <>
      <div className="pl-legend">
        <span><i className="pl-key pl-key-line" />S, gross move</span>
        <span><i className="pl-key pl-key-loss" />r, arithmetic loss</span>
        <span><i className="pl-key pl-key-onset" />Karamata onset</span>
      </div>
      <div className="pl-plot">
        <svg viewBox={`0 0 ${LW} ${LH}`} role="img" aria-label="Survival curves, gross move S and arithmetic loss r">
          <path d={yt.map((y) => `M${PX0},${ZY(y).toFixed(1)}H${PX1}`).join('') + xt.map((x) => `M${ZX(x).toFixed(1)},${PY1 - 4}V${PY1 + 4}`).join('')} className="pl-c-grid" />
          {fit && <path d={fit} className="pl-c-fit" data-testid="tail-fit" />}
          {onsetX != null && <path d={`M${onsetX.toFixed(1)},${PY0}V${PY1}`} className="pl-c-onset" />}
          {gross.length > 1 && <path d={curve(gross)} className="pl-c-line" />}
          {loss.length > 1 && <path d={curve(loss)} className="pl-c-loss" />}
        </svg>
        <ChartLabels labels={labels} w={LW} h={LH} />
      </div>
      <RealisedCaveat realised={realised} />
    </>
  )
}

function RealisedCaveat({ realised }: { realised: SurfaceRealised }) {
  return (
    <p className="pl-chart-caption">
      {realised.n_beyond} observations beyond the onset
      {realised.onset != null && ` at ${tickText(realised.onset * 100)}%`}
      {realised.is_flat === false && ' — the stable plateau is NOT flat (stable: false), so no onset is claimed'}.
      Horizon {realised.horizon_days} calendar days. Log-basis α{' '}
      {realised.log_basis.alpha == null
        ? `refused (${realised.log_basis.refusal ?? 'no reason given'})`
        : fmtFixed(realised.log_basis.alpha, 2)}{' '}
      — {realised.log_basis.note}.
    </p>
  )
}

// ------------------------------------------------------------- disclosures --

function LadderTable({ rungs }: { rungs: SurfaceRung[] }) {
  return (
    <div className="pl-scroll">
      <table className="pl-table pl-ladder-table" aria-label="Tail ladder rungs">
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
              <td className="num bold">{r.iv_ratio == null ? 'no IV' : fmtFixed(r.iv_ratio, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SurfaceCaveat({ s }: { s: SurfaceReading }) {
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
        A rung whose Paretan price sits inside its own spread is not distinguishable from the market.
      </p>
    </details>
  )
}

// -------------------------------------------------------------------- view --

export function SurfaceView({
  surface,
  controls,
  narrow,
}: {
  surface: ResourceState<SurfaceResponse>
  controls: PutLabControls
  narrow: boolean
}) {
  const head = (
    <div>
      <h2 className="pl-view-title">Surface</h2>
      <p className="pl-view-lede">
        The market&rsquo;s implied tail index beside the realised one, read from one option-chain session.
      </p>
      {surface.status === 'ready' && (
        <p className="pl-view-note">
          {surface.data.asset.toUpperCase()}, {surface.data.surface.t_days} days to {surface.data.surface.expiration}{' '}
          · anchored at {surface.data.surface.moneyness_pct}% out of the money (the rail&rsquo;s strike control)
        </p>
      )}
    </div>
  )
  if (surface.status === 'loading') {
    return (
      <div className="pl-surface">
        {head}
        <p className="pl-status" role="status" aria-live="polite">
          Reading the Surface for {controls.asset.toUpperCase()}…
        </p>
      </div>
    )
  }
  if (surface.status === 'no-data') {
    return (
      <div className="pl-surface">
        {head}
        <p className="pl-status" role="status">
          No option chain is known for {controls.asset.toUpperCase()} yet, so there is no Surface to read.
        </p>
      </div>
    )
  }
  if (surface.status === 'error') {
    return (
      <div className="pl-surface">
        {head}
        <p className="pl-status pl-status-error" role="alert">
          Could not read the Surface: {surface.message}
        </p>
      </div>
    )
  }
  const data = surface.data
  const s = data.surface
  return (
    <div className="pl-surface">
      {head}
      <section aria-label="Tail index" className="pl-surface-block">
        <AlphaFigures s={s} />
        <AlphaChart s={s} narrow={narrow} />
        <p className="pl-chart-caption">
          Lower α is a fatter tail. Filled dots are the implied α fitted at each anchor strike; open circles are that
          fit&rsquo;s ceiling. A genuine power law holds α constant across anchors.
        </p>
      </section>
      <div className="pl-surface-panes">
        <TailLadder s={s} />
        <section aria-label="Realised tail" className="pl-surface-pane">
          <div>
            <h3>Realised tail</h3>
            <p className="pl-chart-sub">
              Share of {s.realised?.horizon_days ?? s.t_days}-day moves larger than x, both axes log₁₀. A straight
              line past the onset is a power law.
            </p>
          </div>
          <SurvivalChart s={s} />
        </section>
      </div>
      <div className="pl-surface-disclosures">
        {s.ladder.length > 0 && (
          <details>
            <summary>The ladder as numbers</summary>
            <LadderTable rungs={s.ladder.slice(0, MAX_RUNGS)} />
          </details>
        )}
        <SurfaceCaveat s={s} />
      </div>
      <p className="pl-micro">
        Chain snapshot {data.chain_snapshot ?? 'none'} · prices snapshot {data.ohlcv_snapshot ?? 'none'} · code{' '}
        {data.code_sha} · as of {data.as_of}
      </p>
    </div>
  )
}
