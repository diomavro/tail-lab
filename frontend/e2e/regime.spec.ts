import { expect, test } from '@playwright/test'
import { REGIMES } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The regime backdrop every verdict is read against.
//
// Two things this guards. The VIX-stretch reading (z-score, close, 20d mean,
// 20d std) had gone missing from the view -- `.vix-panel .tile` in the old
// stylesheet is the fossil of where it used to live. And the bands are VIX
// LEVELS, not realized volatility: calm < 17, elevated 17-28, crisis >= 28. Any
// copy describing them as realized vol is simply wrong.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Regime' }).click()
})

test('leads with the regime we are in and the VIX beside it', async ({ page }) => {
  await expect(page.locator('.pl-regime-now')).toHaveText('elevated')
  await expect(page.getByText('VIX 21.4')).toBeVisible()
})

test('defines the bands as VIX levels, never as realized volatility', async ({ page }) => {
  const body = page.getByRole('tabpanel')
  await expect(body).toContainText('calm below 17')
  await expect(body).toContainText('crisis at 28 and above')
  await expect(body).not.toContainText(/realized vol/i)
})

test('restores the VIX stretch reading', async ({ page }) => {
  const stretch = page.getByRole('region', { name: 'VIX stretch' })
  await expect(stretch).toContainText('1.21σ')
  await expect(stretch).toContainText('21.42') // close
  await expect(stretch).toContainText('17.86') // 20d mean
  await expect(stretch).toContainText('2.94') // 20d std
})

test('draws the whole timeline as one strip', async ({ page }) => {
  await expect(page.locator('.pl-regime-strip > span')).toHaveCount(REGIMES.segments.length)
  await expect(page.locator('.pl-regime-axis')).toContainText(REGIMES.segments[0]!.start)
})

test('gives each regime its share, its sessions, and its model residual', async ({ page }) => {
  const cards = page.locator('.pl-regime-card')
  await expect(cards).toHaveCount(3)
  const calm = cards.filter({ hasText: 'calm' })
  await expect(calm).toContainText('53%')
  await expect(calm).toContainText('529 sessions')
  // The residual is positive in calm and negative in crisis -- the sign flip is
  // the finding, so the two must not print with the same sign.
  await expect(calm).toContainText('+2.19%/yr')
  await expect(cards.filter({ hasText: 'crisis' })).toContainText('−1.46%/yr')
})

test('ranks the longest stretches', async ({ page }) => {
  const rows = page.getByRole('table', { name: 'Longest regime stretches' }).locator('tbody tr')
  await expect(rows).toHaveCount(5)
  await expect(rows.first()).toContainText('408')
})
