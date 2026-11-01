import type { PutBacktestCycle } from '../../api/client'
import { fmtDollar, fmtFixed, fmtPrice } from './format'
import { Th } from './Th'

/* The roll ledger — every cycle the backtest actually placed.
 *
 * Each roll spends the whole Budget on puts (`contracts = notional / premium`,
 * shares, not whole 100-share contracts) and pays Fill on top: the entry
 * brokerage, commission plus half-spread (`put_roll.py`'s `cost`). So Net is
 * `payoff - budget - fill`, a subtraction a reader can do across the row.
 * Every return on the page divides by the budget (concepts.ts roi_on_premium:
 * `total_premium = n_cycles x notional`), not by budget plus brokerage.
 *
 * Cts is printed in whole shares: the field is an unrounded float, and printed
 * raw it ran to sixteen digits.
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
            <Th concept="roll_cost" className="num">Fill</Th>
            <Th concept="roll_budget" className="num">Budget</Th>
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
              <td className="num dim">{fmtFixed(c.contracts, 0)}</td>
              {/* entry brokerage, paid on top of the budget */}
              <td className="num dim">{fmtDollar(c.cost)}</td>
              {/* spent in full on the puts */}
              <td className="num dimmer">{fmtDollar(notional)}</td>
              <td className="num dim">{fmtDollar(c.payoff)}</td>
              <td className={`num bold ${c.net >= 0 ? 'pl-pos' : 'pl-neg'}`}>{fmtDollar(c.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
