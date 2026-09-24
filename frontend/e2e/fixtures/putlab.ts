// Deterministic stand-in payloads for every Put Lab endpoint, shaped exactly
// like src/api/client.ts (which in turn mirrors src/tail_lab/api/schemas.py).
//
// These exist so the UI suite can assert on *behaviour* -- which band a sweep
// cell lands in, whether a tape marker is drawn, which bake-off branch renders
// -- without a lake, a network, or 35 server-side backtests. Every number here
// is invented; the shapes and the relationships between fields are not.
//
// The values are chosen to exercise the edges the redesign changed:
//   * cycles include a roll with 0 < payoff < notional, so a marker filtered on
//     `payoff > 0` disagrees with hit_rate and the test can catch it;
//   * sweep cells span all three bands (loss / under-benchmark / beat);
//   * metric-screen has a beats-baseline and a nobody-beats-baseline variant.

import type {
  AccuracyResponse,
  CadenceResponse,
  DataQualityResponse,
  EquityPoint,
  MetricScreenComparison,
  PortfolioResponse,
  PricePoint,
  PutBacktestCycle,
  PutBacktestResponse,
  PutLabLeaderboardResponse,
  RegimeTimelineView,
  RegimeVerdictResponse,
  SweepResponse,
  UniverseMember,
  VixStretchResponse,
} from '../../src/api/client'

export const NOTIONAL = 1000

const day = 86_400_000
const START = Date.parse('2022-01-03T00:00:00Z')
const iso = (t: number) => new Date(t).toISOString().slice(0, 10)

// A gently exponential underlying so the tape's log price axis has something to
// be right about: a linear axis flattens a multi-year single-name path.
export const PRICE_PATH: PricePoint[] = Array.from({ length: 240 }, (_, i) => ({
  date: iso(START + i * 6 * day),
  price: Number((320 * Math.exp(0.0022 * i) * (1 + 0.05 * Math.sin(i / 7))).toFixed(2)),
}))

// 12 rolls. `payoff` is the cash the put returned; `cost` is what the contracts
// actually filled at, which is NOT the per-roll budget (NOTIONAL) -- that gap is
// the whole reason the ledger prints Fill and Budget as separate columns.
export const CYCLES: PutBacktestCycle[] = Array.from({ length: 12 }, (_, i) => {
  const spot = Number(PRICE_PATH[i * 18]!.price.toFixed(2))
  const strike = Number((spot * 0.95).toFixed(2))
  // roll 3 pays 420 -- real intrinsic value, but under the 1000 budget, so it is
  // NOT a hit; rolls 6 and 9 clear the budget and are.
  const payoff = i === 3 ? 420 : i === 6 ? 2600 : i === 9 ? 3200 : 0
  // Roll 11 is priced above the budget on purpose: `max(1, floor(notional /
  // (premium * 100)))` forces at least one contract, so the fill can land
  // EITHER side of the budget. That is why they are two columns.
  const premium = i === 11 ? 10.28 : Number((8.2 + i * 0.11).toFixed(2))
  const contracts = Math.max(1, Math.floor(NOTIONAL / (premium * 100)))
  const cost = Number((contracts * premium * 100).toFixed(2))
  return {
    entry_date: iso(START + i * 108 * day),
    expiry_date: iso(START + (i * 108 + 28) * day),
    spot,
    strike,
    sigma: Number((0.17 + i * 0.004).toFixed(4)),
    premium,
    contracts,
    cost,
    payoff,
    net: Number((payoff - NOTIONAL).toFixed(2)),
  }
})

const TOTAL_PAYOFF = CYCLES.reduce((s, c) => s + c.payoff, 0) // 6220
const TOTAL_PREMIUM = CYCLES.length * NOTIONAL // 12000
const TOTAL_BROKERAGE = 78
const NET_PNL = TOTAL_PAYOFF - TOTAL_PREMIUM - TOTAL_BROKERAGE

const MTM_CURVE: EquityPoint[] = PRICE_PATH.map((p, i) => ({
  date: p.date,
  cum_pnl: Number((-52 * i + (i > 108 ? 2600 : 0) + (i > 162 ? 3200 : 0)).toFixed(2)),
}))

export const BACKTEST: PutBacktestResponse = {
  asset: 'spy',
  as_of: '2026-08-21',
  notional: NOTIONAL,
  moneyness_pct: 5,
  tenor_weeks: 4,
  lookback_years: 4,
  rate: 0.0421,
  spot: 512.4,
  n_cycles: CYCLES.length,
  total_premium: TOTAL_PREMIUM,
  total_payoff: TOTAL_PAYOFF,
  total_brokerage: TOTAL_BROKERAGE,
  net_pnl: NET_PNL,
  roi_on_premium: -0.4817,
  annualized_return: -0.1554,
  // The hedge loses to the plain long, so the hero spread is negative -- the
  // ordinary case for a tail hedge, and the one the copy has to survive.
  benchmark_annualized: 0.1132,
  priced_from: 'model',
  hit_rate: 2 / 12,
  biggest_payoff_mult: 3.2,
  worst_bleed_streak: 7,
  sharpe_ratio: -0.42,
  // The first points annualize a sub-year ROI and so can reach thousands of
  // percent -- +4,180%/yr here. Real data always does this, and a pane that
  // scales to it flattens every later point onto the axis.
  annualized_so_far: PRICE_PATH.filter((_, i) => i % 12 === 0 && i > 0).map((p, i) => ({
    date: p.date,
    annualized: i === 0 ? 41.8 : i === 1 ? 6.2 : Number((-0.62 + i * 0.024).toFixed(4)),
  })),
  equity_curve: CYCLES.map((c, i) => ({
    date: c.expiry_date,
    cum_pnl: CYCLES.slice(0, i + 1).reduce((s, x) => s + x.net, 0),
  })),
  mtm_curve: MTM_CURVE,
  price_path: PRICE_PATH,
  cycles: CYCLES,
}

// Strikes 5/10/15/20 x tenors 1/4/12. Benchmark is +11.32%/yr, so:
//   negative            -> loss band
//   0 <= ann < 0.1132   -> under band
//   ann >= 0.1132       -> beat band
// At least one cell of each, and the default cell (5% / 4wk) is a loss.
// 15% and 20% sit past MODEL_PRICED_MAX_MONEYNESS_PCT on purpose: those are
// the cells the grid must show and the argmax must refuse.
const SWEEP_GRID: [number, number, number][] = [
  [5, 1, -0.212],
  [5, 4, -0.1554],
  [5, 12, -0.081],
  [10, 1, -0.146],
  [10, 4, 0.042],
  [10, 12, 0.098],
  [15, 1, 0.061],
  [15, 4, 0.187],
  [15, 12, 0.244],
  [20, 1, -0.033],
  [20, 4, 0.126],
  [20, 12, 0.309],
]

/** Mirrors research/backtest/sweep.py's MODEL_PRICED_MAX_MONEYNESS_PCT. */
export const MODEL_PRICED_MAX = 10

export const SWEEP: SweepResponse = {
  asset: 'spy',
  as_of: '2026-08-21',
  notional: NOTIONAL,
  lookback_years: 4,
  cells: SWEEP_GRID.map(([m, t, ann]) => ({
    moneyness_pct: m,
    tenor_weeks: t,
    annualized_return: ann,
    roi_on_premium: Number((ann * 3.1).toFixed(4)),
    n_cycles: Math.round((52 * 4) / t),
  })),
  benchmark_symbol: 'SPY',
  benchmark_annualized: 0.1132,
  benchmark_total: 0.537,
  model_priced_max_moneyness_pct: MODEL_PRICED_MAX,
}

export const ACCURACY: AccuracyResponse = {
  asset: 'spy',
  as_of: '2026-08-21',
  years: 4,
  window_start: '2022-08-21',
  model: {
    expected_optimism: 0.0134,
    applicability: 'direct',
    reference: 'PPUT',
    regime_mix: [
      { regime: 'calm', n_days: 612, share: 0.61 },
      { regime: 'elevated', n_days: 291, share: 0.29 },
      { regime: 'crisis', n_days: 100, share: 0.1 },
    ],
    // Residuals FLIP SIGN across regimes -- MODEL_RESIDUAL.md's headline
    // finding. A monotonic calm->crisis ramp would get this backwards.
    residual_by_regime: { calm: 0.0219, elevated: 0.0087, crisis: -0.0146 },
    basis: 'Measured against Cboe PPUT over 2022-08-21 to 2026-08-21.',
    caveat: 'Measured on a 5% OOM monthly program; read it as indicative away from that strike.',
  },
  benchmarks: [
    { index_symbol: 'PPUT', label: 'Cboe S&P 500 5% Put Protection', total_return: 0.318, annualized: 0.0712 },
    { index_symbol: 'PPUT3M', label: 'Cboe S&P 500 3-Month Put Protection', total_return: 0.264, annualized: 0.0601 },
  ],
  data_quality_flags: 0,
  data_quality_note: 'No bad ticks or stale runs in 1003 bars of SPY data.',
  assumptions: [
    { name: 'Pricing', value: 'Black–Scholes, trailing 30d realized vol as IV proxy', leverage: 'understates crisis premiums' },
    { name: 'Brokerage', value: '$0.65 per contract', leverage: 'small at this size' },
    { name: 'Fill', value: 'mid, no slippage', leverage: 'optimistic in a dislocation' },
  ],
  // The partial-coverage case on purpose: real quotes exist for SPY and the
  // window is only half covered by them, which is the state the panel most
  // has to get right. A fixture showing "complete" would let a regression that
  // drops the gap warning pass unnoticed.
  quote_coverage: {
    priced_from: 'model',
    real_quotes_available: true,
    window_months: 49,
    months_present: 16,
    months_missing: 33,
    complete: false,
    panel_first_month: '201001',
    panel_last_month: '202312',
    note:
      'Real quotes for this name are 33 of the 49 months in this window MISSING. ' +
      'The figures on screen do NOT use them either way — the roll engine still ' +
      'prices every leg with Black-Scholes at a flat volatility (docs/adr/0004). ' +
      'Holding the data is not the same as using it.',
  },
}

export const CADENCE: CadenceResponse = {
  symbol: 'spy',
  cadence: 'weekly',
  avg_gap_days: 3.4,
  label: 'Weekly expiries',
  detail: 'SPY lists weeklies; a 4-week tenor lands on a real expiry every roll.',
}

export const DATA_QUALITY: DataQualityResponse = {
  asset: 'spy',
  as_of: '2026-08-21',
  n_bars: 1003,
  n_suspicious: 0,
  flags: [],
}

export const UNIVERSE: UniverseMember[] = [
  ['SPY', 'S&P 500', 'Broad US large cap'],
  ['QQQ', 'Nasdaq-100', 'US mega-cap tech'],
  ['IWM', 'Russell 2000', 'US small cap'],
  ['TSLA', 'Tesla', 'High-beta single name'],
  ['GLD', 'Gold', 'Metals'],
  ['EEM', 'Emerging Markets', 'EM equity'],
  ['XLF', 'Financials', 'US bank complex'],
].map(([symbol, name, label]) => ({
  symbol: symbol!,
  name: name!,
  cadence: 'weekly' as const,
  avg_gap_days: 3.4,
  label: label!,
  detail: `${symbol} lists weekly expiries.`,
}))

// `fragility_score` is a fractional rank in 0..1, exactly as
// research/backtest/ranking.py serves it -- NOT a 0-100 score. Ranked so the
// strip's top three are TSLA / IWM / XLF, and each advertises a
// DIFFERENT best cell from the currently-selected 5% / 4wk -- which is what
// makes "clicking a row lands on the cell it advertised" testable.
export const LEADERBOARD: PutLabLeaderboardResponse = {
  as_of: '2026-08-21',
  moneyness_pct: 5,
  tenor_weeks: 4,
  lookback_years: 4,
  notional: NOTIONAL,
  ranked: [
    ['tsla', 'Tesla', 0.884, 412.6, 1.62, -0.94, 4.1, 1.44, 1.31, 0.244, 10, 12, 'confirmed'],
    ['iwm', 'Russell 2000', 0.712, 214.8, 1.28, -0.61, 3.2, 1.19, 1.12, 0.118, 10, 4, 'regime_only'],
    ['xlf', 'Financials', 0.639, 48.2, 1.14, -0.44, 2.9, 1.06, 1.03, 0.061, 8, 12, 'regime_only'],
    ['eem', 'Emerging Markets', 0.551, 41.9, 0.98, -0.29, 2.6, 0.94, 0.97, -0.022, 5, 4, 'failed'],
    ['qqq', 'Nasdaq-100', 0.487, 468.1, 1.09, -0.18, 3.4, 1.02, 1.05, -0.048, 6, 12, 'failed'],
    ['spy', 'S&P 500', 0.402, 512.4, 1.0, -0.12, 3.6, 1.0, 1.0, -0.081, 5, 12, 'failed'],
    ['gld', 'Gold', 0.125, 198.3, -0.21, 0.34, 2.1, -0.18, 0.31, 0.312, 5, 1, 'confirmed'],
  ].map((r) => {
    const [asset, name, frag, spot, dnBeta, coSkew, coKurt, tailBeta, dnCap, bestAnn, bestM, bestT, verdict] =
      r as [string, string, number, number, number, number, number, number, number, number, number, number, string]
    return {
      asset,
      name,
      spot,
      downside_beta: dnBeta,
      co_skewness: coSkew,
      co_kurtosis: coKurt,
      tail_beta: tailBeta,
      downside_capture: dnCap,
      // Not a fixture column of its own -- derived from `frag` like the other
      // fields below, more negative for the more-fragile names (vol_beta is
      // fragile-when-low, same convention as co_skewness).
      vol_beta: Number((-(0.5 + frag * 3)).toFixed(2)),
      fragility_score: frag,
      roi_on_premium: bestAnn * 2.8,
      annualized_return: bestAnn - 0.03,
      verdict: verdict as RegimeVerdictResponse['verdict'],
      hit_rate: 0.08 + frag / 9,
      biggest_payoff_mult: 1 + frag * 4,
      n_cycles: 52,
      // The screen never passes real quotes in today (AGENT_TODO.md, "the
      // ranked screen is still model-priced"), so every row is "model".
      priced_from: 'model',
      best_annualized: bestAnn,
      best_moneyness_pct: bestM,
      best_tenor_weeks: bestT,
      // All five below are measured at (bestM, bestT) -- the same cell -- which
      // is what the recommendations view ranks on.
      best_roi_on_premium: Number((bestAnn * 3.4).toFixed(4)),
      best_hit_rate: Number((0.06 + frag / 7).toFixed(4)),
      best_biggest_payoff_mult: Number((1.4 + frag * 5).toFixed(2)),
      best_n_cycles: Math.round((52 * 4) / bestT),
      best_verdict: verdict as RegimeVerdictResponse['verdict'],
    }
  }),
}

const screenEntry = (
  metric: string,
  label: string,
  assets: string[],
  roi: number,
  lift: number,
  spearman: number | null,
  verdict: RegimeVerdictResponse['verdict'],
  pvalue: number | null,
  significantCorrected: boolean,
): MetricScreenComparison['entries'][number] => ({
  metric,
  label,
  top_k_assets: assets,
  roi_on_premium: roi,
  annualized_return: roi / 3.1,
  hit_rate: 0.1 + Math.abs(lift),
  combined_max_drawdown: -4200 - Math.round(lift * 1000),
  verdict,
  regime_slices: [
    { regime: 'calm', n_cycles: 31, roi_on_premium: roi - 0.08, paid_off: roi > 0.08 },
    { regime: 'crisis', n_cycles: 7, roi_on_premium: roi + 0.31, paid_off: true },
  ],
  spearman_vs_payoff: spearman,
  spearman_pvalue: pvalue,
  significant_raw: pvalue != null && pvalue < 0.05,
  significant_corrected: significantCorrected,
  lift_vs_baseline: lift,
})

// top_k = 5: the composite wins with real lift above the baseline. Only the
// composite survives Benjamini-Hochberg correction; downside beta clears the
// uncorrected p < 0.05 hurdle alone (the "raw only" tag), which is the exact
// raw-vs-corrected divergence the bake-off's Sig. column exists to show.
export const METRIC_SCREEN_WINNER: MetricScreenComparison = {
  as_of: '2026-08-21',
  moneyness_pct: 5,
  tenor_weeks: 4,
  lookback_years: 4,
  top_k: 5,
  universe_size: 35,
  baseline_roi: -0.241,
  n_comparisons: 7,
  fdr_alpha: 0.05,
  entries: [
    screenEntry('fragility_score', 'Composite fragility', ['tsla', 'iwm', 'xlf', 'eem', 'qqq'], -0.225, 0.016, 0.34, 'confirmed', 0.018, true),
    screenEntry('downside_beta', 'Downside beta', ['tsla', 'iwm', 'qqq', 'xlf', 'spy'], -0.238, 0.003, 0.21, 'regime_only', 0.041, false),
    screenEntry('co_skewness', 'Co-skewness', ['tsla', 'eem', 'iwm', 'xlf', 'gld'], -0.262, -0.021, 0.09, 'regime_only', 0.35, false),
    screenEntry('tail_beta', 'Tail beta', ['tsla', 'iwm', 'eem', 'qqq', 'xlf'], -0.271, -0.03, -0.04, 'failed', 0.82, false),
    screenEntry('downside_capture', 'Downside capture', ['tsla', 'xlf', 'iwm', 'eem', 'spy'], -0.289, -0.048, -0.12, 'failed', 0.49, false),
    screenEntry('vol_beta', 'Vol beta', ['tsla', 'iwm', 'xlf', 'qqq', 'eem'], -0.301, -0.06, 0.14, 'regime_only', 0.24, false),
    // Co-kurtosis picks the broad indices -- the index-flattering behaviour
    // MODEL_RESIDUAL.md describes, and why it is excluded from the composite.
    screenEntry('co_kurtosis', 'Co-kurtosis', ['spy', 'iwm', 'qqq', 'eem', 'xlf'], -0.316, -0.075, -0.28, 'failed', 0.06, false),
  ],
}

// top_k = 3: nothing clears the buy-everything baseline, so the honest branch
// of the verdict copy has to render instead.
export const METRIC_SCREEN_NO_WINNER: MetricScreenComparison = {
  ...METRIC_SCREEN_WINNER,
  top_k: 3,
  baseline_roi: -0.198,
  entries: METRIC_SCREEN_WINNER.entries.map((e) => ({
    ...e,
    top_k_assets: e.top_k_assets.slice(0, 3),
    roi_on_premium: e.roi_on_premium - 0.06,
    lift_vs_baseline: e.roi_on_premium - 0.06 - -0.198,
  })),
}

export const REGIME_VERDICT: RegimeVerdictResponse = {
  asset: 'spy',
  as_of: '2026-08-21',
  rule_hash: 'a91f3c2e',
  verdict: 'regime_only',
  slices: [
    { regime: 'calm', n_cycles: 8, roi_on_premium: -0.72, paid_off: false },
    { regime: 'elevated', n_cycles: 3, roi_on_premium: -0.19, paid_off: false },
    { regime: 'crisis', n_cycles: 1, roi_on_premium: 1.86, paid_off: true },
  ],
}

export const REGIMES: RegimeTimelineView = {
  as_of: '2026-08-21',
  current: 'elevated',
  segments: [
    { regime: 'calm', start: '2022-08-22', end: '2023-02-14', n_days: 121 },
    { regime: 'elevated', start: '2023-02-15', end: '2023-06-30', n_days: 96 },
    { regime: 'crisis', start: '2023-07-03', end: '2023-08-18', n_days: 34 },
    { regime: 'calm', start: '2023-08-21', end: '2025-04-04', n_days: 408 },
    { regime: 'elevated', start: '2025-04-07', end: '2026-08-21', n_days: 344 },
  ],
  day_counts: { calm: 529, elevated: 440, crisis: 34 },
}

export const VIX: VixStretchResponse = {
  date: '2026-08-21',
  close: 21.42,
  rolling_mean_20d: 17.86,
  rolling_std_20d: 2.94,
  z_score: 1.21,
}

export const PORTFOLIO: PortfolioResponse = {
  as_of: '2026-08-21',
  notional: 10_000,
  lookback_years: 4,
  total_premium: 48_000,
  total_payoff: 39_400,
  net_pnl: -8_920,
  roi_on_premium: -0.1858,
  annualized_return: -0.0492,
  combined_max_drawdown: -6_240,
  sum_individual_max_drawdown: -9_810,
  verdict: 'regime_only',
  legs: [
    { asset: 'tsla', name: 'Tesla', weight: 0.5, total_premium: 24_000, net_pnl: -2_100, roi_on_premium: -0.0875, annualized_return: -0.0228, verdict: 'confirmed', n_cycles: 48 },
    { asset: 'spy', name: 'S&P 500', weight: 0.5, total_premium: 24_000, net_pnl: -6_820, roi_on_premium: -0.2842, annualized_return: -0.0781, verdict: 'failed', n_cycles: 48 },
  ],
  equity_curve: MTM_CURVE.filter((_, i) => i % 4 === 0),
  snapshot_ids: ['snap_9f21', 'snap_9f22'],
}
