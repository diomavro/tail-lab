import type { PutBacktestCycle } from '../../api/client'
import { fmtDollar, fmtPrice } from './format'

/* The roll ledger — every cycle the backtest actually placed.
 *
 * The change worth reading: Fill and Budget are separate columns.
 *
 * The old table showed one "Cost" column holding `contracts x premium x 100` —
 * the cash actually filled — next to a Net computed as `payoff - notional`, the
 * per-roll premium BUDGET. Those are different numbers, so a reader subtracting
 * the two visible columns got the wrong answer. `max(1, floor(notional /
 * (premium * 100)))` forces at least one contract, so the fill can land either
 * side of the budget: at a $1,000 budget an $8.27 premium fills at $827, a
 * $10.28 premium fills at $1,028.
 *
 * Every return on the page divides by the budget (concepts.ts roi_on_premium:
 * `total_premium = n_cycles x notional`), so Budget is the column Net
 * reconciles against, and Fill is shown because what the contracts really cost
 * is still worth knowing.
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
            <th>Entry</th>
            <th>Expiry</th>
            <th className="num">Spot</th>
            <th className="num">Strike</th>
            <th className="num">&sigma;</th>
            <th className="num">Premium</th>
            <th className="num">Cts</th>
            <th className="num">Fill</th>
            <th className="num">Budget</th>
            <th className="num">Payoff</th>
            <th className="num">Net</th>
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
              <td className="num dim">{c.contracts}</td>
              {/* the cash the contracts really cost */}
              <td className="num dim">{fmtDollar(c.cost)}</td>
              {/* the basis Net is measured against */}
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
