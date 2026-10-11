// Shading for the ranking strip's tables: colour carries the reading at a
// glance, and every shaded cell still prints its value as text (colour is never
// the only carrier -- SweepGrid.tsx:49-56). The ramps are capped so ink text
// stays at or above 4.5:1 on both sheets (putlab.css, `.pl-heat-loss` and
// `.pl-shade-rank`).

/** Per value, how deep its loss is within the column, 0..1 (1 = the column's
 *  largest loss); `null` for a gain, a zero or a missing value, which take no
 *  heat. */
export function lossHeat(values: (number | null | undefined)[]): (number | null)[] {
  const worst = Math.min(0, ...values.filter((v): v is number => v != null))
  return values.map((v) => (v == null || v >= 0 || worst === 0 ? null : v / worst))
}

/** Per value, its fragility rank within the column, 0..1 (1 = most fragile),
 *  read in the direction the server serves (`fragile_high`) -- a rank, not a
 *  scaled value, so one outlier cannot wash out the rest of the column. Tied
 *  values share their mean rank; `null` for a missing value. */
export function fragilityRank(values: (number | null)[], fragileHigh: boolean): (number | null)[] {
  const present = values.filter((v): v is number => v != null)
  if (present.length < 2) return values.map((v) => (v == null ? null : 0.5))
  const sorted = [...present].sort((a, b) => a - b)
  const last = sorted.length - 1
  return values.map((v) => {
    if (v == null) return null
    const first = sorted.indexOf(v)
    const pos = (first + sorted.lastIndexOf(v)) / 2 / last
    return fragileHigh ? pos : 1 - pos
  })
}

/** A biggest-payoff multiple as a bar width in px, on a log scale: multiples
 *  span orders of magnitude (0.4x to 60x), and a linear bar would show only the
 *  largest. */
export function biggestBarWidth(mult: number, max: number, full = 40): number {
  if (max <= 0 || mult <= 0) return 0
  return Math.max(1, Math.round((Math.log1p(mult) / Math.log1p(max)) * full))
}
