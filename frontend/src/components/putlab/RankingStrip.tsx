import { type CSSProperties, useEffect, useId, useRef, useState } from 'react'
import type { PutLabLeaderboardResponse, RankedAsset, SignalTestResult, Verdict } from '../../api/client'
import { UNKNOWN_Q_CAUSES } from '../../content/concepts'
import { BestReturnTable } from './BestReturnTable'
import { type BestRow, bestReturnRows, VERDICT_TITLE } from './rankOrder'
import { ConceptInfo } from './ConceptInfo'
import { fmtFixed, fmtMult, fmtPct, fmtPrice } from './format'
import type { ResourceState } from './PutLab'
import { biggestBarWidth, fragilityRank, lossHeat } from './rankShade'
import { cadenceText, sentenceText, STATUS_LABEL } from './signalWording'
import { Th } from './Th'
import { deltaLabel, type RankSort, STRIP_SIGNAL_TEST } from './types'

/* The screen: the universe ranking at the top of the Workspace.
 *
 * Was a permanently pinned panel above the tab bar, ~232px tall and
 * drag-resizable, which ate the top third of every view and pushed the actual
 * result below the fold. It had to be pinned, because picking a name and
 * reading its result lived on two different tabs. Now that they are one view it
 * opens as a single line -- the top three, clickable -- and expands to the full
 * table on demand.
 *
 * Two orders over ONE response (docs/adr/0028 amendment): by fragility (the
 * server's own order) and by best return (the old Recommendations tab, now
 * `BestReturnTable`). Switching never refetches: every best-return column is
 * independent of the screening strike.
 *
 * Each pick advertises its `best_annualized`, so a click lands on the cell the
 * pick advertised rather than on the currently selected strike -- see the note
 * in PutLab.selectAsset for why that distinction is load-bearing. The
 * screened cell's own figures (at the rail's strike and tenor) are in the
 * fragility table's "At ..." group, each column under the strategy that
 * produced it.
 *
 * The strip stays mounted while its read loads, so the switch and the
 * signal-test line never vanish with it; only the picks, the expand button and
 * the tables wait for `ready`.
 */

const VERDICT_TAG: Record<Verdict, string> = {
  confirmed: 'pl-tag pl-tag-ok',
  regime_only: 'pl-tag pl-tag-mute',
  failed: 'pl-tag pl-tag-bad',
  untested: 'pl-tag pl-tag-outline',
}

const BASIS_TAG: Record<RankedAsset['priced_from'], string> = {
  model: 'pl-tag pl-tag-outline',
  market: 'pl-tag pl-tag-ok',
}
const BASIS_LABEL: Record<RankedAsset['priced_from'], string> = {
  model: 'Model',
  market: 'Market',
}

/** The six raw metrics, in column order, with the header each prints. */
const METRICS: { key: MetricKey; head: string; digits: number }[] = [
  { key: 'downside_beta', head: 'Dn β', digits: 2 },
  { key: 'co_skewness', head: 'Co-skew', digits: 2 },
  { key: 'co_kurtosis', head: 'Co-kurt', digits: 1 },
  { key: 'tail_beta', head: 'Tail β', digits: 2 },
  { key: 'downside_capture', head: 'Dn cap', digits: 2 },
  { key: 'vol_beta', head: 'Vol β', digits: 2 },
]
type MetricKey = 'downside_beta' | 'co_skewness' | 'co_kurtosis' | 'tail_beta' | 'downside_capture' | 'vol_beta'

const num = (v: number | null, digits = 2) => (v == null ? '—' : fmtFixed(v, digits))

/** `fragility_score` is a mean fractional rank in 0..1, so it needs scaling
 *  before it reads as a score. Rounding it as-is collapses the whole universe
 *  onto 0 and 1. */
const fragility = (v: number | null) => (v == null ? '—' : String(Math.round(v * 100)))
const at = (r: RankedAsset) =>
  r.best_moneyness_pct == null || r.best_tenor_weeks == null
    ? '—'
    : `${r.best_moneyness_pct}% · ${r.best_tenor_weeks}w`

const heatStyle = (d: number | null | undefined): CSSProperties | undefined =>
  d == null ? undefined : ({ '--pl-d': d } as CSSProperties)

interface ScreenAxes {
  moneyness_pct: number
  tenor_weeks: number
  years: number
}

interface Props {
  ranking: ResourceState<PutLabLeaderboardResponse>
  /** The fixed STRIP_SIGNAL_TEST read (docs/adr/0033). */
  signal: ResourceState<SignalTestResult>
  sort: RankSort
  onSort: (sort: RankSort) => void
  /** The screen the strip asked for -- what the bake-off line compares itself
   *  with while the ranking is still loading. */
  requested: ScreenAxes
  currentAsset: string
  /** The rail picks strikes by delta: the screen still runs by distance, at
   *  the distance the rail last held, which it no longer shows. */
  deltaMode: boolean
  onSelect: (asset: string, best?: { moneyness_pct: number; tenor_weeks: number }) => void
  onOpenBakeOff: () => void
}

export function RankingStrip({
  ranking,
  signal,
  sort,
  onSort,
  requested,
  currentAsset,
  deltaMode,
  onSelect,
  onOpenBakeOff,
}: Props) {
  // Local on purpose: it survives a re-screen (the strip stays mounted) but
  // not a tab round trip, where a collapsed strip is the right default.
  const [open, setOpen] = useState(false)
  const sortName = useId()

  // The last screen shown, for one honest hold: in the best-return order a
  // re-screen that changes ONLY the strike returns identical rows (every
  // column is strike-free; the tenor and years decide coverage and values),
  // so those rows stay up while it runs. Any other change shows the status
  // line instead (docs/adr/0028 amendment).
  const lastReady = useRef<PutLabLeaderboardResponse | null>(null)
  useEffect(() => {
    if (ranking.status === 'ready') lastReady.current = ranking.data
  }, [ranking])
  const prev = lastReady.current
  const held =
    sort === 'return' &&
    ranking.status === 'loading' &&
    prev != null &&
    prev.tenor_weeks === requested.tenor_weeks &&
    prev.lookback_years === requested.years
      ? prev
      : null
  const data = ranking.status === 'ready' ? ranking.data : held
  const rows: RankedAsset[] = data == null ? [] : sort === 'return' ? bestReturnRows(data.ranked) : data.ranked

  // A name opened from the keyboard (a pick or a table row) re-screens, which
  // unmounts what was focused and drops focus to the page. When the screen
  // returns, focus goes back to the same control -- but only if focus is
  // still lost: a reader who has moved on (the rail, the expand button) keeps
  // their place. One attempt on the next screen shown, then the target is gone.
  const stripRef = useRef<HTMLDivElement>(null)
  const refocus = useRef<string | null>(null)
  useEffect(() => {
    if (refocus.current == null || data == null) return
    const target = refocus.current
    refocus.current = null
    const active = document.activeElement
    if (active != null && active !== document.body) return
    // preventScroll: the next Tab continues from the control used, but a
    // reader who scrolled down to the result is not pulled back up to it.
    stripRef.current?.querySelector<HTMLElement>(`[data-refocus="${target}"]`)?.focus({ preventScroll: true })
    // Keyed on the screen alone: a new screen is what the target waits for.
  }, [data])

  // Armed only from the keyboard: a mouse click also focuses the control,
  // and restoring focus then would scroll a reader who has moved on to the
  // result back up to the strip. `where` names the control: a pick or a row.
  const pick = (r: RankedAsset, byKeyboard = false, where: 'pick' | 'row' = 'row') => {
    // Only an open that re-screens unmounts what was focused: one that lands
    // on the screen already shown (or has no best cell) changes nothing, so
    // there is nothing to restore -- and an armed target would linger.
    const rescreens =
      r.best_moneyness_pct != null &&
      r.best_tenor_weeks != null &&
      (r.best_moneyness_pct !== requested.moneyness_pct || r.best_tenor_weeks !== requested.tenor_weeks)
    refocus.current = byKeyboard && rescreens ? `${where}-${r.asset}` : null
    onSelect(
      r.asset,
      r.best_moneyness_pct != null && r.best_tenor_weeks != null
        ? { moneyness_pct: r.best_moneyness_pct, tenor_weeks: r.best_tenor_weeks }
        : undefined,
    )
  }

  return (
    <div ref={stripRef}>
      <div className="pl-rank-strip" role="group" aria-label="Screen">
        <span className="pl-kicker">Screen · by {sort === 'return' ? 'best return' : 'fragility'}</span>
        <div className="pl-seg pl-rank-sort" role="radiogroup" aria-label="Sort the screen">
          {(['fragility', 'return'] as RankSort[]).map((s) => (
            <label className="pl-seg-opt" key={s}>
              <input type="radio" name={sortName} checked={sort === s} onChange={() => onSort(s)} />
              {s === 'fragility' ? 'Fragility' : 'Best return'}
            </label>
          ))}
        </div>

        {ranking.status === 'loading' && (
          <span className="pl-micro pl-rank-ref" role="status" aria-live="polite" data-testid="rank-status">
            {held
              ? 'Re-screening at the new strike — these best-return rows do not change with it.'
              : 'Screening the universe — one strike × tenor sweep per name…'}
          </span>
        )}
        {ranking.status === 'error' && (
          <span className="pl-micro pl-status-error" role="alert" data-testid="rank-status">
            {ranking.message}
          </span>
        )}
        {ranking.status === 'no-data' && (
          <span className="pl-micro pl-rank-ref" role="status" aria-live="polite" data-testid="rank-status">
            No screen for this window yet.
          </span>
        )}

        {data && rows.length === 0 && (
          <span className="pl-micro pl-rank-insample" data-testid="rank-empty">
            {sort === 'return'
              ? 'No name had enough history to score its grid.'
              : 'No name could be screened for this window.'}
          </span>
        )}
        {data &&
          rows.slice(0, 3).map((r, i) => (
            <button
              key={r.asset}
              type="button"
              className="pl-rank-pick"
              data-testid="rank-pick"
              data-refocus={`pick-${r.asset}`}
              // detail 0: activated from the keyboard (Enter or Space), not a pointer.
              onClick={(e) => pick(r, e.detail === 0, 'pick')}
            >
              <span className="pl-rank-n">{i + 1}</span> <strong>{r.asset.toUpperCase()}</strong>{' '}
              <span className={r.best_annualized == null ? 'pl-rank-at' : r.best_annualized >= 0 ? 'pl-pos' : 'pl-neg'}>
                {r.best_annualized == null ? '—' : fmtPct(r.best_annualized)}
              </span>{' '}
              <span className="pl-rank-at">{at(r)}</span>
              {r.best_priced_from === 'market' && (
                <span className="pl-tag pl-tag-ok pl-rank-pick-q" data-testid={`pick-basis-${r.asset}`}>
                  Market
                </span>
              )}
              {r.best_q_source === 'none' && (
                <span
                  className="pl-tag pl-tag-bad pl-rank-pick-q"
                  data-testid={`pick-q-${r.asset}`}
                  aria-label="q = 0: at least one roll at this best cell was priced with an unknown dividend yield, so it reads too cheap"
                  title="At least one roll at this best cell was priced at q = 0 (dividend yield unknown), so it reads too cheap"
                >
                  q = 0
                </span>
              )}
            </button>
          ))}
        {data && rows.length > 0 && (
          <span className="pl-micro pl-rank-insample" data-testid="rank-insample">
            in-sample best · not advice
          </span>
        )}
        {data && rows.length > 0 && (
          <button
            type="button"
            className="pl-btn pl-btn-ghost pl-rank-more"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Collapse the screen' : `All ${rows.length} name${rows.length === 1 ? '' : 's'}`}
          </button>
        )}

        {sort === 'fragility' && (
          <SignalLine
            signal={signal}
            screenShown={data != null && rows.length > 0}
            listAsOf={data?.as_of ?? null}
            onOpen={onOpenBakeOff}
          />
        )}
      </div>
      {/* The strike note stays up through the re-screen it warns about. */}
      {((data && rows.length > 0) || sort === 'return') && (
        <p className="pl-micro pl-rank-notes">
          {data && rows.length > 0 && (
            <span data-testid="rank-click-note">
              Opening a name loads its best cell into the controls (just the name, where no best cell was scored).
            </span>
          )}
          {sort === 'return' && (
            <span data-testid="rank-strike-note">
              {' '}
              {deltaMode
                ? 'Best-return rows don’t depend on the strike.'
                : 'Best-return rows don’t depend on the strike; changing the distance re-screens the universe.'}
            </span>
          )}
        </p>
      )}
      <div className="pl-rule-hair" />

      {open && data && rows.length > 0 && (
        <div style={{ padding: '4px 0 20px' }}>
          {sort === 'return' ? (
            <BestReturnTable data={data} rows={rows as BestRow[]} currentAsset={currentAsset} onPick={pick} />
          ) : (
            <FragilityTable data={data} currentAsset={currentAsset} deltaMode={deltaMode} onPick={pick} />
          )}
          <p className="pl-micro pl-rank-ref" style={{ padding: '4px 2px 0' }} data-testid="rank-provenance">
            As of {data.as_of} · {data.snapshot_ids.length} bronze snapshot
            {data.snapshot_ids.length === 1 ? '' : 's'} ·{' '}
            {data.code_sha === 'unknown' ? 'code sha unknown' : data.code_sha.slice(0, 7)}
          </p>
        </div>
      )}
    </div>
  )
}

/** The fragility order's full table. Every figure column sits under the
 *  strategy that produced it: the screened cell (the rail's strike and tenor,
 *  by distance) or the row's own best cell. */
function FragilityTable({
  data,
  currentAsset,
  deltaMode,
  onPick,
}: {
  data: PutLabLeaderboardResponse
  currentAsset: string
  deltaMode: boolean
  /** `byKeyboard`: opened with Enter/Space, so focus should come back to it. */
  onPick: (r: RankedAsset, byKeyboard?: boolean) => void
}) {
  // The "q = 0 rolls" tag's explanation is printed under the table, not in a
  // hover-only title, so keyboard, touch and screen-reader readers get it too.
  const qZeroNoteId = useId()
  const ranked = data.ranked
  const anyQZero = ranked.some((r) => r.q_source === 'none' || r.best_q_source === 'none')
  // Read from the response, like the shading: the legend must not keep its
  // own copy of which way is fragile.
  const fragileLow = METRICS.filter((m) => data.fragile_high[m.key] === false).map((m) => m.head)
  // While every row is model-priced (the ranking passes no real quotes yet)
  // a Basis column would repeat one word per row; one printed line says it,
  // and the column appears the day a row is market-priced.
  const anyMarket = ranked.some((r) => r.priced_from === 'market')
  const anyBestMarket = ranked.some((r) => r.best_priced_from === 'market')
  const screened = `At ${data.moneyness_pct}% · ${data.tenor_weeks}w`
  const shade = Object.fromEntries(
    METRICS.map((m) => [m.key, fragilityRank(ranked.map((r) => r[m.key]), data.fragile_high[m.key] ?? true)]),
  ) as Record<MetricKey, (number | null)[]>
  const screenedHeat = lossHeat(ranked.map((r) => r.annualized_return))
  const bestHeat = lossHeat(ranked.map((r) => r.best_annualized))
  const maxPayoff = Math.max(0, ...ranked.map((r) => r.biggest_payoff_mult))

  return (
    <>
      <div className="pl-scroll">
      <table className="pl-table pl-rank-table" aria-label="Full fragility ranking">
        <thead>
          <tr className="pl-rank-groups">
            <td colSpan={4} aria-hidden="true" />
            <Th concept="best_cell" colSpan={2} className="pl-rank-group">
              Best cell
            </Th>
            <Th concept="strike_rule" colSpan={anyMarket ? 6 : 5} className="pl-rank-group">
              {screened}
            </Th>
            <Th concept="fragility_score" colSpan={6} className="pl-rank-group">
              Fragility measures
            </Th>
          </tr>
          <tr>
            <th className="num" data-noexplain="index">
              #
            </th>
            <th data-noexplain="name">Name</th>
            <Th concept="spot" className="num">
              Spot
            </Th>
            <Th concept="fragility_score" className="num">
              Fragility
            </Th>
            <Th concept="annualized_return" className="num pl-rank-group-start" aria-label="Per year at best cell">
              Per year
            </Th>
            <Th concept="best_cell">At</Th>
            <Th
              concept="annualized_return"
              className="num pl-rank-group-start"
              aria-label={`Per year ${screened.toLowerCase()}`}
            >
              Per year
            </Th>
            <Th concept="hit_rate" className="num">
              Hit
            </Th>
            <Th concept="biggest_payoff" className="num">
              Biggest
            </Th>
            <Th concept="verdict" className="num">
              Verdict
            </Th>
            <Th concept="dividend_yield" className="num" style={{ textTransform: 'none' }}>
              q
            </Th>
            {anyMarket && (
              <Th concept="model_priced" className="num">
                Basis
              </Th>
            )}
            {METRICS.map((m) => (
              <Th key={m.key} concept={m.key} className="num">
                {m.head}
              </Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ranked.map((r, i) => (
            <tr
              key={r.asset}
              data-asset={r.asset}
              data-refocus={`row-${r.asset}`}
              className={`pick${r.asset === currentAsset ? ' is-current' : ''}`}
              tabIndex={0}
              aria-label={`Open ${r.asset.toUpperCase()}`}
              onClick={() => onPick(r)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onPick(r, true)
                }
              }}
            >
              <td className="num dimmer">{i + 1}</td>
              {/* Ticker only: the table is wider than a laptop's page, and the
                  pinned column's width decides how much of the strategy groups
                  fits. The full name is in the best-return table and the hover. */}
              <td className="nowrap" title={r.name}>
                <strong className="bold">{r.asset.toUpperCase()}</strong>
              </td>
              <td className="num dim">{fmtPrice(r.spot)}</td>
              <td
                className="num bold pl-rank-frag"
                title={METRICS.map((m) => `${m.head} ${num(r[m.key], m.digits)}`).join(' · ')}
              >
                {fragility(r.fragility_score)}
                {r.fragility_score != null && (
                  <span className="pl-mini-bar pl-frag-bar" style={{ width: `${Math.round(r.fragility_score * 36)}px` }} />
                )}
              </td>
              <td
                className={`num bold pl-rank-group-start${bestHeat[i] == null ? (r.best_annualized == null ? ' dim' : r.best_annualized >= 0 ? ' pl-pos' : ' pl-neg') : ' pl-heat-loss'}`}
                style={heatStyle(bestHeat[i])}
                data-testid={`best-ann-${r.asset}`}
              >
                {r.best_annualized == null ? '—' : fmtPct(r.best_annualized)}
                {/* The best cell's own bases (the q and Basis columns are the
                    screened cell's): flagged here, beside its own figure. */}
                {r.best_q_source === 'none' && (
                  <span className="pl-tag pl-tag-bad pl-cell-tag" aria-describedby={qZeroNoteId} data-testid={`best-cell-q-${r.asset}`}>
                    q = 0
                  </span>
                )}
                {r.best_priced_from === 'market' && (
                  <span className="pl-tag pl-tag-ok pl-cell-tag" data-testid={`best-cell-basis-${r.asset}`}>
                    Market
                  </span>
                )}
              </td>
              <td className="dimmer nowrap">{at(r)}</td>
              <td
                className={`num bold pl-rank-group-start${screenedHeat[i] == null ? (r.annualized_return >= 0 ? ' pl-pos' : ' pl-neg') : ' pl-heat-loss'}`}
                style={heatStyle(screenedHeat[i])}
                data-testid={`screened-ann-${r.asset}`}
              >
                {fmtPct(r.annualized_return)}
              </td>
              <td className="num dim" data-testid={`screened-hit-${r.asset}`}>
                {Math.round(r.hit_rate * 100)}%
              </td>
              <td
                className="num"
                aria-label={`Biggest payoff ${r.biggest_payoff_mult.toFixed(2)} times its premium`}
                data-testid={`screened-biggest-${r.asset}`}
              >
                {fmtMult(r.biggest_payoff_mult)}
                <span className="pl-mini-bar" style={{ width: `${biggestBarWidth(r.biggest_payoff_mult, maxPayoff)}px` }} />
              </td>
              <td className="num" data-testid={`screened-verdict-${r.asset}`}>
                <span className={VERDICT_TAG[r.verdict]} title={VERDICT_TITLE[r.verdict]}>
                  {r.verdict.replace('_', ' ')}
                </span>
              </td>
              <td className="num" data-testid={`q-source-${r.asset}`}>
                {r.q_source === 'none' ? (
                  <span className="pl-tag pl-tag-bad" aria-describedby={qZeroNoteId}>
                    q = 0 rolls
                  </span>
                ) : r.q_source === 'measured' ? (
                  <span className="pl-tag pl-tag-mute">measured</span>
                ) : (
                  '—'
                )}
              </td>
              {anyMarket && (
                <td className="num" data-testid={`basis-${r.asset}`}>
                  <span className={BASIS_TAG[r.priced_from]}>{BASIS_LABEL[r.priced_from]}</span>
                </td>
              )}
              {METRICS.map((m) => {
                const rank = shade[m.key][i]
                return (
                  <td
                    key={m.key}
                    className={`num${rank == null ? ' dim' : ' pl-shade-rank'}`}
                    style={rank == null ? undefined : ({ '--pl-r': rank } as CSSProperties)}
                    data-testid={`shade-${m.key}-${r.asset}`}
                  >
                    {num(r[m.key], m.digits)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {deltaMode && (
        <p className="pl-micro pl-rank-ref" style={{ padding: '6px 2px 0' }} data-testid="rank-screened-delta">
          The &ldquo;{screened}&rdquo; group is screened by distance, at the distance the rail held before it
          switched to delta: the ranking cannot screen by delta.
        </p>
      )}
      <p className="pl-micro pl-rank-legend" style={{ padding: '6px 2px 0' }} data-testid="rank-legend">
        Stronger shade = more fragile on that measure (each column ranked on its own
        {fragileLow.length > 0 && `; ${fragileLow.join(' and ')} ${fragileLow.length === 1 ? 'is' : 'are'} fragile when low`}). Magenta = a loss, deeper for a larger one. Biggest-payoff bars are on a log scale.
        {!anyMarket && !anyBestMarket && (
          <span data-testid="rank-model-priced"> Every figure is model-priced, not quoted.</span>
        )}
        {METRICS.map((m) => (
          <span key={m.key} className="pl-rank-legend-item">
            {m.head}
            <ConceptInfo id={m.key} />
          </span>
        ))}
      </p>
      {anyQZero && (
        <p id={qZeroNoteId} className="pl-micro pl-rank-ref" style={{ padding: '4px 2px 0' }} data-testid="q-zero-note">
          q = 0: at least one roll in that cell was priced at q = 0 because its dividend yield is unknown &mdash;{' '}
          {UNKNOWN_Q_CAUSES}. The tag sits beside the figure it applies to (a Best cell return, or the {screened}{' '}
          cell in the q column); read that figure as the old, cheap price.
        </p>
      )}
    </>
  )
}

/** One line on whether the fragility ordering has earned its keep: the
 *  out-of-sample signal test's verdict at fixed settings (`STRIP_SIGNAL_TEST`,
 *  docs/adr/0033). It states those settings and never compares them with the
 *  list's -- the rail's default is a distance, the test's a delta, so the two
 *  would never match. It does keep the same-day guard: a page left open past
 *  midnight can hold a list and a signal test from different days. */
function SignalLine({
  signal,
  screenShown,
  listAsOf,
  onOpen,
}: {
  signal: ResourceState<SignalTestResult>
  /** Whether a screen is on the page to compare with, or only asked for. */
  screenShown: boolean
  listAsOf: string | null
  onOpen: () => void
}) {
  const s = STRIP_SIGNAL_TEST
  const base = `Signal test · ${deltaLabel(s.target_delta)} · ${s.tenor_weeks}w · ${s.years}y · top ${s.top_k}`
  const link = (
    <button type="button" className="pl-btn pl-btn-ghost pl-rank-bakeoff-link" onClick={onOpen}>
      Bake-off →
    </button>
  )
  if (signal.status !== 'ready') {
    const text =
      signal.status === 'loading'
        ? 'Signal test: loading…'
        : signal.status === 'no-data'
          ? 'Signal test: no result for this window'
          : `Signal test unavailable: ${signal.message}`
    return (
      <p className="pl-micro pl-rank-bakeoff" data-testid="rank-signal">
        {text} {link}
      </p>
    )
  }
  const data = signal.data
  const v = data.verdict
  const which = screenShown ? 'the screen above' : 'the screen asked for'
  const sameDay = listAsOf == null || listAsOf === data.as_of
  return (
    <p className="pl-micro pl-rank-bakeoff" data-testid="rank-signal">
      <strong>{base}</strong>, out of sample, rebalanced {cadenceText(data.tenor_weeks)}:{' '}
      <span data-testid="rank-signal-status">{STATUS_LABEL[v.status]}.</span> {sentenceText(v)}{' '}
      {data.test_start != null && data.test_end != null && (
        <span data-testid="rank-signal-window">
          Test window {data.test_start} to {data.test_end}, {data.n_periods} periods.{' '}
        </span>
      )}
      {!sameDay && `From a different day (${data.as_of}) than ${which}. `}
      <span>
        As of {data.as_of} · {data.snapshot_ids.length} bronze snapshot{data.snapshot_ids.length === 1 ? '' : 's'} ·{' '}
        {data.code_sha === 'unknown' ? 'code sha unknown' : data.code_sha.slice(0, 7)}
      </span>{' '}
      {link}
    </p>
  )
}
