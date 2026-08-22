import { expect, test } from '@playwright/test'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'

// The basket builder. Behaviour is unchanged from the old Portfolio panel --
// leg add/remove/edit, the fragile-basket loader, Top K -- so what these guard
// is the arithmetic the copy claims and the diversification figure, both of
// which are easy to get subtly wrong while restyling.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Portfolio' }).click()
})

test('runs only on an explicit backtest, not on every edit', async ({ page }) => {
  expect(apiRequests(page)).not.toContain('/api/putlab/portfolio')
  await page.getByRole('button', { name: 'Backtest the basket' }).click()
  await expect(page.locator('.pl-stats')).toBeVisible()
  expect(apiRequests(page)).toContain('/api/putlab/portfolio')
})

test('says the weight is a share of capital over the whole window', async ({ page }) => {
  // Treating the share as a PER-ROLL figure makes total premium scale with roll
  // count, so a short-tenor leg silently dominates. The note has to say which.
  await expect(page.getByText(/share of total capital over the window/i)).toBeVisible()
  await expect(page.getByText(/notional × share ÷ n_cycles/)).toBeVisible()
})

test('reports diversification as the gap between combined and summed drawdown', async ({ page }) => {
  await page.getByRole('button', { name: 'Backtest the basket' }).click()
  const stats = page.locator('.pl-stats')
  await expect(stats.locator('div', { hasText: /^Return on premium/ }).first()).toContainText('−19%')
  await expect(stats.locator('div', { hasText: /^Net P&L/ }).first()).toContainText('−$8,920')
  await expect(stats.locator('div', { hasText: /^Combined bleed/ }).first()).toContainText('−$6,240')
  // -6,240 combined against -9,810 summed = $3,570 of drawdown never taken.
  await expect(stats.locator('div', { hasText: /^Diversification/ }).first()).toContainText('$3,570')
})

test('lists each leg with its own verdict', async ({ page }) => {
  await page.getByRole('button', { name: 'Backtest the basket' }).click()
  const table = page.getByRole('table', { name: 'Basket legs' })
  await expect(table.locator('tbody tr')).toHaveCount(2)
  await expect(table.locator('tbody tr', { hasText: 'Tesla' })).toContainText('confirmed')
  await expect(table.locator('tbody tr', { hasText: 'S&P 500' })).toContainText('failed')
})

test('adds and removes legs, and refuses to remove the last one', async ({ page }) => {
  const legs = page.locator('.pl-leg')
  await expect(legs).toHaveCount(2)
  await page.getByRole('button', { name: 'Add leg' }).click()
  await expect(legs).toHaveCount(3)
  await page.getByRole('button', { name: 'Remove leg' }).first().click()
  await expect(legs).toHaveCount(2)

  await page.getByRole('button', { name: 'Remove leg' }).first().click()
  await expect(legs).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Remove leg' })).toBeDisabled()
})

test('loads the fragile basket from the live screen', async ({ page }) => {
  await page.getByRole('button', { name: 'Load the fragile basket' }).click()
  // Top 5 of the ranking: TSLA, IWM, XLF, EEM, QQQ.
  await expect(page.locator('.pl-leg')).toHaveCount(5)
  await expect(page.locator('.pl-stats')).toBeVisible()
})
