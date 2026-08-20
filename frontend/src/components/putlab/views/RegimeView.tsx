import { VixStretchTile } from '../../VixStretchTile'
import { RegimePanel } from '../RegimePanel'

// "What regime are we in?" -- the VIX-based calm/elevated/crisis timeline that
// underpins every verdict, plus the live VIX-stretch context tile. VixStretchTile
// styles itself with App.css's `.tile`; wrapping it in a `.panel` (via `.vix-panel`,
// which neutralizes the inner tile chrome) lets it sit inside the Put Lab design.
export function RegimeView() {
  return (
    <>
      <p className="view-intro">
        <strong>What regime are we in?</strong> The VIX-based backdrop &mdash; calm, elevated, or crisis &mdash; that
        every strategy verdict is read against, plus how stretched fear is right now versus its own recent history.
      </p>
      <RegimePanel />
      <section className="panel vix-panel">
        <VixStretchTile />
      </section>
    </>
  )
}
