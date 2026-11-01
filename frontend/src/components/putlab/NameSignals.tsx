import { useState } from 'react'
import { fetchNameSignal, type SignalNameRecord, type SignalNameScreenRecord } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { signedPts } from './format'
import { nameRuleText, nameSummary, paidText, screenName } from './signalWording'
import { Th } from './Th'
import { STRIP_SIGNAL_TEST } from './types'
import { useRead } from './useRead'

/* "How the signals treated this name" (docs/adr/0033, plan 5.3).
 *
 * The walk-forward signal test is judged on baskets; this section is what it
 * did with ONE name: in how many of its eligible periods the composite put it
 * in the top K, and in how many of those a put on it paid. Read at the strip's
 * fixed settings (STRIP_SIGNAL_TEST), so the number on this page is the one the
 * Bake-off headline is judged on.
 *
 * Descriptive only. With 0.10 delta puts most name-periods return about -100%,
 * so there is no mean, no median and no p-value here -- counts, and the largest
 * payoff multiple (payoff over cost). A group under MIN_GROUP periods says
 * "too few to judge" instead of a rate it cannot support.
 */

const PARAMS = {
  strike_rule: STRIP_SIGNAL_TEST.strike_rule,
  target_delta: STRIP_SIGNAL_TEST.target_delta,
  tenor_weeks: STRIP_SIGNAL_TEST.tenor_weeks,
  signal_years: STRIP_SIGNAL_TEST.years,
  top_k: STRIP_SIGNAL_TEST.top_k,
} as const


function ScreenRow({ s, label, note }: { s: SignalNameScreenRecord; label: string; note?: string }) {
  return (
    <tr>
      <td className="bold">
        {label}
        {note && <div className="pl-micro">{note}</div>}
      </td>
      <td className="num">{s.n_picked_periods}</td>
      <td>{paidText(s.picked)}</td>
      <td>{paidText(s.other)}</td>
      <td className="num">{s.n_picked_periods > 0 ? `${signedPts(s.contribution)} pts` : '—'}</td>
    </tr>
  )
}

function Detail({ rec }: { rec: SignalNameRecord }) {
  const [showOthers, setShowOthers] = useState(false)
  const composite = rec.screens.find((s) => s.role === 'composite')
  const bestVol = rec.screens.find((s) => s.role === 'best_vol_baseline')
  const others = rec.screens.filter((s) => s.role === 'exploratory')
  return (
    <>
      <div className="pl-scroll">
        <table className="pl-table" aria-label="How each screen treated this name">
          <thead>
            <tr>
              <th data-noexplain="row-label">Screen</th>
              <Th concept="name_signal_record" className="num">
                Periods in top {PARAMS.top_k}
              </Th>
              <Th concept="name_signal_record">When picked</Th>
              <Th concept="name_signal_record">Otherwise</Th>
              <Th concept="name_signal_record" className="num">
                Contribution
              </Th>
            </tr>
          </thead>
          <tbody>
            {composite && <ScreenRow s={composite} label="Composite fragility" />}
            {bestVol && (
              <ScreenRow
                s={bestVol}
                label={screenName(bestVol.screen)}
                note="best of 6 plain-volatility screens, picked after the fact"
              />
            )}
            {showOthers &&
              others.map((s) => <ScreenRow key={s.screen} s={s} label={screenName(s.screen)} note="exploratory" />)}
          </tbody>
        </table>
      </div>
      {others.length > 0 && (
        <button
          type="button"
          className="pl-btn pl-btn-ghost"
          aria-expanded={showOthers}
          onClick={() => setShowOthers((v) => !v)}
        >
          {showOthers ? 'Hide the exploratory screens' : `Show ${others.length} exploratory screens`}
        </button>
      )}
      <p className="pl-note" style={{ marginBottom: 14 }}>
        Contribution counts the periods the screen picked this name only: the name&rsquo;s return minus all
        names&rsquo;, divided by K, summed. It can be negative.
      </p>

      <div className="pl-scroll">
        <table className="pl-table" aria-label="This name by regime at entry">
          <thead>
            <tr>
              <Th concept="regime_at_entry">Regime at entry</Th>
              <Th concept="name_signal_record" className="num">
                Eligible periods
              </Th>
              <Th concept="name_signal_record">Paid</Th>
            </tr>
          </thead>
          <tbody>
            {rec.by_regime.map((g) => (
              <tr key={g.regime}>
                <td className="bold">{g.regime}</td>
                <td className="num">{g.n}</td>
                <td>{paidText(g)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="pl-note">
        Every eligible period of this name, whether or not a screen picked it. A put paid when its payoff exceeded
        its cost; the multiple is payoff over cost.
      </p>
    </>
  )
}

export function NameSignals({ asset }: { asset: string }) {
  const [open, setOpen] = useState(false)
  const read = useRead(asset, (signal) => fetchNameSignal(asset, PARAMS, signal))
  const name = asset.toUpperCase()
  const summary = read.status === 'ready' ? nameSummary(read.data, PARAMS.top_k) : null

  return (
    <section className="pl-section" aria-label="How the signals treated this name">
      <div className="pl-section-head">
        <h3>
          How the signals treated this name
          <ConceptInfo id="name_signal_record" />
        </h3>
        <span className="pl-tag pl-tag-outline" data-testid="name-signal-rule">
          {nameRuleText(PARAMS)}
        </span>
        {read.status === 'ready' && summary && (
          <button
            type="button"
            className="pl-btn pl-btn-ghost"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Hide the detail' : 'Show the detail'}
          </button>
        )}
      </div>
      {read.status === 'loading' && (
        <p className="pl-status" role="status" aria-live="polite">
          Reading how the signals treated {name}…
        </p>
      )}
      {read.status === 'missing' && (
        <p className="pl-note" data-testid="name-signal-summary">
          {name} is not in the signal test ({read.message}).
        </p>
      )}
      {read.status === 'error' && (
        <p className="pl-status pl-status-error" role="alert">
          The signal record for {name} is unavailable.
        </p>
      )}
      {read.status === 'ready' && (
        <>
          <p className="pl-note" data-testid="name-signal-summary">
            {summary ?? `No composite row for ${name} in this signal test.`}
          </p>
          {open && <Detail rec={read.data} />}
        </>
      )}
    </section>
  )
}
