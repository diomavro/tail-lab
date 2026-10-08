import type { ReactNode } from 'react'
import type { BookPlanParams, ComparatorOption, ModelPlanParams } from '../../../api/client'
import {
  amount,
  COMPARATOR_BUTTONS,
  MAX_AMOUNT,
  MODEL_DEPTHS,
  pct,
  PLAN_HORIZONS,
  PLAN_PROGRAMS,
  type BookMode,
  type LumpMetric,
  type PlanSource,
} from './planFormat'

/* The Book's plan bar: every input the Book reads, in one row above the
 * result, scoped to this page (docs/adr/0028). No rules or boxes -- whitespace
 * separates it from the result. Comparators the lake cannot offer stay on
 * screen, disabled, with the reason (refusals are values). */

function Seg<T extends string | number>({
  label,
  aria,
  name,
  options,
  value,
  onChange,
  disabled,
}: {
  label: string
  /** The radiogroup's accessible name, when it differs from the visible label. */
  aria?: string
  name: string
  options: readonly { value: T; text: string; title?: string }[]
  value: T
  onChange: (v: T) => void
  disabled?: (v: T) => boolean
}) {
  return (
    <div className="pl-field pl-planbar-field">
      <label id={`${name}-label`}>{label}</label>
      <div
        className="pl-seg"
        role="radiogroup"
        aria-labelledby={aria ? undefined : `${name}-label`}
        aria-label={aria}
      >
        {options.map((o) => (
          <label className="pl-seg-opt" key={String(o.value)} title={o.title}>
            <input
              type="radio"
              name={name}
              checked={value === o.value}
              disabled={disabled?.(o.value) ?? false}
              onChange={() => onChange(o.value)}
            />
            {o.text}
          </label>
        ))}
      </div>
    </div>
  )
}

function Amount({
  label,
  value,
  onChange,
  step,
  min,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  step: number
  /** The spinner's floor: the model plan's API takes only positive amounts. */
  min: number
}) {
  const id = `pl-plan-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <div className="pl-field pl-planbar-field pl-planbar-amount">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="pl-input"
        type="number"
        min={min}
        max={MAX_AMOUNT}
        step={step}
        value={value}
        // Clamped at 0, not at min: a typed 0 reaches the API, whose refusal
        // names the field (detailOf), as before the plan bar.
        onChange={(e) => onChange(amount(e.target.value, 0))}
      />
    </div>
  )
}

function Range({ label, value, min, step, onChange }: { label: ReactNode; value: number; min: number; step: number; onChange: (v: number) => void }) {
  return (
    <div className="pl-field pl-planbar-field pl-planbar-range">
      <label>
        {label}
        <input type="range" min={min} max={1} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      </label>
    </div>
  )
}

const COMPARATOR_TEXT: Record<(typeof COMPARATOR_BUTTONS)[number], string> = {
  spx: 'S&P 500',
  cash: 'Cash',
  bills: 'T-bills',
}

export function BookPlanBar({
  mode,
  setMode,
  metric,
  setMetric,
  source,
  setSource,
  real,
  setReal,
  model,
  setModel,
  options,
}: {
  mode: BookMode
  setMode: (m: BookMode) => void
  metric: LumpMetric
  setMetric: (m: LumpMetric) => void
  source: PlanSource
  setSource: (s: PlanSource) => void
  real: BookPlanParams
  setReal: (p: Partial<BookPlanParams>) => void
  model: ModelPlanParams
  setModel: (p: Partial<ModelPlanParams>) => void
  /** The real-quote source's comparator list, from its last response. */
  options: ComparatorOption[]
}) {
  const option = (key: string) => options.find((o) => o.key === key)
  const others = options.filter((o) => !(COMPARATOR_BUTTONS as readonly string[]).includes(o.key))
  const amounts = source === 'real' ? real : model
  const setAmounts = (p: Partial<{ e0: number; monthly: number; horizon_years: number }>) =>
    source === 'real' ? setReal(p) : setModel(p)

  return (
    <section aria-label="Plan controls" className="pl-planbar">
      <div className="pl-planbar-row">
        <Seg
          label="Plan"
          name="pl-book-plan"
          value={mode}
          onChange={setMode}
          options={[
            { value: 'lump', text: 'Lump sum' },
            { value: 'monthly', text: 'Monthly contributions' },
          ]}
        />
        {mode === 'lump' && (
          <Seg
            label="Judge each mix on"
            name="pl-book-metric"
            value={metric}
            onChange={setMetric}
            options={[
              { value: 'growth', text: 'Growth' },
              { value: 'ratio', text: 'CAGR / vol' },
            ]}
          />
        )}
        {mode === 'monthly' && (
          <>
            <Seg
              label="Priced from"
              name="pl-plan-source"
              value={source}
              onChange={setSource}
              options={[
                { value: 'real', text: 'Real quotes' },
                { value: 'model', text: 'Our model' },
              ]}
            />
            {source === 'real' ? (
              <>
                <Seg
                  label="Hedge program"
                  name="pl-plan-program"
                  value={real.program}
                  onChange={(program) => setReal({ program })}
                  options={PLAN_PROGRAMS.map((p) => ({ value: p, text: p }))}
                />
                <Range
                  label={<>Share of the book hedged · {pct(real.hedge_ratio, 0)}</>}
                  value={real.hedge_ratio}
                  min={0}
                  step={0.1}
                  onChange={(hedge_ratio) => setReal({ hedge_ratio })}
                />
                <Seg
                  label="Compare with"
                  name="pl-plan-comparator"
                  value={real.comparator}
                  onChange={(comparator) => setReal({ comparator })}
                  disabled={(k) => option(k)?.available === false}
                  options={COMPARATOR_BUTTONS.map((k) => ({
                    value: k,
                    text: COMPARATOR_TEXT[k],
                    title: option(k)?.reason ?? undefined,
                  }))}
                />
                <div className="pl-field pl-planbar-field">
                  <label htmlFor="pl-plan-other">or another Cboe index</label>
                  <select
                    id="pl-plan-other"
                    className="pl-input"
                    value={others.some((o) => o.key === real.comparator) ? real.comparator : ''}
                    onChange={(e) => e.target.value && setReal({ comparator: e.target.value })}
                  >
                    <option value="">none</option>
                    {others.map((o) => (
                      <option
                        key={o.key}
                        value={o.key}
                        disabled={!o.available}
                        title={o.available ? o.label : `${o.label}: ${o.reason ?? ''}`}
                      >
                        {o.key}
                        {o.available ? '' : ' (unavailable)'}
                      </option>
                    ))}
                  </select>
                </div>
              </>
            ) : (
              <>
                <Seg
                  label="Strike depth"
                  name="pl-model-depth"
                  value={model.moneyness_pct}
                  onChange={(moneyness_pct) => setModel({ moneyness_pct })}
                  options={MODEL_DEPTHS.map((d) => ({ value: d, text: `${d}% OTM` }))}
                />
                <Range
                  label={<>Of each month on puts · {pct(model.put_share, 0)}</>}
                  value={model.put_share}
                  min={0.05}
                  step={0.05}
                  onChange={(put_share) => setModel({ put_share })}
                />
              </>
            )}
            <Amount label="Start with $" value={amounts.e0} step={1000} min={source === 'real' ? 0 : 1} onChange={(e0) => setAmounts({ e0 })} />
            <Amount
              label="Every month $"
              value={amounts.monthly}
              step={100}
              min={source === 'real' ? 0 : 1}
              onChange={(monthly) => setAmounts({ monthly })}
            />
            <Seg
              label="Horizon"
              name="pl-plan-horizon"
              value={amounts.horizon_years}
              onChange={(horizon_years) => setAmounts({ horizon_years })}
              options={PLAN_HORIZONS.map((h) => ({ value: h, text: `${h}y` }))}
            />
          </>
        )}
      </div>
      {mode === 'monthly' && source === 'real' && (
        <div className="pl-planbar-notes">
          {options
            .filter((o) => !o.available && (COMPARATOR_BUTTONS as readonly string[]).includes(o.key))
            .map((o) => (
              <p key={o.key} data-testid="comparator-refusal">
                {o.label} unavailable: {o.reason}.
              </p>
            ))}
          <p data-testid="no-stocks">
            Stocks and ETFs are not offered: the lake stores split-adjusted prices only, so their dividends would be
            missing and their return understated.
          </p>
        </div>
      )}
    </section>
  )
}
