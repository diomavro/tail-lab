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
  /** "model" or "market" -- whether every premium in this run came from
   *  BlackScholesPricer or a real listed ask. The route never passes real
   *  quotes in today (see RankedAsset.priced_from), so this is always
   *  "model"; carried through so a model-priced ROI never looks identical to
   *  a market-priced one once the route can serve either. */
  priced_from: 'model' | 'market'
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

/**
 * Whether REAL option quotes exist for this name, and whether the figures on
 * screen actually used them. Two different questions, and the second is the
 * one that misleads: holding a fourteen-year market-priced panel does not make
 * a displayed backtest market-priced.
 */
export interface QuoteCoverage {
  /** What produced the premiums behind the figures shown. */
  priced_from: 'model' | 'market'
  /** Whether a panel exists for this underlying AT ALL — it may lie entirely
   *  outside the window below. */
  real_quotes_available: boolean
  /** Counts are scoped to the report's window, not to the whole panel:
   *  months_present + months_missing === window_months. */
  window_months: number
  months_present: number
  months_missing: number
  complete: boolean
  /** Full extent of the stored panel, "YYYYMM". */
  panel_first_month: string | null
  panel_last_month: string | null
  note: string
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
  quote_coverage: QuoteCoverage
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
// server-side.

export interface RankedAsset {
  asset: string
  name: string
  spot: number
  downside_beta: number | null
  co_skewness: number | null
  co_kurtosis: number | null
  tail_beta: number | null
  downside_capture: number | null
  vol_beta: number | null
  fragility_score: number | null
  roi_on_premium: number
  annualized_return: number
  verdict: Verdict
  hit_rate: number
  biggest_payoff_mult: number
  n_cycles: number
  /** "model" or "market" -- whether this row's ROI came from BlackScholesPricer
   *  or a real listed quote. The screen never passes real quotes in today, so
   *  this is always "model"; surfaced so a model-priced ROI never looks
   *  identical to a market-priced one once it can be either. */
  priced_from: 'model' | 'market'
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
  /** The bronze snapshots this screen actually read (VIX + one per ranked
   *  name) and the code that computed it -- docs/STANDARDS.md: "a result
   *  without both is not trustworthy and should not be surfaced." */
  snapshot_ids: string[]
  code_sha: string
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
  // Two-sided p-value for spearman_vs_payoff being zero; null exactly when
  // spearman_vs_payoff is null. significant_raw is the uncorrected p < fdr_alpha
  // hurdle a single test would use in isolation; significant_corrected is the
  // same test after Benjamini-Hochberg FDR correction across every screen in
  // this comparison (research/backtest/multiple_testing.py) -- read the
  // corrected flag before calling a screen a real winner.
  spearman_pvalue: number | null
  significant_raw: boolean
  significant_corrected: boolean
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
  // How many of the seven screens had a defined spearman_pvalue and so entered
  // the Benjamini-Hochberg correction (Harvey, Liu & Zhu 2016 says this must be
  // reported alongside any "which metric wins" verdict).
  n_comparisons: number
  fdr_alpha: number
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

// ---- Surface (GET /api/putlab/surface) -- mirrors api/schemas.SurfaceResponse.
// Every fit and ceiling that can refuse carries a `refusal` string beside a null
// number; the view renders that as the word REFUSED, never as a blank or a zero.

export interface SurfaceAnchorQuote {
  underlying: string
  quote_date: string
  expiration: string
  spot: number
  strike: number
  price: number
  bid: number
  ask: number
}

export interface SurfaceImpliedFit {
  anchor: SurfaceAnchorQuote
  alpha: number | null
  rmse_log_price: number | null
  n_strikes: number
  strike_span: number
  refusal: string | null
}

export interface SurfaceSmile {
  slope: number | null
  std_error: number | null
  strikes: number[]
  refusal: string | null
}

export interface SurfaceCeiling {
  anchor: SurfaceAnchorQuote
  alpha: number | null
  market_slope: number | null
  refusal: string | null
}

export interface SurfaceAnchorReading {
  strike: number
  fit: SurfaceImpliedFit | null
  refusal: string | null
  smile: SurfaceSmile | null
  ceiling: SurfaceCeiling | null
  ceiling_reason: string | null
}

export interface SurfaceRung {
  strike: number
  bid: number | null
  ask: number | null
  paretan_price: number
  market_price: number | null
  black_scholes_price: number | null
  paretan_iv: number | null
  market_iv: number | null
  iv_ratio: number | null
}

export interface SurfaceSurvivalCurve {
  points: [number, number][]
}

export interface SurfaceLogBasis {
  alpha: number | null
  k: number | null
  refusal: string | null
  note: string
}

export interface SurfaceRealised {
  horizon_days: number
  alpha: number | null
  standard_error: number | null
  plateau_k: number | null
  onset: number | null
  n_beyond: number
  is_flat: boolean | null
  refusal: string | null
  log_basis: SurfaceLogBasis
  survival_gross: SurfaceSurvivalCurve | null
  survival_loss: SurfaceSurvivalCurve | null
}

export interface SurfaceReading {
  underlying: string
  quote_date: string
  expiration: string
  t_days: number
  spot: number
  moneyness_pct: number
  parameterisation: string
  r: number
  q: number
  rate_note: string
  anchor_iv: number | null
  lambda_guard_ok: boolean | null
  anchors: {
    readings: SurfaceAnchorReading[]
    dispersion: number | null
    dispersion_reason: string | null
  }
  ladder: SurfaceRung[]
  ladder_reason: string | null
  realised: SurfaceRealised | null
  realised_reason: string | null
  alpha_gap: number | null
  alpha_gap_reason: string | null
}

export interface SurfaceResponse {
  asset: string
  as_of: string
  chain_snapshot: string | null
  ohlcv_snapshot: string | null
  code_sha: string
  surface: SurfaceReading
}

export async function fetchSurface(
  params: { asset: string; moneyness_pct: number; tenor_days: number },
  signal?: AbortSignal,
): Promise<SurfaceResponse> {
  const qs = new URLSearchParams({
    asset: params.asset,
    moneyness_pct: String(params.moneyness_pct),
    tenor_days: String(params.tenor_days),
  })
  const resp = await fetch(`/api/putlab/surface?${qs}`, { signal })
  if (!resp.ok) {
    throw new ApiError(`GET /api/putlab/surface failed: ${resp.status}`, resp.status)
  }
  return (await resp.json()) as SurfaceResponse
}

// ---- Book (GET /api/putlab/hedge-overlay) -- mirrors api/schemas.HedgeOverlayResponse.

export interface OverlayWeightPoint {
  weight: number
  cagr: number
  volatility: number
  max_drawdown: number
  cagr_per_vol: number | null
}

export interface OverlaySensitivityPoint {
  dividend_yield: number
  best_weight: number
  margin: number
  outcome: OverlayOutcome
}

/** holds / fails: the best interior mix beats / trails the better end by more
 *  than 1bp/yr; inconclusive: within 1bp either way. */
export type OverlayOutcome = 'holds' | 'inconclusive' | 'fails'

export interface OverlayWindow {
  key: string
  label: string
  requested_start: string
  requested_end: string
  start: string
  end: string
  clipped: boolean
  years: number
  points: OverlayWeightPoint[]
  best_weight: number
  margin: number
  outcome: OverlayOutcome
  best_weight_risk_adjusted: number | null
  outcome_risk_adjusted: OverlayOutcome | null
  sensitivity: OverlaySensitivityPoint[]
}

export interface ProgramOverlay {
  index_symbol: string
  description: string
  first_date: string
  windows: OverlayWindow[]
  unavailable: Record<string, string>
}

export interface HedgeOverlayResponse {
  cboe_snapshot: string | null
  code_sha: string
  overlay: {
    as_of: string
    dividend_yield: number
    weights: number[]
    programs: ProgramOverlay[]
    missing: Record<string, string>
  }
}

export async function fetchHedgeOverlay(signal?: AbortSignal): Promise<HedgeOverlayResponse> {
  const resp = await fetch('/api/putlab/hedge-overlay', { signal })
  if (!resp.ok) {
    // A 404 carries the reason (no snapshot, or a snapshot without SPX); keep
    // it, so the page never guesses which one it was.
    const body: unknown = await resp.json().catch(() => null)
    const detail =
      body !== null && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
        ? body.detail
        : `GET /api/putlab/hedge-overlay failed: ${resp.status}`
    throw new ApiError(detail, resp.status)
  }
  return (await resp.json()) as HedgeOverlayResponse
}

/** FastAPI's error ``detail``: a string from an HTTPException, or a list of
 *  ``{msg}`` objects from request validation (a 422) -- joined so the page
 *  shows the reason, not a bare status code. */
function detailOf(body: unknown): string | null {
  if (body === null || typeof body !== 'object' || !('detail' in body)) return null
  const d = body.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) {
    const msgs = d
      .map((e: unknown) => {
        if (e === null || typeof e !== 'object' || !('msg' in e) || typeof e.msg !== 'string') return null
        // FastAPI's loc is e.g. ["query", "monthly"]: name the field.
        const field = 'loc' in e && Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : null
        return typeof field === 'string' ? `${field}: ${e.msg}` : e.msg
      })
      .filter((m): m is string => m !== null)
    return msgs.length ? msgs.join('; ') : null
  }
  return null
}

// ---- Book plan (GET /api/putlab/book-plan) -- mirrors api/schemas.BookPlanResponse.

export interface PlanArm {
  label: string
  contributed: number
  terminal_wealth: number
  irr: number
  max_drawdown: number
}

export interface PlanWindow {
  start: string
  end: string
  years: number
  hedged: PlanArm
  comparator: PlanArm
  irr_gap: number
  outcome: OverlayOutcome
}

export interface RollingSummary {
  horizon_years: number
  n_starts: number
  first_start: string
  last_start: string
  share_ahead: number
  share_behind: number
  share_inconclusive: number
  median_gap: number
  p10_gap: number
  p90_gap: number
  worst_gap: number
  best_gap: number
}

export interface ComparatorOption {
  key: string
  label: string
  available: boolean
  reason: string | null
}

export interface BookPlanResponse {
  cboe_snapshot: string | null
  rates_snapshot: string | null
  code_sha: string
  plan: {
    as_of: string
    program: string
    hedge_ratio: number
    e0: number
    monthly: number
    horizon_years: number
    dividend_yield: number
    comparator: string
    comparators: ComparatorOption[]
    window: PlanWindow | null
    rolling: RollingSummary | null
    by_yield: { dividend_yield: number; share_ahead: number; median_gap: number }[]
    refusal: string | null
  }
}

export interface BookPlanParams {
  program: string
  hedge_ratio: number
  e0: number
  monthly: number
  comparator: string
  horizon_years: number
}

export async function fetchBookPlan(params: BookPlanParams, signal?: AbortSignal): Promise<BookPlanResponse> {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))
  const resp = await fetch(`/api/putlab/book-plan?${qs}`, { signal })
  if (!resp.ok) {
    const body: unknown = await resp.json().catch(() => null)
    const detail = detailOf(body) ?? `GET /api/putlab/book-plan failed: ${resp.status}`
    throw new ApiError(detail, resp.status)
  }
  return (await resp.json()) as BookPlanResponse
}

// ---- Book model plan (GET /api/putlab/book-plan/model) -- mirrors api/schemas.ModelPlanResponse.

export interface ModelPlanResponse {
  cboe_snapshot: string | null
  vix_snapshot: string | null
  code_sha: string
  plan: {
    as_of: string
    accounting: string
    moneyness_pct: number
    e0: number
    monthly: number
    put_share: number
    horizon_years: number
    dividend_yield: number
    accuracy: {
      moneyness_pct: number
      vol_gap: number
      calm_ratio: number
      stress_ratio: number
      statement: string
    }
    window: PlanWindow | null
    rolling: RollingSummary | null
    refusal: string | null
  }
}

export interface ModelPlanParams {
  moneyness_pct: number
  e0: number
  monthly: number
  put_share: number
  horizon_years: number
}

export async function fetchModelPlan(params: ModelPlanParams, signal?: AbortSignal): Promise<ModelPlanResponse> {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))
  const resp = await fetch(`/api/putlab/book-plan/model?${qs}`, { signal })
  if (!resp.ok) {
    const body: unknown = await resp.json().catch(() => null)
    const detail = detailOf(body) ?? `GET /api/putlab/book-plan/model failed: ${resp.status}`
    throw new ApiError(detail, resp.status)
  }
  return (await resp.json()) as ModelPlanResponse
}
