import { useState } from 'react'
import {
  ApiError,
  fetchPortfolio,
  fetchPutLabLeaderboard,
  type PortfolioLeg,
  type PortfolioResponse,
  type UniverseMember,
} from '../../api/client'
import { EquityCurve } from './EquityCurve'
import { fmtDollar, fmtPct } from './format'
import { PUTLAB_ASSETS, PUTLAB_TENORS } from './types'

type State =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; result: PortfolioResponse }

const VERDICT_LABEL: Record<PortfolioResponse['verdict'], string> = {
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

// Mix a basket of OOM-put legs into one hedge and see the blended result --
// combined P&L, the diversification effect (combined drawdown vs the legs'
// summed), and each leg's contribution + verdict. Weights are shares of total
// capital (normalized), so a short-tenor leg doesn't silently dominate.
export function Portfolio({ universe }: { universe: UniverseMember[] }) {
  const [legs, setLegs] = useState<PortfolioLeg[]>(DEFAULT_LEGS)
  const [notional, setNotional] = useState(10000)
  const [years, setYears] = useState(4)
  const [state, setState] = useState<State>({ status: 'idle' })
  const [basketK, setBasketK] = useState(5)
  const [screening, setScreening] = useState(false)

  const options = universe.length > 0 ? universe.map((m) => ({ value: m.symbol.toLowerCase(), label: m.name })) : PUTLAB_ASSETS

  const setLeg = (i: number, patch: Partial<PortfolioLeg>) =>
    setLegs((ls) => ls.map((l, k) => (k === i ? { ...l, ...patch } : l)))
  const addLeg = () => setLegs((ls) => [...ls, { asset: 'qqq', moneyness_pct: 5, tenor_weeks: 4, weight: 1 }])
  const removeLeg = (i: number) => setLegs((ls) => ls.filter((_, k) => k !== i))

  const totalWeight = legs.reduce((s, l) => s + l.weight, 0) || 1

  const run = (useLegs: PortfolioLeg[] = legs) => {
    setState({ status: 'loading' })
    fetchPortfolio({ legs: useLegs, notional, years })
      .then((result) => setState({ status: 'ready', result }))
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError ? `Portfolio backtest failed (${err.status})` : err instanceof Error ? err.message : String(err)
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
          setState({ status: 'error', message: 'No names with an estimable fragility score were found to build a basket.' })
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
          err instanceof ApiError ? `Fragility screen failed (${err.status})` : err instanceof Error ? err.message : String(err)
        setScreening(false)
        setState({ status: 'error', message })
      })
  }

  const r = state.status === 'ready' ? state.result : null
  const saved = r ? r.combined_max_drawdown - r.sum_individual_max_drawdown : 0 // >= 0, capital NOT lost by mixing

  return (
    <section className="panel" style={{ marginTop: 22 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">The portfolio</span>
          <h2 style={{ marginTop: 6 }}>Mix a basket of puts</h2>
          <div className="hint">
            Blend several OOM-put legs into one hedge. Weights are shares of total capital (normalized), so a
            short-tenor leg can&rsquo;t silently dominate. See the combined P&amp;L and how the mix&rsquo;s drawdown
            compares to the legs&rsquo; summed &mdash; that gap is diversification.
          </div>
        </div>
        <button className="lb-run" onClick={() => run()} disabled={state.status === 'loading' || legs.length === 0}>
          {state.status === 'loading' ? 'Backtesting…' : 'Backtest portfolio'}
        </button>
      </div>

      <div className="pf-controls">
        <button className="lb-run" onClick={loadFragileBasket} disabled={screening || state.status === 'loading'}>
          {screening ? 'Screening…' : '⚡ Load the fragile basket'}
        </button>
        <label className="pf-field">
          Top
          <select value={basketK} onChange={(e) => setBasketK(+e.target.value)} aria-label="Basket size">
            {BASKET_SIZES.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <span className="hint">
          Equal-weight puts on the K most fragile names &mdash; the timing-free strategy as one basket, screened live.
        </span>
      </div>

      <div className="pf-builder">
        {legs.map((leg, i) => (
          <div className="pf-leg" key={i}>
            <select value={leg.asset} onChange={(e) => setLeg(i, { asset: e.target.value })} aria-label="Leg asset">
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <label className="pf-field">
              <input
                type="number"
                min={1}
                max={25}
                value={leg.moneyness_pct}
                onChange={(e) => setLeg(i, { moneyness_pct: +e.target.value })}
              />
              % OOM
            </label>
            <select value={leg.tenor_weeks} onChange={(e) => setLeg(i, { tenor_weeks: +e.target.value })} aria-label="Leg tenor">
              {PUTLAB_TENORS.map((t) => (
                <option key={t.weeks} value={t.weeks}>
                  {t.label}
                </option>
              ))}
            </select>
            <label className="pf-field">
              <input
                type="number"
                min={0}
                step={1}
                value={leg.weight}
                onChange={(e) => setLeg(i, { weight: +e.target.value })}
              />
              wt ({Math.round((leg.weight / totalWeight) * 100)}%)
            </label>
            <button className="pf-remove" onClick={() => removeLeg(i)} aria-label="Remove leg" disabled={legs.length <= 1}>
              &times;
            </button>
          </div>
        ))}
        <div className="pf-controls">
          <button className="pf-add" onClick={addLeg}>
            + Add leg
          </button>
          <label className="pf-field">
            $
            <input type="number" min={100} step={1000} value={notional} onChange={(e) => setNotional(+e.target.value)} />
            total
          </label>
          <label className="pf-field">
            <input type="number" min={1} max={20} value={years} onChange={(e) => setYears(+e.target.value)} />
            years
          </label>
        </div>
      </div>

      {state.status === 'error' && (
        <p className="putlab-status putlab-status-error" role="alert">
          {state.message}
        </p>
      )}

      {r && (
        <>
          <div className="stats" style={{ marginTop: 18 }}>
            <div className="stat hero-stat">
              <div className="k">Return on premium</div>
              <div className={`v ${r.roi_on_premium >= 0 ? 'pos' : 'neg'}`}>{fmtPct(r.roi_on_premium)}</div>
              <div className="note">
                <span className={`badge ${r.verdict}`}>{VERDICT_LABEL[r.verdict]}</span>
              </div>
            </div>
            <div className="stat">
              <div className="k">Net P&amp;L</div>
              <div className={`v ${r.net_pnl >= 0 ? 'pos' : 'neg'}`}>{fmtDollar(r.net_pnl)}</div>
              <div className="note">on {fmtDollar(r.total_premium)} premium</div>
            </div>
            <div className="stat">
              <div className="k">Combined bleed</div>
              <div className="v">{fmtDollar(r.combined_max_drawdown)}</div>
              <div className="note">worst drawdown of the mix</div>
            </div>
            <div className="stat">
              <div className="k">Diversification</div>
              <div className="v pos">{fmtDollar(saved)}</div>
              <div className="note">shallower than the legs&rsquo; summed drawdown</div>
            </div>
          </div>

          <section className="panel" style={{ marginTop: 18 }}>
            <div className="panel-head">
              <div>
                <h2>Combined P&amp;L</h2>
                <div className="hint">Every leg&rsquo;s realized P&amp;L, summed on one date axis.</div>
              </div>
            </div>
            <EquityCurve equityCurve={r.equity_curve} cycles={[]} />
          </section>

          <div className="lb-scroll" style={{ marginTop: 14 }}>
            <table className="lb-table mono">
              <thead>
                <tr>
                  <th>Leg</th>
                  <th className="lb-num">Weight</th>
                  <th className="lb-num">Premium</th>
                  <th className="lb-num">Return</th>
                  <th>Verdict</th>
                  <th className="lb-num">Rolls</th>
                </tr>
              </thead>
              <tbody>
                {r.legs.map((leg) => (
                  <tr key={leg.asset}>
                    <td className="lb-name">{leg.name}</td>
                    <td className="lb-num">{Math.round(leg.weight * 100)}%</td>
                    <td className="lb-num">{fmtDollar(leg.total_premium)}</td>
                    <td className="lb-num" style={{ color: leg.roi_on_premium >= 0 ? 'var(--gain)' : 'var(--loss)', fontWeight: 700 }}>
                      {fmtPct(leg.roi_on_premium)}
                    </td>
                    <td>
                      <span className={`badge ${leg.verdict}`}>{VERDICT_LABEL[leg.verdict]}</span>
                    </td>
                    <td className="lb-num">{leg.n_cycles}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
