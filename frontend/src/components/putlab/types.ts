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
