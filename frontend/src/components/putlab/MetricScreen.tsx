import { useState } from 'react'
import {
  ApiError,
  fetchMetricScreen,
  type MetricScreenEntry,
  type RegimeSlice,
  type Verdict,
} from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtDollar, fmtPct } from './format'
import type { PutLabControls } from './types'

// The metric bake-off: hold an equal-weight OOM-put basket on each fragility
// metric's most-fragile names and see which basket earned the best put return.
// It answers "which sensitivity metric is worth ranking on" (docs/END_STATE.md
// §4 Q1). HONESTY: this is an in-sample cross-sectional association over the
// historical lookback — a screen CHOOSER, not a forward guarantee. Runs ~35
// backtests server-side, so it's an explicit action, not auto-fetch.

const TOP_K = 5

type State =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; entries: MetricScreenEntry[]; baselineRoi: number; universeSize: number }

const VERDICT_LABEL: Record<Verdict, string> = {
  confirmed: 'confirmed',
  regime_only: 'regime only',
  failed: 'failed',
  untested: 'untested',
}

// "calm +12% · elevated −40% · crisis +180%" for the verdict-cell tooltip.
const regimeTooltip = (slices: RegimeSlice[]): string =>
  slices.map((s) => `${s.regime} ${fmtPct(s.roi_on_premium)}`).join(' · ')

export function MetricScreen({ controls }: { controls: PutLabControls }) {
  const [state, setState] = useState<State>({ status: 'idle' })

  const run = () => {
    setState({ status: 'loading' })
    fetchMetricScreen({
      moneyness_pct: controls.moneyness_pct,
      tenor_weeks: controls.tenor_weeks,
      years: controls.years,
      top_k: TOP_K,
    })
      .then((resp) =>
        setState({
          status: 'ready',
          entries: resp.entries,
          baselineRoi: resp.baseline_roi,
          universeSize: resp.universe_size,
        }),
      )
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? `Bake-off failed (${err.status})`
            : err instanceof Error
              ? err.message
              : String(err)
        setState({ status: 'error', message })
      })
  }

  return (
    <section className="panel" style={{ marginTop: 22 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">
            The metric bake-off
            <ConceptInfo id="metric_bakeoff" />
          </span>
          <h2 style={{ marginTop: 6 }}>Which fragility metric picks the best puts?</h2>
          <div className="hint">
            For each fragility metric, hold an equal-weight basket of{' '}
            <span className="mono">
              {controls.moneyness_pct}% OOM &middot; {controls.tenor_weeks}-week
            </span>{' '}
            puts on its <strong>{TOP_K}</strong> most fragile names, and see which basket earned the best
            model-priced put return. <strong>Spearman</strong> is how well the metric&rsquo;s fragility ranking
            sorted every name&rsquo;s realized put ROI; <strong>Lift</strong> is the basket&rsquo;s return above
            buying puts on the whole universe.
          </div>
        </div>
        <button className="lb-run" onClick={run} disabled={state.status === 'loading'}>
          {state.status === 'loading' ? 'Running the bake-off…' : 'Run the bake-off'}
        </button>
      </div>

      {state.status === 'loading' && (
        <p className="putlab-status" role="status" aria-live="polite">
          Backtesting every name once, then ranking six ways &mdash; this takes ~20 seconds.
        </p>
      )}
      {state.status === 'error' && (
        <p className="putlab-status putlab-status-error" role="alert">
          {state.message}
        </p>
      )}

      {state.status === 'ready' && (
        <>
          <div className="lb-scroll">
            <table className="lb-table mono">
              <thead>
                <tr>
                  <th className="lb-num">#</th>
                  <th>Metric</th>
                  <th>Top names</th>
                  <th className="lb-num" title="Blended return on premium of this metric's put basket">
                    Basket put ret
                    <ConceptInfo id="roi_on_premium" />
                  </th>
                  <th className="lb-num">Hit</th>
                  <th className="lb-num" title="Combined max drawdown of the basket (worst peak-to-trough)">
                    Bleed
                    <ConceptInfo id="bleed" />
                  </th>
                  <th>
                    Verdict
                    <ConceptInfo id="verdict" />
                  </th>
                  <th
                    className="lb-num"
                    title="Spearman rank corr between the metric's fragility ranking and realized put ROI (in-sample)"
                  >
                    Spearman
                    <ConceptInfo id="spearman" />
                  </th>
                  <th className="lb-num" title="Basket ROI minus the buy-puts-on-everyone baseline">
                    Lift
                    <ConceptInfo id="lift" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.entries.map((e, i) => (
                  <tr key={e.metric} className={i === 0 ? 'lb-current' : undefined}>
                    <td className="lb-num lb-rank">{i + 1}</td>
                    <td className="lb-name" style={i === 0 ? { fontWeight: 700 } : undefined}>
                      {e.label}
                    </td>
                    <td className="lb-name">{e.top_k_assets.map((a) => a.toUpperCase()).join(', ') || '—'}</td>
                    <td
                      className="lb-num"
                      style={{ color: e.roi_on_premium >= 0 ? 'var(--gain)' : 'var(--loss)', fontWeight: 700 }}
                    >
                      {fmtPct(e.roi_on_premium)}
                    </td>
                    <td className="lb-num">{Math.round(e.hit_rate * 100)}%</td>
                    <td className="lb-num" style={{ color: 'var(--loss)' }}>
                      {fmtDollar(e.combined_max_drawdown)}
                    </td>
                    <td>
                      <span className={`badge ${e.verdict}`} title={regimeTooltip(e.regime_slices)}>
                        {VERDICT_LABEL[e.verdict]}
                      </span>
                    </td>
                    <td className="lb-num">
                      {e.spearman_vs_payoff === null ? '—' : e.spearman_vs_payoff.toFixed(2)}
                    </td>
                    <td
                      className="lb-num"
                      style={{ color: e.lift_vs_baseline >= 0 ? 'var(--gain)' : 'var(--loss)' }}
                    >
                      {fmtPct(e.lift_vs_baseline)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            {state.universeSize} names scored; baseline (buy puts on everyone) {fmtPct(state.baselineRoi)}.
            This is an <strong>in-sample cross-sectional association</strong>
            <ConceptInfo id="in_sample" /> measured over the lookback &mdash;
            it shows which metric <em>sorted realized put payoffs</em> over this window, a screen chooser, not a
            forward guarantee. The metric and the payoff are measured over the same historical window.
          </p>
        </>
      )}
    </section>
  )
}
