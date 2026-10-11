import type { RankedAsset } from '../../api/client'

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
