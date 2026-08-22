import { useMemo, useState } from 'react'
import type { CadenceResponse, DataQualityResponse, UniverseMember } from '../../api/client'
import { PUTLAB_ASSETS, PUTLAB_OOM_PRESETS, PUTLAB_TENORS, PUTLAB_YEARS, type PutLabControls } from './types'

/* The one control surface.
 *
 * Replaces BOTH QuestionBar (the plain-English sentence, on Screen + Portfolio)
 * and ChartCockpit (the four rails around the hero chart, on Backtest). Those
 * were two ways to set the same four params, which meant a reader had to learn
 * where the controls lived per tab, and the cockpit's spatial arrangement made
 * the eye hunt in four directions for one position.
 *
 * The sentence form was expressive but it could not hold a search field or the
 * provenance block, and it wrapped unpredictably at narrow widths. A rail can.
 */

interface Props {
  controls: PutLabControls
  onChange: (patch: Partial<PutLabControls>) => void
  universe: UniverseMember[]
  dataQuality: DataQualityResponse | null
  cadence: CadenceResponse | null
  asOf: string | null
}

export function ParamRail({ controls, onChange, universe, dataQuality, cadence, asOf }: Props) {
  const [query, setQuery] = useState('')

  const options = useMemo(() => {
    // The universe fetch failing must not leave the rail empty -- fall back to
    // the built-in six, exactly as QuestionBar did.
    if (universe.length === 0) {
      return PUTLAB_ASSETS.map((a) => ({ symbol: a.value, name: a.label, blurb: '' }))
    }
    return universe.map((m) => ({ symbol: m.symbol.toLowerCase(), name: m.name, blurb: m.label }))
  }, [universe])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(
      (o) => o.symbol.includes(q) || o.name.toLowerCase().includes(q) || o.blurb.toLowerCase().includes(q),
    )
  }, [options, query])

  return (
    <aside className="pl-side" aria-label="Position controls">
      <div className="pl-kicker" style={{ marginBottom: 10 }}>
        Universe
      </div>
      <input
        className="pl-input"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter the universe…"
        aria-label="Filter the universe"
        style={{ marginBottom: 10 }}
      />
      <div className="pl-list">
        {filtered.length === 0 && <p className="pl-list-empty">No name matches “{query}”.</p>}
        {filtered.map((o) => (
          <button
            key={o.symbol}
            type="button"
            className="pl-list-row"
            aria-pressed={o.symbol === controls.asset}
            onClick={() => onChange({ asset: o.symbol })}
          >
            <span>
              <strong>{o.symbol.toUpperCase()}</strong>{' '}
              {/* The instrument, not the API's `label` -- that field is the
                  options cadence ("Weeklies") for every name in the real
                  universe, and printing it here gives 35 identical blurbs. The
                  cadence has its own line in Provenance. */}
              <span className="pl-list-blurb">{o.name}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="pl-kicker" style={{ marginBottom: 12 }}>
        Position
      </div>

      <div className="pl-two-col" style={{ marginBottom: 15 }}>
        <div className="pl-field">
          <label htmlFor="pl-notional">Premium per roll, $</label>
          <input
            className="pl-input"
            id="pl-notional"
            type="number"
            min={100}
            max={100000}
            step={100}
            value={controls.notional}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (Number.isFinite(v) && v >= 100 && v <= 100000) onChange({ notional: v })
            }}
          />
        </div>
        <div className="pl-field">
          <label htmlFor="pl-oom">Out of the money, %</label>
          <input
            className="pl-input"
            id="pl-oom"
            type="number"
            min={1}
            max={25}
            step={1}
            value={controls.moneyness_pct}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (Number.isFinite(v) && v >= 1 && v <= 25) onChange({ moneyness_pct: v })
            }}
          />
        </div>
      </div>

      <div className="pl-field" style={{ marginBottom: 15 }}>
        <label id="pl-oom-label">Strike presets</label>
        <div className="pl-seg pl-seg-block" role="radiogroup" aria-labelledby="pl-oom-label">
          {PUTLAB_OOM_PRESETS.map((o) => (
            <label className="pl-seg-opt" key={o}>
              <input
                type="radio"
                name="pl-oom-preset"
                checked={controls.moneyness_pct === o}
                onChange={() => onChange({ moneyness_pct: o })}
              />
              {o}%
            </label>
          ))}
        </div>
      </div>

      <div className="pl-field" style={{ marginBottom: 15 }}>
        <label id="pl-tenor-label">Held to expiry</label>
        <div className="pl-seg pl-seg-block" role="radiogroup" aria-labelledby="pl-tenor-label">
          {PUTLAB_TENORS.map((t) => (
            <label className="pl-seg-opt" key={t.weeks}>
              <input
                type="radio"
                name="pl-tenor"
                checked={controls.tenor_weeks === t.weeks}
                onChange={() => onChange({ tenor_weeks: t.weeks })}
              />
              {t.label}
            </label>
          ))}
        </div>
      </div>

      <div className="pl-field" style={{ marginBottom: 20 }}>
        <label id="pl-years-label">Rolled over</label>
        <div className="pl-seg pl-seg-block" role="radiogroup" aria-labelledby="pl-years-label">
          {PUTLAB_YEARS.map((y) => (
            <label className="pl-seg-opt" key={y}>
              <input
                type="radio"
                name="pl-years"
                checked={controls.years === y}
                onChange={() => onChange({ years: y })}
              />
              {y}y
            </label>
          ))}
        </div>
      </div>

      <div className="pl-kicker" style={{ marginBottom: 9 }}>
        Provenance
      </div>
      <dl className="pl-prov">
        {asOf && (
          <>
            <dt>as_of</dt>
            <dd>{asOf}</dd>
          </>
        )}
        {dataQuality && (
          <>
            <dt>bars</dt>
            <dd>{dataQuality.n_bars}</dd>
            <dt>flagged</dt>
            <dd className={dataQuality.n_suspicious ? 'pl-neg' : undefined}>{dataQuality.n_suspicious}</dd>
          </>
        )}
        {cadence && (
          <>
            <dt>cadence</dt>
            <dd>{cadence.cadence}</dd>
            <dt>gap</dt>
            <dd>{cadence.avg_gap_days.toFixed(1)}d</dd>
          </>
        )}
      </dl>
    </aside>
  )
}
