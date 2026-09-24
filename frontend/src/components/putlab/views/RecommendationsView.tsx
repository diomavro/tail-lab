import type { PutLabLeaderboardResponse, RankedAsset, Verdict } from '../../../api/client'
import { fmtDollar, fmtPct } from '../format'
import type { ResourceState } from '../PutLab'
import type { PutLabControls } from '../types'

/* Every name's best strategy, ranked against each other.
 *
 * The ranking strip answers "which names are most fragile". This answers the
 * question after it: given that, what would you actually have bought, and how
 * did each of those trades compare? So it sorts on the strategy's own return,
 * not on fragility, and states the whole position — ticker, strike, and roll
 * cadence — rather than leaving the reader to reconstruct it.
 *
 * The discipline that makes the table honest: every figure on a row comes from
 * the SAME (strike, tenor) run. `RankedAsset` carries two sets of numbers — one
 * at whatever strike the rail is set to, one at the name's own argmax — and
 * pairing an argmax return with a screened-cell hit rate would describe two
 * different strategies on one line. Only `best_*` fields appear below.
 *
 * And the strikes are bounded: the argmax cannot land past the depth where the
 * flat-vol pricer stops producing a price (docs/adr/0018), so this view cannot
 * recommend a strategy whose return is a pricing artefact.
 */

const VERDICT_TAG: Record<Verdict, string> = {
  confirmed: 'pl-tag pl-tag-ok',
  regime_only: 'pl-tag pl-tag-mute',
  failed: 'pl-tag pl-tag-bad',
  untested: 'pl-tag pl-tag-outline',
}

/** The familiar name for a tenor, where one exists. Used as a gloss beside the
 *  week count rather than instead of it: "rolled every 4 weeks (monthly)" is
 *  actionable, and repeating the same phrase twice is not. */
function cadenceGloss(weeks: number): string {
  if (weeks === 1) return ' (weekly)'
  if (weeks === 4) return ' (monthly)'
  if (weeks === 12 || weeks === 13) return ' (quarterly)'
  return ''
}

/** How many strategies the exported schedule carries. Ten is a basket a person
 *  can actually hold and review, not the whole 70-name screen. */
const SCHEDULE_TOP_K = 10

interface Props {
  ranking: ResourceState<PutLabLeaderboardResponse>
  controls: PutLabControls
  onSelectAsset: (asset: string, best?: { moneyness_pct: number; tenor_weeks: number }) => void
}

export function RecommendationsView({ ranking, controls, onSelectAsset }: Props) {
  if (ranking.status !== 'ready') {
    return (
      <p className="pl-status" role="status" aria-live="polite">
        {ranking.status === 'error'
          ? ranking.message
          : ranking.status === 'no-data'
            ? 'No screen for this window yet.'
            : `Screening the universe — one strike × tenor sweep per name…`}
      </p>
    )
  }

  // Built from the screen that produced this table, so the export and the page
  // can never describe different runs.
  const scheduleHref =
    '/api/putlab/roll-schedule?' +
    new URLSearchParams({
      moneyness_pct: String(ranking.data.moneyness_pct),
      tenor_weeks: String(ranking.data.tenor_weeks),
      years: String(ranking.data.lookback_years),
      notional: String(ranking.data.notional),
      top_k: String(SCHEDULE_TOP_K),
    }).toString()

  const scored = ranking.data.ranked
    .filter((r): r is RankedAsset & { best_annualized: number } => r.best_annualized != null)
    .sort((a, b) => b.best_annualized - a.best_annualized)

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>Recommendations</h2>
      <p className="pl-lede" style={{ marginBottom: 10 }}>
        Each name&rsquo;s <strong>best</strong> put strategy over this window &mdash; the argmax of
        its own strike × tenor grid &mdash; ranked against every other name&rsquo;s. Every figure on
        a row is measured at that same strike and tenor, so a row describes one position rather
        than a blend of two.
      </p>
      <p className="pl-caveat pl-rec-caveat">
        This is a screen, <strong>not advice</strong>. Every number here is measured{' '}
        <strong>in sample</strong> &mdash; the parameters were chosen by looking at the same window
        they are scored on, which is the definition of overfitting a backtest. It is model-priced,
        not quoted. Read it as &ldquo;where has convexity been worth owning&rdquo;, never as
        &ldquo;what will pay off next&rdquo;.
      </p>

      <div className="pl-scroll">
        <table className="pl-table pl-rec-table" aria-label="Ranked strategies">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>The strategy</th>
              <th className="num">Per year</th>
              <th className="num">On premium</th>
              <th className="num">Hit</th>
              <th className="num">Rolls</th>
              <th className="num">Fragility</th>
              <th className="num">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {scored.map((r, i) => (
              <tr
                key={r.asset}
                className={`pick${r.asset === controls.asset ? ' is-current' : ''}`}
                onClick={() =>
                  onSelectAsset(
                    r.asset,
                    r.best_moneyness_pct != null && r.best_tenor_weeks != null
                      ? { moneyness_pct: r.best_moneyness_pct, tenor_weeks: r.best_tenor_weeks }
                      : undefined,
                  )
                }
              >
                <td className="num dimmer">{i + 1}</td>
                <td>
                  <div className="pl-rec-name">
                    <strong>{r.asset.toUpperCase()}</strong> <span className="dimmer">{r.name}</span>
                  </div>
                  <div className="pl-rec-recipe">
                    Buy puts <span className="pl-rec-strike">{r.best_moneyness_pct}%</span> out of
                    the money, rolled every {r.best_tenor_weeks} weeks
                    <span className="dimmer">{cadenceGloss(r.best_tenor_weeks!)}</span>, held to
                    expiry.
                  </div>
                </td>
                <td className={`num bold pl-rec-ann ${r.best_annualized >= 0 ? 'pl-pos' : 'pl-neg'}`}>
                  {fmtPct(r.best_annualized)}
                </td>
                <td
                  className={`num dim pl-rec-premium ${
                    (r.best_roi_on_premium ?? 0) >= 0 ? 'pl-pos' : 'pl-neg'
                  }`}
                >
                  {r.best_roi_on_premium == null ? '—' : fmtPct(r.best_roi_on_premium)}
                  {/* The % alone hides the stake behind it -- two rows can show
                      the same return on a $1,000 budget and a $17,000 one.
                      best_n_cycles is measured at the same cell as
                      best_roi_on_premium, so it is the right multiplier
                      (AGENT_TODO.md, "roi_on_premium needs its denominator
                      named on the surface"). */}
                  {r.best_n_cycles != null && (
                    <div className="pl-micro">{fmtDollar(ranking.data.notional * r.best_n_cycles)}</div>
                  )}
                </td>
                <td className="num dim pl-rec-hit">
                  {r.best_hit_rate == null ? '—' : `${Math.round(r.best_hit_rate * 100)}%`}
                </td>
                <td className="num dim pl-rec-rolls">{r.best_n_cycles ?? '—'}</td>
                {/* A mean fractional rank in 0..1, scaled to read as a score;
                    rounding it unscaled puts the whole universe on 0 and 1. */}
                <td className="num dim pl-rec-frag">
                  {r.fragility_score == null ? '—' : Math.round(r.fragility_score * 100)}
                </td>
                <td className="num pl-rec-verdict">
                  {r.best_verdict == null ? (
                    '—'
                  ) : (
                    <span className={VERDICT_TAG[r.best_verdict]}>
                      {r.best_verdict.replace('_', ' ')}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pl-two-up" style={{ marginTop: 30 }}>
        <div>
          <div className="pl-kicker" style={{ marginBottom: 5 }}>
            How to read a row
          </div>
          <p className="pl-note">
            <strong>Per year</strong> is the geometric annualization of the return on premium over
            the {ranking.data.lookback_years}-year window. <strong>Hit</strong> is the share of
            rolls whose payoff cleared the premium budget &mdash; a tail hedge with a 6% hit rate
            and a positive return is working exactly as intended. <strong>Verdict</strong> counts
            how many VIX regimes the strategy was entered in and paid off in: two or more is{' '}
            <em>confirmed</em>, one is <em>regime only</em> &mdash; a bet on that regime recurring
            rather than a standalone edge.
          </p>
        </div>
        <div>
          <div className="pl-kicker" style={{ marginBottom: 5 }}>
            Why no strike is deeper than {ranking.data.moneyness_pct > 0 ? '10%' : '10%'}
          </div>
          <p className="pl-note">
            Past roughly 10% out of the money the flat-volatility pricer here reports these puts as
            nearly free &mdash; against real quotes the market charged up to 21,663× the
            model&rsquo;s premium at 20% OOM. A strategy picked from that region wins by exploiting
            the pricer, not the market, so the search is bounded to strikes where the premium is a
            price. The strike × tenor grid still shows the deep cells, hatched.
          </p>
        </div>
      </div>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>Take it away</h3>
        </div>
        <p className="pl-note" style={{ marginBottom: 12 }}>
          Click any row to open it in the Workspace at exactly these parameters. Or export the
          whole set as order intent: ticker, target strike, target expiry, and the premium{' '}
          <strong>budget</strong> per roll &mdash; sized in budget rather than contracts, because
          the model&rsquo;s premium is not the market&rsquo;s and the executor is the only thing
          that can see the real quote.
        </p>
        <p className="pl-note" style={{ marginBottom: 12 }}>
          tail-lab <strong>never places an order</strong> and holds no brokerage credential
          (adr/0007). The schedule is the artefact that crosses that line; whatever executes it
          &mdash; your own hand, or a process you control &mdash; lives outside this platform.
        </p>
        <a className="pl-btn pl-btn-secondary" href={scheduleHref} target="_blank" rel="noreferrer">
          Export the roll schedule
        </a>
      </section>
    </div>
  )
}
