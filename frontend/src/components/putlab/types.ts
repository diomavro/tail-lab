import type { StrikeRuleKind } from '../../api/client'

// Shared prop/state shapes for the Put Lab question builder + charts.

export interface PutLabControls {
  asset: string
  notional: number
  moneyness_pct: number
  // How the strike is chosen (docs/adr/0029). Only Workspace and Bake-off can
  // pick by delta; every other tab reads moneyness_pct, which is kept intact
  // while a delta is in use so switching back loses nothing.
  strike_rule: StrikeRuleKind
  target_delta: number
  tenor_weeks: number
  years: number
}

export type { StrikeRuleKind }

export const PUTLAB_ASSETS: { value: string; label: string }[] = [
  { value: 'spy', label: 'S&P 500 (SPY)' },
  { value: 'qqq', label: 'Nasdaq-100 (QQQ)' },
  { value: 'iwm', label: 'Russell 2000 (IWM)' },
  { value: 'tsla', label: 'Tesla (TSLA)' },
  { value: 'gld', label: 'Gold (GLD)' },
  { value: 'eem', label: 'Emerging Mkts (EEM)' },
]

// The OOM% presets on the cockpit's right rail. Shared so the cache-warming
// prefetch (PutLab) and the rail buttons (ChartCockpit) can never drift apart.
export const PUTLAB_OOM_PRESETS: number[] = [5, 10, 15, 20]

// Target put deltas offered beside them, and the legal range (the API's).
export const PUTLAB_DELTA_PRESETS: number[] = [0.05, 0.1, 0.2, 0.3]
export const PUTLAB_DELTA_MIN = 0.01
export const PUTLAB_DELTA_MAX = 0.5

/** "0.10Δ at realised vol" -- never a bare "10Δ", which reads as the desk's
 *  market-delta put (see "How the strike is chosen"). */
export const deltaLabel = (target: number) => `${target.toFixed(2)}Δ at realised vol`

// `short` is the collapsed rail's one-line summary on a narrow screen.
export const PUTLAB_TENORS: { weeks: number; label: string; short: string }[] = [
  { weeks: 1, label: '1 week', short: '1w' },
  { weeks: 2, label: '2 wk', short: '2w' },
  { weeks: 4, label: '1 month', short: '1m' },
  { weeks: 12, label: '1 quarter', short: '1q' },
]

// The lookback windows the rail offers. Shared so the rail and any caller that
// needs to know the legal set (e.g. a preset) cannot drift apart.
export const PUTLAB_YEARS: number[] = [4, 3, 2]

export const PUTLAB_DEFAULT_CONTROLS: PutLabControls = {
  asset: 'spy',
  notional: 1000,
  moneyness_pct: 5,
  strike_rule: 'moneyness',
  target_delta: 0.1,
  tenor_weeks: 4,
  years: 4,
}

/** How the Workspace's ranking strip orders the screen. Fragility is the
 *  server's own order; best return re-sorts the same response client-side. */
export type RankSort = 'fragility' | 'return'

/** The in-sample bake-off the server still pre-warms (the strip's verdict line
 *  now reads STRIP_SIGNAL_TEST below). Kept because the backend parity test
 *  reads it: the composite fragility screen at the app's opening screen, by
 *  distance, top 5. It mirrors the server's pre-warmed `_WARM_BAKEOFF`
 *  (api/putlab_routes.py), so the line is read from the server's memo. */
export const STRIP_BAKEOFF = {
  strike_rule: 'moneyness' as const,
  moneyness_pct: 5,
  target_delta: PUTLAB_DEFAULT_CONTROLS.target_delta,
  tenor_weeks: 4,
  years: 4,
  top_k: 5,
}

/** The one signal test the Workspace strip's verdict line reads (docs/adr/0033):
 *  0.10Δ at realised vol, 4-week puts, a 4-year signal window, top 5. It never
 *  follows the rail, and it mirrors the server's pre-warmed `_WARM_SIGNAL_TEST`
 *  (api/putlab_routes.py); a pytest parses this literal, so keep each field a
 *  plain literal with no comments inside the braces. `years` is the signal
 *  window (the API's `signal_years`). */
export const STRIP_SIGNAL_TEST = {
  strike_rule: 'delta',
  target_delta: 0.1,
  tenor_weeks: 4,
  years: 4,
  top_k: 5,
} as const
