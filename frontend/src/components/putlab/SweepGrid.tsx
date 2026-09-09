import { useState } from 'react'

import type { SweepCell } from '../../api/client'
import { fmtPct } from './format'

/* The strike x tenor grid.
 *
 * Replaces SweepHeatmap's continuous cool -> amber -> hot ramp (and with it
 * format.ts's `makeRamp` + `normSigned`, both dead once this lands).
 *
 * The old ramp had two problems. It reused --hot, which was the same hex as
 * --loss, so "hot cell" and "losing cell" were one colour meaning two things.
 * And a continuous ramp cannot express the distinction that actually matters: a
 * cell that made money but lost to the index is a different KIND of outcome
 * from one that lost money, not a slightly different shade of it.
 *
 * Three printings instead, so the verdict reads without consulting a scale:
 *   loss   -> magenta ink, density by size of loss
 *   under  -> newsprint dot screen, dots tighten as it approaches the benchmark
 *   beat   -> solid black ink, density by size of the spread
 *
 * Cyan is reserved for the current-cell ring, so "which cell am I on" never
 * competes with the verdict.
 */

type Band = 'loss' | 'under' | 'beat'

function bandOf(annualized: number, benchmarkAnnualized: number): Band {
  if (annualized < 0) return 'loss'
  return annualized < benchmarkAnnualized ? 'under' : 'beat'
}

interface Props {
  cells: SweepCell[]
  moneynessPct: number
  tenorWeeks: number
  benchmarkSymbol: string
  benchmarkAnnualized: number | null
  /** Strike depth past which the flat-vol model's premium stops being a price.
   *  From the server (see SweepResponse) so this file holds no second copy. */
  modelPricedMaxMoneynessPct: number
  onSelect: (moneynessPct: number, tenorWeeks: number) => void
}

const tenorLabel = (t: number) =>
  t === 1 ? '1 week' : t === 4 ? '1 month' : t === 12 || t === 13 ? '1 quarter' : `${t} wk`

/**
 * Supplementary detail for one cell, on hover and on focus.
 *
 * Deliberately SUPPLEMENTARY. Anything load-bearing -- which band the cell is
 * in, and whether it is beyond what the pricer can price -- already reaches the
 * reader three other ways: the cell's own ink, a legend swatch, and a suffix on
 * its `aria-label`. That matters because hover does not exist on touch, so a
 * fact only reachable by hovering is a fact some readers never get. This popup
 * carries the things worth a second look and nothing a decision rests on.
 *
 * What it adds that the grid cannot: `roi_on_premium`. The cell prints an
 * ANNUALIZED figure, and the total-over-the-window number behind it is already
 * in the response and shown nowhere. On a short window those two differ by a
 * lot -- annualizing a 2-week ROI raises (1+roi) to the 26th power -- so a
 * reader comparing cells across tenors is comparing numbers with very
 * different amounts of compounding baked in.
 *
 * Rendered as a SIBLING of the <button>, not a child: a <button>'s content
 * model is phrasing content, so a <dl> inside it is invalid HTML and React
 * warns. The wrapper is the positioned ancestor.
 */
function CellTip({
  cell,
  bench,
  benchmarkSymbol,
  unpriced,
  id,
  edge,
}: {
  cell: SweepCell
  bench: number
  benchmarkSymbol: string
  unpriced: boolean
  id: string
  edge: 'left' | 'right' | null
}) {
  const over = (cell.annualized_return - bench) * 100
  return (
    <div
      className={['pl-tip', edge ? `pl-tip-${edge}` : ''].filter(Boolean).join(' ')}
      id={id}
      role="tooltip"
    >
      <dl className="pl-tip-rows">
        <dt>Annualized</dt>
        <dd>{fmtPct(cell.annualized_return)}/yr</dd>
        <dt>Total over window</dt>
        <dd>{fmtPct(cell.roi_on_premium)}</dd>
        <dt>vs {benchmarkSymbol}</dt>
        <dd>
          {over >= 0 ? '+' : '−'}
          {Math.abs(over).toFixed(1)} pp/yr
        </dd>
        <dt>Rolls</dt>
        <dd>{cell.n_cycles}</dd>
      </dl>
      {unpriced ? (
        <p className="pl-tip-warn">
          Beyond the pricer. The flat-vol model prices this strike at almost nothing, so the
          premium budget buys an absurd number of contracts — the return above is an artefact of
          that, not a measurement.
        </p>
      ) : null}
    </div>
  )
}

export function SweepGrid({
  cells,
  moneynessPct,
  tenorWeeks,
  benchmarkSymbol: rawSymbol,
  benchmarkAnnualized,
  modelPricedMaxMoneynessPct,
  onSelect,
}: Props) {
  const [openTip, setOpenTip] = useState<string | null>(null)
  if (cells.length === 0) return null

  // The API returns the ticker lowercase ("spy"); it prints as a ticker here.
  const benchmarkSymbol = rawSymbol.toUpperCase()

  const bench = benchmarkAnnualized ?? 0
  const strikes = [...new Set(cells.map((c) => c.moneyness_pct))].sort((a, b) => a - b)
  const tenors = [...new Set(cells.map((c) => c.tenor_weeks))].sort((a, b) => a - b)
  // A floor on the scale so a grid of near-flat results does not get printed as
  // if one cell were dramatic.
  const maxAbs = Math.max(0.25, ...cells.map((c) => Math.abs(c.annualized_return)))

  return (
    <>
      <div className="pl-legend pl-sweep-legend">
        <span>
          <span className="pl-swatch-box pl-swatch-loss" />
          Lost money
        </span>
        <span>
          <span className="pl-swatch-box pl-swatch-under" />
          Made money, under&nbsp;{benchmarkSymbol}
        </span>
        <span>
          <span className="pl-swatch-box pl-swatch-beat" />
          Beat&nbsp;{benchmarkSymbol}
        </span>
        <span>
          <span className="pl-swatch-box pl-swatch-current" />
          Current
        </span>
        <span>
          <span className="pl-swatch-box pl-swatch-unpriced" />
          Beyond the pricer
        </span>
      </div>
      <p className="pl-note pl-sweep-caveat">
        Past <strong>{modelPricedMaxMoneynessPct}% out of the money</strong> the flat-volatility
        model prices these puts at almost nothing &mdash; measured against real quotes, the market
        charged {modelPricedMaxMoneynessPct >= 20 ? '' : 'up to '}21,663&times; the model&rsquo;s
        premium at 20% OOM. Since every backtest here fixes the premium <em>budget</em>, a premium
        rounded to nothing buys an absurd number of contracts and inflates any payoff by the same
        factor. Hatched cells are shown because the deep tail is the point &mdash; not because
        their returns are real.
      </p>

      <div
        className="pl-sweep"
        style={{ ['--pl-sweep-cols' as string]: String(strikes.length) }}
        role="group"
        aria-label="Annualized return by strike and tenor"
      >
        <div />
        {strikes.map((s) => (
          <div className="pl-sweep-head" key={`h${s}`}>
            {s}%
          </div>
        ))}

        {tenors.map((t) => (
          <div className="pl-sweep-row" key={`r${t}`}>
            <div className="pl-sweep-rowhead">{tenorLabel(t)}</div>
            {strikes.map((s) => {
              const cell = cells.find((c) => c.moneyness_pct === s && c.tenor_weeks === t)
              if (!cell) return <div key={`${t}-${s}`} />
              const band = bandOf(cell.annualized_return, bench)
              // Magnitude within the band, 0..1. Read by the CSS as --pl-d so
              // the ramp arithmetic stays in one place.
              const d =
                band === 'loss'
                  ? Math.min(1, Math.abs(cell.annualized_return) / maxAbs)
                  : band === 'under'
                    ? Math.min(1, cell.annualized_return / Math.max(0.02, bench))
                    : Math.min(1, (cell.annualized_return - bench) / Math.max(0.15, maxAbs))
              const isCurrent = s === moneynessPct && t === tenorWeeks
              const overBench = (cell.annualized_return - bench) * 100
              // aria-label, not just title: the cell already has text content
              // ("−21%" / "208 rolls"), so a title alone never becomes the
              // accessible name and the cell reads as a bare number.
              const unpriced = s > modelPricedMaxMoneynessPct
              const label =
                `${s}% OOM · ${tenorLabel(t)} · ${fmtPct(cell.annualized_return)}/yr · ${cell.n_cycles} rolls` +
                (unpriced ? ' · beyond what the model can price' : '')
              const key = `${t}-${s}`
              const tipId = `pl-tip-${key}`
              const open = openTip === key
              // Which columns need the popup flipped inward. The grid is 11
              // columns of ~70px and the popup is ~230px, so anchoring it
              // centrally on the first or last two columns runs it off the
              // container. `.pl-sweep` sets no overflow, so it escapes rather
              // than clipping -- it would run off-screen, not get cut.
              const col = strikes.indexOf(s)
              const edge = col <= 1 ? 'left' : col >= strikes.length - 2 ? 'right' : null
              return (
                <div className="pl-sweep-cellwrap" key={key}>
                <button
                  type="button"
                  className={[
                    'pl-sweep-cell',
                    `is-${band}`,
                    // Past this density the magenta is dark enough that ink-on-it
                    // fails contrast, so the type flips to paper.
                    band === 'loss' && d > 0.46 ? 'is-strong' : '',
                    unpriced ? 'is-unpriced' : '',
                    isCurrent ? 'is-current' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  style={{ ['--pl-d' as string]: d.toFixed(3) }}
                  aria-current={isCurrent || undefined}
                  onClick={() => onSelect(s, t)}
                  aria-label={label}
                  // Focus as well as hover, so the popup is reachable by
                  // keyboard; Escape dismisses it without moving focus, which
                  // is what a reader tabbing the grid expects.
                  onMouseEnter={() => setOpenTip(key)}
                  onMouseLeave={() => setOpenTip((k) => (k === key ? null : k))}
                  onFocus={() => setOpenTip(key)}
                  onBlur={() => setOpenTip((k) => (k === key ? null : k))}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setOpenTip(null)
                  }}
                  aria-describedby={open ? tipId : undefined}
                >
                  <span className="v">{fmtPct(cell.annualized_return)}</span>
                  <span className="n">
                    {band === 'beat'
                      ? `${overBench >= 0 ? '+' : '−'}${Math.abs(overBench).toFixed(1)} vs ${benchmarkSymbol}`
                      : `${cell.n_cycles} rolls`}
                  </span>
                </button>
                {open ? (
                  <CellTip
                    cell={cell}
                    bench={bench}
                    benchmarkSymbol={benchmarkSymbol}
                    unpriced={unpriced}
                    id={tipId}
                    edge={edge}
                  />
                ) : null}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="pl-sweep-foot">
        <span className="pl-kicker">Further out of the money &rarr;</span>
        {benchmarkAnnualized != null && (
          <span>
            {benchmarkSymbol} long {fmtPct(benchmarkAnnualized)}/yr
          </span>
        )}
      </div>
    </>
  )
}
