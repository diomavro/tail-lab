import { useEffect, useState } from 'react'
import { ApiError, fetchModelPlan, type ModelPlanParams, type ModelPlanResponse } from '../../../api/client'
import { amount, MAX_AMOUNT, pct, usd, useDebounced } from './planFormat'
import { PlanVerdict } from './PlanVerdict'

/* The Book's model-priced, contribution-funded plan (docs/adr/0027 §2-3).
 *
 * The one place the Book prices puts itself: Cboe's programs publish an index
 * level, not the premium paid, so "spend $X on puts" needs a model. The
 * measured error is stated ABOVE the numbers, in the loss colour -- a model
 * that flatters puts is exactly the wrong error to trade on.
 */

const DEPTHS = [5, 10] as const
const HORIZONS = [5, 10, 15, 20] as const

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: ModelPlanResponse }

export function ModelPlan() {
  const [params, setParams] = useState<ModelPlanParams>({
    moneyness_pct: 5,
    e0: 10_000,
    monthly: 500,
    put_share: 1,
    horizon_years: 10,
  })
  const [state, setState] = useState<State>({ status: 'loading' })
  const set = (p: Partial<ModelPlanParams>) => setParams((cur) => ({ ...cur, ...p }))
  const settled = useDebounced(params)

  useEffect(() => {
    const ctl = new AbortController()
    setState({ status: 'loading' })
    fetchModelPlan(settled, ctl.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (ctl.signal.aborted) return
        setState({
          status: 'error',
          message:
            err instanceof ApiError && err.status === 404
              ? `Nothing to price: ${err.message}.`
              : err instanceof Error
                ? err.message
                : String(err),
        })
      })
    return () => ctl.abort()
  }, [settled])

  return (
    <>
      <p className="pl-lede" data-testid="accounting">
        Accounting: contribution-funded — the starting {usd(params.e0)} buys the S&P 500;{' '}
        {params.put_share >= 1 ? 'all' : pct(params.put_share, 0)} of each month's {usd(params.monthly)} buys S&P 500
        puts {params.moneyness_pct}% below spot{params.put_share >= 1 ? '' : ', the rest buys the S&P 500'}; payoffs
        are reinvested in it. The comparator puts the same cash into the S&P 500 only. Amounts are in US dollars (a euro investor's result
        also carries the EUR/USD move)
        {state.status === 'ready'
          ? `; the S&P 500 earns an assumed ${pct(state.data.plan.dividend_yield)} dividend yield.`
          : '.'}
      </p>
      {state.status === 'ready' && (
        <p className="pl-caveat" data-testid="model-accuracy">
          {state.data.plan.accuracy.statement}
        </p>
      )}
      <p className="pl-caveat" data-testid="model-not-evidence">
        Model-priced, so the shares below are an estimate to compare plans with, not evidence for the success
        criterion (docs/adr/0004, docs/adr/0027): only the real-quote source can show a hedge paying for itself.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, margin: '10px 0' }}>
        <div className="pl-seg" role="radiogroup" aria-label="Strike depth">
          {DEPTHS.map((d) => (
            <label className="pl-seg-opt" key={d}>
              <input
                type="radio"
                name="pl-model-depth"
                checked={params.moneyness_pct === d}
                onChange={() => set({ moneyness_pct: d })}
              />
              {d}% OTM
            </label>
          ))}
        </div>
        <label>
          On puts {pct(params.put_share, 0)}{' '}
          <input
            type="range"
            min={0.05}
            max={1}
            step={0.05}
            value={params.put_share}
            onChange={(e) => set({ put_share: Number(e.target.value) })}
          />
        </label>
        <label>
          Start with ${' '}
          <input
            type="number"
            min={1}
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
            min={1}
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
      {state.status === 'loading' && <p className="pl-lede">Pricing every month…</p>}
      {state.status === 'ready' && settled !== params && (
        <p className="pl-lede" data-testid="updating">
          Updating for your new inputs…
        </p>
      )}
      {state.status === 'error' && <p className="pl-caveat">{state.message}</p>}
      {state.status === 'ready' && state.data.plan.refusal && (
        <p className="pl-caveat" data-testid="plan-refusal">
          {state.data.plan.refusal}.
        </p>
      )}
      {state.status === 'ready' && (
        <PlanVerdict rolling={state.data.plan.rolling} window={state.data.plan.window} />
      )}
      {state.status === 'ready' && (
        <p className="pl-lede" style={{ fontSize: 12 }}>
          Cboe snapshot {state.data.cboe_snapshot ?? 'unknown'} · VIX {state.data.vix_snapshot ?? 'unknown'} · code{' '}
          {state.data.code_sha} · as of {state.data.plan.as_of}
        </p>
      )}
    </>
  )
}
