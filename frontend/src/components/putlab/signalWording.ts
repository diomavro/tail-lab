import type {
  BeyondVolCi,
  BeyondVolDemotion,
  BeyondVolMode,
  BeyondVolTest,
  SignalNameRecord,
  SignalPaidGroup,
  SignalTestResult,
  SignalVerdict,
  SignalVerdictStatus,
} from '../../api/client'
import { PREREGISTERED } from './preregistered'
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

// ---- Fragility beyond trailing vol (Fama-MacBeth, ADR 0035) ----

/** A coefficient to three decimals with an explicit sign and a true minus. */
export const signed3 = (x: number): string => `${x < 0 ? MINUS : '+'}${Math.abs(x).toFixed(3)}`

const DEMOTION_TEXT: Record<BeyondVolDemotion, string> = {
  intercept: 'it is no longer significant once the market’s own move is netted out',
  drop_best_period: 'it disappears without its single best period',
  drop_best_episode: 'it disappears without its best stress episode',
  equity_only: 'it is absent among equity names alone, or could not be checked there',
}

/** What the sentence is: a run's mode, or no test at all. */
export type BeyondVolLineMode = BeyondVolMode | 'unavailable'

export interface BeyondVolLine {
  mode: BeyondVolLineMode
  text: string
  /** Printed after the sentence, behind a concept tooltip. Null when none applies. */
  caveat: string | null
}

const lowerFirst = (s: string): string => s.charAt(0).toLowerCase() + s.slice(1)

const SURFACE_CAVEAT = 'Beyond this vol surface only.'
const NOT_READ = 'The test of fragility beyond trailing vol could not be read.'

function estimate(gammaBar: number, ci: BeyondVolCi): string {
  return `γ̄ = ${signed3(gammaBar)}, 90% CI [${signed3(ci.low)}, ${signed3(ci.high)}]`
}

/** The p-value clause (", p = 0.012"), or '' when there is nothing true to
 *  print. A value that rounds to 0.05 or more while p < 0.05 reads "p < 0.05",
 *  and a "pass" status with p at or above 0.05 prints no p at all: the page
 *  never shows a number that contradicts the status beside it. */
export function pClause(p: number | null, passing: boolean): string {
  if (p == null || Number.isNaN(p)) return ''
  if (passing && p >= 0.05) return ''
  if (p < 0.001) return ', p < 0.001'
  const rounded = p.toFixed(3)
  if (p < 0.05 && Number(rounded) >= 0.05) return ', p < 0.05'
  return `, p = ${rounded}`
}

/** Why a test could not be read as an estimate. The same words on every path. */
function notEnough(bv: BeyondVolTest): string {
  return `Not enough to test: ${bv.n_periods} periods measured (${bv.n_skipped} skipped for fewer than 40 names), and the test needs at least 24 periods and 3 stress episodes.`
}

/** The pre-registered result, quoted beside a later re-run so the two are never confused. */
function preregisteredText(): string {
  const P = PREREGISTERED
  return `The pre-registered test, on data to ${P.as_of}, found no evidence (γ̄ ${signed3(P.gamma_bar)}, 90% CI [${signed3(P.ci90_low)}, ${signed3(P.ci90_high)}], p = ${P.p_value}; ADR 0035).`
}

/** The confirmatory sentence for each status; the status is not trusted to be one we know. */
function confirmatoryLine(bv: BeyondVolTest, est: string): BeyondVolLine {
  const mode = 'confirmatory'
  const p = (passing: boolean) => pClause(bv.p_value, passing)
  switch (bv.status) {
    case 'no_evidence_beyond_trailing_vol': {
      const wholeBelowZero = bv.ci90 != null && bv.ci90.high < 0
      return {
        mode,
        text: wholeBelowZero
          ? `Controlling for trailing vol, the composite predicted worse put outcomes, not better (${est}; the 90% CI is wholly below zero).`
          : `Controlling for trailing vol, there is no evidence the composite predicts better put outcomes (${est}); this test would catch a pay-rate gap of about ${(bv.mde_pay_rate_gap * 100).toFixed(1)} points 80% of the time.`,
        caveat: SURFACE_CAVEAT,
      }
    }
    case 'predicts_beyond_trailing_vol':
      return {
        mode,
        text: `Fragility predicted put outcomes beyond trailing vol (${est}${p(true)}), possibly through their exposure to market crashes; under model pricing this means they moved more than their trailing vol implied, which real option prices may already reflect.`,
        caveat: SURFACE_CAVEAT,
      }
    case 'not_robust':
      return {
        mode,
        text: `Controlling for trailing vol the composite looks significant (${est}${p(true)}), but the result is not robust, so it is not a pass: ${bv.demotion == null ? 'a robustness check failed' : (DEMOTION_TEXT[bv.demotion] ?? 'a robustness check failed')}.`,
        caveat: SURFACE_CAVEAT,
      }
    case 'insufficient_data':
      return { mode, text: notEnough(bv), caveat: null }
    default: {
      const unknown: never = bv.status
      void unknown
      return { mode: 'unavailable', text: NOT_READ, caveat: null }
    }
  }
}

/** The one sentence on whether the composite predicts beyond trailing vol,
 *  built from the API fields (PLAN_FM section 5).
 *
 *  Only a `confirmatory` run (the pre-registered setting on the pre-registered
 *  date) gets a p-value and a verdict. A `monitoring` re-run and an
 *  `exploratory` setting read gamma_bar and its CI only: the server sends
 *  `p_value` and `status` regardless, so this is the line that enforces it. A
 *  missing or unknown mode is exploratory, never confirmatory. */
export function beyondVolLine(
  r: Pick<SignalTestResult, 'beyond_vol' | 'beyond_vol_mode' | 'beyond_vol_unavailable_reason' | 'as_of'>,
): BeyondVolLine {
  const bv = r.beyond_vol
  if (bv == null) {
    const why = r.beyond_vol_unavailable_reason
    return {
      mode: 'unavailable',
      text: `The test of fragility beyond trailing vol could not be run on these periods${why == null ? '' : ` (${why})`}.`,
      caveat: null,
    }
  }
  const mode: BeyondVolMode = r.beyond_vol_mode === 'confirmatory' || r.beyond_vol_mode === 'monitoring' ? r.beyond_vol_mode : 'exploratory'
  if (mode === 'confirmatory') {
    return bv.gamma_bar == null || bv.ci90 == null
      ? { mode, text: notEnough(bv), caveat: null }
      : confirmatoryLine(bv, estimate(bv.gamma_bar, bv.ci90))
  }
  const label =
    mode === 'monitoring'
      ? `Re-run on data to ${r.as_of} (monitoring, not the pre-registered test)`
      : 'Exploratory (not the pre-registered setting)'
  const body = bv.gamma_bar == null || bv.ci90 == null ? lowerFirst(notEnough(bv)) : `${estimate(bv.gamma_bar, bv.ci90)}.`
  return { mode, text: `${label}: ${body}${mode === 'monitoring' ? ` ${preregisteredText()}` : ''}`, caveat: null }
}
