import { type CSSProperties, useId, useState } from 'react'
import type { MetricScreenComparison, PutLabLeaderboardResponse, RankedAsset, Verdict } from '../../api/client'
import { CONCEPTS, UNKNOWN_Q_CAUSES } from '../../content/concepts'
import { BestReturnTable } from './BestReturnTable'
import { type BestRow, bestReturnRows, VERDICT_TITLE } from './rankOrder'
import { ConceptInfo } from './ConceptInfo'
import { fmtFixed, fmtMult, fmtPct, fmtPrice } from './format'
import type { ResourceState } from './PutLab'
import { biggestBarWidth, fragilityRank, lossHeat } from './rankShade'
import { type RankSort, STRIP_BAKEOFF } from './types'

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
 * bake-off line never vanish with it; only the picks, the expand button and
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

/** A level to one decimal, so a small gap between two levels stays visible. */
const fmtPct1 = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)}%`

const heatStyle = (d: number | null | undefined): CSSProperties | undefined =>
  d == null ? undefined : ({ '--pl-d': d } as CSSProperties)

interface ScreenAxes {
  moneyness_pct: number
  tenor_weeks: number
  years: number
}

interface Props {
  ranking: ResourceState<PutLabLeaderboardResponse>
  bakeoff: ResourceState<MetricScreenComparison>
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
  bakeoff,
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

  const data = ranking.status === 'ready' ? ranking.data : null
  const rows: RankedAsset[] = data == null ? [] : sort === 'return' ? bestReturnRows(data.ranked) : data.ranked

  const pick = (r: RankedAsset) =>
    onSelect(
      r.asset,
      r.best_moneyness_pct != null && r.best_tenor_weeks != null
        ? { moneyness_pct: r.best_moneyness_pct, tenor_weeks: r.best_tenor_weeks }
        : undefined,
    )

  const axes: ScreenAxes = data
    ? { moneyness_pct: data.moneyness_pct, tenor_weeks: data.tenor_weeks, years: data.lookback_years }
    : requested
  const sameAsBakeoff =
    axes.moneyness_pct === STRIP_BAKEOFF.moneyness_pct &&
    axes.tenor_weeks === STRIP_BAKEOFF.tenor_weeks &&
    axes.years === STRIP_BAKEOFF.years

  return (
    <>
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
            Screening the universe &mdash; one strike × tenor sweep per name…
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
            <button key={r.asset} type="button" className="pl-rank-pick" data-testid="rank-pick" onClick={() => pick(r)}>
              <span className="pl-rank-n">{i + 1}</span> <strong>{r.asset.toUpperCase()}</strong>{' '}
              <span className={r.best_annualized == null ? 'pl-rank-at' : r.best_annualized >= 0 ? 'pl-pos' : 'pl-neg'}>
                {r.best_annualized == null ? '—' : fmtPct(r.best_annualized)}
              </span>{' '}
              <span className="pl-rank-at">{at(r)}</span>
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
          <BakeoffLine
            bakeoff={bakeoff}
            sameAsList={sameAsBakeoff}
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
    </>
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
  onPick: (r: RankedAsset) => void
}) {
  // The "q = 0 rolls" tag's explanation is printed under the table, not in a
  // hover-only title, so keyboard, touch and screen-reader readers get it too.
  const qZeroNoteId = useId()
  const ranked = data.ranked
  const anyQZero = ranked.some((r) => r.q_source === 'none')
  // Read from the response, like the shading: the legend must not keep its
  // own copy of which way is fragile.
  const fragileLow = METRICS.filter((m) => data.fragile_high[m.key] === false).map((m) => m.head)
  // While every row is model-priced (the ranking passes no real quotes yet)
  // a Basis column would repeat one word per row; one printed line says it,
  // and the column appears the day a row is market-priced.
  const anyMarket = ranked.some((r) => r.priced_from === 'market')
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
            <th colSpan={4} />
            <th colSpan={2} className="pl-rank-group" title="Each name's own best cell over its strike × tenor grid">
              Best cell
            </th>
            <th
              colSpan={anyMarket ? 6 : 5}
              className="pl-rank-group"
              title={
                deltaMode
                  ? 'The last distance below spot the rail held, and its tenor: the ranking screens by distance only'
                  : "The rail's strike and tenor, screened by distance below spot"
              }
            >
              {screened}
            </th>
            <th colSpan={6} className="pl-rank-group" title="Each measure ranked across the screen on its own">
              Fragility measures
            </th>
          </tr>
          <tr>
            <th className="num">#</th>
            <th>Name</th>
            <th className="num">Spot</th>
            <th
              className="num"
              title="Composite fragility, 0-100: the mean of the name's rank on each composite measure (100 = the most fragile on every one)"
            >
              Fragility
            </th>
            <th className="num pl-rank-group-start" aria-label="Per year at best cell">
              Per year
            </th>
            <th>At</th>
            <th className="num pl-rank-group-start" aria-label={`Per year ${screened.toLowerCase()}`}>
              Per year
            </th>
            <th className="num">Hit</th>
            <th className="num" title="The largest single roll's payoff, as a multiple of its premium">
              Biggest
            </th>
            <th className="num">Verdict</th>
            <th
              className="num"
              title="Whether every roll was priced with the name's own dividend yield, or some at an unknown q = 0"
              style={{ textTransform: 'none' }}
            >
              q
            </th>
            {anyMarket && (
              <th className="num" title="Whether this row's ROI came from the pricing model or a real listed quote">
                Basis
              </th>
            )}
            {METRICS.map((m) => (
              <th key={m.key} className="num" title={CONCEPTS[m.key]?.short}>
                {m.head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ranked.map((r, i) => (
            <tr
              key={r.asset}
              className={`pick${r.asset === currentAsset ? ' is-current' : ''}`}
              tabIndex={0}
              aria-label={`Open ${r.asset.toUpperCase()}`}
              onClick={() => onPick(r)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onPick(r)
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
        {!anyMarket && <span data-testid="rank-model-priced"> Every row is model-priced, not quoted.</span>}
        {METRICS.map((m) => (
          <span key={m.key} className="pl-rank-legend-item">
            {m.head}
            <ConceptInfo id={m.key} />
          </span>
        ))}
      </p>
      {anyQZero && (
        <p id={qZeroNoteId} className="pl-micro pl-rank-ref" style={{ padding: '4px 2px 0' }} data-testid="q-zero-note">
          q = 0 rolls: at least one roll was priced at q = 0 because its dividend yield is unknown &mdash;{' '}
          {UNKNOWN_Q_CAUSES}. Read that row&rsquo;s return as the old, cheap price.
        </p>
      )}
    </>
  )
}

/** One line on whether the fragility ordering has earned its keep: the
 *  bake-off's composite screen at fixed settings (`STRIP_BAKEOFF`). It states
 *  those settings, and says when they are not the list's -- it is a verdict on
 *  the composite screen, not on the list above it. */
function BakeoffLine({
  bakeoff,
  sameAsList,
  screenShown,
  listAsOf,
  onOpen,
}: {
  bakeoff: ResourceState<MetricScreenComparison>
  sameAsList: boolean
  /** Whether a screen is on the page to compare with, or only asked for. */
  screenShown: boolean
  /** The shown screen's as-of: a page left open across midnight can hold a
   *  list and a bake-off from different days. */
  listAsOf: string | null
  onOpen: () => void
}) {
  const b = STRIP_BAKEOFF
  const base = `Bake-off · ${b.moneyness_pct}% · ${b.tenor_weeks}w · ${b.years}y · by distance · top ${b.top_k}`
  const link = (
    <button type="button" className="pl-btn pl-btn-ghost pl-rank-bakeoff-link" onClick={onOpen}>
      Bake-off →
    </button>
  )
  if (bakeoff.status !== 'ready') {
    const text =
      bakeoff.status === 'loading'
        ? 'Bake-off: loading…'
        : bakeoff.status === 'no-data'
          ? 'Bake-off: no screen for this window'
          : `Bake-off unavailable: ${bakeoff.message}`
    return (
      <p className="pl-micro pl-rank-bakeoff" data-testid="rank-bakeoff">
        {text} {link}
      </p>
    )
  }
  const data = bakeoff.data
  const composite = data.entries.find((e) => e.metric === 'fragility_score')
  const n = data.universe_size
  // Both levels, not only the gap: a "beat" between two losing baskets reads
  // as a gain. Under half a tenth of a point the gap rounds away: "matched".
  const levels = composite
    ? `returned ${fmtPct1(composite.roi_on_premium)} on premium against ${fmtPct1(data.baseline_roi)} for buying all ${n}`
    : ''
  // The gap between the two PRINTED levels, so the sentence's own figures add
  // up: rounding each level and the raw lift separately can disagree by 0.1.
  const r1 = (x: number) => Math.round(x * 1000) / 10
  const gapPts = composite ? r1(composite.roi_on_premium) - r1(data.baseline_roi) : 0
  const gap = Math.abs(gapPts).toFixed(1)
  // With no more scored names than the basket holds, the top K IS everyone:
  // there is no selection to measure, and "beat/matched buying all N" would
  // describe comparing a basket with itself.
  const liftText =
    composite == null
      ? 'This bake-off carried no composite row.'
      : b.top_k >= n
      ? `It scored only ${n} name${n === 1 ? '' : 's'}, so its top ${b.top_k} is all of them: no selection to measure.`
      : Math.abs(gapPts) < 0.05
      ? `The composite screen's top ${b.top_k} ${levels}: no difference.`
      : `The composite screen's top ${b.top_k} ${levels}: ${gap} pts ${gapPts > 0 ? 'better' : 'worse'}.`
  // The p-value is two-sided, so "significant" alone would endorse a screen
  // that ordered names significantly the WRONG way: the sign is stated.
  const rho = composite?.spearman_vs_payoff
  const sigText =
    composite == null || composite.spearman_pvalue == null || rho == null
      ? 'Its correlation with payoff could not be tested (too few names, or no variation to rank).'
      : `Its ranking's correlation with payoff (ρ = ${fmtFixed(rho)}) is ${composite.significant_corrected ? '' : 'not '}significant after FDR correction across ${data.n_comparisons} screens${composite.significant_corrected && rho < 0 ? ': it ordered names the wrong way, more fragile paying less' : ''}.`
  const which = screenShown ? 'the screen above' : 'the screen asked for'
  const sameDay = listAsOf == null || listAsOf === data.as_of
  const scope = !sameDay
    ? `From a different day (${data.as_of}) than ${which}.`
    : sameAsList
      ? `Same settings as ${which}; its universe can differ.`
      : `Not the settings of ${which}.`
  return (
    <p className="pl-micro pl-rank-bakeoff" data-testid="rank-bakeoff">
      <strong>{`${base} of ${n}`}</strong>: {liftText} {sigText} {scope}{' '}
      <span>
        As of {data.as_of} · {data.snapshot_ids.length} bronze snapshot{data.snapshot_ids.length === 1 ? '' : 's'} ·{' '}
        {data.code_sha === 'unknown' ? 'code sha unknown' : data.code_sha.slice(0, 7)}
      </span>{' '}
      {link}
    </p>
  )
}
