import { useState } from 'react'
import {
  ApiError,
  fetchPortfolio,
  fetchPutLabLeaderboard,
  type PortfolioLeg,
  type PortfolioResponse,
  type UniverseMember,
  type Verdict,
} from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { EquityCurve } from './EquityCurve'
import { fmtDollar, fmtPct } from './format'
import { PUTLAB_ASSETS, PUTLAB_TENORS, type PutLabControls } from './types'

// Mix a basket of OOM-put legs into one hedge and see the blended result --
// combined P&L, the diversification effect (combined drawdown vs the legs'
// summed), and each leg's contribution + verdict.
//
// Behaviour is unchanged from the panel this replaces; the styling moved to
// .pl-* and the weight copy was corrected. A weight is a share of TOTAL CAPITAL
// OVER THE WINDOW, so a leg's per-roll budget is `notional x share / n_cycles`.
// Reading the share as a per-roll figure instead makes total premium scale with
// roll count, which lets a short-tenor leg silently dominate the basket.

type State =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; result: PortfolioResponse }

const VERDICT_TAG: Record<Verdict, string> = {
  confirmed: 'pl-tag pl-tag-ok',
  regime_only: 'pl-tag pl-tag-mute',
  failed: 'pl-tag pl-tag-bad',
  untested: 'pl-tag pl-tag-outline',
}
const VERDICT_LABEL: Record<Verdict, string> = {
  confirmed: 'confirmed',
  regime_only: 'regime only',
  failed: 'failed',
  untested: 'untested',
}

const DEFAULT_LEGS: PortfolioLeg[] = [
  { asset: 'spy', moneyness_pct: 5, tenor_weeks: 4, weight: 1 },
  { asset: 'tsla', moneyness_pct: 10, tenor_weeks: 4, weight: 1 },
]

const BASKET_SIZES = [3, 5, 8]

export function Portfolio({
  universe,
  controls,
}: {
  universe: UniverseMember[]
  controls: PutLabControls
}) {
  const [legs, setLegs] = useState<PortfolioLeg[]>(DEFAULT_LEGS)
  const [notional, setNotional] = useState(10000)
  const [years, setYears] = useState(controls.years)
  const [state, setState] = useState<State>({ status: 'idle' })
  const [basketK, setBasketK] = useState(5)
  const [screening, setScreening] = useState(false)

  const options =
    universe.length > 0
      ? universe.map((m) => ({ value: m.symbol.toLowerCase(), label: m.name }))
      : PUTLAB_ASSETS

  const setLeg = (i: number, patch: Partial<PortfolioLeg>) =>
    setLegs((ls) => ls.map((l, k) => (k === i ? { ...l, ...patch } : l)))
  const addLeg = () =>
    setLegs((ls) => [...ls, { asset: 'qqq', moneyness_pct: 5, tenor_weeks: 4, weight: 1 }])
  const removeLeg = (i: number) => setLegs((ls) => ls.filter((_, k) => k !== i))

  const totalWeight = legs.reduce((s, l) => s + l.weight, 0) || 1

  const run = (useLegs: PortfolioLeg[] = legs) => {
    setState({ status: 'loading' })
    fetchPortfolio({ legs: useLegs, notional, years })
      .then((result) => setState({ status: 'ready', result }))
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? `Portfolio backtest failed (${err.status})`
            : err instanceof Error
              ? err.message
              : String(err)
        setState({ status: 'error', message })
      })
  }

  const loadFragileBasket = () => {
    setScreening(true)
    fetchPutLabLeaderboard({ moneyness_pct: 10, tenor_weeks: 4, years })
      .then((resp) => {
        const picks = resp.ranked.filter((r) => r.fragility_score !== null).slice(0, basketK)
        if (picks.length === 0) {
          setScreening(false)
          setState({
            status: 'error',
            message: 'No names with an estimable fragility score were found to build a basket.',
          })
          return
        }
        const newLegs: PortfolioLeg[] = picks.map((r) => ({
          asset: r.asset,
          moneyness_pct: 10,
          tenor_weeks: 4,
          weight: 1,
        }))
        setLegs(newLegs)
        setScreening(false)
        run(newLegs)
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? `Fragility screen failed (${err.status})`
            : err instanceof Error
              ? err.message
              : String(err)
        setScreening(false)
        setState({ status: 'error', message })
      })
  }

  const r = state.status === 'ready' ? state.result : null
  // >= 0: the drawdown the mix never took. Combined and summed are both
  // negative, so the gap between them is what diversification saved.
  const saved = r ? r.combined_max_drawdown - r.sum_individual_max_drawdown : 0
  const busy = state.status === 'loading' || screening

  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>Mix a basket of puts</h2>
      <p className="pl-lede" style={{ marginBottom: 20 }}>
        Blend several OOM-put legs into one hedge and read the combined P&amp;L against the legs&rsquo;
        summed drawdown &mdash; that gap is diversification. Each leg&rsquo;s weight is its{' '}
        <strong>share of total capital over the window</strong>, so its per-roll budget is{' '}
        <span className="pl-formula">notional × share ÷ n_cycles</span>. Read the share as a
        per-roll figure instead and total premium scales with roll count, which lets a short-tenor
        leg quietly dominate.
      </p>

      <div className="pl-leg-actions">
        <button
          type="button"
          className="pl-btn pl-btn-secondary"
          onClick={loadFragileBasket}
          disabled={busy}
        >
          {screening ? 'Screening…' : 'Load the fragile basket'}
        </button>
        <div className="pl-field">
          <label id="pl-basket-k">Basket size</label>
          <div className="pl-seg" role="radiogroup" aria-labelledby="pl-basket-k">
            {BASKET_SIZES.map((k) => (
              <label className="pl-seg-opt" key={k}>
                <input
                  type="radio"
                  name="pl-basket-k"
                  checked={basketK === k}
                  onChange={() => setBasketK(k)}
                />
                Top {k}
              </label>
            ))}
          </div>
        </div>
        <span className="pl-micro">
          Equal-weight puts on the K most fragile names &mdash; the timing-free strategy as one
          basket, screened live.
        </span>
      </div>

      <div className="pl-legs">
        {legs.map((leg, i) => (
          <div className="pl-leg" key={i}>
            <div className="pl-field">
              <label htmlFor={`pl-leg-asset-${i}`}>Name</label>
              <select
                id={`pl-leg-asset-${i}`}
                className="pl-input"
                value={leg.asset}
                onChange={(e) => setLeg(i, { asset: e.target.value })}
              >
                {options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="pl-field">
              <label htmlFor={`pl-leg-oom-${i}`}>% OOM</label>
              <input
                id={`pl-leg-oom-${i}`}
                className="pl-input"
                type="number"
                min={1}
                max={25}
                value={leg.moneyness_pct}
                onChange={(e) => setLeg(i, { moneyness_pct: +e.target.value })}
              />
            </div>
            <div className="pl-field">
              <label htmlFor={`pl-leg-tenor-${i}`}>Tenor</label>
              <select
                id={`pl-leg-tenor-${i}`}
                className="pl-input"
                value={leg.tenor_weeks}
                onChange={(e) => setLeg(i, { tenor_weeks: +e.target.value })}
              >
                {PUTLAB_TENORS.map((t) => (
                  <option key={t.weeks} value={t.weeks}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="pl-field">
              <label htmlFor={`pl-leg-wt-${i}`}>
                Weight ({Math.round((leg.weight / totalWeight) * 100)}%)
              </label>
              <input
                id={`pl-leg-wt-${i}`}
                className="pl-input"
                type="number"
                min={0}
                step={1}
                value={leg.weight}
                onChange={(e) => setLeg(i, { weight: +e.target.value })}
              />
            </div>
            <button
              type="button"
              className="pl-btn pl-btn-icon pl-btn-secondary"
              onClick={() => removeLeg(i)}
              aria-label="Remove leg"
              disabled={legs.length <= 1}
            >
              &times;
            </button>
          </div>
        ))}
      </div>

      <div className="pl-leg-actions">
        <button type="button" className="pl-btn pl-btn-secondary" onClick={addLeg}>
          Add leg
        </button>
        <div className="pl-field">
          <label htmlFor="pl-pf-notional">Total capital, $</label>
          <input
            id="pl-pf-notional"
            className="pl-input"
            type="number"
            min={100}
            step={1000}
            value={notional}
            onChange={(e) => setNotional(+e.target.value)}
          />
        </div>
        <div className="pl-field">
          <label htmlFor="pl-pf-years">Years</label>
          <input
            id="pl-pf-years"
            className="pl-input"
            type="number"
            min={1}
            max={20}
            value={years}
            onChange={(e) => setYears(+e.target.value)}
          />
        </div>
        <button
          type="button"
          className="pl-btn pl-btn-primary"
          onClick={() => run()}
          disabled={busy || legs.length === 0}
        >
          {state.status === 'loading' ? 'Backtesting…' : 'Backtest the basket'}
        </button>
      </div>

      {state.status === 'error' && (
        <p className="pl-status pl-status-error" role="alert">
          {state.message}
        </p>
      )}

      {r && (
        <>
          <div className="pl-stats">
            <div>
              <div className="pl-stat-k">
                Return on premium
                <ConceptInfo id="roi_on_premium" />
              </div>
              <div className={`pl-stat-v ${r.roi_on_premium >= 0 ? 'pl-pos' : 'pl-neg'}`}>
                {fmtPct(r.roi_on_premium)}
              </div>
              <div className="pl-stat-note">
                {fmtPct(r.annualized_return)}/yr &middot;{' '}
                <span className={VERDICT_TAG[r.verdict]}>{VERDICT_LABEL[r.verdict]}</span>
              </div>
            </div>
            <div>
              <div className="pl-stat-k">Net P&amp;L</div>
              <div className={`pl-stat-v ${r.net_pnl >= 0 ? 'pl-pos' : 'pl-neg'}`}>
                {fmtDollar(r.net_pnl)}
              </div>
              <div className="pl-stat-note">on {fmtDollar(r.total_premium)} premium</div>
            </div>
            <div>
              <div className="pl-stat-k">Combined bleed</div>
              <div className="pl-stat-v pl-neg">{fmtDollar(r.combined_max_drawdown)}</div>
              <div className="pl-stat-note">worst drawdown of the mix</div>
            </div>
            <div>
              <div className="pl-stat-k">
                Diversification
                <ConceptInfo id="diversification" />
              </div>
              <div className="pl-stat-v">{fmtDollar(saved)}</div>
              <div className="pl-stat-note">
                shallower than the legs&rsquo; summed {fmtDollar(r.sum_individual_max_drawdown)}
              </div>
            </div>
          </div>

          <section className="pl-section">
            <div className="pl-section-head">
              <h3>Combined P&amp;L</h3>
            </div>
            <p className="pl-note" style={{ marginBottom: 12 }}>
              Every leg&rsquo;s realized P&amp;L, summed on one date axis.
            </p>
            <EquityCurve equityCurve={r.equity_curve} cycles={[]} />
          </section>

          <section className="pl-section">
            <div className="pl-section-head">
              <h3>The legs</h3>
            </div>
            <div className="pl-scroll">
              <table className="pl-table" aria-label="Basket legs">
                <thead>
                  <tr>
                    <th>Leg</th>
                    <th className="num">Weight</th>
                    <th className="num">Premium</th>
                    <th className="num">Return</th>
                    <th className="num">/yr</th>
                    <th className="num">Rolls</th>
                    <th className="num">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {r.legs.map((leg) => (
                    <tr key={leg.asset}>
                      <td className="nowrap">{leg.name}</td>
                      <td className="num dim">{Math.round(leg.weight * 100)}%</td>
                      <td className="num dim">{fmtDollar(leg.total_premium)}</td>
                      <td className={`num bold ${leg.roi_on_premium >= 0 ? 'pl-pos' : 'pl-neg'}`}>
                        {fmtPct(leg.roi_on_premium)}
                      </td>
                      <td className="num dim">{fmtPct(leg.annualized_return)}</td>
                      <td className="num dim">{leg.n_cycles}</td>
                      <td className="num">
                        <span className={VERDICT_TAG[leg.verdict]}>
                          {VERDICT_LABEL[leg.verdict]}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
