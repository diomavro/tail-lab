import type { MarksCoverage } from '../../src/api/client'

// Two sources with a gap between them (2023-03 .. 2023-05 held by neither),
// the archive stored to 40% out and the sweep to 60%: every absence the grid
// distinguishes is present. 504 usable sessions at 16% / 2-6w = 2.0 years.
export const MARKS: MarksCoverage = {
  symbol: 'spy',
  as_of: '2026-10-30',
  oom_step: 2,
  bands: ['<=2w', '2-6w', '6-17w'],
  max_relative_spread: 0.5,
  sources: [
    {
      source: 'optionsdx',
      first: '2023-01-03',
      last: '2023-02-28',
      sessions: 39,
      stored_depth_pct: 40,
      months: ['2023-01', '2023-02'],
      month_sessions: [20, 19],
      cells: [
        [0, 16, 1, 20, 20],
        [1, 16, 1, 5, 19],
        [0, 16, 0, 2, 2],
      ],
    },
    {
      source: 'chain',
      first: '2023-06-01',
      last: '2023-06-30',
      sessions: 21,
      stored_depth_pct: 60,
      months: ['2023-06'],
      month_sessions: [21],
      cells: [[0, 50, 1, 21, 21]],
    },
  ],
  depth: [
    { oom: 16, band: '2-6w', usable_sessions: 504, traded_sessions: 600, usable_by_regime: { calm: 252, elevated: 189, crisis: 63 } },
    { oom: 16, band: '<=2w', usable_sessions: 2, traded_sessions: 2, usable_by_regime: { calm: 2, elevated: 0, crisis: 0 } },
    { oom: 50, band: '2-6w', usable_sessions: 21, traded_sessions: 21, usable_by_regime: { calm: 21, elevated: 0, crisis: 0 } },
  ],
  regime_sessions: { calm: 40, elevated: 20, crisis: 0 },
  price_span: ['2016-10-31', '2026-10-29'],
  symbols: ['nvda', 'qqq', 'spy', 'tsla'],
}
