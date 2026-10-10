import type { DividendBasis, DividendSource, PutBacktestResponse } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtPrice } from './format'

/* "How the price is built": the latest roll's premium, taken apart into the
 * inputs that produced it, with the dividend yield redone by hand from the
 * payments it came from. The first step of the thread a reader follows --
 * where q comes from -> how it moves the price -> how the strike is chosen ->
 * whether any of it earns a place in the book.
 *
 * Collapsed by default: it is the derivation, not the result. But every number
 * in it is the live one, not an illustration.
 */

const SOURCE_TEXT: Record<DividendSource, string> = {
  measured: "measured from this name's own paid dividends",
  non_payer: 'a real zero: this name has never paid a dividend',
  suspended: 'a real zero: this name stopped paying (its last dividend is more than two periods old)',
  short_history: 'measured, but from fewer payments than a full year, scaled up to one',
  carried: "measured on the data's last day and carried forward",
  stale: "measured, but carried more than three weeks past the data's last day -- refresh Tiingo",
  unknown:
    'unknown, so the roll was priced at q = 0: no dividend data for this name, a single dividend so far (no frequency to read), or a dividend the close cannot support',
}

const pct = (x: number, digits = 2) => `${(x * 100).toFixed(digits)}%`
const days = (n: number) => `${n} day${n === 1 ? '' : 's'}`

/** The session q was measured on: the requested date, less any carry. */
function measuredOn(basis: DividendBasis): string {
  const d = new Date(`${basis.date}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - basis.age_days)
  return d.toISOString().slice(0, 10)
}

export function PriceBuild({ bt }: { bt: PutBacktestResponse }) {
  const last = bt.cycles[bt.cycles.length - 1]
  if (!last) return null
  const basis = bt.dividend_basis
  const moneyness = 1 - last.strike / last.spot
  return (
    <details className="pl-explain" data-testid="price-build">
      <summary>How the price is built</summary>
      <p>
        Every roll is priced as a Black&ndash;Scholes put with five inputs: the spot, the strike, the time to expiry,
        a flat rate, and a volatility &mdash; plus the name&rsquo;s <strong>dividend yield q</strong>
        <ConceptInfo id="dividend_yield" />. The volatility is the trailing 20-day realised volatility, an
        implied-vol stand-in clamped to 6&ndash;200%
        <ConceptInfo id="model_priced" />. The latest roll, as priced:
      </p>
      <dl className="pl-explain-inputs" data-testid="price-build-inputs">
        <dt>Entry</dt>
        <dd>{last.entry_date}</dd>
        <dt>Spot</dt>
        <dd>{fmtPrice(last.spot)}</dd>
        <dt>Strike</dt>
        <dd>
          {fmtPrice(last.strike)} ({pct(moneyness, 1)} below spot)
        </dd>
        <dt>Time</dt>
        <dd>{last.t_years == null ? 'to the listed expiry' : `${last.t_years.toFixed(4)} years (trading days / 252)`}</dd>
        <dt>Rate r</dt>
        <dd>{pct(bt.rate, 3)}</dd>
        <dt>Volatility σ</dt>
        <dd>{pct(last.sigma, 3)} (20-day realised)</dd>
        <dt>Dividend yield q</dt>
        <dd data-testid="price-build-q">
          {bt.priced_from === 'market' ? 'not used: the premium is a real quote' : pct(last.q, 3)}
        </dd>
        <dt>Premium</dt>
        <dd data-testid="price-build-premium">
          {fmtPrice(last.premium)} per share
          {last.premium_floored && (
            <span className="pl-caveat" data-testid="price-build-floored">
              {' '}
              &mdash; a floor (spot &times; 0.0001), not a model price: Black&ndash;Scholes gives less at this depth, so
              it cannot be reproduced from the inputs above
            </span>
          )}
        </dd>
      </dl>
      {basis && <DividendDerivation basis={basis} />}
      <p className="pl-caveat">
        What a trailing yield cannot see: a cut shows up only as the next payments arrive (up to a year late), a
        special dividend moves q for up to a year &mdash; up if it is larger than a regular payment, down if smaller
        &mdash; and a dividend falling inside a short put&rsquo;s life is spread
        across the year &mdash; a 21-day SPY put across an ex-date loses a whole quarterly dividend (~0.3% of spot at q &asymp; 1.3%),
        where q&middot;T, with T = 21/252, charges ~0.1%.
      </p>
      <p className="pl-note">
        Next: the strike itself is a fixed distance below spot here, which reaches very different deltas in calm and
        crisis. Whether any of this earns a place in a portfolio is the Book&rsquo;s question &mdash; and there only
        Cboe&rsquo;s real-quote programs count as evidence, never a model-priced backtest like this one (ADR 0027).
      </p>
    </details>
  )
}

function DividendDerivation({ basis }: { basis: DividendBasis }) {
  const total = basis.payments.reduce((s, p) => s + p.adjusted, 0)
  const scaled = basis.payments.length > 0 && basis.payments.length < basis.per_year
  const carried = basis.age_days > 0 ? ` (${days(basis.age_days)} old)` : ''
  const on = measuredOn(basis)
  return (
    <div data-testid="price-build-dividends">
      <p>
        <strong>Where q comes from:</strong> {SOURCE_TEXT[basis.source]}
        {carried}.
      </p>
      {basis.payments.length > 0 && basis.close != null && (
        <>
          <p className="pl-explain-formula" data-testid="price-build-formula">
            q = &minus;ln(1 &minus; D / S) = &minus;ln(1 &minus; {basis.annual.toFixed(2)} / {basis.close.toFixed(2)}) ={' '}
            {pct(basis.q, 3)}
          </p>
          <p data-testid="price-build-d">
            {scaled ? (
              <>
                D is a full year from a short history: the {basis.payments.length} dividends below sum to{' '}
                {total.toFixed(2)}, scaled by {basis.per_year}/{basis.payments.length} to the {basis.per_year} a year this
                name pays.
              </>
            ) : (
              <>
                D is the last {basis.per_year} dividends &mdash; this name pays {basis.per_year} a year &mdash; each on the
                share basis of {on}.
              </>
            )}{' '}
            A later split divides the cash paid; S is the as-traded close on {on}
            {basis.age_days > 0 ? `, the data's last day, ${days(basis.age_days)} before ${basis.date}` : ''}.
          </p>
          <div className="pl-scroll">
            <table className="pl-table">
              <thead>
                <tr>
                  <th>Ex-date</th>
                  <th className="num">Paid per share</th>
                  <th className="num">On {on}&rsquo;s basis</th>
                </tr>
              </thead>
              <tbody>
                {basis.payments.map((p) => (
                  <tr key={p.ex_date}>
                    {/* The date labels its row: a row header, not a figure cell. */}
                    <th scope="row" className="pl-explain-rowhead">
                      {p.ex_date}
                    </th>
                    <td className="num">{p.cash.toFixed(4)}</td>
                    <td className="num">{p.adjusted.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {basis.source === 'unknown' && (
        <p className="pl-caveat" data-testid="price-build-unknown">
          Priced at q = 0. On any dividend payer that under-prices the put &mdash; before measured dividends existed,
          HYG&rsquo;s delta was off by 0.197 against the exchange&rsquo;s own.
        </p>
      )}
    </div>
  )
}
