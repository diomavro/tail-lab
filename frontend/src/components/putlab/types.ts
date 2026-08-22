// Shared prop/state shapes for the Put Lab question builder + charts.

export interface PutLabControls {
  asset: string
  notional: number
  moneyness_pct: number
  tenor_weeks: number
  years: number
}

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

export const PUTLAB_TENORS: { weeks: number; label: string }[] = [
  { weeks: 1, label: '1 week' },
  { weeks: 2, label: '2 wk' },
  { weeks: 4, label: '1 month' },
  { weeks: 12, label: '1 quarter' },
]

// The lookback windows the rail offers. Shared so the rail and any caller that
// needs to know the legal set (e.g. a preset) cannot drift apart.
export const PUTLAB_YEARS: number[] = [4, 3, 2]

export const PUTLAB_DEFAULT_CONTROLS: PutLabControls = {
  asset: 'spy',
  notional: 1000,
  moneyness_pct: 5,
  tenor_weeks: 4,
  years: 4,
}
