import type {
  AccuracyResponse,
  RegimeLabel,
  RegimeTimelineView,
  VixStretchResponse,
} from '../../../api/client'
import { ConceptInfo } from '../ConceptInfo'
import { fmtFixed } from '../format'
import type { ResourceState } from '../PutLab'

/* "What regime are we in?"
 *
 * The bands are VIX LEVELS -- calm below 17, elevated 17 to 28, crisis at 28 and
 * above -- not realized volatility. Copy that describes them as realized vol is
 * wrong, and this view is where a reader learns which one they are.
 *
 * The VIX-stretch reading (z-score, close, 20d mean, 20d std) is back. The old
 * stylesheet still carried a `.vix-panel .tile` rule with nothing rendering into
 * it; the numbers had quietly gone missing from the view they belong to.
 */

const REGIME_ORDER: RegimeLabel[] = ['calm', 'elevated', 'crisis']
const REGIME_FILL: Record<RegimeLabel, string> = {
  calm: 'var(--ink-25)',
  elevated: 'var(--warn-fill)',
  crisis: 'var(--mag)',
}

const signedPct = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(2)}%`

interface Props {
  regimes: RegimeTimelineView | null
  vix: VixStretchResponse | null
  accuracy: ResourceState<AccuracyResponse>
}

export function RegimeView({ regimes, vix, accuracy }: Props) {
  if (!regimes || regimes.segments.length === 0) {
    return (
      <p className="pl-status" role="status" aria-live="polite">
        Loading the regime timeline…
      </p>
    )
  }

  const total = Object.values(regimes.day_counts).reduce((a, b) => a + b, 0) || 1
  const first = regimes.segments[0]!.start
  const last = regimes.segments[regimes.segments.length - 1]!.end
  const residuals =
    accuracy.status === 'ready' ? accuracy.data.model.residual_by_regime : ({} as Record<string, number>)
  const longest = [...regimes.segments].sort((a, b) => b.n_days - a.n_days).slice(0, 5)

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>The backdrop</h2>
      <p className="pl-lede" style={{ marginBottom: 24 }}>
        Every verdict on this site is read against the market regime each roll was entered in. The
        bands are VIX levels &mdash; <strong>calm below 17</strong>, elevated from there to 28, and{' '}
        <strong>crisis at 28 and above</strong>.
        <ConceptInfo id="regime" />
      </p>

      <div className="pl-hero">
        <div>
          <div className="pl-kicker" style={{ marginBottom: 6 }}>
            Right now
          </div>
          <div className="pl-hero-fig">
            <span className="pl-regime-now">{regimes.current}</span>
            {vix && <span className="pl-hero-unit">VIX {vix.close.toFixed(1)}</span>}
          </div>
          <p className="pl-hero-sentence">
            Read as of {regimes.as_of}, over a window running {first} to {last}.
          </p>
        </div>

        {vix && (
          <section aria-label="VIX stretch">
            <dl className="pl-brokenout">
              <div className="pl-kicker">VIX stretch</div>
              <div className="pl-brokenout-row">
                <dt>z-score</dt>
                <dd>{fmtFixed(vix.z_score)}σ</dd>
              </div>
              <div className="pl-brokenout-row">
                <dt>Close</dt>
                <dd>{vix.close.toFixed(2)}</dd>
              </div>
              <div className="pl-brokenout-row">
                <dt>20d mean</dt>
                <dd>{vix.rolling_mean_20d.toFixed(2)}</dd>
              </div>
              <div className="pl-brokenout-row">
                <dt>20d std</dt>
                <dd>{vix.rolling_std_20d.toFixed(2)}</dd>
              </div>
            </dl>
          </section>
        )}
      </div>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>The timeline</h3>
        </div>
        <div
          className="pl-regime-strip"
          role="img"
          aria-label={`Regime history from ${first} to ${last}; currently ${regimes.current}`}
        >
          {regimes.segments.map((s) => (
            <span
              key={`${s.regime}-${s.start}`}
              style={{ flexGrow: s.n_days, background: REGIME_FILL[s.regime] }}
              title={`${s.regime}: ${s.start} → ${s.end} (${s.n_days} sessions)`}
            />
          ))}
        </div>
        <div className="pl-regime-axis">
          <span>{first}</span>
          <span>{last}</span>
        </div>
      </section>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>How the window split</h3>
        </div>
        <p className="pl-note" style={{ marginBottom: 14 }}>
          The residual beside each band is how far the model&rsquo;s price missed the published Cboe
          program in that regime. It <em>flips sign</em> in a crisis rather than ramping up from
          calm &mdash; the model is too cheap in quiet markets and too dear in a dislocation.
        </p>
        <div className="pl-regime-cards">
          {REGIME_ORDER.map((r) => {
            const days = regimes.day_counts[r] ?? 0
            const residual = residuals[r]
            return (
              <div className="pl-regime-card" key={r}>
                <div className="pl-kicker">
                  <span className="pl-dot" style={{ background: REGIME_FILL[r] }} />
                  {r}
                </div>
                <div className="pl-stat-v">{Math.round((days / total) * 100)}%</div>
                <div className="pl-stat-note">{days} sessions</div>
                <div className="pl-stat-note">
                  {residual === undefined ? 'residual unmeasured' : `${signedPct(residual)}/yr residual`}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <section className="pl-section">
        <div className="pl-section-head">
          <h3>Longest stretches</h3>
        </div>
        <div className="pl-scroll">
          <table className="pl-table" aria-label="Longest regime stretches">
            <thead>
              <tr>
                <th>Regime</th>
                <th>From</th>
                <th>To</th>
                <th className="num">Sessions</th>
              </tr>
            </thead>
            <tbody>
              {longest.map((s) => (
                <tr key={`${s.regime}-${s.start}`}>
                  <td className="nowrap">
                    <span className="pl-dot" style={{ background: REGIME_FILL[s.regime] }} />
                    {s.regime}
                  </td>
                  <td className="dim nowrap">{s.start}</td>
                  <td className="dim nowrap">{s.end}</td>
                  <td className="num bold">{s.n_days}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
