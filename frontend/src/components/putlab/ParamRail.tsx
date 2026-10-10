import { useEffect, useMemo, useState } from 'react'
import type { CadenceResponse, DataQualityResponse, UniverseMember } from '../../api/client'
import type { TabId } from './PutLab'
import { RAIL, railSummary, type RailSection } from './rail'
import {
  PUTLAB_ASSETS,
  PUTLAB_DELTA_MAX,
  PUTLAB_DELTA_MIN,
  PUTLAB_DELTA_PRESETS,
  PUTLAB_OOM_PRESETS,
  PUTLAB_TENORS,
  PUTLAB_YEARS,
  type PutLabControls,
} from './types'

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
 *
 * It shows only the controls the open tab actually reads (RAIL, in rail.ts),
 * and a tab that reads none gets no rail at all. A control that changes nothing on
 * screen invites the reader to believe it did.
 */

interface Props {
  tab: TabId
  tabLabel: string
  controls: PutLabControls
  onChange: (patch: Partial<PutLabControls>) => void
  universe: UniverseMember[]
  dataQuality: DataQualityResponse | null
  cadence: CadenceResponse | null
  asOf: string | null
  narrow: boolean
}

export function ParamRail({ tab, tabLabel, controls, onChange, universe, dataQuality, cadence, asOf, narrow }: Props) {
  const [query, setQuery] = useState('')
  // Narrow screens open collapsed, so the result is the first thing read.
  const [open, setOpen] = useState(false)
  // The delta being typed. Committed on blur or Enter, never per keystroke:
  // typing "0.15" passes through "0.1" and, over a selected "0.1", "0.115" --
  // both legal deltas, so a per-keystroke commit would run one nobody asked for.
  const [deltaDraft, setDeltaDraft] = useState<string | null>(null)
  const [deltaRefused, setDeltaRefused] = useState(false)
  // The refusal speaks about the entry just made: any later change of target
  // or rule makes it stale ("kept 0.20" after a preset click is false).
  useEffect(() => setDeltaRefused(false), [controls.target_delta, controls.strike_rule])
  const commitDelta = () => {
    if (deltaDraft == null) return
    // Rounded to the input's own 0.01 step: every label prints two decimals.
    const v = Math.round(Number(deltaDraft) * 100) / 100
    const ok = deltaDraft.trim() !== '' && Number.isFinite(v) && v >= PUTLAB_DELTA_MIN && v <= PUTLAB_DELTA_MAX
    if (ok) onChange({ target_delta: v })
    // A refused entry reverts -- and says why, rather than silently.
    setDeltaRefused(!ok)
    setDeltaDraft(null)
  }
  const sections = RAIL[tab]
  const has = (s: RailSection) => sections.includes(s)
  const surface = tab === 'surface'
  // Delta only where the tab reads it; elsewhere the strike is moneyness.
  const byDelta = has('delta') && controls.strike_rule === 'delta'

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

  const showBody = !narrow || open
  return (
    <aside className="pl-side" aria-label="Position controls">
      {narrow && (
        <button
          type="button"
          className="pl-btn pl-btn-secondary pl-rail-toggle"
          aria-expanded={open}
          aria-controls="pl-rail-body"
          onClick={() => setOpen((o) => !o)}
        >
          <span className="pl-rail-toggle-text">
            <span className="pl-kicker">Controls</span>
            <span data-testid="rail-summary">{railSummary(controls, sections)}</span>
          </span>
          <span className="pl-rail-toggle-act">{open ? 'Hide' : 'Change'}</span>
        </button>
      )}
      <div id="pl-rail-body" hidden={!showBody}>
        {!narrow && (
          <div className="pl-kicker" style={{ marginBottom: 12 }}>
            Controls {tabLabel} reads
          </div>
        )}
        {has('universe') && (
          <>
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
          </>
        )}

        {(has('notional') || has('strike')) && (
          <>
            <div className="pl-kicker" style={{ marginBottom: 12 }}>
              {surface ? 'Anchor' : 'Position'}
            </div>

            {has('delta') && (
              <div className="pl-field" style={{ marginBottom: 12 }}>
                <label id="pl-rule-label">Strike chosen by</label>
                <div className="pl-seg pl-seg-block" role="radiogroup" aria-labelledby="pl-rule-label">
                  <label className="pl-seg-opt">
                    <input
                      type="radio"
                      name="pl-rule"
                      checked={!byDelta}
                      onChange={() => onChange({ strike_rule: 'moneyness' })}
                    />
                    Distance
                  </label>
                  <label className="pl-seg-opt">
                    <input
                      type="radio"
                      name="pl-rule"
                      checked={byDelta}
                      onChange={() => onChange({ strike_rule: 'delta' })}
                    />
                    Delta
                  </label>
                </div>
              </div>
            )}

            <div className={has('notional') ? 'pl-two-col' : undefined} style={{ marginBottom: 15 }}>
              {has('notional') && (
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
                  {/* The Book sizes hedges; this budget only scales a standalone put. */}
                  <span className="pl-note" data-testid="notional-note">
                    Standalone · fixed premium, not a size recommendation
                  </span>
                  <span className="pl-note" data-testid="notional-book">
                    The Book sizes Cboe&rsquo;s rule-based programs, not this strategy.
                  </span>
                </div>
              )}
              {has('strike') && byDelta && (
                <div className="pl-field">
                  <label htmlFor="pl-delta">Put delta, at realised vol</label>
                  <input
                    className="pl-input"
                    id="pl-delta"
                    type="number"
                    min={PUTLAB_DELTA_MIN}
                    max={PUTLAB_DELTA_MAX}
                    step={0.01}
                    value={deltaDraft ?? controls.target_delta}
                    onChange={(e) => setDeltaDraft(e.target.value)}
                    onBlur={commitDelta}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitDelta()
                    }}
                    aria-invalid={deltaRefused}
                    aria-describedby={deltaRefused ? 'pl-delta-refused' : undefined}
                  />
                  {deltaRefused && (
                    <p id="pl-delta-refused" className="pl-note pl-neg" role="alert">
                      A put delta from {PUTLAB_DELTA_MIN.toFixed(2)} to {PUTLAB_DELTA_MAX.toFixed(2)}; kept{' '}
                      {controls.target_delta.toFixed(2)}.
                    </p>
                  )}
                </div>
              )}
              {has('strike') && !byDelta && (
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
              )}
            </div>
          </>
        )}

        {has('strike') && byDelta && (
          <div className="pl-field" style={{ marginBottom: 15 }}>
            <label id="pl-delta-label">Delta presets</label>
            <div className="pl-seg pl-seg-block" role="radiogroup" aria-labelledby="pl-delta-label">
              {PUTLAB_DELTA_PRESETS.map((d) => (
                <label className="pl-seg-opt" key={d}>
                  <input
                    type="radio"
                    name="pl-delta-preset"
                    checked={controls.target_delta === d}
                    onChange={() => onChange({ target_delta: d })}
                  />
                  {d.toFixed(2)}
                </label>
              ))}
            </div>
          </div>
        )}

        {has('strike') && !byDelta && (
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
        )}

        {has('tenor') && (
          <div className="pl-field" style={{ marginBottom: 15 }}>
            {/* On the Surface the tenor picks the listed expiry nearest it;
                nothing is held. */}
            <label id="pl-tenor-label">{surface ? 'Expiry nearest' : 'Held to expiry'}</label>
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
        )}

        {has('years') && (
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
        )}

        {/* Only what this tab can source: a heading over an empty list reads
            as missing provenance. */}
        {(asOf || has('universe')) && (
          <>
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
            {/* Bars and cadence describe one name: shown only where the name is. */}
            {has('universe') && dataQuality && (
              <>
                <dt>bars</dt>
                <dd>{dataQuality.n_bars}</dd>
                <dt>flagged</dt>
                <dd className={dataQuality.n_suspicious ? 'pl-neg' : undefined}>{dataQuality.n_suspicious}</dd>
              </>
            )}
            {has('universe') && cadence && (
              <>
                <dt>cadence</dt>
                <dd>{cadence.cadence}</dd>
                <dt>gap</dt>
                <dd>{cadence.avg_gap_days.toFixed(1)}d</dd>
              </>
            )}
          </dl>
          </>
        )}
      </div>
    </aside>
  )
}
