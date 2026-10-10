import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  fetchStrikePreview,
  type PutBacktestResponse,
  type RegimeLabel,
  type RegimeSegment,
  type StrikePreview,
} from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtPrice } from './format'
import { deltaLabel } from './types'

/* "How the strike is chosen": the second step of the thread -- where q comes
 * from -> how it moves the price -> HOW THE STRIKE IS CHOSEN -> whether any of
 * it earns a place in the book (docs/adr/0029).
 *
 * It shows the rule as a formula, then what the rule let move across these
 * rolls (a fixed distance lets the delta swing with the regime; a fixed delta
 * lets the distance swing instead), and today's strike two ways: the one the
 * backtest picks, at realised vol, beside the market's, on the chain's implied
 * vol. The gap between those two is the reason the label always says "at
 * realised vol".
 */

const pct = (x: number, digits = 1) => `${x.toFixed(digits)}%`
/** A distance from spot in words: a strike near 0.50 delta sits ABOVE spot. */
const fromSpot = (x: number, digits = 1) =>
  x >= 0 ? `${x.toFixed(digits)}% below spot` : `${(-x).toFixed(digits)}% above spot`

/* Previews keyed by what they depend on, kept across re-runs (the section
 * remounts on every one) so a tab of "Rolled over" changes refetches nothing. */
const PREVIEWS = new Map<string, StrikePreview>()
const PREVIEWS_MAX = 24

const STATUS_TEXT: Record<Exclude<StrikePreview['market_status'], 'quoted'>, string> = {
  not_collected: 'not collected: the daily chain sweep does not cover this name',
  no_expiry: 'no listed expiry reaches this tenor in the latest chain',
  no_iv: 'the exchange published no implied vol at that expiry',
}

function regimeOn(date: string, segments: RegimeSegment[]): RegimeLabel | null {
  const seg = segments.find((s) => s.start <= date && date <= s.end)
  return seg ? seg.regime : null
}

export function StrikeBuild({
  bt,
  regimes,
  modelPricedMaxMoneynessPct,
  open,
  onToggle,
}: {
  bt: PutBacktestResponse
  regimes: RegimeSegment[]
  modelPricedMaxMoneynessPct: number | null
  /** Held by the Workspace, which outlives a re-run: a control change must not
   *  close the section that shows what the change did. */
  open: boolean
  onToggle: (open: boolean) => void
}) {
  const byDelta = bt.strike_rule === 'delta'
  // The preview answers "today's strike for THIS run's delta": only a delta
  // run has one. A distance run never shows a delta it did not use.
  const target = bt.target_delta
  const key = target == null ? null : `${bt.asset}|${target}|${bt.tenor_weeks}`
  const [preview, setPreview] = useState<StrikePreview | null>(() => (key ? (PREVIEWS.get(key) ?? null) : null))
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // Cleared first, every time: a cached run keeps this section mounted, and
    // an old target's strikes must never sit under the new target's header.
    setPreview(key ? (PREVIEWS.get(key) ?? null) : null)
    setFailed(false)
    if (!open || key == null || target == null || PREVIEWS.has(key)) return
    const controller = new AbortController()
    fetchStrikePreview({ asset: bt.asset, target_delta: target, tenor_weeks: bt.tenor_weeks }, controller.signal)
      .then((p) => {
        if (PREVIEWS.size >= PREVIEWS_MAX) PREVIEWS.clear()
        PREVIEWS.set(key, p)
        setPreview(p)
      })
      .catch((err: unknown) => {
        if (!(err instanceof DOMException && err.name === 'AbortError')) setFailed(true)
      })
    return () => controller.abort()
  }, [open, key, bt.asset, target, bt.tenor_weeks])

  // What the rule let move: the delta under a fixed distance, the distance
  // under a fixed delta.
  const points = bt.cycles
    .map((c) => ({
      date: c.entry_date,
      value: byDelta ? c.entry_moneyness_pct : c.entry_delta == null ? null : -c.entry_delta,
      regime: regimeOn(c.entry_date, regimes),
    }))
    .filter((p): p is { date: string; value: number; regime: RegimeLabel | null } => p.value != null)
  const values = points.map((p) => p.value)
  const lo = values.length ? Math.min(...values) : 0
  const hi = values.length ? Math.max(...values) : 0
  // Two decimals for distances, so a narrow range never prints one number twice.
  const fmt = (v: number) => (byDelta ? fromSpot(v, 2) : v.toFixed(3))
  const share = bt.beyond_model_depth_share
  const deep = share == null ? 0 : Math.round(share * bt.cycles.length)

  return (
    <details
      className="pl-explain"
      data-testid="strike-build"
      open={open}
      onToggle={(e) => onToggle(e.currentTarget.open)}
    >
      <summary>How the strike is chosen</summary>
      {byDelta && target != null ? (
        <p data-testid="strike-build-rule">
          Each roll strikes where the put&rsquo;s <strong>delta</strong>
          <ConceptInfo id="put_delta" /> is &minus;{target.toFixed(2)}, solved from that day&rsquo;s 20-day realised
          volatility &mdash; so the strike follows the market instead of sitting a fixed distance below it
          <ConceptInfo id="strike_rule" />.
        </p>
      ) : (
        <p data-testid="strike-build-rule">
          Each roll strikes <strong>{bt.moneyness_pct}% below</strong> that day&rsquo;s spot
          <ConceptInfo id="strike_rule" />. Simple, but the same distance is a different option in a different
          market: in calm markets it is far out in the tail, in a crisis it is close to the money. Choosing the
          strike by <strong>delta</strong>
          <ConceptInfo id="put_delta" /> instead (&ldquo;Strike chosen by&rdquo; on the rail) holds the option fixed:
        </p>
      )}
      <p className="pl-explain-formula" data-testid="strike-build-formula">
        &Delta; = &minus;e<sup>&minus;qT</sup> N(&minus;d<sub>1</sub>), &nbsp; K = S &middot; exp(&minus;d<sub>1</sub>
        &sigma;&radic;T + (r &minus; q + &sigma;&sup2;/2)T), &nbsp; d<sub>1</sub> = &minus;N<sup>&minus;1</sup>(|&Delta;|e
        <sup>qT</sup>)
      </p>
      {byDelta && target != null && (
        <>
      <p>
        One convention everywhere: the Black&ndash;Scholes spot delta with the dividend yield q and the flat rate r
        the backtest prices with &mdash; for today&rsquo;s strikes below, today&rsquo;s q, which can differ from
        the latest roll&rsquo;s in the price above. Which &sigma; goes in decides everything, and the backtest has only realised vol for every
        past day &mdash; so its strike is &ldquo;{deltaLabel(target)}&rdquo;, never the desk&rsquo;s
        &ldquo;{Math.round(target * 100)}-delta put&rdquo;, which uses the market&rsquo;s implied vol. Implied sits above realised and is
        skewed, so the market&rsquo;s strike for the same delta lies further below spot:
      </p>

      <div className="pl-scroll">
        <table className="pl-table" aria-label="Today's strike two ways" data-testid="strike-preview">
          <thead>
            <tr>
              <th>Target delta &minus;{target.toFixed(2)}</th>
              <th className="num">Vol</th>
              <th className="num">Strike</th>
              <th className="num">From spot</th>
            </tr>
          </thead>
          <tbody>
            <tr data-testid="strike-preview-model">
              <th scope="row" className="pl-explain-rowhead">
                Backtest, realised vol
              </th>
              <td className="num">{preview?.model ? pct(preview.model.sigma * 100) : '—'}</td>
              <td className="num">{preview?.model ? fmtPrice(preview.model.strike) : '—'}</td>
              <td className="num">{preview?.model ? fromSpot(preview.model.moneyness_pct) : '—'}</td>
            </tr>
            <tr data-testid="strike-preview-market">
              <th scope="row" className="pl-explain-rowhead">
                Market, implied vol
              </th>
              <td className="num">{preview?.market ? pct(preview.market.iv * 100) : '—'}</td>
              <td className="num">{preview?.market ? fmtPrice(preview.market.strike) : '—'}</td>
              <td className="num">{preview?.market ? fromSpot(preview.market.moneyness_pct) : '—'}</td>
            </tr>
          </tbody>
        </table>
      </div>
      {preview && (
        <p className="pl-note" data-testid="strike-preview-inputs">
          Both rows use q = {(preview.q * 100).toFixed(3)}% ({preview.q_source.replace(/_/g, ' ')}, as of{' '}
          {preview.as_of}) and r = {(preview.r * 100).toFixed(3)}%.
        </p>
      )}
      {(preview?.model || preview?.market) && (
        <p className="pl-note" data-testid="strike-preview-dates">
          {preview.model && `Backtest: prices to ${preview.model.vol_date}. `}
          {preview.market && `Market: Cboe chain of ${preview.market.session}, expiry ${preview.market.expiration}.`}
        </p>
      )}
      {failed && <p className="pl-note">Today&rsquo;s strikes could not be loaded.</p>}
      {preview && preview.market_status !== 'quoted' && (
        <p className="pl-note" data-testid="strike-preview-status">
          Market strike {STATUS_TEXT[preview.market_status]}.
        </p>
      )}
      {preview?.market_is_older && (
        <p className="pl-note" data-testid="strike-preview-older">
          The chain&rsquo;s session ({preview.market?.session}) is older than the price data (
          {preview.model?.vol_date}): the two rows are not the same day.
        </p>
      )}
      {preview?.market && (
        <p className="pl-note">
          The market row is the listed put whose delta &mdash; this formula on its own implied vol &mdash; is
          nearest the target, at the first expiry {bt.tenor_weeks} week{bt.tenor_weeks === 1 ? '' : 's'} or more
          out (its delta:{' '}
          {preview.market.delta.toFixed(3)}). Not the exchange&rsquo;s own delta column, whose convention is not
          this one.
        </p>
      )}

        </>
      )}

      {points.length > 1 && (
        <>
          <p data-testid="strike-build-moves">
            {byDelta
              ? `Holding the delta fixed let the distance move: over these ${points.length} rolls the strike sat between ${fmt(lo)} and ${fmt(hi)}.`
              : `Holding the distance fixed let the delta move: over these ${points.length} rolls it ran from ${fmt(lo)} to ${fmt(hi)} at entry.`}{' '}
            The colours are the VIX regime bands, which measure implied vol; realised vol, which sets
            these strikes, broadly moves with it.
          </p>
          <MovesChart points={points} byDelta={byDelta} lo={lo} hi={hi} />
        </>
      )}
      {byDelta && deep > 0 && (
        <p className="pl-neg" data-testid="strike-build-depth">
          {deep} of these {bt.cycles.length} rolls struck deeper than
          {modelPricedMaxMoneynessPct != null ? ` ${modelPricedMaxMoneynessPct}%` : ' the model can price'} below
          spot, where the flat-vol premium is not a price (it is far too cheap): read those rolls&rsquo; payoffs as
          inflated.
        </p>
      )}
      <p className="pl-micro">
        Next: whether a put chosen either way earns a place in the portfolio &mdash; the Book.
      </p>
    </details>
  )
}

// The frame is drawn at the figure's MEASURED width, one unit to one pixel, so
// text and dots keep their CSS size at every width. A frame picked by viewport
// did not: a fixed 640 squeezed onto a phone shrank 11px labels to ~6px, and a
// 360 frame stretched across a 600-768px page blew them up to ~17-21px.
const FRAME_W = 640 // before the first measurement only
const MIN_W = 240
const H = 150
const PAD = { l: 44, r: 12, t: 10, b: 22 }
const REGIME_CLASS: Record<RegimeLabel, string> = {
  calm: 'pl-dot-calm',
  elevated: 'pl-dot-elevated',
  crisis: 'pl-dot-crisis',
}

function MovesChart({
  points,
  byDelta,
  lo,
  hi,
}: {
  points: { date: string; value: number; regime: RegimeLabel | null }[]
  byDelta: boolean
  lo: number
  hi: number
}) {
  const figRef = useRef<HTMLElement>(null)
  const [measured, setMeasured] = useState<number | null>(null)
  // A layout effect, so the first paint is already at the measured frame.
  useLayoutEffect(() => {
    const el = figRef.current
    if (!el) return
    setMeasured(el.getBoundingClientRect().width)
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setMeasured(entry.contentRect.width)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const W = Math.max(Math.round(measured || FRAME_W), MIN_W)
  const span = hi - lo || Math.abs(hi) || 1
  const y0 = lo - span * 0.1
  const y1 = hi + span * 0.1
  const x = (i: number) => PAD.l + (i / Math.max(points.length - 1, 1)) * (W - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b)
  // Ticks stay bare numbers to fit the axis, so the label says what a negative
  // one means rather than printing "-0.42% below spot".
  const above = byDelta && lo < 0
  const label = byDelta
    ? `Strike below spot at entry${above ? ' (negative: above spot)' : ''}`
    : 'Put delta at entry (absolute)'
  // The sentence's own precision (2 dp distances, 3 dp deltas), and one tick
  // when both ends print the same -- never one number twice.
  const tick = (v: number) => (byDelta ? `${v.toFixed(2)}%` : v.toFixed(3))
  const ticks = tick(lo) === tick(hi) ? [lo] : [lo, hi]
  return (
    <figure className="pl-figure" data-testid="strike-build-chart" ref={figRef}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}, one dot per roll, coloured by regime`}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line className="pl-grid" x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} />
            <text className="pl-axis" x={PAD.l - 6} y={y(v) + 4} textAnchor="end">
              {tick(v)}
            </text>
          </g>
        ))}
        {points.map((p, i) => (
          <circle
            key={p.date}
            cx={x(i)}
            cy={y(p.value)}
            r={3.5}
            className={p.regime ? REGIME_CLASS[p.regime] : 'pl-dot-none'}
          >
            <title>{`${p.date}: ${byDelta ? fromSpot(p.value, 2) : `delta ${p.value.toFixed(3)}`}${p.regime ? ` (${p.regime})` : ''}`}</title>
          </circle>
        ))}
        <text className="pl-axis" x={PAD.l} y={H - 4}>
          {points[0]?.date}
        </text>
        <text className="pl-axis" x={W - PAD.r} y={H - 4} textAnchor="end">
          {points[points.length - 1]?.date}
        </text>
      </svg>
      <figcaption className="pl-note">
        {label}, one dot per roll: <span className="pl-dot-key pl-dot-calm" /> calm{' '}
        <span className="pl-dot-key pl-dot-elevated" /> elevated <span className="pl-dot-key pl-dot-crisis" /> crisis
        {points.some((p) => p.regime == null) && (
          <>
            {' '}
            <span className="pl-dot-key pl-dot-none" /> no regime label
          </>
        )}
        .
      </figcaption>
    </figure>
  )
}
