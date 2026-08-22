import { expect, test, type Page } from '@playwright/test'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'

// The guard the old dashboard spec carried, widened to the whole workspace: a
// full walk of every tab must not throw, log an error, or make the page scroll
// sideways.

const TABS = ['Workspace', 'Portfolio', 'Bake-off', 'Regime', 'Glossary']

function collectErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(String(e)))
  return errors
}

test('walks every tab with a clean console', async ({ page }) => {
  const errors = collectErrors(page)
  await mockPutLabApi(page)
  await page.goto('/')

  for (const name of TABS) {
    await page.getByRole('tab', { name }).click()
    await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tabpanel')).toBeVisible()
  }
  // Both sheets, since the plate re-points every role token.
  await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Plate').click()
  await page.getByRole('tab', { name: 'Workspace' }).click()
  await expect(page.locator('.pl-hero-num')).toBeVisible()

  expect(errors).toEqual([])
})

test('never scrolls the page sideways, at any width', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')

  for (const width of [1440, 1024, 900, 720, 390]) {
    await page.setViewportSize({ width, height: 900 })
    for (const name of ['Workspace', 'Bake-off', 'Glossary']) {
      await page.getByRole('tab', { name }).click()
      // Wide content (tables, the sweep, the tape) scrolls inside its own
      // container; the body itself must not.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, `${name} at ${width}px`).toBeLessThanOrEqual(1)
    }
  }
})

test('stacks the control rail above the result on a narrow screen', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.setViewportSize({ width: 720, height: 900 })

  const rail = (await page.locator('.pl-side').boundingBox())!
  const main = (await page.locator('.pl-main').boundingBox())!
  expect(rail.y + rail.height).toBeLessThanOrEqual(main.y + 1)
})

test('survives an API that is entirely down', async ({ page }) => {
  const errors = collectErrors(page)
  await mockPutLabApi(page, {
    fail: [
      '/api/putlab/universe',
      '/api/putlab/backtest',
      '/api/putlab/sweep',
      '/api/putlab/accuracy',
      '/api/putlab/cadence',
      '/api/putlab/data-quality',
      '/api/putlab/leaderboard',
      '/api/putlab/regime-verdict',
      '/api/putlab/regimes',
      '/api/vix/stretch',
    ],
  })
  await page.goto('/')

  // The shell still renders, the rail falls back to its built-in six names, and
  // the failures are reported rather than shown as an empty page.
  await expect(page.getByRole('tab', { name: 'Workspace' })).toBeVisible()
  await expect(page.locator('.pl-list-row')).toHaveCount(6)
  await expect(page.getByRole('alert').first()).toBeVisible()
  expect(errors.filter((e) => !e.includes('500'))).toEqual([])
})

test('does not re-screen the universe from a tab that cannot show it', async ({ page }) => {
  // The ranking runs one backtest per name in the universe -- the most
  // expensive read in the app -- and only the Workspace renders it. Changing a
  // screening axis from a tab that cannot show the result must not pay for one.
  await mockPutLabApi(page)
  await page.goto('/')
  await expect(page.locator('.pl-rank-strip button').first()).toBeVisible()

  await page.getByRole('tab', { name: 'Glossary' }).click()
  const before = apiRequests(page).filter((p) => p === '/api/putlab/leaderboard').length

  await page.getByRole('radiogroup', { name: 'Strike presets' }).getByText('15%').click()
  await expect(page.getByLabel('Out of the money, %')).toHaveValue('15')
  await page.waitForTimeout(600) // past the 250ms debounce

  expect(apiRequests(page).filter((p) => p === '/api/putlab/leaderboard').length).toBe(before)

  // ...and going back to the Workspace does screen, at the new strike. The
  // strip keeps showing the previous strike's ranking until the new one lands,
  // so poll for the request rather than for the strip.
  await page.getByRole('tab', { name: 'Workspace' }).click()
  await expect
    .poll(() => apiRequests(page).filter((p) => p === '/api/putlab/leaderboard').length)
    .toBeGreaterThan(before)
})
