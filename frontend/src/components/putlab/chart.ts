// Shared SVG geometry for the Surface and Book charts; ChartLabels.tsx
// draws their labels.

export type Pt = readonly [number, number]

export type Label = {
  x: number
  y: number
  text: string
  align?: 'start' | 'middle' | 'end'
  /** Ink instead of the muted neutral every axis label uses. */
  strong?: boolean
}

export const poly = (pts: readonly Pt[]): string =>
  pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('')

/** Filled or stroked circles as one path, so a series is one DOM node. */
export const dots = (pts: readonly Pt[], r: number): string =>
  pts
    .map(
      ([x, y]) =>
        `M${(x - r).toFixed(1)},${y.toFixed(1)}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0`,
    )
    .join('')

/** Log-scale tick values inside [lo, hi]. 1-2-5 per decade; a range too
 *  narrow for three of those (a ladder of $4 to $12 puts) takes finer
 *  mantissas. Thinned to at most ``max`` so a wide range is not a fence. */
export function logTicks(lo: number, hi: number, max = 6): number[] {
  if (!(lo > 0) || !(hi > lo)) return []
  const within = (mantissas: number[]) => {
    const out: number[] = []
    for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
      for (const m of mantissas) {
        const v = Number((m * 10 ** e).toPrecision(6))
        if (v >= lo && v <= hi) out.push(v)
      }
    }
    return out
  }
  const sets = [[1, 2, 5], [1, 2, 3, 4, 6, 8], [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9]]
  let ticks = within(sets[0]!)
  for (const m of sets.slice(1)) {
    if (ticks.length >= 3) break
    ticks = within(m)
  }
  if (ticks.length <= max) return ticks
  const step = Math.ceil(ticks.length / max)
  return ticks.filter((_, i) => i % step === 0)
}

/** A short, exact-enough tick label: no float noise like 0.30000000000000004. */
export const tickText = (v: number): string => String(Number(v.toPrecision(3)))
