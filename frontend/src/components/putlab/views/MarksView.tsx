import { useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  fetchMarks,
  type MarksCoverage,
  type MarksSource,
  type RegimeLabel,
  type RegimeTimelineView,
} from '../../../api/client'

/* "Do we even hold a quote there?"
 *
 * Read BEFORE any result: a rule tested at 16% OOM on a handful of sessions is a
 * model result wearing a market label. The grid is time across, depth out of
 * the money down; a cell's ink is the share of that month's sessions with at
 * least one usable put in the bucket (positive bid, spread within the marking
 * bar). Hatching means NOTHING WAS COLLECTED -- either no source covers the
 * month, or the source never stored a strike that deep -- which is a different
 * absence from "collected, and nobody quoted it" (a blank cell).
 *
 * The bar beside each row is the same question summed: years of usable quotes
 * at that depth, split by the regime each session was in, so "how much crisis
 * data do we have at 30% out" is one glance across.
 */

const SESSIONS_PER_YEAR = 252
const REGIME_ORDER: RegimeLabel[] = ['calm', 'elevated', 'crisis']
const REGIME_FILL: Record<RegimeLabel, string> = {
  calm: 'var(--ink-25)',
  elevated: 'var(--warn-fill)',
  crisis: 'var(--mag)',
}
const BAND_LABEL: Record<string, string> = { '<=2w': 'up to 2 weeks', '2-6w': '2–6 weeks', '6-17w': '6–17 weeks' }
const SOURCE_LABEL: Record<MarksSource, string> = { optionsdx: 'optionsDX archive', chain: 'Forward sweep' }
/** Ink steps for the share of sessions covered: 0 is blank paper. */
const STEPS = [0.25, 0.5, 0.75, 1] as const
const stepOf = (share: number) => STEPS.findIndex((s) => share <= s + 1e-9)
const stepFill = (i: number) => `color-mix(in srgb, var(--cyan) ${[22, 45, 70, 100][i]}%, var(--paper-2))`

const ROW_H = 10
const GRID_W = 760
const LABEL_W = 46
const BAR_W = 200
const RIBBON_H = 8
const TOP = RIBBON_H + 6

type Measure = 'usable' | 'traded'
type Load =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: MarksCoverage }

interface Hover {
  month: string
  oom: number
  /** Pixels from the wrapper's top-left, read off the cell's own box. */
  left: number
  top: number
}

/** Names a fresh page offers before the first answer lists them all. */
const FALLBACK_SYMBOLS = ['nvda', 'qqq', 'spy', 'tsla']

function monthsBetween(first: string, last: string): string[] {
  const out: string[] = []
  let [y, m] = first.split('-').map(Number) as [number, number]
  const [ly, lm] = last.split('-').map(Number) as [number, number]
  while (y < ly || (y === ly && m <= lm)) {
    out.push(`${y}-${String(m).padStart(2, '0')}`)
    m += 1
    if (m > 12) {
      m = 1
      y += 1
    }
  }
  return out
}

const years = (sessions: number) => sessions / SESSIONS_PER_YEAR
const fmtYears = (sessions: number) => {
  const y = years(sessions)
  return y >= 0.95 ? `${y.toFixed(1)} y` : `${sessions} d`
}

interface Props {
  asset: string
  regimes: RegimeTimelineView | null
}

export function MarksView({ asset, regimes }: Props) {
  const [symbol, setSymbol] = useState(asset.toLowerCase())
  const [band, setBand] = useState(1)
  const [measure, setMeasure] = useState<Measure>('usable')
  const [focus, setFocus] = useState(16)
  const [hover, setHover] = useState<Hover | null>(null)
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  // Kept across a failed read, so an error never strands the picker on the
  // one name that failed.
  const [known, setKnown] = useState<string[]>(FALLBACK_SYMBOLS)

  useEffect(() => {
    const ctl = new AbortController()
    setLoad({ status: 'loading' })
    fetchMarks(symbol, ctl.signal)
      .then((data) => {
        setLoad({ status: 'ready', data })
        setKnown(data.symbols)
      })
      .catch((err: unknown) => {
        if (ctl.signal.aborted) return
        const message =
          err instanceof ApiError && err.status === 404
            ? `the lake holds no put quotes for ${symbol.toUpperCase()}`
            : err instanceof Error
              ? err.message
              : 'request failed'
        setLoad({ status: 'error', message })
      })
    return () => ctl.abort()
  }, [symbol])

  const data = load.status === 'ready' ? load.data : null
  const grid = useMemo(() => (data ? buildGrid(data, band, measure) : null), [data, band, measure])

  const symbols = known
  const picker = (
    <div className="pl-marks-controls">
      <label className="pl-marks-field">
        <span className="pl-kicker">Name</span>
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Name">
          {(symbols.includes(symbol) ? symbols : [symbol, ...symbols]).map((s) => (
            <option key={s} value={s}>
              {s.toUpperCase()}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="pl-marks-field">
        <legend className="pl-kicker">Tenor</legend>
        <div className="pl-seg">
          {(data?.bands ?? ['<=2w', '2-6w', '6-17w']).map((b, i) => (
            <label className="pl-seg-opt" key={b}>
              <input type="radio" name="marks-band" checked={band === i} onChange={() => setBand(i)} />
              {BAND_LABEL[b] ?? b}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="pl-marks-field">
        <legend className="pl-kicker">Counts as held</legend>
        <div className="pl-seg">
          <label className="pl-seg-opt">
            <input type="radio" name="marks-measure" checked={measure === 'usable'} onChange={() => setMeasure('usable')} />
            Usable quote
          </label>
          <label className="pl-seg-opt">
            <input type="radio" name="marks-measure" checked={measure === 'traded'} onChange={() => setMeasure('traded')} />
            Traded
          </label>
        </div>
      </fieldset>
    </div>
  )

  if (!data || !grid) {
    return (
      <div>
        <Header />
        {picker}
        {load.status === 'error' ? (
          <p className="pl-caveat" role="alert">
            The coverage could not be read: {load.message}.
          </p>
        ) : (
          <p className="pl-status" role="status" aria-live="polite">
            Counting the quotes the lake holds for {symbol.toUpperCase()}…
          </p>
        )}
      </div>
    )
  }

  const bandName = data.bands[band]!
  const rowsByOom = new Map(data.depth.filter((r) => r.band === bandName).map((r) => [r.oom, r]))
  const focusRow = rowsByOom.get(focus)
  const focusHeld = focusRow ? (measure === 'usable' ? focusRow.usable_sessions : focusRow.traded_sessions) : 0
  const maxBar = Math.max(1, ...grid.depths.map((o) => rowsByOom.get(o)?.usable_sessions ?? 0))
  const height = TOP + grid.depths.length * ROW_H
  const colW = GRID_W / grid.months.length
  const x = (i: number) => LABEL_W + i * colW
  const monthIdx = new Map(grid.months.map((m, i) => [m, i]))
  const hovered = hover ? grid.cell(hover.month, hover.oom) : null

  return (
    <div>
      <Header />
      {picker}

      <div className="pl-hero">
        <div>
          <div className="pl-kicker" style={{ marginBottom: 6 }}>
            {data.symbol.toUpperCase()} · {focus}–{focus + data.oom_step}% out · {BAND_LABEL[bandName]}
          </div>
          <div className="pl-hero-fig" data-testid="marks-hero">
            <span className="pl-hero-num">{years(focusHeld).toFixed(1)}</span>
            <span className="pl-hero-unit">years of {measure === 'usable' ? 'usable quotes' : 'traded strikes'}</span>
          </div>
          <p className="pl-hero-sentence">
            {focusRow && measure === 'usable' ? (
              <>
                {REGIME_ORDER.map((r, i) => (
                  <span key={r}>
                    {i > 0 && (i === REGIME_ORDER.length - 1 ? ' and ' : ', ')}
                    <strong>{years(focusRow.usable_by_regime[r] ?? 0).toFixed(1)}</strong> in {r}
                  </span>
                ))}
                . Click a row to read another depth.
              </>
            ) : focusRow ? (
              <>Sessions where some strike in the bucket printed volume. Click a row to read another depth.</>
            ) : (
              <>Nothing held at this depth and tenor. Click a row to read another depth.</>
            )}
          </p>
        </div>
        <dl className="pl-brokenout" aria-label="Quote sources">
          <div className="pl-kicker">Sources</div>
          {data.sources.map((s) => (
            <div className="pl-brokenout-row" key={s.source}>
              <dt>{SOURCE_LABEL[s.source]}</dt>
              <dd>
                {s.first} → {s.last} · {s.sessions.toLocaleString()} sessions · to {s.stored_depth_pct}% out
              </dd>
            </div>
          ))}
          <div className="pl-brokenout-row">
            <dt>Price series</dt>
            <dd>{data.price_span ? `${data.price_span[0]} → ${data.price_span[1]}` : 'none in the lake'}</dd>
          </div>
        </dl>
      </div>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>Where a quote is held</h3>
        </div>
        <div className="pl-marks-legend">
          <span>Share of the month&rsquo;s sessions:</span>
          {STEPS.map((s, i) => (
            <span key={s}>
              <i style={{ background: stepFill(i) }} />
              {i === 0 ? 'up to 25%' : `to ${s * 100}%`}
            </span>
          ))}
          <span>
            <i className="pl-marks-blank" />
            held, unquoted
          </span>
          <span>
            <i className="pl-marks-hatch" />
            never collected
          </span>
          {REGIME_ORDER.map((r) => (
            <span key={r}>
              <i style={{ background: REGIME_FILL[r] }} />
              {r}
            </span>
          ))}
        </div>
        <div className="pl-scroll pl-marks-wrap">
          <svg
            className="pl-marks-grid"
            viewBox={`0 0 ${LABEL_W + GRID_W + 12 + BAR_W + 52} ${height + 30}`}
            role="img"
            aria-label={`${data.symbol.toUpperCase()} put quotes held by month and depth out of the money, ${BAND_LABEL[bandName]}`}
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              <pattern id="marks-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="4" height="4" fill="var(--paper)" />
                <line x1="0" y1="0" x2="0" y2="4" stroke="var(--ink-25)" strokeWidth="1.5" />
              </pattern>
            </defs>

            {/* Regime ribbon: the backdrop each column of quotes was taken in. */}
            {regimes?.segments.map((seg) => {
              const last = grid.months.length - 1
              const sm = seg.start.slice(0, 7)
              const em = seg.end.slice(0, 7)
              if (em < grid.months[0]! || sm > grid.months[last]!) return null
              const from = monthIdx.get(sm) ?? 0
              const to = monthIdx.get(em) ?? last
              return (
                <rect
                  className="pl-marks-ribbon"
                  key={`${seg.regime}-${seg.start}`}
                  x={x(from)}
                  y={0}
                  width={Math.max(colW * 0.5, (to - from + 1) * colW)}
                  height={RIBBON_H}
                  fill={REGIME_FILL[seg.regime]}
                >
                  <title>{`${seg.regime}: ${seg.start} → ${seg.end}`}</title>
                </rect>
              )
            })}

            {grid.depths.map((oom, r) => {
              const y = TOP + r * ROW_H
              const row = rowsByOom.get(oom)
              const isFocus = oom === focus
              return (
                <g
                  key={oom}
                  className={isFocus ? 'pl-marks-row is-focus' : 'pl-marks-row'}
                  onClick={() => setFocus(oom)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setFocus(oom)
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`Read ${oom}–${oom + data.oom_step}% out`}
                  aria-pressed={isFocus}
                  data-oom={oom}
                >
                  <text x={LABEL_W - 6} y={y + ROW_H - 2} textAnchor="end" className="pl-marks-axis">
                    {oom % 10 === 0 || isFocus ? `${oom}%` : ''}
                  </text>
                  {grid.months.map((m, i) => {
                    const c = grid.cell(m, oom)
                    const fill =
                      c.kind === 'uncollected'
                        ? 'url(#marks-hatch)'
                        : c.share <= 0
                          ? 'var(--paper-2)'
                          : stepFill(stepOf(c.share))
                    return (
                      <rect
                        key={m}
                        x={x(i)}
                        y={y}
                        width={colW + 0.02}
                        height={ROW_H - 1}
                        fill={fill}
                        onMouseEnter={(e) => {
                          const wrap = e.currentTarget.closest('.pl-marks-wrap')
                          if (!wrap) return
                          const box = e.currentTarget.getBoundingClientRect()
                          const frame = wrap.getBoundingClientRect()
                          setHover({
                            month: m,
                            oom,
                            left: box.right - frame.left + wrap.scrollLeft,
                            top: box.bottom - frame.top + wrap.scrollTop + 4,
                          })
                        }}
                      />
                    )
                  })}
                  {/* Years held at this depth, stacked by regime. */}
                  {(() => {
                    let at = LABEL_W + GRID_W + 12
                    return REGIME_ORDER.map((reg) => {
                      const n = row?.usable_by_regime[reg] ?? 0
                      const w = (n / maxBar) * BAR_W
                      const rect = (
                        <rect key={reg} className="pl-marks-bar" data-regime={reg} x={at} y={y + 1} width={w} height={ROW_H - 3} fill={REGIME_FILL[reg]}>
                          <title>{`${oom}% out, ${reg}: ${fmtYears(n)}`}</title>
                        </rect>
                      )
                      at += w
                      return rect
                    })
                  })()}
                  <text x={LABEL_W + GRID_W + 12 + BAR_W + 4} y={y + ROW_H - 2} className="pl-marks-axis">
                    {row && (oom % 10 === 0 || isFocus) ? fmtYears(row.usable_sessions) : ''}
                  </text>
                  {isFocus && (
                    <rect
                      x={LABEL_W - 1}
                      y={y - 1}
                      width={GRID_W + 2}
                      height={ROW_H + 1}
                      fill="none"
                      className="pl-marks-focus"
                    />
                  )}
                </g>
              )
            })}

            {/* Year ticks, and the span a roll backtest can use (needs a price). */}
            {grid.months.map((m, i) =>
              m.endsWith('-01') && Number(m.slice(0, 4)) % 2 === 0 ? (
                <text key={m} x={x(i)} y={height + 11} className="pl-marks-axis">
                  {m.slice(0, 4)}
                </text>
              ) : null,
            )}
            {data.price_span &&
              data.price_span[1].slice(0, 7) >= grid.months[0]! &&
              data.price_span[0].slice(0, 7) <= grid.months[grid.months.length - 1]! &&
              (() => {
                const a = monthIdx.get(data.price_span[0].slice(0, 7)) ?? 0
                const b = monthIdx.get(data.price_span[1].slice(0, 7)) ?? grid.months.length - 1
                return (
                  <g className="pl-marks-price">
                    <line x1={x(a)} x2={x(b + 1)} y1={height + 20} y2={height + 20} />
                    <text x={x(a)} y={height + 29}>
                      price series — a roll backtest needs both
                    </text>
                  </g>
                )
              })()}
            <text x={LABEL_W + GRID_W + 12} y={height + 11} className="pl-marks-axis">
              years of usable quotes, by regime
            </text>
          </svg>
          {hover && hovered && (
            <div
              className="pl-marks-tip"
              role="tooltip"
              style={{ left: hover.left, top: hover.top }}
            >
              <strong>
                {hover.month} · {hover.oom}–{hover.oom + data.oom_step}% out
              </strong>
              <br />
              {hovered.kind === 'uncollected'
                ? hovered.why
                : `${hovered.held} of ${hovered.sessions} sessions ${measure === 'usable' ? 'with a usable quote' : 'traded'}`}
            </div>
          )}
        </div>
        <p className="pl-note">
          Usable means a positive bid and a spread within {Math.round(data.max_relative_spread * 100)}% of mid
          &mdash; the bar a quote must clear to mark a position. One quote anywhere in the 2-point bucket counts the
          session, so a held cell says a strike there <em>could</em> be priced, not that every strike could.
        </p>
      </section>

      <section className="pl-section">
        <details>
          <summary>The same counts as a table</summary>
          <div className="pl-scroll">
            <table className="pl-table" aria-label="Sessions of quotes held by depth">
              <thead>
                <tr>
                  <th>Out of the money</th>
                  <th className="num">Usable</th>
                  <th className="num">Traded</th>
                  {REGIME_ORDER.map((r) => (
                    <th className="num" key={r}>
                      {r}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grid.depths.map((oom) => {
                  const row = rowsByOom.get(oom)
                  return (
                    <tr key={oom}>
                      <td>
                        {oom}–{oom + data.oom_step}%
                      </td>
                      <td className="num bold">{fmtYears(row?.usable_sessions ?? 0)}</td>
                      <td className="num">{fmtYears(row?.traded_sessions ?? 0)}</td>
                      {REGIME_ORDER.map((r) => (
                        <td className="num" key={r}>
                          {fmtYears(row?.usable_by_regime[r] ?? 0)}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </div>
  )
}

function Header() {
  return (
    <>
      <h2 style={{ marginBottom: 4 }}>Where we hold a mark</h2>
      <p className="pl-lede" style={{ marginBottom: 18 }}>
        Before a result is believed: for this name, how deep out of the money and in which years does the lake hold a
        real put quote &mdash; and in which regime was each session?
      </p>
    </>
  )
}

type GridCell =
  | { kind: 'uncollected'; why: string; share: 0 }
  | { kind: 'held'; sessions: number; held: number; share: number }

function buildGrid(data: MarksCoverage, band: number, measure: Measure) {
  const firsts = data.sources.map((s) => s.months[0]!).sort()
  const lasts = data.sources.map((s) => s.months[s.months.length - 1]!).sort()
  const months = monthsBetween(firsts[0]!, lasts[lasts.length - 1]!)
  const deepest = Math.max(...data.sources.map((s) => s.stored_depth_pct))
  const depths: number[] = []
  for (let o = 0; o < deepest; o += data.oom_step) depths.push(o)

  // month -> (source depth, sessions, oom -> held)
  const byMonth = new Map<string, { depth: number; sessions: number; held: Map<number, number> }>()
  for (const s of data.sources) {
    s.months.forEach((m, i) => byMonth.set(m, { depth: s.stored_depth_pct, sessions: s.month_sessions[i]!, held: new Map() }))
    for (const [mi, oom, b, usable, traded] of s.cells) {
      if (b !== band) continue
      byMonth.get(s.months[mi]!)!.held.set(oom, measure === 'usable' ? usable : traded)
    }
  }

  const cell = (month: string, oom: number): GridCell => {
    const m = byMonth.get(month)
    if (!m) return { kind: 'uncollected', why: 'No quote source covers this month.', share: 0 }
    if (oom >= m.depth) return { kind: 'uncollected', why: `This source never stored strikes past ${m.depth}% out.`, share: 0 }
    const held = m.held.get(oom) ?? 0
    return { kind: 'held', sessions: m.sessions, held, share: held / m.sessions }
  }
  return { months, depths, cell }
}
