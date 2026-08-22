// Number formatting shared by the Put Lab views and charts.
//
// The colour-ramp helpers that used to live here (`makeRamp`, `normSigned`, and
// their `lerpColor`/`parseHex` support) went with SweepHeatmap: the sweep now
// prints three qualitatively different bands rather than one continuous
// cool -> amber -> hot ramp, so there is nothing left to interpolate. The
// imperative SVG helpers (`svgEl`, `showTooltip`, `hideTooltip`) went with them
// -- both charts render declaratively now.
//
// A true minus sign (U+2212), not a hyphen: these figures sit in ranged columns
// where a hyphen is visibly too short and sits at the wrong height.

export function fmtDollar(n: number): string {
  const sign = n < 0 ? '−' : ''
  const abs = Math.abs(n)
  const body = abs >= 1000 ? Math.round(abs).toLocaleString('en-US') : abs.toFixed(0)
  return `${sign}$${body}`
}

export function fmtPct(n: number): string {
  const sign = n >= 0 ? '+' : '−'
  return `${sign}${Math.abs(n * 100).toFixed(0)}%`
}

export function fmtMult(n: number): string {
  return `${n.toFixed(1)}×`
}

// A plain number at fixed precision, with the same true minus. `toFixed` on its
// own emits an ASCII hyphen, which is visibly shorter and lower than the minus
// every other figure on the page uses -- and these sit in the same columns.
export function fmtFixed(n: number, digits = 2): string {
  return `${n < 0 ? '−' : ''}${Math.abs(n).toFixed(digits)}`
}

// Cents-precision dollar formatting for spot/strike prices, where fmtDollar's
// whole-number rounding would hide the difference between adjacent strikes.
export function fmtPrice(n: number): string {
  return `$${n.toFixed(2)}`
}
