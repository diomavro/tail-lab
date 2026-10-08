import { useState, type KeyboardEvent, type PointerEvent } from 'react'
import type { HedgeOverlayResponse, OverlayOutcome, OverlayWindow, ProgramOverlay } from '../../../api/client'
import { dots, poly, type Label, type Pt } from '../chart'
import { ChartLabels } from '../ChartLabels'
import { fmtFixed } from '../format'
import { inWords, type LumpMetric } from './planFormat'

/* The Book, lump sum: one panel per program and window, the growth (or CAGR
 * per unit of vol) of every hedge ratio from 0% to 100% hedged. The verdict is
 * read off the payload's own outcome fields, never re-derived here: the server
 * applies the 1bp threshold and the tie rules (docs/adr/0027). */

const pct = (x: number, digits = 1) => `${fmtFixed(x * 100, digits)}%`
/** A margin in pp/yr at the precision the 1bp threshold needs. */
const pp = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(3)}pp/yr`
const hedged = (w: number) => `${Math.round(w * 100)}% hedged`

/** CAGR per unit of vol is the letter's yardstick, not the paradox's (which
 *  the page defines on growth), so its tags never say "Paradox". */
const RATIO_TAG: Record<OverlayOutcome, { cls: string; text: string }> = {
  holds: { cls: 'pl-tag pl-tag-ok', text: 'Interior mix wins' },
  inconclusive: { cls: 'pl-tag pl-tag-mute', text: 'Too close to call' },
  fails: { cls: 'pl-tag pl-tag-bad', text: 'An end wins' },
}

const TAG: Record<OverlayOutcome, { cls: string; text: string }> = {
  holds: { cls: 'pl-tag pl-tag-ok', text: 'Paradox holds' },
  inconclusive: { cls: 'pl-tag pl-tag-mute', text: 'Too close to call' },
  fails: { cls: 'pl-tag pl-tag-bad', text: 'Paradox fails' },
}

/** The growth verdict in words. The margin's sign decides the verb, so a mix
 *  that beats the ends by a sliver is never described as trailing them, and
 *  the reverse. */
function growthSentence(w: OverlayWindow): string {
  const size = `${Math.abs(w.margin * 100).toFixed(3)}pp/yr`
  if (w.outcome === 'holds') return `${hedged(w.best_weight)} beats both ends by ${size}.`
  if (w.outcome === 'fails') return `No interior mix beats the better end; the best one trails it by ${size}.`
  // At or below zero the better end is best (ties go to an end), so the
  // interior mix is "within" it -- never "beating" or, at exactly 0, "trailing".
  return w.margin > 0
    ? `${hedged(w.best_weight)} beats both ends, but only by ${size}, under the 1bp/yr threshold.`
    : `No interior mix beats the better end, but the best one is within ${size} of it, under the 1bp/yr threshold.`
}

const RA_WHERE: Record<OverlayOutcome, string> = {
  holds: 'an interior mix wins',
  inconclusive: 'too close to call',
  fails: 'an end wins',
}

function ratioSentence(w: OverlayWindow): string {
  if (w.best_weight_risk_adjusted == null || w.outcome_risk_adjusted == null) {
    return 'CAGR per unit of vol is undefined here (a mix has zero volatility).'
  }
  return (
    `Best CAGR per unit of vol at ${hedged(w.best_weight_risk_adjusted)} (${RA_WHERE[w.outcome_risk_adjusted]}). ` +
    'The weaker test: it favours any mix that lowers volatility.'
  )
}

const inWindow = (label: string) => `${label.charAt(0).toLowerCase()}${label.slice(1)}`
/** Where a test ran: the window's name, or the span it actually covered when
 *  the data cut it short -- never a shorter test wearing the letter's label
 *  (hedge_overlay.run_hedge_overlay). */
const ranIn = (w: OverlayWindow) =>
  w.clipped
    ? `in ${inWindow(w.label.replace(/\s*\([^)]*\)$/, ''))}, shortened to ${w.start} to ${w.end}`
    : `in ${inWindow(w.label)}`
const WEAKER = 'This is the weaker test: it rewards any mix that lowers volatility.'

/** The page's one-line answer, counted from the outcomes on screen. */
function lumpHeadline(programs: ProgramOverlay[], metric: LumpMetric): string {
  const all = programs.flatMap((p) => p.windows.map((w) => ({ p, w })))
  const n = all.length
  if (n === 0) return 'No program and window could be tested.'
  const tests = `${inWords(n)} ${n === 1 ? 'test' : 'tests'}`
  if (n === 1) {
    const { w } = all[0]!
    if (metric === 'ratio') {
      if (w.outcome_risk_adjusted == null) {
        return 'On CAGR per unit of vol, the one test is undefined here (a mix has zero volatility).'
      }
      return `On CAGR per unit of vol, the one test ${w.outcome_risk_adjusted === 'holds' ? 'favours' : 'does not favour'} an interior mix. ${WEAKER}`
    }
    return w.outcome === 'holds'
      ? `On growth, the one test holds: ${hedged(w.best_weight)}, by ${Math.abs(w.margin * 100).toFixed(3)}pp/yr.`
      : 'On growth, the one test does not hold: it fails or is too close to call.'
  }
  if (metric === 'ratio') {
    const k = all.filter(({ w }) => w.outcome_risk_adjusted === 'holds').length
    const u = all.filter(({ w }) => w.outcome_risk_adjusted == null).length
    const lead =
      k === n ? `every one of the ${tests} favours` : k === 0 ? `none of the ${tests} favours` : `${inWords(k)} of the ${tests} ${k === 1 ? 'favours' : 'favour'}`
    const undef = u === 0 ? '' : ` ${u === 1 ? 'One is' : `${inWords(u).replace(/^./, (c) => c.toUpperCase())} are`} undefined (a mix has zero volatility).`
    return `On CAGR per unit of vol, ${lead} an interior mix.${undef} ${WEAKER}`
  }
  const holds = all.filter(({ w }) => w.outcome === 'holds')
  if (holds.length === 0) return `On growth, none of the ${tests} holds: each fails or is too close to call.`
  const list = holds
    .map(({ p, w }) => `${p.index_symbol} ${ranIn(w)}, ${hedged(w.best_weight)}, by ${Math.abs(w.margin * 100).toFixed(3)}pp/yr`)
    .join('; ')
  if (holds.length === n) return `On growth, every one of the ${tests} holds: ${list}.`
  return `On growth, ${inWords(holds.length)} of ${tests} ${holds.length === 1 ? 'holds' : 'hold'}: ${list}. The rest fail or are too close to call.`
}

// ------------------------------------------------------------------ panel --

const PW = 300
const PH = 150
const X0 = 46
const X1 = 290
const Y0 = 12
const Y1 = 124

const SENS_COLOR: Record<OverlayOutcome, string> = { holds: 'is-holds', inconclusive: 'is-close', fails: 'is-fails' }

function Panel({ prog, w, metric }: { prog: ProgramOverlay; w: OverlayWindow; metric: LumpMetric }) {
  const [hover, setHover] = useState<number | null>(null)
  const growth = metric === 'growth'
  const pts = w.points
  const value = (i: number) => (growth ? pts[i]!.cagr : pts[i]!.cagr_per_vol)
  const vals = pts.map((_, i) => value(i)).filter((v): v is number => v != null && Number.isFinite(v))
  const bestWeight = growth ? w.best_weight : w.best_weight_risk_adjusted
  const outcome = growth ? w.outcome : w.outcome_risk_adjusted
  const bestIdx = bestWeight == null ? null : pts.findIndex((p) => Math.abs(p.weight - bestWeight) < 1e-9)
  const shown = hover ?? (bestIdx != null && bestIdx >= 0 ? bestIdx : 0)

  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const pad = (hi - lo) * 0.15 || 0.001
  const X = (wt: number) => X0 + wt * (X1 - X0)
  const Y = (v: number) => Y0 + (1 - (v - (lo - pad)) / (hi + pad - (lo - pad))) * (Y1 - Y0)
  const line: Pt[] = pts.flatMap((p, i) => {
    const v = value(i)
    return v == null ? [] : [[X(p.weight), Y(v)] as Pt]
  })
  // The better end: whichever of 0% and 100% hedged did better on this test.
  const ends = [value(0), value(pts.length - 1)].filter((v): v is number => v != null)
  const ref = ends.length ? Math.max(...ends) : null
  const ry = ref == null ? null : Y(ref)
  let area = ''
  if (ry != null && line.some(([, y]) => y < ry - 0.01)) {
    // The curve clipped to the better-end line, with the exact crossings
    // added: joining a point above the line straight to the next point's
    // clamp would shade a wedge where no mix out-grows the better end.
    const above: Pt[] = []
    line.forEach(([x, y], i) => {
      above.push([x, Math.min(y, ry)])
      const next = line[i + 1]
      if (next && (y - ry) * (next[1] - ry) < 0) {
        above.push([x + ((ry - y) / (next[1] - y)) * (next[0] - x), ry])
      }
    })
    // Only the span between the outermost crossings: clamped points on the
    // line itself enclose nothing and would stretch the shape past them.
    const first = above.findIndex(([, y]) => y < ry - 0.01)
    let last = above.length - 1
    while (last > first && above[last]![1] >= ry - 0.01) last--
    const span = above.slice(Math.max(0, first - 1), Math.min(above.length, last + 2))
    area = `${poly(span)}L${span[span.length - 1]![0].toFixed(1)},${ry.toFixed(1)}L${span[0]![0].toFixed(1)},${ry.toFixed(1)}Z`
  }
  const f = growth ? (v: number) => pct(v, 2) : (v: number) => v.toFixed(3)
  const labels: Label[] = [
    ...(vals.length ? [{ x: X0 - 6, y: Y(hi), text: f(hi), align: 'end' as const }, { x: X0 - 6, y: Y(lo), text: f(lo), align: 'end' as const }] : []),
    { x: X0, y: 140, text: '0%', align: 'start' },
    { x: (X0 + X1) / 2, y: 140, text: '50%' },
    { x: X1, y: 140, text: '100% hedged', align: 'end' },
  ]

  const nearest = (fx: number) => {
    let best = 0
    pts.forEach((p, i) => {
      if (Math.abs(X(p.weight) - fx) < Math.abs(X(pts[best]!.weight) - fx)) best = i
    })
    return best
  }
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    setHover(nearest(((e.clientX - r.left) / r.width) * PW))
  }
  // A keyboard reads the same readout: Left/Right step through the mixes.
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    setHover(Math.max(0, Math.min(pts.length - 1, shown + (e.key === 'ArrowRight' ? 1 : -1))))
  }

  const p = pts[shown]!
  const name = p.weight === 0 ? 'S&P 500 only' : p.weight === 1 ? `${prog.index_symbol} only` : hedged(p.weight)
  const tag = outcome ? (growth ? TAG : RATIO_TAG)[outcome] : { cls: 'pl-tag pl-tag-mute', text: 'Undefined' }
  const hoverPt = hover != null && value(hover) != null ? [X(pts[hover]!.weight), Y(value(hover) as number)] as Pt : null

  return (
    <section aria-label={`${prog.index_symbol} ${w.label}`} className="pl-panel">
      <div className="pl-panel-head">
        <div>
          <h4 className="pl-panel-sym">{prog.index_symbol}</h4>
          <span className="pl-panel-desc" data-testid="panel-desc">
            {prog.description}
          </span>
          <span className="pl-panel-span">
            {w.start} to {w.end}
          </span>
        </div>
        <span className={tag.cls} data-testid="panel-tag">
          {tag.text}
        </span>
      </div>
      {w.clipped && (
        <p className="pl-caveat pl-panel-note" data-testid="clipped">
          Shortened: the data cover {w.start} to {w.end}, not the {w.requested_start} to {w.requested_end} asked for
          ({prog.index_symbol} or the S&P 500 starts late or stops early, or the as-of date falls inside the window).
        </p>
      )}
      <div className="pl-plot">
        <svg
          viewBox={`0 0 ${PW} ${PH}`}
          role="img"
          tabIndex={0}
          aria-label={`${prog.index_symbol}, ${growth ? 'growth' : 'CAGR per unit of vol'} at every hedge ratio; arrow keys step through them`}
          className="pl-panel-svg"
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={() => setHover(null)}
          onKeyDown={onKey}
          onBlur={() => setHover(null)}
          data-testid="panel-chart"
        >
          {area && <path d={area} className="pl-c-area" data-testid="panel-area" />}
          <path d={`M${X0},${Y1}H${X1}`} className="pl-c-grid" />
          {ry != null && <path d={`M${X0},${ry.toFixed(1)}H${X1}`} className="pl-c-parity" />}
          {hover != null && <path d={`M${X(pts[hover]!.weight)},${Y0}V${Y1}`} className="pl-c-cursor" />}
          <path d={poly(line)} className="pl-c-line pl-c-thin" />
          <path d={dots(line, 2.2)} className="pl-c-ink" />
          {bestIdx != null && bestIdx >= 0 && value(bestIdx) != null && (
            <path d={dots([[X(pts[bestIdx]!.weight), Y(value(bestIdx) as number)]], 4.5)} className="pl-c-best" data-testid="panel-best" />
          )}
          {hoverPt && <path d={dots([hoverPt], 3.5)} className="pl-c-hover" />}
        </svg>
        <ChartLabels labels={labels} w={PW} h={PH} />
      </div>
      <div className="pl-panel-readout" data-testid="panel-readout">
        <strong>{name}</strong> · CAGR {pct(p.cagr, 2)} · vol {pct(p.volatility)} · max DD {pct(p.max_drawdown)} · CAGR/vol{' '}
        {p.cagr_per_vol == null ? 'n/a' : fmtFixed(p.cagr_per_vol, 3)}
      </div>
      <p className="pl-panel-verdict" data-testid="verdict">
        {growth ? growthSentence(w) : ratioSentence(w)}
      </p>
      {growth && (
        <div className="pl-panel-sens">
          <span className="dim" aria-hidden="true">At dividend yield</span>
          {w.sensitivity.map((s) => (
            <span
              key={s.dividend_yield}
              aria-hidden="true"
              title={`${TAG[s.outcome].text} · best ${s.best_weight === 0 || s.best_weight === 1 ? 'is an end' : hedged(s.best_weight)} · ${pp(s.margin)}`}
            >
              <i className={`pl-sens-dot ${SENS_COLOR[s.outcome]}`} />
              {pct(s.dividend_yield)}
            </span>
          ))}
          {/* The dots say it at a glance by colour; this says it in words, for
              anyone who cannot tell the colours apart or hover a title. */}
          <span className="pl-panel-sens-text" data-testid="sensitivity">
            Best interior mix vs the better end, by assumed dividend yield:{' '}
            {w.sensitivity
              .map(
                (s) =>
                  `at ${pct(s.dividend_yield)}, ${TAG[s.outcome].text.toLowerCase()} (best ${s.best_weight === 0 || s.best_weight === 1 ? 'is an end' : `mix ${hedged(s.best_weight)}`}, ${pp(s.margin)})`,
              )
              .join('; ')}
            .
          </span>
        </div>
      )}
      <details className="pl-panel-numbers">
        <summary>As numbers</summary>
        <div className="pl-scroll">
          <table className="pl-table">
            <thead>
              <tr>
                <th>Hedge ratio</th>
                <th className="num">Growth (CAGR)</th>
                <th className="num">Volatility</th>
                <th className="num">Max drawdown</th>
                <th className="num">CAGR / vol</th>
              </tr>
            </thead>
            <tbody>
              {pts.map((q) => (
                <tr key={q.weight} aria-current={q.weight === bestWeight ? 'true' : undefined}>
                  <td>
                    {q.weight === 0 ? 'S&P 500 only' : q.weight === 1 ? `${prog.index_symbol} only` : hedged(q.weight)}
                    {q.weight === bestWeight && (
                      <span className="pl-tag pl-tag-ok"> {growth ? 'best growth' : 'best CAGR / vol'}</span>
                    )}
                  </td>
                  <td className="num">{pct(q.cagr, 2)}</td>
                  <td className="num">{pct(q.volatility)}</td>
                  <td className="num">{pct(q.max_drawdown)}</td>
                  <td className="num">{q.cagr_per_vol == null ? 'n/a' : fmtFixed(q.cagr_per_vol, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

export function LumpBook({ data, metric }: { data: HedgeOverlayResponse; metric: LumpMetric }) {
  const programs = data.overlay.programs
  // One section per window, in the order the payload first lists each.
  const keys: { key: string; label: string }[] = []
  for (const p of programs) {
    for (const w of p.windows) if (!keys.some((k) => k.key === w.key)) keys.push({ key: w.key, label: w.label })
  }
  return (
    <div className="pl-lump">
      {Object.entries(data.overlay.missing).map(([symbol, reason]) => (
        <p key={symbol} className="pl-caveat" data-testid="missing">
          {symbol}: not tested ({reason}).
        </p>
      ))}
      <p className="pl-lump-headline" data-testid="lump-headline">
        {lumpHeadline(programs, metric)}
      </p>
      {keys.map(({ key, label }) => (
        <section key={key} className="pl-lump-window" aria-label={label}>
          <h3>{label}</h3>
          <div className="pl-panels">
            {programs.flatMap((p) => p.windows.filter((w) => w.key === key).map((w) => <Panel key={p.index_symbol} prog={p} w={w} metric={metric} />))}
          </div>
        </section>
      ))}
      {programs.flatMap((p) =>
        Object.entries(p.unavailable).map(([key, reason]) => (
          <p key={`${p.index_symbol}-${key}`} className="pl-caveat" data-testid="unavailable">
            {p.index_symbol}, {key === 'cole' ? "the letter's window" : 'full history'}: not computed ({reason}).
          </p>
        )),
      )}
      <div className="pl-keyrow pl-lump-legend">
        <span>
          <i className="pl-key pl-key-area" />
          {metric === 'growth' ? 'Where a mix out-grows the better end' : 'Where a mix beats the better end'}
        </span>
        <span><i className="pl-key pl-key-dash" />The better end</span>
        <span><i className="pl-key pl-key-ring" />Best mix (an end, when none beats it)</span>
        {metric === 'growth' && (
          <>
            <span><i className="pl-sens-dot is-holds" />holds</span>
            <span><i className="pl-sens-dot is-close" />too close</span>
            <span><i className="pl-sens-dot is-fails" />fails</span>
          </>
        )}
      </div>
      <p className="pl-micro">
        Cboe snapshot {data.cboe_snapshot ?? 'unknown'} · code {data.code_sha} · as of {data.overlay.as_of}
      </p>
    </div>
  )
}
