import type { RankedAsset, Verdict } from '../../api/client'

/** What each verdict means, for its tag's hover text (supplementary: the
 *  best-return table's "How to read a row" note prints all four). */
export const VERDICT_TITLE: Record<Verdict, string> = {
  confirmed: 'Entered and paid off in two or more VIX regimes',
  regime_only: 'Paid off in one VIX regime only -- a bet on that regime recurring',
  failed: 'Did not pay off in any regime it was entered in',
  untested: 'Too few rolls in any regime to judge',
}

export type BestRow = RankedAsset & { best_annualized: number }

/** The rows the screen's best-return order ranks: names with a scored best
 *  cell, best return first. A filtered copy -- the response is the shared
 *  client cache's (RANKING_CACHE), and sorting it in place would reorder the
 *  fragility view too. */
export function bestReturnRows(ranked: RankedAsset[]): BestRow[] {
  return ranked
    .filter((r): r is BestRow => r.best_annualized != null)
    .sort((a, b) => b.best_annualized - a.best_annualized)
}
