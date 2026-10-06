import { useEffect, useState } from 'react'
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
