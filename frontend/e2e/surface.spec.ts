import { expect, test, type Page } from '@playwright/test'
import type { SurfaceResponse } from '../src/api/client'
import { mockPutLabApi } from './fixtures/mock-api'
import { SURFACE_FITTED } from './fixtures/putlab'

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
  for (const id of ['alpha-dots', 'alpha-ceilings']) {
    const box = await page.getByTestId(id).evaluate((el) => {
      const b = (el as SVGGraphicsElement).getBBox()
      return { x: b.x, width: b.width }
    })
    expect(box.x, id).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width, id).toBeLessThanOrEqual(vbW!)
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
  await expect(page.getByRole('tabpanel')).toContainText('1.031')
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
  // The chart rows refuse in words too, ceiling reason included.
  await expect(page.getByRole('tabpanel')).toContainText('REFUSED — no smile slope')
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
