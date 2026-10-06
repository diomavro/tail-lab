import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchBookPlan,
  type BookPlanParams,
  type BookPlanResponse,
  type ComparatorOption,
} from '../../../api/client'
import { ModelPlan } from './ModelPlan'
import { amount, MAX_AMOUNT, pct, usd, useDebounced } from './planFormat'
import { PlanVerdict } from './PlanVerdict'

/* The Book in contributions mode (docs/adr/0027 §3).
 *
 * The same cash -- E0 now, X every month -- goes into a self-financed hedged
 * book or into a comparator. The headline is the rolling-start verdict, not
 * the one illustrated window: a plan you actually run begins on whatever date
 * you begin it. Comparators the lake cannot offer stay on screen, disabled,
 * with the reason (refusals are values).
 */

const PROGRAMS = ['PPUT', 'PPUT3M', 'VXTH'] as const
const HORIZONS = [5, 10, 15, 20] as const
const BUTTONS = ['spx', 'cash', 'bills'] as const


type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: BookPlanResponse }

function RealQuotePlan() {
  const [params, setParams] = useState<BookPlanParams>({
    program: 'PPUT',
    hedge_ratio: 0.5,
    e0: 10_000,
    monthly: 500,
    comparator: 'spx',
    horizon_years: 10,
  })
  const [state, setState] = useState<State>({ status: 'loading' })
  // Kept across reloads, so a disabled comparator stays disabled while the
  // next plan is computing.
  const [options, setOptions] = useState<ComparatorOption[]>([])
  const set = (p: Partial<BookPlanParams>) => setParams((cur) => ({ ...cur, ...p }))
  const settled = useDebounced(params)

  useEffect(() => {
    const ctl = new AbortController()
    setState({ status: 'loading' })
    fetchBookPlan(settled, ctl.signal)
      .then((data) => {
        setOptions(data.plan.comparators)
        setState({ status: 'ready', data })
      })
      .catch((err: unknown) => {
        if (ctl.signal.aborted) return
        setState({
          status: 'error',
          message:
            err instanceof ApiError && err.status === 404
              ? `Nothing to plan: ${err.message}.`
              : err instanceof Error
                ? err.message
                : String(err),
        })
      })
    return () => ctl.abort()
  }, [settled])

  const option = (key: string) => options.find((o) => o.key === key)
  const others = options.filter((o) => !(BUTTONS as readonly string[]).includes(o.key))

  return (
    <>
      <p className="pl-lede" data-testid="accounting">
        Accounting: self-financed — the hedge's premium is paid from the book (docs/adr/0027).
      </p>
      <p className="pl-lede">
        Pay in {usd(params.e0)} now and {usd(params.monthly)} every month. One arm holds the S&P 500 with{' '}
        {pct(params.hedge_ratio, 0)} of the book carrying {params.program}'s hedge, its premium paid from the book at
        Cboe's real-quote prices; the other puts the same cash into the comparator. Amounts are in US dollars, the
        indices' currency; a euro investor's result also carries the EUR/USD move.
      </p>
      {state.status === 'ready' && (
        <p className="pl-caveat" data-testid="dividend-caveat">
          The S&P 500 — in the hedged book, and as a comparator — earns an assumed{' '}
          {pct(state.data.plan.dividend_yield)} dividend yield, not a measured one; the line after the verdict shows
          how much that assumption moves it.
        </p>
      )}
      <div className="pl-plan-controls" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, margin: '10px 0' }}>
        <label>
          Program{' '}
          <select value={params.program} onChange={(e) => set({ program: e.target.value })}>
            {PROGRAMS.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label>
          Hedge ratio {pct(params.hedge_ratio, 0)}{' '}
          <input
            type="range"
            min={0}
            max={1}
            step={0.1}
            value={params.hedge_ratio}
            onChange={(e) => set({ hedge_ratio: Number(e.target.value) })}
          />
        </label>
        <label>
          Start with ${' '}
          <input
            type="number"
            min={0}
            max={MAX_AMOUNT}
            step={1000}
            value={params.e0}
            style={{ width: 90 }}
            onChange={(e) => set({ e0: amount(e.target.value, 0) })}
          />
        </label>
        <label>
          Monthly ${' '}
          <input
            type="number"
            min={0}
            max={MAX_AMOUNT}
            step={100}
            value={params.monthly}
            style={{ width: 80 }}
            onChange={(e) => set({ monthly: amount(e.target.value, 0) })}
          />
        </label>
        <label>
          Horizon{' '}
          <select value={params.horizon_years} onChange={(e) => set({ horizon_years: Number(e.target.value) })}>
            {HORIZONS.map((h) => (
              <option key={h} value={h}>
                {h} years
              </option>
            ))}
          </select>
        </label>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        <span>Compare with:</span>
        <div className="pl-seg" role="radiogroup" aria-label="Comparator">
          {BUTTONS.map((key) => {
            const o = option(key)
            return (
              <label className="pl-seg-opt" key={key} title={o?.reason ?? undefined}>
                <input
                  type="radio"
                  name="pl-plan-comparator"
                  checked={params.comparator === key}
                  disabled={o != null && !o.available}
                  onChange={() => set({ comparator: key })}
                />
                {key === 'spx' ? 'S&P 500' : key === 'cash' ? 'Cash' : 'T-bills'}
              </label>
            )
          })}
        </div>
        <label>
          or another Cboe index{' '}
          <select
            aria-label="Other comparator"
            style={{ maxWidth: '100%' }}
            value={others.some((o) => o.key === params.comparator) ? params.comparator : ''}
            onChange={(e) => e.target.value && set({ comparator: e.target.value })}
          >
            <option value="">—</option>
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
        </label>
      </div>
      <p className="pl-lede" style={{ fontSize: 12 }} data-testid="no-stocks">
        Stocks and ETFs are not offered: the lake stores split-adjusted prices only, so their dividends would be
        missing and their return understated.
      </p>
      {options
        .filter((o) => !o.available && (BUTTONS as readonly string[]).includes(o.key))
        .map((o) => (
          <p key={o.key} className="pl-lede" data-testid="comparator-refusal" style={{ fontSize: 12 }}>
            {o.label} unavailable: {o.reason}.
          </p>
        ))}
      {state.status === 'loading' && <p className="pl-lede">Running every start month…</p>}
      {state.status === 'ready' && settled !== params && (
        <p className="pl-lede" data-testid="updating">
          Updating for your new inputs…
        </p>
      )}
      {state.status === 'error' && <p className="pl-caveat">{state.message}</p>}
      {state.status === 'ready' &&
        state.data.plan.refusal &&
        // Shown unless a comparator-refusal line above already says it: only the
        // three buttons get one, so a refused picker index still shows its reason.
        !(
          (BUTTONS as readonly string[]).includes(state.data.plan.comparator) &&
          option(state.data.plan.comparator)?.available === false
        ) && (
        <p className="pl-caveat" data-testid="plan-refusal">
          {state.data.plan.refusal}.
        </p>
      )}
      {state.status === 'ready' && (
        <PlanVerdict
          rolling={state.data.plan.rolling}
          window={state.data.plan.window}
          byYield={state.data.plan.by_yield}
        />
      )}
      {state.status === 'ready' && (
        <p className="pl-lede" style={{ fontSize: 12 }}>
          Cboe snapshot {state.data.cboe_snapshot ?? 'unknown'} · rates {state.data.rates_snapshot ?? 'none'} · code{' '}
          {state.data.code_sha} · as of {state.data.plan.as_of}
        </p>
      )}
    </>
  )
}

type Source = 'real' | 'model'

export function BookPlan() {
  const [source, setSource] = useState<Source>('real')
  return (
    <section aria-label="Monthly contributions plan">
      <div className="pl-seg" role="radiogroup" aria-label="Source" style={{ marginBottom: 8 }}>
        {(
          [
            ['real', 'Real quotes'],
            ['model', 'Our model'],
          ] as const
        ).map(([key, label]) => (
          <label className="pl-seg-opt" key={key}>
            <input type="radio" name="pl-plan-source" checked={source === key} onChange={() => setSource(key)} />
            {label}
          </label>
        ))}
      </div>
      {source === 'real' ? <RealQuotePlan /> : <ModelPlan />}
    </section>
  )
}
