import { useMemo, useState } from 'react'
import {
  ApiError,
  fetchMetricScreen,
  type MetricScreenComparison,
  type MetricScreenEntry,
  type Verdict,
} from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtDollar, fmtFixed, fmtPct } from './format'
import type { PutLabControls } from './types'

/* The metric bake-off — the panel the API already served and nothing called.
 *
 * `fetchMetricScreen` exists in api/client.ts and was never invoked anywhere in
 * the app, so `spearman_vs_payoff`, `lift_vs_baseline` and
 * `combined_max_drawdown` were computed server-side and thrown away. This
 * surfaces them.
 *
 * The question it answers sits UPSTREAM of the fragility ranking: the ranking
 * assumes these metrics are worth ranking on, and this tests that assumption by
 * holding an equal-weight basket on each screen's top K and comparing, all from
 * one shared backtest pass.
 *
 * The in-sample caveat is deliberately above the table and in the loss colour,
 * not in a footnote. concepts.ts `in_sample` is explicit that reading a bake-off
 * winner as a forward signal is the exact overreach the caveat exists to block,
 * and a caveat placed after the number it qualifies has already lost.
 *
 * One backtest per screened name runs server-side, so it stays an explicit
 * action rather than firing on every control change.
 */

type State =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: MetricScreenComparison }

const TOP_K = [3, 5, 8]

const VERDICT_TAG: Record<Verdict, string> = {
  confirmed: 'pl-tag pl-tag-ok',
  regime_only: 'pl-tag pl-tag-mute',
  failed: 'pl-tag pl-tag-bad',
  untested: 'pl-tag pl-tag-outline',
}

/** Lift is a difference of two rates, so it prints in points, not percent. */
function signedPts(x: number): string {
  return `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(1)}`
}

/** Raw-vs-corrected significance, so an uncorrected "winner" can't be read at
 * face value: `significant_corrected` is the same Spearman test after a
 * Benjamini-Hochberg FDR correction across every screen in the comparison
 * (Harvey, Liu & Zhu 2016) -- a screen that only clears the uncorrected hurdle
 * is exactly the false-discovery risk the correction exists to catch. */
function significanceTag(e: MetricScreenEntry): { cls: string; text: string; title: string } {
  if (e.spearman_pvalue == null) {
    return { cls: 'pl-tag pl-tag-outline', text: '—', title: 'No p-value (fewer than 3 usable pairs)' }
  }
  const p = `p = ${fmtFixed(e.spearman_pvalue, 3)}`
  if (e.significant_corrected) {
    return { cls: 'pl-tag pl-tag-ok', text: 'FDR-sig.', title: `${p} — survives Benjamini-Hochberg correction` }
  }
  if (e.significant_raw) {
    return {
      cls: 'pl-tag pl-tag-mute',
      text: 'raw only',
      title: `${p} — clears the uncorrected hurdle alone, not after FDR correction`,
    }
  }
  return { cls: 'pl-tag pl-tag-outline', text: 'n.s.', title: p }
}

export function BakeOff({ controls }: { controls: PutLabControls }) {
  const [topK, setTopK] = useState(5)
  const [state, setState] = useState<State>({ status: 'idle' })

  const run = () => {
    setState({ status: 'loading' })
    fetchMetricScreen({
      moneyness_pct: controls.moneyness_pct,
      tenor_weeks: controls.tenor_weeks,
      years: controls.years,
      top_k: topK,
    })
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) =>
        setState({
          status: 'error',
          message:
            err instanceof ApiError
              ? `The metric-screen failed: ${err.status}. Nothing to compare yet.`
              : err instanceof Error
                ? err.message
                : String(err),
        }),
      )
  }

  const rows = useMemo(() => {
    if (state.status !== 'ready') return []
    return [...state.data.entries].sort((a, b) => b.roi_on_premium - a.roi_on_premium)
  }, [state])

  const winner = rows[0]
  const data = state.status === 'ready' ? state.data : null

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>The bake-off</h2>
      <p className="pl-lede" style={{ marginBottom: 10 }}>
        The fragility ranking assumes these metrics are worth ranking on. This tests the prior
        question: which one actually sorted realized put payoffs over the lookback. Each screen
        holds an equal-weight basket on its own top K most fragile names, from one shared backtest
        pass.
      </p>
      <p className="pl-caveat pl-bake-caveat">
        In sample, and cross-sectional. The metric and the payoff it is judged against are computed
        over the same window, so a winner here is a screen chooser &mdash; not a forward signal.
        Quoting it as proof the strategy works going forward is the exact overreach this caveat
        exists to block.
        <ConceptInfo id="in_sample" />
      </p>

      <div className="pl-bake-controls">
        <span className="pl-kicker">Basket size</span>
        <div className="pl-seg" role="radiogroup" aria-label="Basket size">
          {TOP_K.map((k) => (
            <label className="pl-seg-opt" key={k}>
              <input type="radio" name="pl-bakek" checked={topK === k} onChange={() => setTopK(k)} />
              Top {k}
            </label>
          ))}
        </div>
        <button
          type="button"
          className="pl-btn pl-btn-primary"
          onClick={run}
          disabled={state.status === 'loading'}
        >
          {state.status === 'loading' ? 'Screening…' : 'Run the bake-off'}
        </button>
        <span className="pl-micro">
          {controls.moneyness_pct}% OOM · {controls.tenor_weeks}wk · {controls.years}y
        </span>
      </div>

      {state.status === 'error' && (
        <p className="pl-status pl-status-error" role="alert">
          {state.message}
        </p>
      )}
      {state.status === 'idle' && (
        <p className="pl-status">
          One backtest per screened name runs server-side, so this is an explicit action rather
          than a live recompute.
        </p>
      )}

      {data && winner && (
        <>
          <p className="pl-bake-verdict">
            {winner.roi_on_premium > data.baseline_roi ? (
              <>
                <strong>{winner.label}</strong> sorted this window&rsquo;s payoffs best: a top-
                {data.top_k} basket returned {fmtPct(winner.roi_on_premium)} on premium,{' '}
                {signedPts(winner.lift_vs_baseline)} points above buying puts on all{' '}
                {data.universe_size} names. That makes it the screen to rank on &mdash; for this
                window, in sample.
              </>
            ) : (
              <>
                No screen beat the buy-everything baseline of {fmtPct(data.baseline_roi)} over this
                window. Selecting on fragility added nothing here, which is the honest read: the
                ranking earned its keep only if lift is positive.
              </>
            )}
          </p>

          <div className="pl-scroll">
            <table className="pl-table" aria-label="Metric bake-off">
              <thead>
                <tr>
                  <th className="num">#</th>
                  <th>Screen</th>
                  <th>Its top names</th>
                  <th className="num">ROI</th>
                  <th className="num">/yr</th>
                  <th className="num">Hit</th>
                  <th className="num">Bleed</th>
                  <th className="num">Spearman</th>
                  <th className="num">Sig.</th>
                  <th className="num">Lift</th>
                  <th className="num">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e, i) => (
                  <tr key={e.metric} className={i === 0 ? 'is-winner' : undefined}>
                    <td className="num dimmer">{i + 1}</td>
                    <td className="nowrap">
                      <strong className="bold">{e.label}</strong>
                    </td>
                    <td className="dim pl-bake-names">
                      {e.top_k_assets.map((a) => a.toUpperCase()).join(' · ')}
                    </td>
                    <td className={`num bold pl-bake-roi ${e.roi_on_premium >= 0 ? 'pl-pos' : 'pl-neg'}`}>
                      {fmtPct(e.roi_on_premium)}
                    </td>
                    <td className="num dim">{fmtPct(e.annualized_return)}</td>
                    <td className="num dim">{Math.round(e.hit_rate * 100)}%</td>
                    <td className="num dim">{fmtDollar(e.combined_max_drawdown)}</td>
                    <td
                      className={`num ${
                        e.spearman_vs_payoff != null && e.spearman_vs_payoff >= 0 ? 'pl-pos' : 'pl-neg'
                      }`}
                    >
                      {e.spearman_vs_payoff == null ? '—' : fmtFixed(e.spearman_vs_payoff)}
                    </td>
                    <td className="num">
                      {(() => {
                        const tag = significanceTag(e)
                        return (
                          <span className={tag.cls} title={tag.title}>
                            {tag.text}
                          </span>
                        )
                      })()}
                    </td>
                    <td className={`num bold ${e.lift_vs_baseline >= 0 ? 'pl-pos' : 'pl-neg'}`}>
                      {signedPts(e.lift_vs_baseline)}
                    </td>
                    <td className="num">
                      <span className={VERDICT_TAG[e.verdict]}>{e.verdict.replace('_', ' ')}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pl-two-up pl-bake-notes">
            <div>
              <div className="pl-kicker" style={{ marginBottom: 5 }}>
                The baseline
              </div>
              <p className="pl-note">
                Buying puts on every one of the {data.universe_size} screened names, equal weight,
                returned <strong>{fmtPct(data.baseline_roi)}</strong> on premium. Lift is each
                screen&rsquo;s basket above that &mdash; it isolates selection skill from whether
                puts happened to work at all.
              </p>
            </div>
            <div>
              <div className="pl-kicker" style={{ marginBottom: 5 }}>
                Reading Spearman
              </div>
              <p className="pl-note">
                Beyond the top K, Spearman asks whether the screen ordered the <em>whole</em>{' '}
                universe sensibly: +1 a perfect ordering, 0 none, negative means it sorted the wrong
                way. A screen can win its basket and still order badly.
              </p>
            </div>
            <div>
              <div className="pl-kicker" style={{ marginBottom: 5 }}>
                Reading Sig.
                <ConceptInfo id="multiple_testing" />
              </div>
              <p className="pl-note">
                Testing {data.n_comparisons} screens against one window is a multiple-comparisons
                problem: a per-test hurdle of p &lt; {data.fdr_alpha} names a winner far more often
                than it should. <strong>FDR-sig.</strong> survives a Benjamini-Hochberg correction
                across all {data.n_comparisons}; <strong>raw only</strong> clears the uncorrected
                hurdle alone and should not be called a real winner.
              </p>
            </div>
            <div>
              <div className="pl-kicker" style={{ marginBottom: 5 }}>
                Why co-kurtosis competes
              </div>
              <p className="pl-note">
                It measures co-movement with the market&rsquo;s own tails, which flatters broad
                indices &mdash; backwards for a single-name screen. It runs here for completeness
                but is structurally excluded from the composite.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
