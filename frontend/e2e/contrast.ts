import type { Locator } from '@playwright/test'

// WCAG contrast of a table cell's own text on its own background.
//
// Computed colours come back as `color(srgb 0..1 ...)` for a `color-mix()` and
// as `rgb(0..255 ...)` elsewhere, so both are normalised by painting them on a
// 1x1 canvas and reading the pixel back (as sweep.spec.ts `paintedRgb` does).
// hold.spec.ts's `contrastOf` measures against the page ground and parses the
// numbers as 0..255, so it cannot read a shaded cell.

export interface CellPaint {
  /** The cell's text colour, painted. */
  fg: [number, number, number]
  /** The cell's own background, painted over white. A cell with no
   *  background of its own (transparent) reads as the canvas white, not as the
   *  sheet's paper -- compare against an unshaded cell read the same way. */
  bg: [number, number, number]
  /** The background's own alpha, 0..255 (255 = it paints, 0 = transparent). */
  bgAlpha: number
}

export async function cellPaint(cell: Locator): Promise<CellPaint> {
  return cell.evaluate((el) => {
    const paint = (css: string, base: string | null): [number, number, number, number] => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 1
      const ctx = canvas.getContext('2d')!
      if (base) {
        ctx.fillStyle = base
        ctx.fillRect(0, 0, 1, 1)
      }
      ctx.fillStyle = css
      ctx.fillRect(0, 0, 1, 1)
      const d = ctx.getImageData(0, 0, 1, 1).data
      return [d[0]!, d[1]!, d[2]!, d[3]!]
    }
    const style = getComputedStyle(el)
    const [fr, fg, fb] = paint(style.color, '#ffffff')
    const [br, bgc, bb] = paint(style.backgroundColor, '#ffffff')
    const alpha = paint(style.backgroundColor, null)[3]
    return { fg: [fr, fg, fb], bg: [br, bgc, bb], bgAlpha: alpha }
  })
}

function luminance([r, g, b]: [number, number, number]): number {
  const lin = (v: number) => {
    const x = v / 255
    return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

/** WCAG contrast ratio of a cell's text on its own (opaque) background. */
export async function cellContrast(cell: Locator): Promise<number> {
  const { fg, bg } = await cellPaint(cell)
  const [a, b] = [luminance(fg), luminance(bg)].sort((x, y) => y - x)
  return (a! + 0.05) / (b! + 0.05)
}
