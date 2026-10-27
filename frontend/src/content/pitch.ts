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
  /** Sales with no qualifying exit bid, credited intrinsic instead;
   *  `_itm` = those where intrinsic was above zero. */
  exits_at_intrinsic: number
  exits_at_intrinsic_itm: number
  /** Of those, sales whose strike/spot at exit is above the panel's 1.02
   *  band: no quote exists, so intrinsic omits real time value. */
  exits_beyond_panel: number
  /** Whether the rule's best roll is one of those. */
  best_roll_beyond_panel: boolean
  by_period: Record<string, { n: number; mean_roi: number; hits: number }>
}

export interface HedgeConfig {
  rule: string
  budget: number
  premium_pct_per_year: number
  growth: number
  edge: number
  max_dd: number
  /** [peak, trough] of the worst drawdown. */
  max_dd_span: string[]
  /** The roll open on the drawdown's peak day (null if none was): its put's
   *  value then and at exit, per unit of premium (1 = its cost). */
  max_dd_peak_put: { entry: string; exit: string; mark_multiple: number; exit_multiple: number } | null
  drop_best?: { edge: number; edge_without_best: number; best_entry: string }
}

export interface Hedge {
  window: string[]
  unhedged_growth: number
  unhedged_max_dd: number
  unhedged_max_dd_span: string[]
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
  phase_min: number | null
  phase_max: number | null
}

export interface UnderlyingEvidence {
  standalone: Record<string, Standalone>
  /** Rolls (over all 12 rules) entered or exited on a session the exchange was shut. */
  rolls_on_closed_days: { rolls: number; touching: number }
  hedge: Hedge
  every_day: Record<string, EveryDay>
  episodes_10pct: { peak: string; trough: string; dd: number; unrecovered?: boolean }[]
}

/** null where a gate took no roll, or every roll (the generator maps NaN to null). */
export interface ComboStats {
  n: number
  taken: number
  roi_diff: number | null
  hits_taken: number
  hits_all: number
  dg: number | null
  mean_roi_taken: number | null
  mean_roi_all: number
}

export interface PValued {
  rule: string
  gate: string
  oos: ComboStats
  oos_p: number
}

export interface CrisisBook {
  budget: number
  terminal: number
  max_dd: number
  terminal_without_best?: number
}

export interface CrisisRule {
  unhedged: { terminal: number; max_dd: number }
  books: CrisisBook[]
  n_beat: number
  rolls: number
  filled: number
  mean_roi: number
  hits: number
  best_roi: number
  best_entry: string
  last: string
  /** Roll dates whose put the guards refused, and which guard refused it. */
  refused: { entry: string; reason: string }[]
}

export interface Selected {
  eligible: number
  rule: string
  gate: string
  is: ComboStats
  oos: ComboStats
  oos_ci95: number[]
  oos_p: number
  /** Eligible combinations that improved in sample, and of those the ones
   *  that also improved out of sample. (Not "the share positive out of
   *  sample": every gate's complement is in the set, so that is ~1/2.) */
  is_positive: number
  is_positive_oos_positive: number
  /** The null: every rule's out-of-sample outcomes rotated by BLOCK..n-BLOCK
   *  rolls (every shift at least one bootstrap block from zero, mod n)
   *  against the filters' fixed choices; how many rotations reach the
   *  observed persistence. */
  null_rotations: number
  null_at_least_observed: number
  /** The same null over every rotation 1..n-1, the window the first run printed. */
  null_all_rotations: number
  null_all_at_least_observed: number
  spearman_is_oos: number
  oos_mean_taken_without_best: number | null
}

export interface PanelInfo {
  sessions: number
  first: string
  last: string
  missing_trading_days: number
  sessions_on_closed_days: string[]
  /** Closed-day rows whose next exchange session is missing: a date shifted one session early. */
  closed_day_then_next_missing: number
  rows: number
}

export interface PitchEvidence {
  as_of: string
  /** The commit that generated the file; dirty = scripts/ or src/ differed
   *  from it. `tree` = git tree hashes of src/ and scripts/, which survive a
   *  squash merge where the branch commit does not. */
  code: { sha: string; tree: { src: string; scripts: string }; dirty: boolean }
  dividend_yield_assumed: number
  commission_per_contract: number
  block_rolls: number
  snapshots: Record<string, string>
  panels: Record<'SPY' | 'QQQ', PanelInfo>
  SPY: UnderlyingEvidence
  QQQ: UnderlyingEvidence
  cboe: { span: string[] } & Record<'SPX' | 'PPUT' | 'PPUT3M' | 'VXTH', { growth: number; max_dd: number }>
  crisis_2008: {
    roll_dates: number
    first: string
    /** Days to the nearest listed expiry, min and max over the roll dates. */
    nearest_dte: number[]
    dates_with_two_expiries: string[]
    /** 2008-01..2009-12 months with no roll date. */
    months_without_roll_date: string[]
    /** Where a missing roll date leaves no put held: from the expiry of the
     *  put bought on `entry` to the next roll date. */
    unhedged_spans: { entry: string; expiry: string; next_entry: string }[]
    n_beat: number
    configs: number
    /** The same check with the 50%-of-mid spread cap lifted (a wide quote bought at its ask). */
    spread_guard_off: {
      n_beat: number
      depths: Record<
        string,
        {
          filled: number
          rolls: number
          n_beat: number
          best_entry: string
          unhedged: { terminal: number; max_dd: number }
          books: CrisisBook[]
        }
      >
    }
  } & Record<'5pct' | '10pct' | '15pct' | '20pct', CrisisRule>
  timing: {
    combinations: number
    rules: number
    gates: number
    is_period: string[]
    oos_period: string[]
    /** A combination is eligible only if its filter took at least this many in-sample rolls. */
    min_is_taken: number
    /** Share of wealth spent per roll taken in the growth comparison. */
    budget: number
    /** Share of entries with an implied-alpha reading, lowest and highest over the rules. */
    alpha_coverage: number[]
    roi_diff: Selected
    dg: Selected
    bh: {
      tested: number
      fdr: number
      survivors: PValued[]
      min_p: number
      p_below_05: number
      p_floor: number
      /** BH over the sold-after-4-weeks rules alone. */
      sell_rules: { tested: number; survivors: number; min_p: number }
      smallest_p: PValued[]
      /** A perfect-foresight filter (the best third of out-of-sample rolls), per rule. */
      oracle: Record<string, { oos_winners: number; p: number }>
      /** How alike the smallest-p tests are: one result seen several times, or several. */
      smallest_p_overlap: {
        tests: number
        same_oos_entries: boolean
        oos_entries: number
        same_skipped: boolean
        skipped: string[]
        /** Pairwise correlation of out-of-sample roll returns: min, median (null unless same entries). */
        roi_corr: number[] | null
      }
    }
  }
}

export const EVIDENCE = raw as PitchEvidence

/** "20pct_4w_hold" -> "20% OOM, 4-week put held to expiry". */
export function ruleLabel(rule: string): string {
  const [depth, struct] = [rule.split('pct_')[0], rule.split('pct_')[1]]
  const how: Record<string, string> = {
    '4w_hold': '4-week put, held to expiry',
    '8w_sell4w': '8-week put, sold at the bid after 4 weeks',
    '12w_sell4w': '12-week put, sold at the bid after 4 weeks',
  }
  return `${depth}% OOM, ${how[struct ?? ''] ?? struct}`
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

/** "20pct_4w_hold" -> "20% · 4w held": the narrow-screen label. */
export function ruleShort(rule: string): string {
  const [depth, struct] = [rule.split('pct_')[0], rule.split('pct_')[1]]
  const how: Record<string, string> = { '4w_hold': '4w held', '8w_sell4w': '8w sold@4w', '12w_sell4w': '12w sold@4w' }
  return `${depth}% · ${how[struct ?? ''] ?? struct}`
}

/** Live readings of the deployed app, 2026-10-20..27 (GET only). */
export const LIVE = {
  read_on: '2026-10-27',
  code_sha: 'fa0fa3d',
  /** GET /api/putlab/regime-verdict?asset=spy&moneyness_pct=5&tenor_weeks=4&years=4&as_of=D,
   *  every session 2026-10-20..27; first_entry / last_expiry are the first and
   *  last cycle of GET /api/putlab/backtest with the same parameters. */
  spy_verdicts: [
    { as_of: '2026-10-20', verdict: 'failed', calm: -0.916, elevated: -1.035, crisis_n: 1, first_entry: '2022-10-11', last_expiry: '2026-10-08' },
    { as_of: '2026-10-21', verdict: 'failed', calm: -0.941, elevated: -0.821, crisis_n: 2, first_entry: '2022-10-12', last_expiry: '2026-10-09' },
    { as_of: '2026-10-22', verdict: 'regime_only', calm: -0.747, elevated: 0.721, crisis_n: 2, first_entry: '2022-10-13', last_expiry: '2026-10-12' },
    { as_of: '2026-10-23', verdict: 'failed', calm: -0.457, elevated: -0.423, crisis_n: 2, first_entry: '2022-10-14', last_expiry: '2026-10-13' },
    { as_of: '2026-10-26', verdict: 'failed', calm: -0.457, elevated: -0.423, crisis_n: 2, first_entry: '2022-10-14', last_expiry: '2026-10-13' },
    { as_of: '2026-10-27', verdict: 'regime_only', calm: 1.073, elevated: -0.92, crisis_n: 2, first_entry: '2022-10-18', last_expiry: '2026-10-15' },
  ],
  /** gh run list --workflow daily-verdict-sweep.yml: scheduled 06:30 UTC,
   *  started 11:25-13:47 UTC on 2026-10-19..26. */
  verdict_sweep_started: '11:25–13:47 UTC',
  /** GET /api/putlab/hedge-overlay -- margin: the CAGR of the best partial
   *  mix minus the better of the two ends (index alone, program alone), per
   *  year, at the 1.9% ASSUMED dividend yield (no tiingo_eod). Within
   *  +-1bp/yr (hedge_sizing.MIN_MARGIN) it reads "inconclusive". */
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
    { rank: 1, asset: 'KRE', rule: '10% OOM, 2 weeks', model_premium: 0.0008, market: 'bid 0.00 / ask 0.88', expected_roi: 18.03 },
    /* The listed contract is the Feb-2027 expiry (115 days) against the
     * model's 2027-01-19 target (84 days): 7.40 / 0.9527 = 7.8x is NOT like
     * for like. At the 115-day mid's implied vol (29.1%, r = 4%) the 84-day
     * put is about 5.36, 5.6x the model. */
    { rank: 7, asset: 'AAPL', rule: '10% OOM, 12 weeks', model_premium: 0.95, market: 'mid 7.40 on the nearest listed expiry, February 2027 (115 days against the model’s 84); at that quote’s implied vol an 84-day put is about 5.36, 5.6× the model', expected_roi: 5.3 },
  ],
  roll_schedule_quote_status: { quoted: 1, illiquid: 2, not_collected: 7 },
} as const
