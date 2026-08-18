// Typed API client mirroring the backend's pydantic response models
// (src/tail_lab/api/schemas.py). Keep these in sync by hand.

export interface VixStretchResponse {
  date: string
  close: number
  rolling_mean_20d: number
  rolling_std_20d: number
  z_score: number
}

export type FeedbackKind = 'big_picture' | 'issue'

export interface FeedbackRecord {
  id: string
  text: string
  kind: FeedbackKind
  created_at: string
  status: 'open' | 'resolved'
  resolved_at: string | null
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function fetchVixStretch(signal?: AbortSignal): Promise<VixStretchResponse> {
  const resp = await fetch('/api/vix/stretch', { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/vix/stretch failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as VixStretchResponse
}

// ---------------------------------------------------------------------------
// Put Lab (src/tail_lab/api/putlab_routes.py, .../research/backtest/put_roll.py,
// .../contracts/options_calendar.py). All model-priced backtests over real
// underlying paths -- see docs/adr/0004.
// ---------------------------------------------------------------------------

export interface PutBacktestCycle {
  entry_date: string
  expiry_date: string
  spot: number
  strike: number
  sigma: number
  premium: number
  contracts: number
  payoff: number
  net: number
}

export interface EquityPoint {
  date: string
  cum_pnl: number
}

export interface PutBacktestResponse {
  asset: string
  as_of: string
  notional: number
  moneyness_pct: number
  tenor_weeks: number
  lookback_years: number
  rate: number
  n_cycles: number
  total_premium: number
  total_payoff: number
  net_pnl: number
  roi_on_premium: number
  hit_rate: number
  biggest_payoff_mult: number
  worst_bleed_streak: number
  equity_curve: EquityPoint[]
  cycles: PutBacktestCycle[]
}

export interface PutBacktestParams {
  asset: string
  notional: number
  moneyness_pct: number
  tenor_weeks: number
  years: number
}

export async function fetchPutBacktest(
  params: PutBacktestParams,
  signal?: AbortSignal,
): Promise<PutBacktestResponse> {
  const qs = new URLSearchParams({
    asset: params.asset,
    notional: String(params.notional),
    moneyness_pct: String(params.moneyness_pct),
    tenor_weeks: String(params.tenor_weeks),
    years: String(params.years),
  })
  const resp = await fetch(`/api/putlab/backtest?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/backtest failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as PutBacktestResponse
}

export interface SweepCell {
  moneyness_pct: number
  tenor_weeks: number
  roi_on_premium: number
  n_cycles: number
}

export interface SweepResponse {
  asset: string
  as_of: string
  notional: number
  lookback_years: number
  cells: SweepCell[]
}

export interface SweepParams {
  asset: string
  notional: number
  years: number
}

export async function fetchSweep(params: SweepParams, signal?: AbortSignal): Promise<SweepResponse> {
  const qs = new URLSearchParams({
    asset: params.asset,
    notional: String(params.notional),
    years: String(params.years),
  })
  const resp = await fetch(`/api/putlab/sweep?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/sweep failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as SweepResponse
}

export type OptionsCadenceKind = 'weekly' | 'monthly'

export interface CadenceResponse {
  symbol: string
  cadence: OptionsCadenceKind
  avg_gap_days: number
  label: string
  detail: string
}

export async function fetchCadence(asset: string, signal?: AbortSignal): Promise<CadenceResponse> {
  const qs = new URLSearchParams({ asset })
  const resp = await fetch(`/api/putlab/cadence?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/cadence failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as CadenceResponse
}

// Public, unauthenticated write (see api/feedback_routes.py) -- the
// token-gated GET/resolve routes are for the daily agent only and are
// deliberately not called from the browser.
export async function submitFeedback(
  text: string,
  kind: FeedbackKind,
  signal?: AbortSignal,
): Promise<FeedbackRecord> {
  const resp = await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, kind }),
    signal,
  })
  if (!resp.ok) {
    throw new ApiError(`POST /api/feedback failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as FeedbackRecord
}
