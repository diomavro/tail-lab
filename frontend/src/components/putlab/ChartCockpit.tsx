import type { ReactNode } from 'react'
import type { UniverseMember } from '../../api/client'
import { PUTLAB_OOM_PRESETS, PUTLAB_TENORS, type PutLabControls } from './types'

interface ChartCockpitProps {
  controls: PutLabControls
  onChange: (patch: Partial<PutLabControls>) => void
  universe: UniverseMember[]
  children: ReactNode
}

// Vertical OOM-strategy presets shown on the right rail (shared with the
// cache-warming prefetch); a custom % input sits below them so fine values
// (7%, 11%) stay reachable.
const OOM_PRESETS = PUTLAB_OOM_PRESETS
// Vertical evaluation-window presets on the left rail.
const YEAR_PRESETS = [4, 3, 2]

// The "control cockpit": the four backtest parameters laid on the four edges of
// the hero chart, mapped to their meaning in the picture. Bottom = roll
// frequency (under the time axis), right = OOM strategy (beside the price
// axis), left = evaluation period, top = ticker + notional. The rails are thin
// and muted on purpose -- the chart (passed as children) stays the focus.
export function ChartCockpit({ controls, onChange, universe, children }: ChartCockpitProps) {
  const symbols = universe.map((m) => m.symbol.toLowerCase())
  const idx = symbols.indexOf(controls.asset)
  const currentName = idx >= 0 ? universe[idx]!.name : controls.asset.toUpperCase()
  const canStep = symbols.length > 1

  const step = (dir: 1 | -1) => {
    if (symbols.length === 0) return
    const base = idx < 0 ? 0 : idx
    const next = (base + dir + symbols.length) % symbols.length
    onChange({ asset: symbols[next]! })
  }

  return (
    <div className="cockpit">
      {/* TOP EDGE -- ticker (stepper + direct-jump select) + notional */}
      <div className="cockpit-rail cockpit-top">
        <span className="cockpit-label">ticker</span>
        <div className="cockpit-stepper">
          <button
            type="button"
            className="cockpit-arrow"
            aria-label="previous ticker"
            disabled={!canStep}
            onClick={() => step(-1)}
          >
            &#9666;
          </button>
          <span className="cockpit-ticker-name" title={currentName}>
            {currentName}
          </span>
          <button
            type="button"
            className="cockpit-arrow"
            aria-label="next ticker"
            disabled={!canStep}
            onClick={() => step(1)}
          >
            &#9656;
          </button>
        </div>
        <span className="ctl">
          <select
            aria-label="Jump to ticker"
            value={controls.asset}
            onChange={(e) => onChange({ asset: e.target.value })}
          >
            {universe.map((m) => (
              <option key={m.symbol} value={m.symbol.toLowerCase()}>
                {m.symbol}
              </option>
            ))}
          </select>
        </span>
        <span className="ctl num">
          <label htmlFor="cockpit-notional">$</label>
          <input
            id="cockpit-notional"
            className="mono"
            type="number"
            min={100}
            max={100000}
            step={100}
            inputMode="numeric"
            aria-label="Notional per roll in dollars"
            value={controls.notional}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (Number.isFinite(v) && v >= 100 && v <= 100000) onChange({ notional: v })
            }}
          />
        </span>
      </div>

      {/* LEFT EDGE -- evaluation period */}
      <div className="cockpit-rail cockpit-left">
        <span className="cockpit-label">period</span>
        <span className="seg seg-v" role="group" aria-label="Evaluation period">
          {YEAR_PRESETS.map((y) => (
            <button
              key={y}
              type="button"
              aria-pressed={controls.years === y}
              onClick={() => onChange({ years: y })}
            >
              {y}y
            </button>
          ))}
        </span>
      </div>

      {/* CENTER -- the hero chart */}
      <div className="cockpit-chart">{children}</div>

      {/* RIGHT EDGE -- OOM strategy (presets + custom %) */}
      <div className="cockpit-rail cockpit-right">
        <span className="cockpit-label">OOM</span>
        <span className="seg seg-v" role="group" aria-label="Out-of-the-money strategy">
          {OOM_PRESETS.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={controls.moneyness_pct === m}
              onClick={() => onChange({ moneyness_pct: m })}
            >
              {m}%
            </button>
          ))}
        </span>
        <span className="ctl num cockpit-oom-custom">
          <input
            id="cockpit-moneyness"
            className="mono"
            type="number"
            min={1}
            max={25}
            step={1}
            aria-label="Custom out-of-the-money percent"
            value={controls.moneyness_pct}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (Number.isFinite(v) && v >= 1 && v <= 25) onChange({ moneyness_pct: v })
            }}
          />
          <label htmlFor="cockpit-moneyness">%</label>
        </span>
      </div>

      {/* BOTTOM EDGE -- roll frequency, under the chart's time axis */}
      <div className="cockpit-rail cockpit-bottom">
        <span className="cockpit-label">roll frequency</span>
        <span className="seg" role="group" aria-label="Roll frequency">
          {PUTLAB_TENORS.map((t) => (
            <button
              key={t.weeks}
              type="button"
              aria-pressed={controls.tenor_weeks === t.weeks}
              onClick={() => onChange({ tenor_weeks: t.weeks })}
            >
              {t.label}
            </button>
          ))}
        </span>
      </div>
    </div>
  )
}
