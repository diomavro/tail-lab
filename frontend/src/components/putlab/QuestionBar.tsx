import { PUTLAB_ASSETS, PUTLAB_TENORS, type PutLabControls } from './types'

interface QuestionBarProps {
  controls: PutLabControls
  onChange: (patch: Partial<PutLabControls>) => void
}

// The plain-English control row ("the question") -- port of the mock's
// `.question` section, wired to real state instead of `state`/`render()`.
export function QuestionBar({ controls, onChange }: QuestionBarProps) {
  return (
    <section className="question" aria-label="Strategy question builder">
      <div className="eyebrow" style={{ marginBottom: 10 }}>
        The question
      </div>
      <div className="q-line">
        <span className="lede">Invest</span>
        <span className="ctl num">
          <label htmlFor="pl-notional">$</label>
          <input
            id="pl-notional"
            className="mono"
            type="number"
            min={100}
            max={100000}
            step={100}
            inputMode="numeric"
            value={controls.notional}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (Number.isFinite(v) && v >= 100 && v <= 100000) onChange({ notional: v })
            }}
          />
        </span>
        <span className="lede">per roll in</span>
        <span className="ctl">
          <select
            aria-label="Underlying asset"
            value={controls.asset}
            onChange={(e) => onChange({ asset: e.target.value })}
          >
            {PUTLAB_ASSETS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </span>
        <span className="lede">puts,</span>
        <span className="ctl num">
          <input
            id="pl-moneyness"
            className="mono"
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
          <label htmlFor="pl-moneyness">% OOM</label>
        </span>
        <span className="lede">, held</span>
        <span className="seg" role="group" aria-label="Tenor to expiry">
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
        <span className="lede">to expiry, rolled continuously over the</span>
        <span className="ctl">
          <select
            className="mono"
            aria-label="Backtest window"
            value={controls.years}
            onChange={(e) => onChange({ years: Number(e.target.value) })}
          >
            <option value={4}>last 4 years</option>
            <option value={3}>last 3 years</option>
            <option value={2}>last 2 years</option>
          </select>
        </span>
        <span className="lede">.</span>
      </div>
    </section>
  )
}
