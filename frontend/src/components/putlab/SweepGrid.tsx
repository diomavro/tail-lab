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
  onSelect: (moneynessPct: number, tenorWeeks: number) => void
}

const tenorLabel = (t: number) =>
  t === 1 ? '1 week' : t === 4 ? '1 month' : t === 12 || t === 13 ? '1 quarter' : `${t} wk`

export function SweepGrid({
  cells,
  moneynessPct,
  tenorWeeks,
  benchmarkSymbol: rawSymbol,
  benchmarkAnnualized,
  onSelect,
}: Props) {
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
      </div>

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
              const label = `${s}% OOM · ${tenorLabel(t)} · ${fmtPct(cell.annualized_return)}/yr · ${cell.n_cycles} rolls`
              return (
                <button
                  type="button"
                  key={`${t}-${s}`}
                  className={[
                    'pl-sweep-cell',
                    `is-${band}`,
                    // Past this density the magenta is dark enough that ink-on-it
                    // fails contrast, so the type flips to paper.
                    band === 'loss' && d > 0.46 ? 'is-strong' : '',
                    isCurrent ? 'is-current' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  style={{ ['--pl-d' as string]: d.toFixed(3) }}
                  aria-current={isCurrent || undefined}
                  onClick={() => onSelect(s, t)}
                  aria-label={label}
                  title={label}
                >
                  <span className="v">{fmtPct(cell.annualized_return)}</span>
                  <span className="n">
                    {band === 'beat'
                      ? `${overBench >= 0 ? '+' : '−'}${Math.abs(overBench).toFixed(1)} vs ${benchmarkSymbol}`
                      : `${cell.n_cycles} rolls`}
                  </span>
                </button>
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
