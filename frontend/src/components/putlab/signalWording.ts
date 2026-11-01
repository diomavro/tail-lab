import type {
  SignalNameRecord,
  SignalPaidGroup,
  SignalTestResult,
  SignalVerdict,
  SignalVerdictStatus,
} from '../../api/client'
import { deltaLabel } from './types'

// Wording for the out-of-sample signal test, shared by the Bake-off headline
// and the Workspace strip so the two can never describe one result two ways.
// The verdict sentence itself comes from the API (walk_forward.py); the UI adds
// the cadence, the window and the per-status label.

const MINUS = '−'

/** A ratio as a signed percent with one decimal and a true minus. */
export function pct1(x: number): string {
  return `${x >= 0 ? '+' : MINUS}${Math.abs(x * 100).toFixed(1)}%`
}

/** The API's sentence with its ASCII hyphen-minus set as a true minus. For
 *  insufficient data the sentence opens with the status label's own words
 *  ("Too little history to judge (fewer than 24 periods)"), and the status is
 *  printed beside it, so the repeated words are dropped and the reasons stay. */
export function sentenceText(v: SignalVerdict): string {
  const s = v.sentence.replace(/-(\d)/g, `${MINUS}$1`)
  return v.status === 'insufficient_data' ? s.replace(/^Too little history to judge\s*/, '') : s
}

/** How often the test rebalances, from the tenor: one roll per rebalance. */
export function cadenceText(tenorWeeks: number): string {
  if (tenorWeeks === 1) return 'weekly'
  if (tenorWeeks === 12) return 'every 12 weeks (quarterly)'
  return `every ${tenorWeeks} weeks`
}

/** "0.10Δ at realised vol · 4w · 4y · top 5" -- the settings a result ran at. */
export function settingsText(r: Pick<SignalTestResult, 'strike_rule' | 'target_delta' | 'moneyness_pct' | 'tenor_weeks' | 'signal_years' | 'top_k'>): string {
  const strike =
    r.strike_rule === 'delta' && r.target_delta != null ? deltaLabel(r.target_delta) : `${r.moneyness_pct}% OOM`
  return `${strike} · ${r.tenor_weeks}w · ${r.signal_years}y signal window · top ${r.top_k}`
}

export const STATUS_LABEL: Record<SignalVerdictStatus, string> = {
  beats_random: 'Beats random baskets',
  no_better_than_random: 'No better than random baskets',
  crash_carried: 'Carried by one event',
  insufficient_data: 'Too little history to judge',
}

export const STATUS_TAG: Record<SignalVerdictStatus, string> = {
  beats_random: 'pl-tag pl-tag-ok',
  no_better_than_random: 'pl-tag pl-tag-mute',
  crash_carried: 'pl-tag pl-tag-bad',
  insufficient_data: 'pl-tag pl-tag-outline',
}

/** The magnitude behind "carried by one event": the worse of the two excesses
 *  once the best period / best single name-period is dropped. */
export function exBestText(v: SignalVerdict): string | null {
  if (v.status !== 'crash_carried') return null
  const xs = [v.excess_ex_best_period, v.excess_ex_best_name_period].filter((x): x is number => x != null)
  return xs.length === 0 ? null : `Without its best period or name-period the excess is ${pct1(Math.min(...xs))}.`
}

/** Test window, as one clause. */
export function windowText(r: Pick<SignalTestResult, 'test_start' | 'test_end' | 'n_periods' | 'n_episodes' | 'tenor_weeks'>): string {
  if (r.test_start == null || r.test_end == null) return 'No test window.'
  return `Rebalanced ${cadenceText(r.tenor_weeks)}: ${r.n_periods} periods from ${r.test_start} to ${r.test_end}, ${r.n_episodes} stress episode${r.n_episodes === 1 ? '' : 's'}.`
}

// ---- One name's record (the Workspace's "How the signals treated this name") ----

/** A group of name-periods smaller than this is not summarised: "too few to judge". */
export const MIN_GROUP = 5

/** Screens by id, as the API names them (walk_forward.SCREENS). */
const SCREEN_NAME: Record<string, string> = {
  fragility_score: 'Composite fragility',
  downside_beta: 'Downside beta',
  co_skewness: 'Co-skewness',
  co_kurtosis: 'Co-kurtosis',
  tail_beta: 'Tail beta',
  downside_capture: 'Downside capture',
  vol_beta: 'Vol beta',
  rv20_high: '20-day vol, high',
  rv20_low: '20-day vol, low',
  rv_long_high: 'Long-window vol, high',
  rv_long_low: 'Long-window vol, low',
  rv_ratio_high: 'Vol ratio (long / 20-day), high',
  rv_ratio_low: 'Vol ratio (long / 20-day), low',
}
export const screenName = (id: string): string => SCREEN_NAME[id] ?? id

/** A payoff multiple (payoff / cost), signed: "+12×", "+3.4×". */
export function multText(x: number): string {
  return `+${x >= 10 ? x.toFixed(0) : x.toFixed(1)}×`
}

/** "k of n paid (largest +X×)", or "too few to judge" under MIN_GROUP periods. */
export function paidText(g: SignalPaidGroup): string {
  if (g.n < MIN_GROUP) return 'too few to judge'
  const largest = g.n_paid > 0 && g.largest_payoff_multiple != null ? ` (largest ${multText(g.largest_payoff_multiple)})` : ''
  return `${g.n_paid} of ${g.n} paid${largest}`
}

/** The rule every per-name figure was measured under, from the strip's settings. */
export function nameRuleText(s: { target_delta: number; tenor_weeks: number }): string {
  return `signal test rule: Δ${s.target_delta.toFixed(2)} · ${s.tenor_weeks}w, no commission`
}

/** The composite's one-line record for a name. Null when the record has no composite row. */
export function nameSummary(rec: SignalNameRecord, topK: number): string | null {
  const c = rec.screens.find((s) => s.role === 'composite')
  if (!c) return null
  const picked = c.picked.n < MIN_GROUP ? 'too few to judge' : paidText(c.picked).replace(/^(\d+) of (\d+) paid/, '$1 of those $2 paid')
  const other = c.other.n < MIN_GROUP ? 'too few to judge' : `${c.other.n_paid} of ${c.other.n}`
  return `Composite put it in the top ${topK} in ${c.n_picked_periods} of ${rec.n_eligible_periods} periods; ${picked}, vs ${other} otherwise.`
}
