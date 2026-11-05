import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'
import { SMILE_ONE_WING } from './fixtures/smile'

// The Surface tab's Smile section (docs/adr/0036). Guards: a refused slice
// reads as REFUSED with its reason, never a blank or a zero; a failed
// butterfly check is said in words beside the number; the drawn curves come
// from the served SVI parameters, one per fitted expiry the chart picks.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Surface' }).click()
})

const smile = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Smile' })

test('the smile chart draws three fitted expiries with their SVI curves', async ({ page }) => {
  const s = smile(page)
  await expect(s.getByTestId('smile-chart')).toBeVisible()
  await expect(s.getByTestId('smile-series')).toHaveCount(3)
  await expect(s.getByTestId('smile-fit')).toHaveCount(3)
  await expect(s).toContainText('30 days (2026-12-04)')
  await expect(s).toContainText('181 days (2027-05-04)')
})

test('the session note says what the forwards and the calendar check found', async ({ page }) => {
  const note = smile(page).getByTestId('smile-session')
  await expect(note).toContainText('Session 2026-11-04')
  await expect(note).toContainText('puts and calls')
  await expect(note).toContainText('dividend yield unknown, carry forwards at q = 0')
  await expect(note).toContainText('1 calendar violation between adjacent expiries')
})

test('every expiry is listed, refusals and failed checks in words', async ({ page }) => {
  const s = smile(page)
  await s.getByText('Every expiry').click()
  const table = s.getByRole('table', { name: 'Every expiry' })
  await expect(table.getByRole('row')).toHaveCount(5)
  await expect(table).toContainText('REFUSED: tenor_below_3_days')
  await expect(table).toContainText('butterfly FAILS (min g -0.0329)')
  await expect(table).toContainText('18.3%')
  await expect(table).toContainText('-4.1 pts')
  await expect(table).toContainText('(carry, q unknown)')
})

test('a refused surface says so instead of drawing nothing', async ({ page }) => {
  // Serve the refusal for every name; a later route takes precedence.
  await page.route(
    (url) => url.pathname === '/api/putlab/vol-surface',
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ symbol: 'spy', as_of: '2026-11-05', session: null, spot: null, r: 0.04, q: 0, q_source: 'unknown', two_wing: false, one_wing_reason: null, slices: [], calendar_violations: [], refusal: 'no spy put quotes on or before 2026-11-05' }),
      }),
  )
  await page.reload()
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(smile(page).getByTestId('smile-refusal')).toContainText('REFUSED: no spy put quotes')
  await expect(smile(page).getByTestId('smile-chart')).toHaveCount(0)
})

test('at phone width the opened expiry table does not scroll the page sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await smile(page).getByText('Every expiry').click()
  await expect(smile(page).getByRole('table', { name: 'Every expiry' })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

test('the drawn curve is the served SVI: its vol at k = 0 matches the parameters', async ({ page }) => {
  // Fixture SVI (a, b, rho, m, sigma) = (0.004, 0.05, -0.7, 0.02, 0.1), 30 days:
  // w(0) = 0.004 + 0.05 * (0.014 + sqrt(0.0004 + 0.01)) = 0.0097990,
  // sigma = sqrt(w / (30 / 365.25)) = 0.3454.
  const fits = smile(page).getByTestId('smile-fit')
  await expect(fits.first()).toHaveAttribute('data-atm-vol', '0.3454')
})

test('a one-wing session says why, draws no curve, and shows no number it did not measure', async ({ page }) => {
  await page.route(
    (url) => url.pathname === '/api/putlab/vol-surface',
    (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SMILE_ONE_WING) }),
  )
  await page.reload()
  await page.getByRole('tab', { name: 'Surface' }).click()
  const s = smile(page)
  await expect(s.getByTestId('smile-session')).toContainText('puts only (no call quotes for the 2026-10-20 session)')
  await expect(s.getByTestId('smile-series')).toHaveCount(1)
  await expect(s.getByTestId('smile-fit')).toHaveCount(0)
  await expect(s).toContainText('31 days (2026-11-20), one wing')
  await s.getByText('Every expiry').click()
  const row = s.getByRole('table', { name: 'Every expiry' }).getByRole('row').nth(1)
  await expect(row).toContainText('one wing: shape only')
  await expect(row.getByRole('cell').nth(5)).toHaveText('—')
})
