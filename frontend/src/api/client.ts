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
  cost: number
  payoff: number
  net: number
}

export interface EquityPoint {
  date: string
  cum_pnl: number
}

// One point of the "annualized return so far" curve -- at `date` (a roll's
// expiry), the geometric yearly rate earned on premium from the first entry up
// to that date (net of brokerage). See put_roll.AnnualizedPoint.
export interface AnnualizedPoint {
  date: string
  annualized: number
}

export interface PutBacktestResponse {
  asset: string
  as_of: string
  notional: number
  moneyness_pct: number
  tenor_weeks: number
  lookback_years: number
  rate: number
  spot: number
  n_cycles: number
  total_premium: number
  total_payoff: number
  total_brokerage: number
  net_pnl: number
  roi_on_premium: number
  annualized_return: number
  hit_rate: number
  biggest_payoff_mult: number
  worst_bleed_streak: number
  sharpe_ratio: number | null
  annualized_so_far: AnnualizedPoint[]
  benchmark_annualized: number | null
  equity_curve: EquityPoint[]
  mtm_curve: EquityPoint[]
  price_path: PricePoint[]
  cycles: PutBacktestCycle[]
}

export interface PricePoint {
  date: string
  price: number
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

export interface RegimeShare {
  regime: 'calm' | 'elevated' | 'crisis'
  n_days: number
  share: number
}

export interface ModelAccuracy {
  /** Return per year by which this window's backtest is likely overstated.
   *  Positive = the model prices puts too cheap. Null = not measured. */
  expected_optimism: number | null
  applicability: 'direct' | 'indicative' | 'unmeasured'
  /** The published Cboe program this error bar was measured on. */
  reference: string | null
  regime_mix: RegimeShare[]
  residual_by_regime: Record<string, number>
  basis: string
  caveat: string
}

export interface BenchmarkComparison {
  index_symbol: string
  label: string
  total_return: number
  annualized: number
}

export interface Assumption {
  name: string
  value: string
  leverage: string | null
}

export interface AccuracyResponse {
  asset: string
  as_of: string
  years: number
  window_start: string
  model: ModelAccuracy
  benchmarks: BenchmarkComparison[]
  data_quality_flags: number | null
  data_quality_note: string
  assumptions: Assumption[]
}

export interface AccuracyParams {
  asset: string
  moneyness_pct: number
  tenor_weeks: number
  years: number
}

export async function fetchAccuracy(
  params: AccuracyParams,
  signal?: AbortSignal,
): Promise<AccuracyResponse> {
  const qs = new URLSearchParams({
    asset: params.asset,
    moneyness_pct: String(params.moneyness_pct),
    tenor_weeks: String(params.tenor_weeks),
    years: String(params.years),
  })
  const resp = await fetch(`/api/putlab/accuracy?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/accuracy failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as AccuracyResponse
}

export interface SweepCell {
  moneyness_pct: number
  tenor_weeks: number
  roi_on_premium: number
  annualized_return: number
  n_cycles: number
}

export interface SweepResponse {
  asset: string
  as_of: string
  notional: number
  lookback_years: number
  cells: SweepCell[]
  benchmark_symbol: string
  benchmark_annualized: number | null
  benchmark_total: number | null
  /** Strike depth past which the flat-vol model's premium stops being a price.
   *  Served rather than hard-coded here so the client and
   *  research/backtest/sweep.py cannot drift. See docs/adr/0018. */
  model_priced_max_moneyness_pct: number
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

// The Put Lab screening universe (contracts/options_calendar.py) -- the
// dropdown's source of truth. Shares the CadenceResponse shape
// plus the ticker.
export interface UniverseMember {
  symbol: string
  name: string
  cadence: OptionsCadenceKind
  avg_gap_days: number
  label: string
  detail: string
}

export async function fetchUniverse(signal?: AbortSignal): Promise<UniverseMember[]> {
  const resp = await fetch('/api/putlab/universe', { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/universe failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as UniverseMember[]
}

// --- Sensitivity leaderboard (src/tail_lab/api/leaderboard_routes.py,
// .../research/leaderboard.py, docs/END_STATE.md §1.1) ---

export interface LeaderboardRow {
  rank: number
  symbol: string
  score: number
  metric: string
}

export interface LeaderboardResponse {
  as_of: string
  metric: string
  benchmark: string
  rows: LeaderboardRow[]
}

export type LeaderboardMetric = 'downside_beta' | 'co_skewness'

export async function fetchLeaderboard(
  signal?: AbortSignal,
  metric?: LeaderboardMetric,
): Promise<LeaderboardResponse> {
  const qs = metric ? `?metric=${encodeURIComponent(metric)}` : ''
  const resp = await fetch(`/api/leaderboard${qs}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/leaderboard failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as LeaderboardResponse
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

// --- Put Lab live regime verdict (docs/adr/0015) ---

export type Verdict = 'confirmed' | 'regime_only' | 'failed' | 'untested'

export interface RegimeSlice {
  regime: 'calm' | 'elevated' | 'crisis'
  n_cycles: number
  roi_on_premium: number
  paid_off: boolean
}

export interface RegimeVerdictResponse {
  asset: string
  as_of: string
  rule_hash: string
  verdict: Verdict
  slices: RegimeSlice[]
}

export async function fetchRegimeVerdict(
  params: PutBacktestParams,
  signal?: AbortSignal,
): Promise<RegimeVerdictResponse> {
  const qs = new URLSearchParams({
    asset: params.asset,
    notional: String(params.notional),
    moneyness_pct: String(params.moneyness_pct),
    tenor_weeks: String(params.tenor_weeks),
    years: String(params.years),
  })
  const resp = await fetch(`/api/putlab/regime-verdict?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/regime-verdict failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as RegimeVerdictResponse
}

// --- Put Lab universe leaderboard (research/backtest/ranking.py) --
// "which names' OOM puts got the best results at this strike/tenor," ranked
// server-side. NOT `fetchLeaderboard` above -- that's the unrelated
// sensitivity leaderboard at /api/leaderboard.

export interface RankedAsset {
  asset: string
  name: string
  spot: number
  downside_beta: number | null
  co_skewness: number | null
  co_kurtosis: number | null
  tail_beta: number | null
  downside_capture: number | null
  fragility_score: number | null
  roi_on_premium: number
  annualized_return: number
  verdict: Verdict
  hit_rate: number
  biggest_payoff_mult: number
  n_cycles: number
  /** The best this name gets when its parameters are chosen well: the argmax
   *  of its own strike x tenor sweep, bounded to the strikes the model can
   *  actually price. This is the headline the compact ranking shows, and the
   *  parameters a click on the row lands on — so the number in the table is
   *  the number the backtest then displays.
   *
   *  EVERY `best_*` field is measured at the SAME cell. Never pair one of them
   *  with a figure from the screened cell above: that is two strategies on one
   *  line, and the recommendations view ranks on exactly this row. */
  best_annualized: number | null
  best_moneyness_pct: number | null
  best_tenor_weeks: number | null
  best_roi_on_premium: number | null
  best_hit_rate: number | null
  best_biggest_payoff_mult: number | null
  best_n_cycles: number | null
  best_verdict: Verdict | null
}

export interface PutLabLeaderboardResponse {
  as_of: string
  moneyness_pct: number
  tenor_weeks: number
  lookback_years: number
  notional: number
  ranked: RankedAsset[]
}

export interface PutLabLeaderboardParams {
  moneyness_pct: number
  tenor_weeks: number
  years: number
}

export async function fetchPutLabLeaderboard(
  params: PutLabLeaderboardParams,
  signal?: AbortSignal,
): Promise<PutLabLeaderboardResponse> {
  const qs = new URLSearchParams({
    moneyness_pct: String(params.moneyness_pct),
    tenor_weeks: String(params.tenor_weeks),
    years: String(params.years),
  })
  const resp = await fetch(`/api/putlab/leaderboard?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/leaderboard failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as PutLabLeaderboardResponse
}

// --- Metric bake-off (research/backtest/metric_screen.py) ---
// "which fragility metric best sorted realized put payoffs over the lookback."
// An in-sample cross-sectional association (a screen chooser), NOT a forward
// predictive backtest. One backtest per screened name server-side, so an
// explicit action rather than a live recompute.

export interface MetricScreenEntry {
  metric: string
  label: string
  top_k_assets: string[]
  roi_on_premium: number
  annualized_return: number
  hit_rate: number
  combined_max_drawdown: number
  verdict: Verdict
  regime_slices: RegimeSlice[]
  spearman_vs_payoff: number | null
  lift_vs_baseline: number
}

export interface MetricScreenComparison {
  as_of: string
  moneyness_pct: number
  tenor_weeks: number
  lookback_years: number
  top_k: number
  universe_size: number
  baseline_roi: number
  entries: MetricScreenEntry[]
}

export interface MetricScreenParams {
  moneyness_pct: number
  tenor_weeks: number
  years: number
  top_k: number
}

export async function fetchMetricScreen(
  params: MetricScreenParams,
  signal?: AbortSignal,
): Promise<MetricScreenComparison> {
  const qs = new URLSearchParams({
    moneyness_pct: String(params.moneyness_pct),
    tenor_weeks: String(params.tenor_weeks),
    years: String(params.years),
    top_k: String(params.top_k),
  })
  const resp = await fetch(`/api/putlab/metric-screen?${qs.toString()}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/metric-screen failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as MetricScreenComparison
}

// --- Portfolio of mixed puts (research/backtest/portfolio.py) ---

export interface PortfolioLeg {
  asset: string
  moneyness_pct: number
  tenor_weeks: number
  weight: number
}

export interface PortfolioLegResult {
  asset: string
  name: string
  weight: number
  total_premium: number
  net_pnl: number
  roi_on_premium: number
  annualized_return: number
  verdict: Verdict
  n_cycles: number
}

export interface PortfolioResponse {
  as_of: string
  notional: number
  lookback_years: number
  total_premium: number
  total_payoff: number
  net_pnl: number
  roi_on_premium: number
  annualized_return: number
  combined_max_drawdown: number
  sum_individual_max_drawdown: number
  verdict: Verdict
  legs: PortfolioLegResult[]
  equity_curve: EquityPoint[]
  snapshot_ids: string[]
}

export async function fetchPortfolio(
  body: { legs: PortfolioLeg[]; notional: number; years: number },
  signal?: AbortSignal,
): Promise<PortfolioResponse> {
  const resp = await fetch('/api/putlab/portfolio', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok) {
    throw new ApiError(`POST /api/putlab/portfolio failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as PortfolioResponse
}

// --- Data quality (research/data_quality.py) ---

export interface DataQualityFlag {
  date: string
  kind: string
  detail: string
}

export interface DataQualityResponse {
  asset: string
  as_of: string
  n_bars: number
  n_suspicious: number
  flags: DataQualityFlag[]
}

export async function fetchDataQuality(asset: string, signal?: AbortSignal): Promise<DataQualityResponse> {
  const resp = await fetch(`/api/putlab/data-quality?asset=${encodeURIComponent(asset)}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/data-quality failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as DataQualityResponse
}

// --- Regime panel (research/regimes/timeline.py) ---

export type RegimeLabel = 'calm' | 'elevated' | 'crisis'

export interface RegimeSegment {
  regime: RegimeLabel
  start: string
  end: string
  n_days: number
}

export interface RegimeTimelineView {
  as_of: string
  current: RegimeLabel
  segments: RegimeSegment[]
  day_counts: Record<string, number>
}

export async function fetchRegimes(signal?: AbortSignal): Promise<RegimeTimelineView> {
  const resp = await fetch('/api/putlab/regimes', { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/regimes failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as RegimeTimelineView
}
