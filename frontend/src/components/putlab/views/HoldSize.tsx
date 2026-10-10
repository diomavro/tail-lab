import type { HedgeOverlayResponse, OverlayWindow, ProgramOverlay, SizingAnswer } from '../../../api/client'
import { dots, poly, type Label, type Pt } from '../chart'
import { ChartLabels } from '../ChartLabels'
import { ConceptInfo } from '../ConceptInfo'
import { bp, inWords, pct } from './planFormat'

/* "How much to hold": the Book's answer to the question its title asks, per
 * program and window, read off the payload's own SizingAnswer
 * (research/backtest/hedge_sizing) -- never re-derived here. Full-Kelly w* is
 * shown beside the recommended ratio with the reason for the haircut named
 * where the number appears ("accuracy is surfaced, not filed", README), and
 * "none" is a first-class answer, not an empty cell. */

const ratio = (x: number) => `${x < 0 ? '−' : ''}${Math.abs(Math.round(x * 1000) / 10)}%`
/** A change in hedge ratio, in percentage points, signed with a true minus. */
const ppChange = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)}pp`
/** A CAGR at the precision a sub-basis-point gain needs. */
const cagr3 = (x: number) => pct(x, 3)
const windowName = (w: OverlayWindow) => (w.key === 'cole' ? "letter's window" : 'full history')

/** The recommended ratio in words: a number, "none", or "withheld". */
function recommendedText(s: SizingAnswer): string {
  if (s.recommended_ratio == null) return 'withheld'
  if (s.recommended_ratio === 0) return 'none (0%)'
  return ratio(s.recommended_ratio)
}

/** w* at the cap of 1, by the backend's own test (SizingAnswer.at_cap,
 *  hedge_sizing._CAP) whatever size is held: half of it is a cap, never
 *  called half-Kelly, and the 3/4 line is not quoted. */
const isCapped = (s: SizingAnswer) => s.at_cap
/** The cap is actually held: w* is at the cap AND the gate recommends a size.
 *  A failed gate (0) or a withheld size holds nothing, so no cap is offered. */
const capHeld = (s: SizingAnswer) => s.at_cap && (s.recommended_ratio ?? 0) > 0

function breakEvenText(s: SizingAnswer): string {
  if (s.break_even_status === 'none') return 'none (no ratio out-grows no hedge)'
  if (s.break_even_status === 'beyond_1') return 'beyond 100% hedged'
  return ratio(s.break_even ?? 0)
}

// ------------------------------------------------------------------ chart --

const W = 300
const H = 150
const X0 = 46
const X1 = 290
const Y0 = 14
const Y1 = 120

function GrowthCurve({ prog, w }: { prog: ProgramOverlay; w: OverlayWindow }) {
  const s = w.sizing
  const vals = s.curve.map((c) => c.cagr)
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const pad = (hi - lo) * 0.15 || 0.001
  const X = (x: number) => X0 + x * (X1 - X0)
  const Y = (v: number) => Y0 + (1 - (v - (lo - pad)) / (hi + pad - (lo - pad))) * (Y1 - Y0)
  const line: Pt[] = s.curve.map((c) => [X(c.weight), Y(c.cagr)])
  const markers: { w: number; g: number; text: string; cls: string }[] = [{ w: 0, g: s.g0, text: '0', cls: 'is-zero' }]
  if (s.w_star > 0) {
    // At the cap the held 50% is a cap, not half-Kelly: never label it w*/2.
    // A cap the gate withholds is not offered, so it gets no mark at all.
    if (!isCapped(s)) markers.push({ w: s.w_star / 2, g: s.g_half, text: 'w*/2', cls: 'is-half' })
    else if (capHeld(s)) markers.push({ w: s.w_star / 2, g: s.g_half, text: 'cap 50%', cls: 'is-half' })
    markers.push({ w: s.w_star, g: s.g_star, text: 'w*', cls: 'is-star' })
  }
  if (s.break_even_status === 'found' && s.break_even != null) {
    markers.push({ w: s.break_even, g: s.g0, text: 'break-even', cls: 'is-even' })
  }
  // A mark is labelled only when no higher-priority labelled mark sits within
  // 12% of the axis (w* first): close marks would overprint, and the key under
  // the charts names every mark by its shape anyway.
  const priority = ['is-star', 'is-zero', 'is-even', 'is-half']
  const labelled: typeof markers = []
  for (const m of [...markers].sort((a, b) => priority.indexOf(a.cls) - priority.indexOf(b.cls))) {
    if (labelled.every((l) => Math.abs(l.w - m.w) >= 0.12)) labelled.push(m)
  }
  const labels: Label[] = [
    { x: X0 - 6, y: Y(hi), text: pct(hi, 2), align: 'end' },
    { x: X0 - 6, y: Y(lo), text: pct(lo, 2), align: 'end' },
    { x: X0, y: 140, text: '0%', align: 'start' },
    { x: X1, y: 140, text: '100% hedged', align: 'end' },
    ...labelled.map((m) => ({
      x: X(m.w),
      y: Y(m.g) - 11,
      text: m.text,
      strong: m.cls === 'is-star',
      align: (m.w > 0.85 ? 'end' : m.w < 0.08 ? 'start' : 'middle') as Label['align'],
    })),
  ]
  return (
    <figure className="pl-hold-chart" aria-label={`${prog.index_symbol}, ${windowName(w)}: growth at every hedge ratio`}>
      <figcaption className="pl-kicker">
        {prog.index_symbol} · {windowName(w)}
      </figcaption>
      <div className="pl-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" data-testid="hold-chart" aria-label={`Time-average growth of the book by hedge ratio, ${prog.index_symbol} ${windowName(w)}`}>
          <path d={`M${X0},${Y1}H${X1}`} className="pl-c-grid" />
          <path d={`M${X0},${Y(s.g0).toFixed(1)}H${X1}`} className="pl-c-parity" />
          <path d={poly(line)} className="pl-c-line pl-c-thin" />
          {markers.map((m) => (
            <path key={m.text} d={dots([[X(m.w), Y(m.g)]], m.cls === 'is-star' ? 4.5 : 3.2)} className={`pl-hold-mark ${m.cls}`} data-testid={`hold-mark-${m.cls.slice(3)}`} />
          ))}
        </svg>
        <ChartLabels labels={labels} w={W} h={H} />
      </div>
      {s.w_star > 0 && s.g_star - s.g0 <= 1e-4 && (
        <p className="pl-note" data-testid="hold-flat-note">
          The marks sit almost on the no-hedge line: the gain at w* is {bp(s.g_star - s.g0)}, under the 1bp bar.
        </p>
      )}
    </figure>
  )
}

// ------------------------------------------------------------------ text --

function Numbers({ prog, w }: { prog: ProgramOverlay; w: OverlayWindow }) {
  const s = w.sizing
  const zeroThroughout = s.w_star === 0 && s.halves.every((h) => h.w_star === 0) && s.decisive_months.length === 0
  const margins = Object.entries(s.margin_by_leg)
    .map(([leg, m]) => `${leg} ${bp(m)}`)
    .join(', ')
  return (
    <div className="pl-hold-num" data-testid="hold-number">
      <h4>
        {prog.index_symbol}, {windowName(w)} ({w.start} to {w.end})
      </h4>
      <p>
        Growth (CAGR) with no hedge {cagr3(s.g0)}; at full-Kelly w* = {ratio(s.w_star)}, {cagr3(s.g_star)} (
        {bp(s.g_star - s.g0)} over no hedge);{' '}
        {isCapped(s)
          ? capHeld(s)
            ? `at 50%, the cap that applies because w* is 100% (not half-Kelly), ${cagr3(s.g_half)} (${bp(s.g_half - s.g0)})`
            : `at 50%, the cap that would apply because w* is 100% (not half-Kelly), ${cagr3(s.g_half)} (${bp(s.g_half - s.g0)}) — not held: the table says ${recommendedText(s)}`
          : `at w*/2, ${cagr3(s.g_half)} (${bp(s.g_half - s.g0)})${
              s.half_keeps != null
                ? ` — half keeps ${pct(s.half_keeps, 0)} of the gain over no hedge (about 75% for a quadratic curve)`
                : ''
            }`}
        .
        Gain over no hedge at w*, by leg: {margins}. Break-even ratio: {breakEvenText(s)}. The 0.1 grid&rsquo;s best:{' '}
        {ratio(s.w_star_grid)}.
      </p>
      <p>
        Naive (mean-variance) Kelly: {s.naive_kelly == null ? 'undefined (no variance)' : ratio(s.naive_kelly)} — the
        variance approximation a skewed hedge payoff breaks, shown for contrast only.
      </p>
      {s.grid_note && <p className="pl-caveat" data-testid="hold-grid-note">{s.grid_note}</p>}
      {zeroThroughout ? (
        <p data-testid="hold-stability">w* is 0 over the whole window and in both halves; no single month moves it.</p>
      ) : (
        <p data-testid="hold-stability">
          Stability: w* is {s.halves.map((h) => `${ratio(h.w_star)} in ${h.start.slice(0, 7)} to ${h.end.slice(0, 7)}`).join(' and ')}.
          {s.decisive_months.length > 0 &&
            ` Leaving out one month at a time and re-solving, the months that move w* most: ${s.decisive_months
              .map((d) => `${d.month} (to ${ratio(d.w_star_without)}, ${ppChange(d.delta)})`)
              .join(', ')}.`}
        </p>
      )}
    </div>
  )
}

/** The caveat's last sentence, counted from the answers on screen. */
const list = (xs: string[]) => (xs.length === 1 ? xs[0]! : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`)

/** The caveat's last sentences, counted from the answers on screen: which
 *  windows are withheld and why, and whether the put programs on screen earn
 *  a place. Only the put programs actually shown are spoken for. */
function standing(programs: ProgramOverlay[]): string {
  const all = programs.flatMap((p) => p.windows)
  const withheld = all.filter((w) => w.sizing.recommended_ratio == null)
  const out: string[] = []
  if (withheld.length === all.length) {
    out.push(`No size is recommended here: ${list([...new Set(withheld.map((w) => w.sizing.reason))])}.`)
  } else if (withheld.length > 0) {
    out.push(
      `${inWords(withheld.length).replace(/^./, (c) => c.toUpperCase())} of ${inWords(all.length)} program-windows ` +
        `${withheld.length === 1 ? 'is' : 'are'} withheld: ${list([...new Set(withheld.map((w) => w.sizing.reason))])}.`,
    )
  }
  const puts = programs.filter((p) => p.index_symbol.startsWith('PPUT') && p.windows.length > 0)
  const sized = (p: ProgramOverlay) => p.windows.every((w) => w.sizing.recommended_ratio != null)
  if (puts.every(sized)) {
    const none = puts.filter((p) => p.windows.every((w) => w.sizing.recommended_ratio === 0)).map((p) => p.index_symbol)
    const held = puts.filter((p) => !none.includes(p.index_symbol)).map((p) => p.index_symbol)
    if (none.length) {
      out.push(
        none.length === 1
          ? `The put program ${none[0]} currently earns no place in the book: hold none.`
          : `The put programs ${list(none)} currently earn no place in the book: hold none.`,
      )
    }
    if (held.length) out.push(`${list(held)} earn${held.length === 1 ? 's' : ''} a place in at least one window.`)
  }
  return out.join(' ')
}

export function HoldSize({ data }: { data: HedgeOverlayResponse }) {
  const programs = data.overlay.programs
  const rows = programs.flatMap((p) => p.windows.map((w) => ({ p, w })))
  if (rows.length === 0) return null
  const placed = rows.filter(({ w }) => (w.sizing.recommended_ratio ?? 0) > 0).length
  return (
    <section className="pl-hold" aria-label="How much to hold">
      <h3>
        How much to hold <ConceptInfo id="fractional_kelly" />
      </h3>
      <p className="pl-caveat" data-testid="hold-caveat">
        In sample, one history: each w* is estimated on the same record it is judged on, so a size is half of it (full
        Kelly is notoriously sensitive to that error), and a size only exists where w* beats no hedge by more than 1bp a
        year on both index legs. {standing(programs)}
      </p>
      <p className="pl-lede" data-testid="hold-accounting">
        Self-financed: the hedge&rsquo;s premium is paid from the book, as in the verdicts above (docs/adr/0027).
      </p>
      <p className="pl-lede" data-testid="hold-method">
        For each program and window, w* is the hedge ratio with the highest time-average growth{' '}
        <ConceptInfo id="time_average_growth" /> of the whole book (monthly rebalanced), found by golden-section search
        on 0 to 100%. The recommendation is w*/2 when w* beats no hedge by more than 1bp a year on the base and the
        conservative leg; none when it does not; 50% when w* is 100% (the program then dominates the index at every
        mix, and 50% is a cap, not half-Kelly). It sizes Cboe&rsquo;s fixed-strike programs, not a Workspace strategy:
        only real quotes may size a hedge (docs/adr/0027). {inWords(placed).replace(/^./, (c) => c.toUpperCase())} of{' '}
        {inWords(rows.length)} program-windows {placed === 1 ? 'carries' : 'carry'} a size.
      </p>
      <div className="pl-scroll">
        <table className="pl-table pl-hold-table" data-testid="hold-table">
          <thead>
            <tr>
              <th>Program</th>
              <th>Window</th>
              <th className="num">Full-Kelly w*</th>
              <th className="num">Hold</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ p, w }) => (
              <tr key={`${p.index_symbol}-${w.key}`} data-testid="hold-row">
                <td data-label="Program">{p.index_symbol}</td>
                <td data-label="Window">{windowName(w)}</td>
                <td className="num" data-label="Full-Kelly w*">
                  {ratio(w.sizing.w_star)}
                </td>
                <td className="num" data-label="Hold">
                  <strong>{recommendedText(w.sizing)}</strong>
                </td>
                <td className="pl-hold-why" data-label="Why">
                  {w.sizing.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="pl-note" data-testid="hold-not-prefilled">
        Not pre-filled into the monthly plan&rsquo;s hedge ratio: a size is a decision, and the plan tests whatever ratio
        you choose against the same history.
      </p>
      <div className="pl-hold-charts">
        {rows.map(({ p, w }) => (
          <GrowthCurve key={`${p.index_symbol}-${w.key}`} prog={p} w={w} />
        ))}
      </div>
      <div className="pl-keyrow" data-testid="hold-key">
        <span>
          <i className="pl-key pl-hold-key is-zero" data-testid="hold-key-zero" />
          No hedge (0%)
        </span>
        {rows.some(({ w }) => w.sizing.w_star > 0 && !isCapped(w.sizing)) && (
          <span data-testid="hold-key-half">
            <i className="pl-key pl-hold-key is-half" />
            w*/2 (half-Kelly; held only where the table says so)
          </span>
        )}
        {rows.some(({ w }) => capHeld(w.sizing)) && (
          <span data-testid="hold-key-cap">
            <i className="pl-key pl-hold-key is-half" />
            cap 50% (where w* is 100%; not half-Kelly)
          </span>
        )}
        <span>
          <i className="pl-key pl-hold-key is-star" />
          w*, full Kelly
        </span>
        <span>
          <i className="pl-key pl-hold-key is-even" />
          Break-even: growth back to no hedge
        </span>
        <span>
          <i className="pl-key pl-key-dash" data-testid="hold-key-dash" />
          Growth with no hedge
        </span>
      </div>
      <details className="pl-hold-numbers">
        <summary>The sizing in numbers</summary>
        {rows.map(({ p, w }) => (
          <Numbers key={`${p.index_symbol}-${w.key}`} prog={p} w={w} />
        ))}
      </details>
    </section>
  )
}
