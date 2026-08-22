import type { AccuracyResponse } from '../../api/client'
import type { ResourceState } from './PutLab'

// "How wrong is this?" -- the constitutional companion to every backtest result
// (README, "Accuracy is surfaced, not filed"). It renders on EVERY state,
// including failure: a silent accuracy panel is indistinguishable from an
// accurate result, which is the exact failure mode the principle exists to
// prevent. Four blocks, in decreasing order of how much they should change a
// reader's mind: the model's expected optimism over this window, the published
// programs they could have bought instead, the input data's quality, and the
// assumptions underneath.
//
// Restyled onto .pl-* only; the logic is unchanged. The one substantive fix is
// that `.acc-table td` used to be coloured `var(--ink)`, which in the old token
// layer was the page BACKGROUND -- near-invisible text. Ink is text here.

const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`
/** True minus, not a hyphen: these sit in a column of figures. */
const signedPct = (x: number, digits = 2) =>
  `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(digits)}%`

function OptimismHeadline({ model }: { model: AccuracyResponse['model'] }) {
  if (model.expected_optimism === null) {
    return (
      <p className="pl-acc-headline">
        <strong>The size of this result&rsquo;s error is not measured.</strong> Read the returns as
        unqualified.
      </p>
    )
  }
  const optimistic = model.expected_optimism > 0
  return (
    <p className="pl-acc-headline">
      Over this window the model prices puts <strong>{optimistic ? 'too cheap' : 'too dear'}</strong>
      , so these returns are likely{' '}
      <strong>
        {optimistic ? 'overstated' : 'understated'} by ~{pct(Math.abs(model.expected_optimism), 2)}
        /yr
      </strong>
      .
    </p>
  )
}

function RegimeMix({ model }: { model: AccuracyResponse['model'] }) {
  if (model.regime_mix.length === 0) return null
  return (
    <>
      <div className="pl-mix-bar" aria-hidden="true">
        {model.regime_mix.map((s) => (
          <span key={s.regime} className={`pl-mix-${s.regime}`} style={{ width: `${s.share * 100}%` }} />
        ))}
      </div>
      <ul className="pl-mix-legend">
        {model.regime_mix.map((s) => {
          // Printed with its own sign. The residual flips sign in a crisis
          // rather than ramping up from calm -- that flip is the finding, and a
          // display that assumes a monotonic ramp gets it backwards.
          const residual = model.residual_by_regime[s.regime]
          return (
            <li key={s.regime}>
              <span className={`pl-dot pl-mix-${s.regime}`} aria-hidden="true" />
              {s.regime} {pct(s.share, 0)}
              {residual !== undefined && <span className="dimmer"> ({signedPct(residual)}/yr)</span>}
            </li>
          )
        })}
      </ul>
    </>
  )
}

export function AccuracyPanel({ accuracy }: { accuracy: ResourceState<AccuracyResponse> }) {
  if (accuracy.status !== 'ready') {
    // Deliberately not `return null`. An absent panel reads as "nothing to
    // report", which is the opposite of the truth while it is loading or broken.
    const message =
      accuracy.status === 'loading'
        ? 'Measuring how wrong this result might be…'
        : 'Couldn’t load the accuracy context for this result — read the returns as unqualified.'
    return (
      <section aria-label="Result accuracy">
        <div className="pl-section-head">
          <h3>How wrong is this?</h3>
        </div>
        <p className="pl-status" role="status" aria-live="polite">
          {message}
        </p>
      </section>
    )
  }

  const { model, benchmarks, data_quality_flags, data_quality_note, assumptions } = accuracy.data

  return (
    <section aria-label="Result accuracy">
      <div className="pl-section-head">
        <h3>
          How wrong is this?
          <span className={`pl-acc-badge pl-acc-badge-${model.applicability}`}>
            {model.applicability}
            {model.reference && ` · vs ${model.reference}`}
          </span>
        </h3>
      </div>

      <OptimismHeadline model={model} />
      <RegimeMix model={model} />
      <p className="pl-note" style={{ marginBottom: 6 }}>
        {model.caveat}
      </p>
      <p className="pl-micro" style={{ marginBottom: 20 }}>
        {model.basis}
      </p>

      <div className="pl-two-up">
        {benchmarks.length > 0 && (
          <div>
            <div className="pl-kicker" style={{ marginBottom: 6 }}>
              What you could have bought instead
            </div>
            <table className="pl-table" aria-label="Published put programs">
              <thead>
                <tr>
                  <th>Program</th>
                  <th className="num">Total</th>
                  <th className="num">Per year</th>
                </tr>
              </thead>
              <tbody>
                {benchmarks.map((b) => (
                  <tr key={b.index_symbol}>
                    <td title={b.label}>{b.index_symbol}</td>
                    <td className="num dim">{signedPct(b.total_return, 1)}</td>
                    <td className="num dim">{signedPct(b.annualized, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="pl-note" style={{ marginTop: 8 }}>
              Cboe&rsquo;s own put programs over the same window. The honest question is not whether
              this hedge made money but whether it beat the one you could have bought.
            </p>
          </div>
        )}

        <div>
          <div className="pl-kicker" style={{ marginBottom: 6 }}>
            Input data
          </div>
          <p className={data_quality_flags ? 'pl-note pl-neg' : 'pl-note'}>{data_quality_note}</p>
          <details style={{ marginTop: 16 }}>
            <summary className="pl-kicker" style={{ cursor: 'pointer' }}>
              What this rests on ({assumptions.length})
            </summary>
            <dl className="pl-assumptions" style={{ marginTop: 10 }}>
              {assumptions.map((a) => (
                <div key={a.name} style={{ display: 'contents' }}>
                  <dt>{a.name}</dt>
                  <dd>
                    {a.value}
                    {a.leverage && <em> — {a.leverage}</em>}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        </div>
      </div>
    </section>
  )
}
