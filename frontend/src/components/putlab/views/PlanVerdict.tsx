import type { PlanArm, PlanLegRow, PlanWindow, RollingSummary } from '../../../api/client'
import type { Label } from '../chart'
import { ChartLabels } from '../ChartLabels'
import { legName, pct, pp, share, usd } from './planFormat'

/* The rolling-start verdict and one window's two arms, drawn. Shared by both
 * sources; ``legs`` is the real-quote source's per-index-leg rerun. The
 * sentences and the arms table the charts replace stay one click away, in
 * "The plan in numbers", for a reader who wants the figures rather than the
 * marks (and for a keyboard, which cannot hover a chart). */

function ArmRow({ arm }: { arm: PlanArm }) {
  return (
    <tr>
      <td>{arm.label}</td>
      <td className="num">{usd(arm.contributed)}</td>
      <td className="num">{usd(arm.terminal_wealth)}</td>
      <td className="num">{pct(arm.irr, 2)}</td>
      <td className="num">{pct(arm.max_drawdown)}</td>
    </tr>
  )
}

/** Hedged IRR minus comparator IRR across every start: whisker from worst to
 *  best, a 10th-90th percentile box, a median tick, and parity dashed. */
function GapStrip({ r, narrow }: { r: RollingSummary; narrow: boolean }) {
  const lo = Math.min(r.worst_gap, 0)
  const hi = Math.max(r.best_gap, 0)
  const pad = (hi - lo) * 0.06 || 0.001
  const W = narrow ? 360 : 640
  // Worst/best, 10th and 90th each get a row below the strip: on one row they
  // overprint whenever two of them sit close.
  const H = 150
  const X = (v: number) => 20 + ((v - (lo - pad)) / (hi + pad - (lo - pad))) * (W - 40)
  // A label near either edge reads inward, so it never runs off the chart
  // (and, on a phone, off the page).
  const at = (x: number, y: number, text: string, strong = false): Label => {
    const room = 80
    if (x < room) return { x: Math.max(x, 0), y, text, align: 'start', strong }
    if (x > W - room) return { x: Math.min(x, W), y, text, align: 'end', strong }
    return { x, y, text, strong }
  }
  const labels: Label[] = [
    { x: X(0), y: 6, text: 'parity' },
    at(X(r.median_gap), 30, `median ${pp(r.median_gap)}`, true),
    at(X(r.p10_gap), 90, `10th ${pp(r.p10_gap)}`),
    at(X(r.p90_gap), 108, `90th ${pp(r.p90_gap)}`),
    at(X(r.worst_gap), 128, `worst ${pp(r.worst_gap)}`),
    at(X(r.best_gap), 144, `best ${pp(r.best_gap)}`),
  ]
  return (
    <div className="pl-plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Hedged IRR minus comparator IRR across every start" data-testid="gap-strip">
        <path d={`M${X(0)},16V92`} className="pl-c-parity" />
        <path
          d={`M${X(r.worst_gap)},58H${X(r.best_gap)}M${X(r.worst_gap)},52V64M${X(r.best_gap)},52V64`}
          className="pl-c-whisker"
        />
        <rect x={X(r.p10_gap)} y={48} width={Math.max(0, X(r.p90_gap) - X(r.p10_gap))} height={20} className="pl-c-box" />
        <path d={`M${X(r.median_gap)},44V72`} className="pl-c-median" />
      </svg>
      <ChartLabels labels={labels} w={W} h={H} />
    </div>
  )
}

function ArmBar({ arm, max, ddMax, hedged }: { arm: PlanArm; max: number; ddMax: number; hedged: boolean }) {
  return (
    <div className="pl-arm" data-testid="plan-arm">
      <div className="pl-arm-head">
        <span>{arm.label}</span>
        <span>
          <strong className="pl-arm-ends">{usd(arm.terminal_wealth)}</strong> · IRR {pct(arm.irr, 2)}
        </span>
      </div>
      <div className="pl-arm-track">
        <div
          className={hedged ? 'pl-arm-fill' : 'pl-arm-fill is-comparator'}
          style={{ width: `${(arm.terminal_wealth / max) * 100}%` }}
        />
        {/* Paper cut into the fill; ink when it falls past the fill (an arm
            that ended below what was paid in), where paper would vanish. */}
        <div
          className={`pl-arm-paid${arm.contributed > arm.terminal_wealth ? ' is-beyond' : ''}`}
          data-testid="arm-paid"
          style={{ left: `${(arm.contributed / max) * 100}%` }}
          title={`Paid in ${usd(arm.contributed)}`}
        />
      </div>
      <div className="pl-arm-dd">
        <span>Paid in {usd(arm.contributed)} · worst drawdown</span>
        <span className="pl-arm-dd-track">
          <span style={{ width: `${(Math.abs(arm.max_drawdown) / ddMax) * 100}%` }} />
        </span>
        <span className="pl-arm-dd-v">{pct(arm.max_drawdown)}</span>
      </div>
    </div>
  )
}

export function PlanVerdict({
  rolling: r,
  window: w,
  legs,
  narrow,
}: {
  rolling: RollingSummary | null
  window: PlanWindow | null
  legs?: PlanLegRow[]
  narrow: boolean
}) {
  if (r == null || w == null) return null
  const span = (Date.parse(r.last_start) - Date.parse(r.first_start)) / (365.25 * 86_400_000) + r.horizon_years
  const separate = Math.max(1, Math.floor(span / r.horizon_years))
  const max = Math.max(w.hedged.terminal_wealth, w.comparator.terminal_wealth, w.hedged.contributed) * 1.08
  // Drawdown bars share a 0 to 60% scale unless an arm fell further: never a
  // bar silently full at -85%.
  const ddMax = Math.max(0.6, Math.abs(w.hedged.max_drawdown), Math.abs(w.comparator.max_drawdown))
  // 0 to 10% of starts unless a share runs past it: never a bar cut at the edge.
  const yieldScale = Math.max(0.1, ...(legs ?? []).map((y) => y.share_ahead))
  const measured = (legs ?? []).some((y) => y.key.kind === 'leg')
  const byWhat = measured ? 'by index leg' : 'by assumed S&P dividend yield'
  // One decimal: the legs differ by fractions of a point, and whole percents
  // would print two different shares as the same number.
  const legShare = (x: number) => (x > 0 && x < 1 ? pct(x, 1) : share(x))
  const rowName = (y: PlanLegRow) => (y.key.kind === 'assumed_yield' ? pct(y.key.dividend_yield) : legName(y.key))
  const startsText = r.n_starts === 1 ? `the one monthly start` : `${r.n_starts} monthly starts`
  return (
    <>
      <section className="pl-plan-share" aria-label="Share of starts led">
        {/* Above the figure it qualifies: a caveat after the number has lost. */}
        <p className="pl-caveat" data-testid="overlap-caveat">
          {r.n_starts === 1 ? 'This one start covers' : `These ${r.n_starts} starts are not independent: they cover`}{' '}
          {Math.round(span)} years of history, about {separate} separate {r.horizon_years}-year{' '}
          {separate === 1 ? 'period' : 'periods'}, so these shares describe this history; they do not test a
          hypothesis.
        </p>
        <div className="pl-plan-share-head">
          <span className="pl-plan-share-fig" data-testid="plan-share">
            {share(r.share_ahead)}
          </span>
          <p className="pl-plan-share-text">
            of {startsText}, the hedged book&rsquo;s {r.horizon_years}-year IRR led the comparator by more than 1bp a
            year.
          </p>
        </div>
        <div className="pl-plan-stack" role="img" aria-label={`Led ${share(r.share_ahead)}, within 1bp ${share(r.share_inconclusive)}, trailed ${share(r.share_behind)}`}>
          <span className="is-ahead" style={{ width: `${r.share_ahead * 100}%` }} />
          <span className="is-within" style={{ width: `${r.share_inconclusive * 100}%` }} />
          <span className="is-behind" style={{ width: `${r.share_behind * 100}%` }} />
        </div>
        <div className="pl-keyrow">
          <span><i className="pl-key pl-key-sq is-ahead" />Hedged led · {share(r.share_ahead)}</span>
          <span><i className="pl-key pl-key-sq is-within" />Within 1bp · {share(r.share_inconclusive)}</span>
          <span><i className="pl-key pl-key-sq is-behind" />Hedged trailed · {share(r.share_behind)}</span>
        </div>
      </section>

      <section className="pl-plan-block" aria-label="How far ahead or behind">
        <h3>How far ahead or behind, per year</h3>
        <p className="pl-chart-sub">
          Hedged IRR minus comparator IRR across every start. Bar: 10th to 90th percentile; tick: median; whiskers:
          worst and best.
        </p>
        <GapStrip r={r} narrow={narrow} />
        {legs && legs.length > 0 && (
          <div className="pl-plan-yield">
            <div className="pl-kicker">Share of starts led, {byWhat}</div>
            {legs.map((y) => (
              <div className="pl-plan-yield-row" key={rowName(y)}>
                <span className="dim">{rowName(y)}</span>
                <span className="pl-plan-yield-track">
                  <span style={{ width: `${(y.share_ahead / yieldScale) * 100}%` }} />
                </span>
                <strong>{legShare(y.share_ahead)}</strong>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="pl-plan-block" aria-label="One window">
        <div>
          <h3>
            One window, {w.start} to {w.end}
          </h3>
          <p className="pl-chart-sub">What each arm ends with. The tick marks what was paid in.</p>
        </div>
        <ArmBar arm={w.hedged} max={max} ddMax={ddMax} hedged />
        <ArmBar arm={w.comparator} max={max} ddMax={ddMax} hedged={false} />
      </section>

      <details className="pl-plan-numbers">
        <summary>The plan in numbers</summary>
        <p data-testid="plan-verdict">
          Across <strong>{r.n_starts}</strong> monthly{' '}
          {r.n_starts === 1 ? `start (${r.first_start})` : `starts (${r.first_start} to ${r.last_start})`}, each a{' '}
          {r.horizon_years}-year plan, the hedged book's IRR led by more than 1bp/yr in{' '}
          <strong>{share(r.share_ahead)}</strong>, trailed in <strong>{share(r.share_behind)}</strong> and was within
          1bp in {share(r.share_inconclusive)}. Median gap {pp(r.median_gap)}/yr; 10th to 90th percentile{' '}
          {pp(r.p10_gap)} to {pp(r.p90_gap)}; worst {pp(r.worst_gap)}, best {pp(r.best_gap)}.
        </p>
        {legs && (
          <p data-testid="plan-by-yield">
            Share of starts led, {byWhat}:{' '}
            {legs
              .map((y) => `${y.key.kind === 'assumed_yield' ? `at ${rowName(y)}` : rowName(y)}, ${legShare(y.share_ahead)}`)
              .join('; ')}
            .
          </p>
        )}
        <div className="pl-scroll">
          <table className="pl-table">
            <thead>
              <tr>
                <th>Arm</th>
                <th className="num">Paid in</th>
                <th className="num">Ends with</th>
                <th className="num">IRR</th>
                <th className="num">Worst drawdown</th>
              </tr>
            </thead>
            <tbody>
              <ArmRow arm={w.hedged} />
              <ArmRow arm={w.comparator} />
            </tbody>
          </table>
        </div>
      </details>
    </>
  )
}
