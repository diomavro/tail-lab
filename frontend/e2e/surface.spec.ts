import { expect, test, type Page } from '@playwright/test'
import type { SurfaceResponse } from '../src/api/client'
import { mockPutLabApi } from './fixtures/mock-api'
import { SURFACE_FITTED, SURFACE_REFUSED } from './fixtures/putlab'

// The Surface: implied tail index beside the realised one.
//
// Three things this guards. A fit that refused must read as the word REFUSED
// with its reason -- never a blank, a dash or a zero, which would pass for a
// measurement. The caveat must ship in the same view as the number. And every
// chart is scaled from the payload: a value outside the prototype's fixed
// ranges must still land on the plot.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

/** Serve one Surface payload for every name, after the default mock. */
async function serveSurface(page: Page, body: SurfaceResponse) {
  await page.route(
    (url) => url.pathname === '/api/putlab/surface',
    (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

const figures = (page: Page) => page.getByRole('group', { name: 'Alpha figures' })
const subpaths = (d: string | null) => (d ?? '').split('M').length - 1

test('a fitted payload shows the alphas, the gap and the dispersion as figures', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const f = figures(page)
  await expect(f.getByTestId('alpha-figure')).toHaveCount(4)
  await expect(f).toContainText('Implied α3.00')
  await expect(f).toContainText('Dispersion0.000')
  await expect(f).toContainText('Realised α2.40')
  await expect(f).toContainText('Gap0.60')
  // Implied above realised: the market prices a THINNER tail. The sign decides it.
  await expect(f).toContainText('thinner tail than history shows')
  await expect(f).not.toContainText('REFUSED')
})

test('the gap note follows the sign of the gap', async ({ page }) => {
  await serveSurface(page, {
    ...SURFACE_FITTED,
    surface: { ...SURFACE_FITTED.surface, alpha_gap: -0.4 },
  })
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(figures(page)).toContainText('fatter tail than history shows')
  await expect(figures(page)).not.toContainText('thinner')
})

test('the alpha chart draws one row per anchor: a dot at the fit, a ring at its ceiling', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const n = SURFACE_FITTED.surface.anchors.readings.length
  expect(subpaths(await page.getByTestId('alpha-dots').getAttribute('d'))).toBe(n)
  expect(subpaths(await page.getByTestId('alpha-ceilings').getAttribute('d'))).toBe(n)
})

test('an alpha outside 2.0 to 4.5 still lands on the plot', async ({ page }) => {
  // The prototype fixed the axis at 2.0-4.5; a Tesla-like fat tail does not.
  const s = structuredClone(SURFACE_FITTED)
  const first = s.surface.anchors.readings[0]!
  first.fit!.alpha = 1.6
  first.ceiling!.alpha = 5.3
  await serveSurface(page, s)
  await page.getByRole('tab', { name: 'Surface' }).click()
  const chart = page.getByTestId('alpha-chart')
  const [vbW] = ((await chart.getAttribute('viewBox')) ?? '').split(' ').slice(2).map(Number)
  // Inside the plotted axis (10 to width - 10), not merely the frame.
  for (const id of ['alpha-dots', 'alpha-ceilings']) {
    const box = await page.getByTestId(id).evaluate((el) => {
      const b = (el as SVGGraphicsElement).getBBox()
      return { x: b.x, width: b.width }
    })
    expect(box.x, id).toBeGreaterThanOrEqual(10 - 6)
    expect(box.x + box.width, id).toBeLessThanOrEqual(vbW! - 10 + 6)
  }
})

test('the ladder chart has one rung per quoted strike, capped at 8, and a hover readout', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const expected = Math.min(SURFACE_FITTED.surface.ladder.length, 8)
  const rungs = page.getByTestId('ladder-rung')
  await expect(rungs).toHaveCount(expected)
  const readout = page.getByTestId('ladder-readout')
  // Defaults to the first rung (the shallowest), then follows the pointer.
  await expect(readout).toContainText(`Strike ${SURFACE_FITTED.surface.ladder[0]!.strike.toFixed(0)}`)
  await rungs.nth(3).click()
  await expect(readout).toContainText(`Strike ${SURFACE_FITTED.surface.ladder[3]!.strike.toFixed(0)}`)
  await expect(readout).toContainText(`Paretan ${SURFACE_FITTED.surface.ladder[3]!.paretan_price.toFixed(2)}`)

  await page.getByText('The ladder as numbers').click()
  await expect(page.getByRole('table', { name: 'Tail ladder rungs' }).locator('tbody tr')).toHaveCount(expected)
})

test('a ladder longer than 8 rungs draws only the first 8', async ({ page }) => {
  const s = structuredClone(SURFACE_FITTED)
  const last = s.surface.ladder[s.surface.ladder.length - 1]!
  while (s.surface.ladder.length < 10) {
    const k = s.surface.ladder[s.surface.ladder.length - 1]!.strike - 5
    s.surface.ladder.push({ ...last, strike: k })
  }
  await serveSurface(page, s)
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(page.getByTestId('ladder-rung')).toHaveCount(8)
})

test('an IV ratio outside the market spread is a filled dot with its value; inside is an open ring', async ({ page }) => {
  const s = structuredClone(SURFACE_FITTED)
  const rung = s.surface.ladder[2]!
  rung.paretan_price = (rung.ask as number) + 0.5 // outside the quoted bid-ask
  rung.iv_ratio = 1.031
  await serveSurface(page, s)
  await page.getByRole('tab', { name: 'Surface' }).click()
  const shown = Math.min(s.surface.ladder.length, 8)
  expect(subpaths(await page.getByTestId('iv-outside').getAttribute('d'))).toBe(1)
  expect(subpaths(await page.getByTestId('iv-inside').getAttribute('d'))).toBe(shown - 1)
  // On the strip itself, not just in the closed table beneath it.
  const strip = page.locator('.pl-plot').filter({ has: page.getByTestId('iv-outside') })
  await expect(strip).toContainText('1.031')
})

test('the headline implied alpha is the first anchor’s, the one the gap uses', async ({ page }) => {
  const s = structuredClone(SURFACE_FITTED)
  const [a0, a1, a2] = s.surface.anchors.readings
  a0!.fit!.alpha = 3.1
  a1!.fit!.alpha = 2.8
  a2!.fit!.alpha = 2.9
  await serveSurface(page, s)
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(figures(page)).toContainText('Implied α3.10') // not the median, 2.90
})

test('the survival chart draws the fitted tail only when alpha and onset were measured', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(page.getByRole('img', { name: /Survival curves/ })).toBeVisible()
  await expect(page.getByTestId('tail-fit')).toHaveCount(1)
  await expect(page.getByRole('tabpanel')).toContainText('slope −2.40')
})

test('a refused payload says REFUSED with the reason, not a blank or a zero', async ({ page }) => {
  // Scoped: once the ranking loads, the universe row AND the strip pick both match /TSLA/.
  await page.getByRole('group', { name: 'Fragility ranking' }).getByRole('button', { name: /TSLA/ }).click()
  await page.getByRole('tab', { name: 'Surface' }).click()
  const f = figures(page)
  await expect(f).toContainText('REFUSED — fewer than 6 hygienic strikes below the anchor')
  await expect(f).toContainText('REFUSED — fewer than two anchors accepted')
  await expect(f).not.toContainText(/\b0\.00\b/)
  // Every anchor's refusal is listed in words below the chart, ceiling reason
  // included -- the figures above show only the first anchor's.
  const list = page.getByTestId('anchor-refusals').locator('li')
  await expect(list).toHaveCount(SURFACE_REFUSED.surface.anchors.readings.length)
  await expect(list.first()).toContainText('REFUSED — no smile slope')
  await expect(page.getByTestId('alpha-chart')).toHaveAttribute('aria-label', /α REFUSED — fewer than 6 hygienic strikes/)
  await expect(page.getByTestId('tail-fit')).toHaveCount(0)
})

test('the caveat and provenance travel with the number', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const caveat = page.locator('.pl-surface-caveat')
  await caveat.locator('summary').click()
  await expect(caveat).toContainText('A single implied alpha is not evidence of a power law')
  await expect(caveat).toContainText('fixed moneyness')
  await expect(page.getByRole('tabpanel')).toContainText('code abc1234')
})

test.describe('on a phone-width page', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the loaded Surface does not scroll the page sideways', async ({ page }) => {
    await page.getByRole('tab', { name: 'Surface' }).click()
    await expect(page.getByTestId('ladder-rung').first()).toBeAttached()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    // The narrow alpha chart's own frame.
    await expect(page.getByTestId('alpha-chart')).toHaveAttribute('viewBox', /^0 0 400 /)
  })
})

for (const width of [390, 900, 1024]) {
  test(`a refused payload does not scroll the page sideways at ${width}px`, async ({ page }) => {
    // Refusal reasons are long; they belong in the list, which wraps, never
    // in a chart label, which does not.
    await serveSurface(page, SURFACE_REFUSED)
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole('tab', { name: 'Surface' }).click()
    await expect(page.getByTestId('anchor-refusals')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })
}

test('every anchor’s alpha and ceiling is in the chart’s accessible name', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const name = await page.getByTestId('alpha-chart').getAttribute('aria-label')
  for (const r of SURFACE_FITTED.surface.anchors.readings) {
    expect(name).toContain(`K ${r.strike.toFixed(0)}`)
    expect(name).toContain(`ceiling ${r.ceiling!.alpha!.toFixed(2)}`)
  }
})

test('an onset on a plateau that is not flat is not drawn or stated', async ({ page }) => {
  // karamata.py: without is_flat the onset is the minimum-count floor, not a
  // measurement, and a consumer must not use it.
  const s = structuredClone(SURFACE_FITTED)
  const r = s.surface.realised!
  r.is_flat = false
  r.alpha = null
  r.refusal = 'no Hill plateau beyond the Karamata onset'
  await serveSurface(page, s)
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(page.getByRole('img', { name: /Survival curves/ })).toBeVisible()
  const pane = page.getByRole('region', { name: 'Realised tail' })
  await expect(pane).toContainText('No onset is claimed: the stable plateau is NOT flat')
  await expect(pane).not.toContainText('onset 0.02')
  await expect(pane).not.toContainText('beyond the onset at')
  await expect(page.getByTestId('tail-fit')).toHaveCount(0)
})

test('an IV ratio far from 1 never puts a negative ratio on the axis', async ({ page }) => {
  const s = structuredClone(SURFACE_FITTED)
  const rung = s.surface.ladder[0]!
  rung.iv_ratio = 3
  rung.paretan_price = (rung.ask as number) * 2 // a ratio that far is outside the spread
  await serveSurface(page, s)
  await page.getByRole('tab', { name: 'Surface' }).click()
  const strip = page.locator('.pl-plot').filter({ has: page.getByTestId('iv-outside') })
  await expect(strip).toContainText('3.000')
  await expect(strip).not.toContainText('-')
  await expect(strip).not.toContainText('−')
})

test.describe('on a touch screen', () => {
  test.use({ hasTouch: true })

  test('a rung tapped on one ladder does not crash a shorter one', async ({ page }) => {
    // A cached Surface swaps in without a loading state, so the chart is not
    // rebuilt between them: the tapped index must not carry over.
    const short = structuredClone(SURFACE_FITTED)
    short.surface.ladder = short.surface.ladder.slice(0, 3)
    await page.route(
      (url) => url.pathname === '/api/putlab/surface' && url.searchParams.get('moneyness_pct') === '10',
      (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(short) }),
    )
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.getByRole('tab', { name: 'Surface' }).click()
    const presets = page.getByRole('radiogroup', { name: 'Strike presets' })
    await presets.getByText('10%').tap()
    await expect(page.getByTestId('ladder-rung')).toHaveCount(3)
    await presets.getByText('5%', { exact: true }).tap()
    await expect(page.getByTestId('ladder-rung')).toHaveCount(8)
    await page.getByTestId('ladder-rung').nth(7).tap()
    await presets.getByText('10%').tap() // cached: no loading state between
    await expect(page.getByTestId('ladder-rung')).toHaveCount(3)
    await expect(page.getByTestId('ladder-readout')).toBeVisible()
    expect(errors).toEqual([])
  })
})
