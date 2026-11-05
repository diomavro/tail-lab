import type { PutBacktestCycle } from '../../api/client'
import { fmtDollar, fmtPrice } from './format'
import { Th } from './Th'

/* The roll ledger — every cycle the backtest actually placed, columns in the
 * order put_roll.py builds a roll from them.
 *
 * Cts is `budget / premium` shares, not whole 100-share contracts: the backtest
 * spends the budget exactly and never rounds (put_roll.py), so the cash the puts
 * cost IS the Budget column. Brokerage is the roll's `cost` field — commission
 * per real contract plus a modelled half-spread on the budget
 * (brokerage.roll_cost) — and it is charged on top of the budget, so
 *
 *     Net = Payoff − Budget − Brokerage
 *
 * is the subtraction a reader can do on screen, right to within the whole-dollar
 * rounding of the printed figures. An earlier
 * version labelled `cost` "Fill" and described it as the cash the contracts
 * cost; the e2e fixture had been built to match that description, so the suite
 * passed while every live row said otherwise.
 *
 * A dead roll prints $0 rather than an em dash: the em dash reads better, but
 * it breaks the one subtraction this table exists to let a reader perform.
 */

export function Ledger({ cycles, notional }: { cycles: PutBacktestCycle[]; notional: number }) {
  if (cycles.length === 0) return null

  return (
    <div className="pl-scroll pl-scroll-y">
      <table className="pl-table pl-ledger" aria-label="Roll ledger">
        <thead className="sticky">
          <tr>
            <Th concept="roll_dates">Entry</Th>
            <Th concept="roll_dates">Expiry</Th>
            <Th concept="roll_spot" className="num">Spot</Th>
            <Th concept="strike_price" className="num">Strike</Th>
            <Th concept="roll_sigma" className="num">&sigma;</Th>
            <Th concept="roll_premium" className="num">Premium</Th>
            <Th concept="roll_contracts" className="num">Cts</Th>
            <Th concept="roll_budget" className="num">Budget</Th>
            <Th concept="roll_cost" className="num">Brokerage</Th>
            <Th concept="roll_payoff" className="num">Payoff</Th>
            <Th concept="roll_net" className="num">Net</Th>
          </tr>
        </thead>
        <tbody>
          {cycles.map((c) => (
            <tr key={`${c.entry_date}-${c.expiry_date}`}>
              <td className="nowrap">{c.entry_date}</td>
              <td className="nowrap dim">{c.expiry_date}</td>
              <td className="num dim">{fmtPrice(c.spot)}</td>
              <td className="num dim">{fmtPrice(c.strike)}</td>
              <td className="num dim">{(c.sigma * 100).toFixed(1)}%</td>
              <td className="num dim">{fmtPrice(c.premium)}</td>
              {/* fractional shares: whole ones are enough to read and check budget / premium */}
              <td className="num dim">{Math.round(c.contracts).toLocaleString('en-US')}</td>
              {/* the cash the puts cost, and the basis every return divides by */}
              <td className="num dimmer">{fmtDollar(notional)}</td>
              {/* charged on top of the budget */}
              <td className="num dim">{fmtDollar(c.cost)}</td>
              <td className="num dim">{fmtDollar(c.payoff)}</td>
              <td className={`num bold ${c.net >= 0 ? 'pl-pos' : 'pl-neg'}`}>{fmtDollar(c.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
