import raw from './pitch_evidence.json' with { type: 'json' }

/* The Pitch's figures. Two kinds, kept apart on purpose:
 *
 * 1. EVIDENCE -- everything computed from real quotes, written by
 *    `make pitch-evidence` (scripts/pitch_evidence.py) into
 *    pitch_evidence.json. The page reads every study number from there and
 *    never types one in, so regenerating the file regenerates the page.
 * 2. LIVE -- what the deployed app said on the day the page was written,
 *    fetched by hand (GET only). These go stale by design; each carries the
 *    URL that reproduces it and the date it was read.
 */

export interface Standalone {
  n_rolls: number
  mean_roi: number
  mean_roi_ci95: number[]
  hits: number
  best_roi: number
  best_entry: string
  share_of_payoff_from_best_roll: number | null
  longest_losing_streak: number
  median_ask_pct_spot: number
  median_dte: number
  first_entry: string
  last_exit: string
  fill_skips: number
  by_period: Record<string, { n: number; mean_roi: number; hits: number }>
}

export interface HedgeConfig {
  rule: string
  budget: number
  premium_pct_per_year: number
  growth: number
  edge: number
  max_dd: number
  drop_best?: { edge: number; edge_without_best: number; best_entry: string }
}

export interface Hedge {
  window: string[]
  unhedged_growth: number
  unhedged_max_dd: number
  configs: HedgeConfig[]
  n_beat: number
  n_cut_dd: number
}

export interface EveryDay {
  entry_days: number
  refused_days: number
  mean_roi: number
  hit_rate: number
  phases: number
  phases_positive: number
  phase_min: number
  phase_max: number
}

export interface UnderlyingEvidence {
  standalone: Record<string, Standalone>
  hedge: Hedge
  every_day: Record<string, EveryDay>
  episodes_10pct: { peak: string; trough: string; dd: number; unrecovered?: boolean }[]
}

export interface ComboStats {
  n: number
  taken: number
  roi_diff: number
  hits_taken: number
  hits_all: number
  dg: number
  mean_roi_taken: number
  mean_roi_all: number
}

export interface Selected {
  eligible: number
  rule: string
  gate: string
  is: ComboStats
  oos: ComboStats
  oos_ci95: number[]
  oos_p: number
  share_positive_oos: number
  spearman_is_oos: number
  oos_mean_taken_without_best: number | null
}

export interface PanelInfo {
  sessions: number
  first: string
  last: string
  missing_trading_days: number
  sessions_on_closed_days: string[]
  rows: number
}

export interface PitchEvidence {
  as_of: string
  dividend_yield_assumed: number
  commission_per_contract: number
  block_rolls: number
  snapshots: Record<string, string>
  panels: Record<'SPY' | 'QQQ', PanelInfo>
  SPY: UnderlyingEvidence
  QQQ: UnderlyingEvidence
  cboe: Record<'SPX' | 'PPUT' | 'PPUT3M' | 'VXTH', { growth: number; max_dd: number }>
  timing: {
    combinations: number
    rules: number
    gates: number
    is_period: string[]
    oos_period: string[]
    roi_diff: Selected
    dg: Selected
    bh: {
      tested: number
      fdr: number
      survivors: { rule: string; gate: string; oos: ComboStats; oos_p: number }[]
      min_p: number
      p_below_05: number
      p_floor: number
    }
  }
}

export const EVIDENCE = raw as PitchEvidence

/** "20pct_4w_hold" -> "20% OTM, 4-week put held to expiry". */
export function ruleLabel(rule: string): string {
  const [depth, struct] = [rule.split('pct_')[0], rule.split('pct_')[1]]
  const how: Record<string, string> = {
    '4w_hold': '4-week put, held to expiry',
    '8w_sell4w': '8-week put, sold at the bid after 4 weeks',
    '12w_sell4w': '12-week put, sold at the bid after 4 weeks',
  }
  return `${depth}% OTM, ${how[struct ?? ''] ?? struct}`
}

/** The four parts of a timing gate's name, in words. */
export function gateLabel(gate: string): string {
  const names: Record<string, string> = {
    vix_lvl: 'VIX (prior close)',
    vix_z: 'VIX stretch (prior close)',
    put_rich: 'put IV ÷ VIX',
    put_cost: 'put cost, % of spot',
    alpha: 'implied tail index α',
  }
  const regime = gate.match(/^regime=(.+)$/)
  if (regime) return `regime is ${regime[1]!.replace('_', ' ')} (prior close)`
  const m = gate.match(/^(\w+?)(<=|>=)p(\d+)$/)
  if (!m) return gate
  const [, sig, op, q] = m
  return `${names[sig!] ?? sig} ${op === '<=' ? 'at or below' : 'at or above'} its in-sample ${q}th percentile`
}

/** Live readings of the deployed app, 2026-10-20..27 (GET only). */
export const LIVE = {
  read_on: '2026-10-27',
  code_sha: 'fa0fa3d',
  /** GET /api/putlab/regime-verdict?asset=spy&moneyness_pct=5&tenor_weeks=4&years=4&as_of=D */
  spy_verdicts: [
    { as_of: '2026-10-20', verdict: 'failed', calm: -0.916, elevated: -1.035, crisis_n: 1 },
    { as_of: '2026-10-21', verdict: 'failed', calm: -0.941, elevated: -0.821, crisis_n: 2 },
    { as_of: '2026-10-22', verdict: 'regime_only', calm: -0.747, elevated: 0.721, crisis_n: 2 },
    { as_of: '2026-10-23', verdict: 'failed', calm: -0.457, elevated: -0.423, crisis_n: 2 },
    { as_of: '2026-10-27', verdict: 'regime_only', calm: 1.073, elevated: -0.92, crisis_n: 2 },
  ],
  /** GET /api/putlab/hedge-overlay -- growth margin of the best mix over no
   *  hedge, per year, at the 1.9% ASSUMED dividend yield (no tiingo_eod). */
  book: [
    { program: 'PPUT', window: '2005-01 → 2016-03', margin: -0.00203, outcome: 'fails' },
    { program: 'PPUT', window: '1986-06 → 2026-10', margin: -0.00274, outcome: 'fails' },
    { program: 'PPUT3M', window: '2005-01 → 2016-03', margin: -0.00232, outcome: 'fails' },
    { program: 'PPUT3M', window: '2004-03 → 2026-10', margin: -0.00216, outcome: 'fails' },
    { program: 'VXTH', window: '2006-03 → 2016-03', margin: 0.00125, outcome: 'holds' },
    { program: 'VXTH', window: '2006-03 → 2026-10', margin: 0.00003, outcome: 'inconclusive' },
  ],
  /** GET /api/putlab/roll-schedule/marked (as of 2026-10-27, quotes from the
   *  2026-10-26 chain session): two of its ten legs, and how many of the ten
   *  the collected chain could price at all. */
  roll_schedule_legs: [
    { rank: 1, asset: 'KRE', rule: '10% OTM, 2 weeks', model_premium: 0.0008, market: 'bid 0.00 / ask 0.88', expected_roi: 18.03 },
    { rank: 7, asset: 'AAPL', rule: '10% OTM, 12 weeks', model_premium: 0.95, market: 'mid 7.40 (7.8× the model)', expected_roi: 5.3 },
  ],
  roll_schedule_quote_status: { quoted: 1, illiquid: 2, not_collected: 7 },
} as const
