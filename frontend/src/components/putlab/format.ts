// Formatting + color-ramp helpers shared by the Put Lab charts, ported from
// the approved mock's fmt$/fmtPct/fmtX/ramp/lerpColor (put-lab.html).

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

function parseHex(hex: string): [number, number, number] {
  const m = hex.match(/\w\w/g)
  if (!m || m.length < 3) return [0, 0, 0]
  return [parseInt(m[0] ?? '00', 16), parseInt(m[1] ?? '00', 16), parseInt(m[2] ?? '00', 16)]
}

function lerpColor(c1: string, c2: string, f: number): string {
  const a = parseHex(c1)
  const b = parseHex(c2)
  return (
    '#' +
    a
      .map((v, i) => Math.round(v + ((b[i] ?? v) - v) * f).toString(16).padStart(2, '0'))
      .join('')
  )
}

// Reads --cool/--warm/--hot off `el`'s computed style (custom properties
// inherit, so any descendant of .putlab-root resolves the scoped tokens) and
// returns a cool -> amber -> hot ramp function over f in [0, 1].
export function makeRamp(el: Element): (f: number) => string {
  const style = getComputedStyle(el)
  const cool = style.getPropertyValue('--cool').trim().replace('#', '')
  const warm = style.getPropertyValue('--warm').trim().replace('#', '')
  const hot = style.getPropertyValue('--hot').trim().replace('#', '')
  return (f: number) => (f < 0.5 ? lerpColor(cool, warm, f / 0.5) : lerpColor(warm, hot, (f - 0.5) / 0.5))
}

// Centers a signed value at 0.5 so bleed vs. payoff reads on the ramp above.
export function normSigned(v: number, lo: number, hi: number): number {
  if (v >= 0) return 0.5 + 0.5 * (hi > 0 ? v / hi : 0)
  return 0.5 - 0.5 * (lo < 0 ? v / lo : 0)
}

const NS = 'http://www.w3.org/2000/svg'

export function svgEl<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attrs: Record<string, string | number> = {},
): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, tag) as SVGElementTagNameMap[K]
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v))
  return e
}

export function showTooltip(el: HTMLElement, x: number, y: number, html: string): void {
  el.innerHTML = html
  el.style.left = `${x}px`
  el.style.top = `${y}px`
  el.style.opacity = '1'
}

export function hideTooltip(el: HTMLElement): void {
  el.style.opacity = '0'
}
