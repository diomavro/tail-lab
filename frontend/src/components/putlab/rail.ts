import type { TabId } from './PutLab'
import { deltaLabel, PUTLAB_TENORS, type PutLabControls } from './types'

// Which of the shared controls each tab reads. ParamRail renders exactly
// these, and PutLab skips the rail on a tab whose list is empty.

// 'delta' marks a tab whose strike may be picked by delta: it adds the mode
// switch to the strike section. Only Workspace and Bake-off read it
// (docs/adr/0029); the other strike readers take moneyness only.
export type RailSection = 'universe' | 'notional' | 'strike' | 'delta' | 'tenor' | 'years'

/** What each tab reads from the shared controls -- audited against the fetch
 *  keys in PutLab.tsx and the views' own reads. One state behind all of them,
 *  so a Recommendations click still opens Workspace at that name's cell. */
export const RAIL: Record<TabId, readonly RailSection[]> = {
  // Backtest, sweep, verdict and prefetch read all five.
  workspace: ['universe', 'notional', 'strike', 'delta', 'tenor', 'years'],
  // Every row reports that name's own best cell over a fixed strike x tenor
  // grid (ranking.run_sweep), so the rail's strike changes nothing in the
  // table: the screening roll's cycle dates depend on the tenor alone, and
  // the strike reaches only the export's screen_moneyness_pct label. Years is
  // the window every best cell is searched over; tenor decides, at the 90%
  // coverage edge, which names cover that window.
  recommendations: ['tenor', 'years'],
  // fetchMetricScreen runs at these on Run, across the whole universe.
  bakeoff: ['strike', 'delta', 'tenor', 'years'],
  // The anchor IS the strike control and the tenor picks the expiry
  // (read_surface), so both stay; years never enter.
  surface: ['universe', 'strike', 'tenor'],
  // Owns its own basket and window (Portfolio.tsx); it seeds that window from
  // the shared years once, at mount, and never reads the controls again.
  portfolio: [],
  // Reads nothing from the controls; its plan inputs are page-scoped.
  book: [],
  // The timeline is market-wide, but the per-regime residuals replicate
  // whichever Cboe program sits nearest the strike and tenor
  // (accuracy.select_reference), so those two drive numbers on this tab.
  regime: ['strike', 'tenor'],
  glossary: [],
}

/** The collapsed rail's one-line summary on a narrow screen: only what this
 *  tab shows, so the line never names a control the tab does not read. */
export function railSummary(controls: PutLabControls, sections: readonly RailSection[]): string {
  const tenor = PUTLAB_TENORS.find((t) => t.weeks === controls.tenor_weeks)
  const parts: Record<RailSection, string> = {
    universe: controls.asset.toUpperCase(),
    notional: `$${controls.notional.toLocaleString('en-US')}`,
    // A tab without the delta switch reads moneyness whatever the mode is.
    strike:
      sections.includes('delta') && controls.strike_rule === 'delta'
        ? deltaLabel(controls.target_delta)
        : `${controls.moneyness_pct}% OOM`,
    delta: '',
    tenor: tenor ? tenor.short : `${controls.tenor_weeks}wk`,
    years: `${controls.years}y`,
  }
  return sections
    .map((s) => parts[s])
    .filter(Boolean)
    .join(' · ')
}
