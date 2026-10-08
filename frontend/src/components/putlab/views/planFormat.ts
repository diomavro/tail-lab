import { useEffect, useState } from 'react'
import type { BookPlanParams, ModelPlanParams } from '../../../api/client'
import { fmtFixed } from '../format'

// Formatters and the input debounce shared by the Book's two plan sources.

export const pct = (x: number, digits = 1) => `${fmtFixed(x * 100, digits)}%`
export const pp = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(2)}pp`
/** Amounts are in US dollars: every series is a USD index level. */
export const usd = (x: number) => `$${Math.round(x).toLocaleString('en-US')}`

/** A share of start months, never rounded to a false 0% or 100%: whatever
 *  would display as 0% or 100% without being exactly that is bounded instead. */
export function share(x: number): string {
  const shown = Math.round(x * 100)
  if (x > 0 && shown === 0) return '<1%'
  if (x < 1 && shown === 100) return '>99%'
  return pct(x, 0)
}

/** The largest plan amount the API accepts (``MAX_PLAN_AMOUNT``). Typed
 *  values are clamped here, because an input's ``max`` only bounds its
 *  spinner, not what can be typed. */
export const MAX_AMOUNT = 1e9

export const amount = (raw: string, floor: number): number =>
  Math.min(MAX_AMOUNT, Math.max(floor, Number(raw)))

/** ``value`` once it has stopped changing for ``ms`` -- each plan request
 *  costs ~2 s of server time, so a typed number must not fire per keystroke. */
export function useDebounced<T>(value: T, ms = 400): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return settled
}

// ---- The Book's page-scoped plan state (docs/adr/0028: inputs no other tab
// reads stay in their page). OverlayView owns it so the plan bar can sit above
// the result it drives.

export type BookMode = 'lump' | 'monthly'
export type LumpMetric = 'growth' | 'ratio'
export type PlanSource = 'real' | 'model'

export const PLAN_PROGRAMS = ['PPUT', 'PPUT3M', 'VXTH'] as const
export const PLAN_HORIZONS = [5, 10, 15, 20] as const
export const MODEL_DEPTHS = [5, 10] as const
/** The comparators with a button; every other Cboe index is in the picker. */
export const COMPARATOR_BUTTONS = ['spx', 'cash', 'bills'] as const

export const DEFAULT_REAL_PLAN: BookPlanParams = {
  program: 'PPUT',
  hedge_ratio: 0.5,
  e0: 10_000,
  monthly: 500,
  comparator: 'spx',
  horizon_years: 10,
}

export const DEFAULT_MODEL_PLAN: ModelPlanParams = {
  moneyness_pct: 5,
  e0: 10_000,
  monthly: 500,
  put_share: 1,
  horizon_years: 10,
}

/** Small counts in words, as a sentence reads them ("one of six tests"). */
const WORDS = ['none', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
export const inWords = (n: number): string => WORDS[n] ?? String(n)
