import type { Label } from './chart'

/* Chart labels are HTML laid over the SVG at percentage positions, not SVG
 * <text>: the SVG scales with its viewBox, and text inside it would shrink with
 * the column (a 12px label in an 860-wide viewBox drawn into 500px reads at 7px).
 * The wrapper must be `position: relative` (.pl-plot). */

const SHIFT = { start: 'translate(0,-50%)', middle: 'translate(-50%,-50%)', end: 'translate(-100%,-50%)' }

export function ChartLabels({ labels, w, h }: { labels: readonly Label[]; w: number; h: number }) {
  return (
    <>
      {labels.map((l, i) => (
        <span
          key={i}
          className={`pl-chart-label${l.strong ? ' is-strong' : ''}`}
          aria-hidden="true"
          style={{ left: `${(l.x / w) * 100}%`, top: `${(l.y / h) * 100}%`, transform: SHIFT[l.align ?? 'middle'] }}
        >
          {l.text}
        </span>
      ))}
    </>
  )
}
