/** What MarksView (the tab, with its controls) and MarksChart (the chart for one
 *  symbol) both need: the tenor-band wording and the measure's type. */
export const BAND_LABEL: Record<string, string> = { '<=2w': 'up to 2 weeks', '2-6w': '2–6 weeks', '6-17w': '6–17 weeks' }

export type Measure = 'usable' | 'traded'
