import { useEffect, useState } from 'react'
import {
  ApiError,
  fetchHedgeOverlay,
  type HedgeOverlayResponse,
  type OverlayOutcome,
  type OverlayWindow,
} from '../../../api/client'
import { fmtFixed } from '../format'

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
 * tables, in the loss colour, for the same reason as the bake-off's.
 */

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: HedgeOverlayResponse }

const pct = (x: number, digits = 1) => `${fmtFixed(x * 100, digits)}%`
/** A margin in pp/yr at the precision the 1bp threshold needs. */
const pp = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(3)}pp/yr`
const hedged = (w: number) => `${Math.round(w * 100)}% hedged`

const TAG: Record<OverlayOutcome, { cls: string; text: string }> = {
  holds: { cls: 'pl-tag pl-tag-ok', text: 'Paradox holds' },
  inconclusive: { cls: 'pl-tag pl-tag-mute', text: 'Too close to call' },
  fails: { cls: 'pl-tag pl-tag-bad', text: 'Paradox fails' },
}

/** The growth verdict in words. The margin's sign decides the verb, so a mix
 *  that beats the ends by a sliver is never described as trailing them, and
 *  the reverse. */
function growthSentence(w: OverlayWindow): string {
  const size = `${Math.abs(w.margin * 100).toFixed(3)}pp/yr`
  const interior = w.margin > 0
  if (w.outcome === 'holds') return `On growth, ${hedged(w.best_weight)} beats both ends by ${size}.`
  if (w.outcome === 'fails') {
    return `On growth, no interior mix beats the better end; the best one trails it by ${size}.`
  }
  // At or below zero the better end is best (ties go to an end), so the
  // interior mix is "within" it -- never "beating" or, at exactly 0, "trailing".
  return interior
    ? `On growth, ${hedged(w.best_weight)} beats both ends, but only by ${size}, under the 1bp/yr threshold.`
    : `On growth, no interior mix beats the better end, but the best one is within ${size} of it, under the 1bp/yr threshold.`
}

const RA_WHERE: Record<OverlayOutcome, string> = {
  holds: 'an interior mix wins',
  inconclusive: 'too close to call',
  fails: 'an end wins',
}

function riskAdjustedSentence(w: OverlayWindow): string {
  if (w.best_weight_risk_adjusted == null || w.outcome_risk_adjusted == null) {
    return 'CAGR per unit of vol is undefined here (a mix has zero volatility).'
  }
  return `On CAGR per unit of vol the best mix is ${hedged(w.best_weight_risk_adjusted)} (${RA_WHERE[w.outcome_risk_adjusted]}).`
}

function Verdict({ w }: { w: OverlayWindow }) {
  const tag = TAG[w.outcome]
  return (
    <p data-testid="verdict">
      <span className={tag.cls}>{tag.text}</span> {growthSentence(w)} {riskAdjustedSentence(w)}
    </p>
  )
}

function WindowTable({ w, symbol }: { w: OverlayWindow; symbol: string }) {
  return (
    <section aria-label={`${symbol} ${w.label}`} style={{ marginBottom: 18 }}>
      <h4 style={{ marginBottom: 2 }}>
        {w.label}: {w.start} to {w.end}
      </h4>
      {w.clipped && (
        <p className="pl-caveat" data-testid="clipped">
          Shortened: the data cover {w.start} to {w.end}, not the {w.requested_start} to {w.requested_end} asked for
          ({symbol} or the S&P 500 starts late or stops early, or the as-of date falls inside the window).
        </p>
      )}
      <Verdict w={w} />
      <p className="pl-lede" data-testid="sensitivity">
        Best interior mix vs the better end, by assumed dividend yield:{' '}
        {w.sensitivity
          .map(
            (s) =>
              `at ${pct(s.dividend_yield)}, ${TAG[s.outcome].text.toLowerCase()} (best ${s.best_weight === 0 || s.best_weight === 1 ? 'is an end' : `mix ${hedged(s.best_weight)}`}, ${pp(s.margin)})`,
          )
          .join('; ')}
        .
      </p>
      <div className="pl-scroll">
      <table className="pl-table">
        <thead>
          <tr>
            <th>Hedge ratio</th>
            <th className="num">Growth (CAGR)</th>
            <th className="num">Volatility</th>
            <th className="num">Max drawdown</th>
            <th className="num">CAGR / vol</th>
          </tr>
        </thead>
        <tbody>
          {w.points.map((p) => (
            <tr key={p.weight} aria-current={p.weight === w.best_weight ? 'true' : undefined}>
              <td>
                {p.weight === 0 ? 'S&P 500 only' : p.weight === 1 ? `${symbol} only` : hedged(p.weight)}
                {p.weight === w.best_weight && <span className="pl-tag pl-tag-ok"> best growth</span>}
              </td>
              <td className="num">{pct(p.cagr, 2)}</td>
              <td className="num">{pct(p.volatility)}</td>
              <td className="num">{pct(p.max_drawdown)}</td>
              <td className="num">{p.cagr_per_vol == null ? 'n/a' : fmtFixed(p.cagr_per_vol, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </section>
  )
}

export function OverlayView() {
  const [state, setState] = useState<State>({ status: 'loading' })

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
    <div>
      <h2 style={{ marginBottom: 4 }}>The Book</h2>
      <p className="pl-lede" style={{ marginBottom: 10 }}>
        How much of the equity book should carry the hedge? Rodman's Paradox (Artemis Capital, 2016): an asset that
        loses money most years can still make the whole book grow faster, by paying off in the crash. The letter's long-vol fund index is licensed, so this blends each Cboe
        hedge program (priced from real OPRA trades or quotes) with the plain S&P 500 at every hedge ratio, rebalanced monthly. The
        paradox holds if some mix grows faster than both 0% and 100% hedged, by more than 1bp a year.
      </p>
      <p className="pl-caveat" style={{ marginBottom: 14 }}>
        In sample, one history. The S&P 500 leg's dividends are an assumed flat yield
        {state.status === 'ready' ? ` (${pct(state.data.overlay.dividend_yield)})` : ''}, not measured; a low guess
        understates the unhedged leg against every hedged one, so each verdict is re-run at other yields below. CAGR / vol nets no risk-free rate
        and tends to favour any mix that lowers volatility; growth is the test that sizes a hedge.
      </p>
      {state.status === 'loading' && <p className="pl-lede">Blending…</p>}
      {state.status === 'error' && <p className="pl-caveat">{state.message}</p>}
      {state.status === 'ready' && (
        <>
          {Object.entries(state.data.overlay.missing).map(([symbol, reason]) => (
            <p key={symbol} className="pl-caveat" data-testid="missing">
              {symbol}: not tested ({reason}).
            </p>
          ))}
          {state.data.overlay.programs.map((prog) => (
            <section key={prog.index_symbol} style={{ marginBottom: 24 }}>
              <h3 style={{ marginBottom: 4 }}>
                {prog.index_symbol}: {prog.description}
              </h3>
              {prog.windows.map((w) => (
                <WindowTable key={w.key} w={w} symbol={prog.index_symbol} />
              ))}
              {Object.entries(prog.unavailable).map(([key, reason]) => (
                <p key={key} className="pl-caveat" data-testid="unavailable">
                  {key === 'cole' ? "The letter's window" : 'Full history'}: not computed ({reason}).
                </p>
              ))}
            </section>
          ))}
          <p className="pl-lede" style={{ fontSize: 12 }}>
            Cboe snapshot {state.data.cboe_snapshot ?? 'unknown'} · code {state.data.code_sha} · as of{' '}
            {state.data.overlay.as_of}
          </p>
        </>
      )}
    </div>
  )
}
