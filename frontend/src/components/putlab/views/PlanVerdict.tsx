import type { PlanArm, PlanWindow, RollingSummary } from '../../../api/client'
import { pct, pp, share, usd } from './planFormat'

function ArmRow({ arm }: { arm: PlanArm }) {
  return (
    <tr>
      <td>{arm.label}</td>
      <td className="num">{usd(arm.contributed)}</td>
      <td className="num">{usd(arm.terminal_wealth)}</td>
      <td className="num">{pct(arm.irr, 2)}</td>
      <td className="num">{pct(arm.max_drawdown)}</td>
    </tr>
  )
}

/** The rolling-start verdict and one window's two arms. Shared by both
 *  sources; ``byYield`` is the real-quote source's dividend sensitivity. */
export function PlanVerdict({
  rolling: r,
  window: w,
  byYield,
}: {
  rolling: RollingSummary | null
  window: PlanWindow | null
  byYield?: { dividend_yield: number; share_ahead: number }[]
}) {
  if (r == null || w == null) return null
  const span = (Date.parse(r.last_start) - Date.parse(r.first_start)) / (365.25 * 86_400_000) + r.horizon_years
  const separate = Math.max(1, Math.floor(span / r.horizon_years))
  return (
    <>
      <p className="pl-caveat" data-testid="overlap-caveat">
        {r.n_starts === 1 ? 'This one start covers' : `These ${r.n_starts} starts are not independent: they cover`} {Math.round(span)} years of history, about{' '}
        {separate} separate {r.horizon_years}-year {separate === 1 ? 'period' : 'periods'}, so the shares below
        describe this history; they do not test a hypothesis.
      </p>
      <p data-testid="plan-verdict">
        Across <strong>{r.n_starts}</strong> monthly {r.n_starts === 1 ? `start (${r.first_start})` : `starts (${r.first_start} to ${r.last_start})`}, each a{' '}
        {r.horizon_years}-year plan, the hedged book's IRR led by more than 1bp/yr in{' '}
        <strong>{share(r.share_ahead)}</strong>, trailed in <strong>{share(r.share_behind)}</strong> and was
        within 1bp in {share(r.share_inconclusive)}. Median gap {pp(r.median_gap)}/yr; 10th to 90th percentile{' '}
        {pp(r.p10_gap)} to {pp(r.p90_gap)}; worst {pp(r.worst_gap)}, best {pp(r.best_gap)}.
      </p>
      {byYield && (
        <p className="pl-lede" data-testid="plan-by-yield">
          Share of starts led, by assumed S&P dividend yield:{' '}
          {byYield.map((y) => `at ${pct(y.dividend_yield)}, ${share(y.share_ahead)}`).join('; ')}.
        </p>
      )}
      <h4 style={{ marginBottom: 2 }}>
        One window: {w.start} to {w.end}
      </h4>
      <div className="pl-scroll">
        <table className="pl-table">
          <thead>
            <tr>
              <th>Arm</th>
              <th className="num">Paid in</th>
              <th className="num">Ends with</th>
              <th className="num">IRR</th>
              <th className="num">Worst drawdown</th>
            </tr>
          </thead>
          <tbody>
            <ArmRow arm={w.hedged} />
            <ArmRow arm={w.comparator} />
          </tbody>
        </table>
      </div>
    </>
  )
}
