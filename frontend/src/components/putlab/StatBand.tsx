import type { PutBacktestResponse } from '../../api/client'
import { ConceptInfo } from './ConceptInfo'
import { fmtDollar, fmtMult, fmtPct } from './format'

interface StatBandProps {
  backtest: PutBacktestResponse
}

// Headline result cards -- port of the mock's renderStats().
export function StatBand({ backtest }: StatBandProps) {
  const cards: { k: string; v: string; cls?: 'pos' | 'neg'; note: string; hero?: boolean; concept?: string }[] = [
    {
      k: 'Return on premium',
      v: fmtPct(backtest.roi_on_premium),
      cls: backtest.roi_on_premium >= 0 ? 'pos' : 'neg',
      note: `over ${backtest.n_cycles} rolls`,
      hero: true,
      concept: 'roi_on_premium',
    },
    {
      k: 'Net P&L',
      v: fmtDollar(backtest.net_pnl),
      cls: backtest.net_pnl >= 0 ? 'pos' : 'neg',
      note: `paid ${fmtDollar(backtest.total_premium)} in premium`,
    },
    {
      k: 'Hit rate',
      v: `${Math.round(backtest.hit_rate * 100)}%`,
      note: 'cycles that paid back',
      concept: 'hit_rate',
    },
    {
      k: 'Biggest payoff',
      v: fmtMult(backtest.biggest_payoff_mult),
      note: 'best single roll',
      concept: 'biggest_payoff',
    },
    {
      k: 'Worst bleed streak',
      v: `${backtest.worst_bleed_streak}×`,
      note: 'rolls losing in a row',
      concept: 'bleed',
    },
  ]

  return (
    <section className="stats" aria-label="Headline results">
      {cards.map((c) => (
        <div className={`stat${c.hero ? ' hero-stat' : ''}`} key={c.k}>
          <div className="k">
            {c.k}
            {c.concept && <ConceptInfo id={c.concept} />}
          </div>
          <div className={`v ${c.cls ?? ''}`}>{c.v}</div>
          <div className="note">{c.note}</div>
        </div>
      ))}
    </section>
  )
}
