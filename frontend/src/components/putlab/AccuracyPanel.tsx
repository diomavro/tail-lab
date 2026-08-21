import type { AccuracyResponse } from '../../api/client'
import type { ResourceState } from './PutLab'

// "How wrong is this?" -- the constitutional companion to every backtest
// result (README, "Accuracy is surfaced, not filed"). It renders on EVERY
// state, including failure: a silent accuracy panel is indistinguishable from
// an accurate result, which is the exact failure mode the principle exists to
// prevent. Four blocks, in decreasing order of how much they should change a
// reader's mind: the model's expected optimism over this window, the published
// programs they could have bought instead, the input data's quality, and the
// assumptions underneath.

const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`
const signedPct = (x: number, digits = 2) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`

function OptimismHeadline({ model }: { model: AccuracyResponse['model'] }) {
  if (model.expected_optimism === null) {
    return (
      <p className="acc-headline acc-headline-unknown">
        <strong>The size of this result's error is not measured.</strong> Read the returns as
        unqualified.
      </p>
    )
  }
  const optimistic = model.expected_optimism > 0
  return (
    <p className={`acc-headline ${optimistic ? 'acc-headline-warn' : 'acc-headline-cool'}`}>
      Over this window the model prices puts{' '}
      <strong>{optimistic ? 'too cheap' : 'too dear'}</strong>, so these returns are likely{' '}
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
    <div className="acc-mix">
      <div className="acc-mix-bar" aria-hidden="true">
        {model.regime_mix.map((s) => (
          <span
            key={s.regime}
            className={`acc-mix-seg acc-mix-${s.regime}`}
            style={{ width: `${s.share * 100}%` }}
          />
        ))}
      </div>
      <ul className="acc-mix-legend">
        {model.regime_mix.map((s) => {
          const residual = model.residual_by_regime[s.regime]
          return (
            <li key={s.regime}>
              <span className={`acc-dot acc-mix-${s.regime}`} aria-hidden="true" />
              {s.regime} {pct(s.share, 0)}
              {residual !== undefined && (
                <span className="acc-mix-residual"> ({signedPct(residual)}/yr)</span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function AccuracyPanel({ accuracy }: { accuracy: ResourceState<AccuracyResponse> }) {
  if (accuracy.status !== 'ready') {
    // Deliberately not `return null`. An absent panel reads as "nothing to
    // report", which is the opposite of the truth while it is loading or broken.
    const message =
      accuracy.status === 'loading'
        ? 'Measuring how wrong this result might be...'
        : "Couldn't load the accuracy context for this result — read the returns as unqualified."
    return (
      <section className="panel acc-panel" aria-label="Result accuracy">
        <h3 className="acc-title">How wrong is this?</h3>
        <p className="putlab-status" role="status">
          {message}
        </p>
      </section>
    )
  }

  const { model, benchmarks, data_quality_flags, data_quality_note, assumptions } = accuracy.data

  return (
    <section className="panel acc-panel" aria-label="Result accuracy">
      <h3 className="acc-title">
        How wrong is this?
        <span className={`acc-badge acc-badge-${model.applicability}`}>
          {model.applicability}
          {model.reference && ` · vs ${model.reference}`}
        </span>
      </h3>

      <OptimismHeadline model={model} />
      <RegimeMix model={model} />
      <p className="acc-note">{model.caveat}</p>
      <p className="acc-basis">{model.basis}</p>

      {benchmarks.length > 0 && (
        <div className="acc-block">
          <h4>What you could have bought instead</h4>
          <table className="acc-table">
            <thead>
              <tr>
                <th scope="col">Program</th>
                <th scope="col">Total</th>
                <th scope="col">Per year</th>
              </tr>
            </thead>
            <tbody>
              {benchmarks.map((b) => (
                <tr key={b.index_symbol}>
                  <th scope="row" title={b.label}>
                    {b.index_symbol}
                  </th>
                  <td>{signedPct(b.total_return, 1)}</td>
                  <td>{signedPct(b.annualized, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="acc-note">
            Cboe's own put programs over the same window. The honest question is not whether this
            hedge made money but whether it beat the one you could have bought.
          </p>
        </div>
      )}

      <div className="acc-block">
        <h4>Input data</h4>
        <p className={data_quality_flags ? 'acc-flagged' : 'acc-clean'}>{data_quality_note}</p>
      </div>

      <details className="acc-block acc-assumptions">
        <summary>What this rests on ({assumptions.length})</summary>
        <dl>
          {assumptions.map((a) => (
            <div key={a.name}>
              <dt>{a.name}</dt>
              <dd>
                {a.value}
                {a.leverage && <em className="acc-leverage"> — {a.leverage}</em>}
              </dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  )
}
