import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchHedgeOverlay,
  type BookPlanParams,
  type ComparatorOption,
  type HedgeOverlayResponse,
  type ModelPlanParams,
} from '../../../api/client'
import { fmtFixed } from '../format'
import { BookPlan } from './BookPlan'
import { BookPlanBar } from './BookPlanBar'
import { LumpBook } from './LumpBook'
import { ModelPlan } from './ModelPlan'
import {
  DEFAULT_MODEL_PLAN,
  DEFAULT_REAL_PLAN,
  type BookMode,
  type LumpMetric,
  type PlanSource,
} from './planFormat'

/* The Book: how much of the equity book should carry the hedge -- Rodman's
 * Paradox, tested on Cboe's real-quote programs. Named from docs/adr/0021's
 * closed desk list.
 *
 * Artemis Capital's 2016 letter claims a negative-carry long-vol asset makes
 * the combined portfolio grow faster than either part. The free data has no
 * standalone long-vol sleeve -- every Cboe hedge program already holds the
 * S&P 500 -- so this blends each program with plain S&P 500 total return at
 * every hedge ratio, and asks whether any mix out-grows both ends.
 *
 * Growth is the headline because it is the only yardstick a hedge can be sized
 * on (docs/END_STATE.md §4 Q8); CAGR per unit of vol is shown because it is the
 * letter's own, and labelled as the weaker test. The caveats sit above the
 * charts, in the loss colour, for the same reason as the bake-off's.
 *
 * Every input the Book reads is page-scoped (docs/adr/0028) and owned here, so
 * the plan bar can sit above the result it drives.
 */

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: HedgeOverlayResponse }

const pct = (x: number, digits = 1) => `${fmtFixed(x * 100, digits)}%`

export function OverlayView({ narrow }: { narrow: boolean }) {
  const [mode, setMode] = useState<BookMode>('lump')
  const [metric, setMetric] = useState<LumpMetric>('growth')
  const [source, setSource] = useState<PlanSource>('real')
  const [real, setRealState] = useState<BookPlanParams>(DEFAULT_REAL_PLAN)
  const [model, setModelState] = useState<ModelPlanParams>(DEFAULT_MODEL_PLAN)
  const [options, setOptions] = useState<ComparatorOption[]>([])
  const [state, setState] = useState<State>({ status: 'loading' })
  const setReal = (p: Partial<BookPlanParams>) => setRealState((cur) => ({ ...cur, ...p }))
  const setModel = (p: Partial<ModelPlanParams>) => setModelState((cur) => ({ ...cur, ...p }))

  useEffect(() => {
    const ctl = new AbortController()
    fetchHedgeOverlay(ctl.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        if (ctl.signal.aborted) return
        setState({
          status: 'error',
          message:
            err instanceof ApiError && err.status === 404
              ? `Nothing to blend: ${err.message}.`
              : err instanceof Error
                ? err.message
                : String(err),
        })
      })
    return () => ctl.abort()
  }, [])

  return (
    <div className="pl-book">
      <div className="pl-book-head">
        <h2 className="pl-view-title">The Book</h2>
        {mode === 'lump' && (
          <>
            <p className="pl-view-lede pl-lede">
              How much of the equity book should carry the hedge? Rodman's Paradox (Artemis Capital, 2016): an asset
              that loses money most years can still make the whole book grow faster, by paying off in the crash. The
              letter's long-vol fund index is licensed, so this blends each Cboe hedge program (priced from real OPRA
              trades or quotes) with the plain S&P 500 at every hedge ratio, rebalanced monthly. The paradox holds if
              some mix grows faster than both 0% and 100% hedged, by more than 1bp a year.
            </p>
            <p className="pl-lede" data-testid="accounting">
              Accounting: self-financed — the hedge's premium is paid from the book (docs/adr/0027).
            </p>
            <p className="pl-caveat">
              In sample, one history. The S&P 500 leg's dividends are an assumed flat yield
              {state.status === 'ready' ? ` (${pct(state.data.overlay.dividend_yield)})` : ''}, not measured; a low
              guess understates the unhedged leg against every hedged one, so each growth verdict is re-run at
              other yields below. Every test shares one S&amp;P 500 history, so they are not independent. CAGR / vol
              nets no risk-free rate and tends to favour any mix that lowers volatility; growth is
              the test that sizes a hedge.
            </p>
          </>
        )}
      </div>

      <BookPlanBar
        mode={mode}
        setMode={setMode}
        metric={metric}
        setMetric={setMetric}
        source={source}
        setSource={setSource}
        real={real}
        setReal={setReal}
        model={model}
        setModel={setModel}
        options={options}
      />

      {mode === 'lump' && (
        <>
          {state.status === 'loading' && <p className="pl-lede">Blending…</p>}
          {state.status === 'error' && <p className="pl-caveat">{state.message}</p>}
          {state.status === 'ready' && <LumpBook data={state.data} metric={metric} />}
        </>
      )}
      {mode === 'monthly' &&
        (source === 'real' ? (
          <section aria-label="Monthly contributions plan">
            <BookPlan params={real} onOptions={setOptions} narrow={narrow} />
          </section>
        ) : (
          <section aria-label="Monthly contributions plan">
            <ModelPlan params={model} narrow={narrow} />
          </section>
        ))}
    </div>
  )
}
