import { useState } from 'react'
import { fetchCoverage, type CoverageYear, type RegimeTimelineView, type SymbolCoverage } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { Th } from './Th'
import { useRead } from './useRead'
import { MarksChart } from './views/MarksChart'

/* "Data coverage" (docs/adr/0033, plan 5.3).
 *
 * What the lake holds for the name on screen, before a result is believed: one
 * line always visible; the datasets x years grid behind a button. A cell is the
 * sessions present out of the sessions SPY had that year (the expected count),
 * ink-coded have / partial / none -- and the fraction is printed, so colour is
 * never the only carrier. The coverage endpoint is per year, so there is no
 * month-level note: a cell's hover says the fraction.
 *
 * Quotes are a different dataset with their own chart (the Marks tab's). It is
 * mounted only once the grid is open, and only for a name the lake collects
 * quotes for -- an uncollected name never requests /marks.
 */

type Level = 'have' | 'partial' | 'none' | 'noref'
// No denominator (a year SPY's history does not fully cover): the lake HAS rows
// that year, but "complete" cannot be claimed, so it gets its own level.
const levelOf = (c: CoverageYear): Level =>
  c.sessions_expected === null
    ? 'noref'
    : c.sessions_present >= c.sessions_expected
      ? 'have'
      : c.sessions_present === 0
        ? 'none'
        : 'partial'

const DATASETS: { key: 'ohlcv' | 'tiingo_eod' | 'vix'; label: string; note: string }[] = [
  { key: 'ohlcv', label: 'Prices', note: 'daily OHLCV' },
  { key: 'tiingo_eod', label: 'Dividends', note: 'Tiingo end of day' },
  { key: 'vix', label: 'VIX', note: 'shared by every name' },
]

/** "2016–2026" over the years that hold any session; null when none does. */
function span(rows: CoverageYear[]): string | null {
  const years = rows.filter((r) => r.sessions_present > 0).map((r) => r.year)
  if (years.length === 0) return null
  const [a, b] = [Math.min(...years), Math.max(...years)]
  return a === b ? String(a) : `${a}–${b}`
}

function summaryText(c: SymbolCoverage): string {
  const quotes = c.quotes_collected ? 'quotes: expand to load' : 'quotes: not collected for this name'
  return `Prices ${span(c.ohlcv) ?? 'none'} · dividends ${span(c.tiingo_eod) ?? 'none'} · ${quotes}`
}

function Grid({ c }: { c: SymbolCoverage }) {
  const years = [...new Set(DATASETS.flatMap((d) => c[d.key].map((r) => r.year)))].sort((a, b) => a - b)
  return (
    <div className="pl-scroll">
      <table className="pl-table pl-cov" aria-label="Data coverage by year">
        <thead>
          <tr>
            <Th concept="data_coverage">Dataset</Th>
            {years.map((y) => (
              <th key={y} className="num" data-noexplain="index">
                {y}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {DATASETS.map((d) => {
            const byYear = new Map(c[d.key].map((r) => [r.year, r]))
            return (
              <tr key={d.key}>
                <td className="bold nowrap">
                  {d.label}
                  <div className="pl-micro">{d.note}</div>
                </td>
                {c[d.key].length === 0 ? (
                  <td colSpan={Math.max(1, years.length)} className="dim">
                    no rows in the lake
                  </td>
                ) : (
                  years.map((y) => {
                    const cell = byYear.get(y)
                    if (!cell) return <td key={y} className="pl-cov-cell pl-cov-none">—</td>
                    const level = levelOf(cell)
                    return (
                      <td
                        key={y}
                        className={`pl-cov-cell pl-cov-${level}`}
                        data-level={level}
                        title={
                          cell.sessions_expected === null
                            ? `${cell.sessions_present} sessions in ${y} (SPY's price history does not cover this whole year, so there is no session count to compare against)`
                            : `${cell.sessions_present} of ${cell.sessions_expected} SPY sessions in ${y}`
                        }
                      >
                        {cell.sessions_expected === null
                          ? cell.sessions_present
                          : `${cell.sessions_present}/${cell.sessions_expected}`}
                      </td>
                    )
                  })
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function NameCoverage({ asset, regimes }: { asset: string; regimes: RegimeTimelineView | null }) {
  const [open, setOpen] = useState(false)
  const [focus, setFocus] = useState(16)
  const read = useRead(asset, (signal) => fetchCoverage(asset, signal))
  const name = asset.toUpperCase()

  return (
    <section className="pl-section" aria-label="Data coverage">
      <div className="pl-section-head">
        <h3>
          Data coverage
          <ConceptInfo id="data_coverage" />
        </h3>
        {read.status === 'ready' && (
          <button
            type="button"
            className="pl-btn pl-btn-ghost"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Hide the grid' : 'Show the grid'}
          </button>
        )}
      </div>
      {read.status === 'loading' && (
        <p className="pl-status" role="status" aria-live="polite">
          Counting the sessions the lake holds for {name}…
        </p>
      )}
      {read.status === 'missing' && (
        <p className="pl-note" data-testid="coverage-summary">
          The lake holds no price or dividend rows for {name}.
        </p>
      )}
      {read.status === 'error' && (
        <p className="pl-status pl-status-error" role="alert">
          The data coverage for {name} is unavailable.
        </p>
      )}
      {read.status === 'ready' && (
        <>
          <p className="pl-note" data-testid="coverage-summary">
            {summaryText(read.data)}
          </p>
          {open && (
            <>
              <Grid c={read.data} />
              <p className="pl-note">
                Sessions present out of the sessions SPY had that year. Full years are shaded solid, partial years
                lighter, empty years blank. Striped cells are years SPY's own price history does not fully cover:
                they show the dataset's session count with nothing to compare it against.
              </p>
              {read.data.quotes_collected ? (
                <div data-testid="coverage-quotes">
                  <div className="pl-kicker" style={{ margin: '14px 0 8px' }}>
                    Put quotes
                  </div>
                  <MarksChart
                    symbol={asset}
                    regimes={regimes}
                    band={1}
                    measure="usable"
                    focus={focus}
                    onFocus={setFocus}
                  />
                </div>
              ) : (
                <p className="pl-note">Put quotes are not collected for {name}.</p>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}
