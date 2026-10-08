import type { TabId } from './PutLab'
import { PUTLAB_TENORS, type PutLabControls } from './types'

// Which of the shared controls each tab reads. ParamRail renders exactly
// these, and PutLab skips the rail on a tab whose list is empty.

export type RailSection = 'universe' | 'notional' | 'strike' | 'tenor' | 'years'

/** What each tab reads from the shared controls -- audited against the fetch
 *  keys in PutLab.tsx and the views' own reads. One state behind all of them,
 *  so a Recommendations click still opens Workspace at that name's cell. */
export const RAIL: Record<TabId, readonly RailSection[]> = {
  // Backtest, sweep, verdict and prefetch read all five.
  workspace: ['universe', 'notional', 'strike', 'tenor', 'years'],
  // The ranking keys on strike, tenor and years (rankKey); not on the name or
  // on how much premium is spent. Years is the window every name's best cell
  // is searched over; strike and tenor set the screening roll, which decides
  // which names cover the window and is what the exported roll schedule
  // states. Each row's figures are that name's own best cell.
  recommendations: ['strike', 'tenor', 'years'],
  // fetchMetricScreen runs at these on Run, across the whole universe.
  bakeoff: ['strike', 'tenor', 'years'],
  // The anchor IS the strike control and the tenor picks the expiry
  // (read_surface), so both stay; years never enter.
  surface: ['universe', 'strike', 'tenor'],
  // Owns its own basket and window (Portfolio.tsx).
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
    strike: `${controls.moneyness_pct}% OOM`,
    tenor: tenor ? tenor.short : `${controls.tenor_weeks}wk`,
    years: `${controls.years}y`,
  }
  return sections.map((s) => parts[s]).join(' · ')
}
