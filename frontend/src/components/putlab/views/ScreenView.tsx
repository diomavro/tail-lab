import { Leaderboard } from '../Leaderboard'
import { MetricScreen } from '../MetricScreen'
import type { PutLabControls } from '../types'

// "What should I hedge?" -- the fragility screen (auto-run on entry so the user
// sees results without clicking-then-scrolling), followed by the heavier
// metric bake-off as an explicit secondary analysis right underneath it.
export function ScreenView({
  controls,
  currentAsset,
}: {
  controls: PutLabControls
  currentAsset: string
}) {
  return (
    <>
      <p className="view-intro">
        <strong>What should I hedge?</strong> Rank the universe by how <em>fragile</em> each name is versus the
        market &mdash; not by a forecast of when a crash comes. The thesis is timing-free: hold convexity on the names
        most likely to detonate whenever a dislocation eventually hits.
      </p>
      <Leaderboard controls={controls} currentAsset={currentAsset} autoRun />
      <MetricScreen controls={controls} />
    </>
  )
}
