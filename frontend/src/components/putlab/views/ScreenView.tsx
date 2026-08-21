import { MetricScreen } from '../MetricScreen'
import type { PutLabControls } from '../types'

// "What should I hedge?" -- the metric bake-off, which asks which *screen*
// actually picks winners. The fragility ranking itself used to live here; it is
// now pinned above the tabs in PutLab.tsx so a name can be picked from any tab,
// leaving this view as the deeper "is the screen any good?" analysis.
export function ScreenView({ controls }: { controls: PutLabControls }) {
  return (
    <>
      <p className="view-intro">
        <strong>What should I hedge?</strong> Rank the universe by how <em>fragile</em> each name is versus the
        market &mdash; not by a forecast of when a crash comes. The thesis is timing-free: hold convexity on the names
        most likely to detonate whenever a dislocation eventually hits. The ranking is pinned above &mdash; click a
        row to backtest that name. Below, the bake-off asks the harder question: which screen actually picks winners?
      </p>
      <MetricScreen controls={controls} />
    </>
  )
}
