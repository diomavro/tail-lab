import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchBookPlan,
  type BookPlanParams,
  type BookPlanResponse,
  type ComparatorOption,
} from '../../../api/client'
import { COMPARATOR_BUTTONS, legSource, NO_BILLS_REASON, pct, usd, useDebounced } from './planFormat'
import { PlanVerdict } from './PlanVerdict'

/* The Book in contributions mode (docs/adr/0027 §3), priced from real quotes.
 *
 * The same cash -- E0 now, X every month -- goes into a self-financed hedged
 * book or into a comparator. The headline is the rolling-start verdict, not
 * the one illustrated window: a plan you actually run begins on whatever date
 * you begin it. Its inputs live in the Book's plan bar; this renders the
 * result they drive.
 */

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: BookPlanResponse }

export function BookPlan({
  params,
  onOptions,
  narrow,
}: {
  params: BookPlanParams
  /** The comparator list from each response, kept by the plan bar across
   *  reloads so a disabled comparator stays disabled while the next plan runs. */
  onOptions: (options: ComparatorOption[]) => void
  narrow: boolean
}) {
  const [state, setState] = useState<State>({ status: 'loading' })
  const settled = useDebounced(params)

  useEffect(() => {
    const ctl = new AbortController()
    setState({ status: 'loading' })
    fetchBookPlan(settled, ctl.signal)
      .then((data) => {
        onOptions(data.plan.comparators)
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
    // onOptions is a state setter's wrapper; the request depends on the inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled])

  const plan = state.status === 'ready' ? state.data.plan : null
  // A refused comparator already says why under the plan bar; say it once.
  const refusalShownAbove =
    plan != null &&
    (COMPARATOR_BUTTONS as readonly string[]).includes(plan.comparator) &&
    plan.comparators.find((o) => o.key === plan.comparator)?.available === false

  return (
    <div className="pl-book-result">
      <p className="pl-lede" data-testid="accounting">
        Accounting: self-financed — the hedge's premium is paid from the book (docs/adr/0027).
      </p>
      <p className="pl-lede">
        Pay in {usd(params.e0)} now and {usd(params.monthly)} every month. One arm holds the S&P 500 with{' '}
        {pct(params.hedge_ratio, 0)} of the book carrying {params.program}'s hedge, its premium paid from the book at
        Cboe's real-quote prices; the other puts the same cash into the comparator. Amounts are in US dollars, the
        indices' currency; a euro investor's result also carries the EUR/USD move.
      </p>
      {plan &&
        (plan.dividend.source === 'measured' ? (
          <p className="pl-caveat" data-testid="dividend-caveat">
            The S&P 500 — in the hedged book, and as a comparator — is {legSource(plan.dividend)}. SPY&rsquo;s total
            return understates the index&rsquo;s, which flatters the hedge.{' '}
            {plan.legs.some((y) => y.key.kind === 'leg' && y.key.leg === 'conservative')
              ? 'The conservative row below adds SPY’s dividend cash drag back. It usually narrows the hedge’s edge, but not always: the drag turns negative when equities trail T-bills, which is when hedges win, so its share of starts led can be higher.'
              : plan.dividend.conservative_from == null
                ? plan.dividend.conservative_reason === NO_BILLS_REASON
                  ? 'There is no conservative row: adding SPY’s dividend cash drag back needs T-bill rates, which this lake does not have.'
                  : `There is no conservative row: SPY’s dividend cash drag cannot be added back here (${plan.dividend.conservative_reason ?? 'no reason served'}).`
                : // A refused plan has no rows at all, whatever the T-bills cover;
                  // only a plan that ran dropped the conservative row, and it
                  // drops it only when the leg is missing on a plan date.
                  plan.refusal == null && plan.legs.length > 0
                  ? `There is no conservative row: T-bill rates start ${plan.dividend.conservative_from}, after this plan's history begins.`
                  : ''}
          </p>
        ) : (
          <p className="pl-caveat" data-testid="dividend-caveat">
            The S&P 500 — in the hedged book, and as a comparator — earns an assumed {pct(plan.dividend_yield ?? 0)}{' '}
            dividend yield, not a measured one; the dividend-yield rows below show how much that assumption moves it.
          </p>
        ))}
      <p className="pl-note" data-testid="plan-sizing-link">
        This plan tests the hedge ratio you choose; it recommends none. How much to hold is answered in lump-sum mode,
        under &ldquo;How much to hold&rdquo;.
      </p>
      {state.status === 'loading' && <p className="pl-lede">Running every start month…</p>}
      {state.status === 'ready' && settled !== params && (
        <p className="pl-updating" role="status" data-testid="updating">
          Updating for your new inputs…
        </p>
      )}
      {state.status === 'error' && <p className="pl-caveat">{state.message}</p>}
      {plan?.refusal && !refusalShownAbove && (
        <p className="pl-caveat" data-testid="plan-refusal">
          {plan.refusal}.
        </p>
      )}
      {state.status === 'ready' && (
        <>
          <PlanVerdict rolling={state.data.plan.rolling} window={state.data.plan.window} legs={state.data.plan.legs} narrow={narrow} />
          <p className="pl-micro">
            Cboe snapshot {state.data.cboe_snapshot ?? 'unknown'} · rates {state.data.rates_snapshot ?? 'none'} · Tiingo{' '}
            {state.data.tiingo_snapshot ?? 'none'} · code{' '}
            {state.data.code_sha} · as of {state.data.plan.as_of}
          </p>
        </>
      )}
    </div>
  )
}
