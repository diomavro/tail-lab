import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// The Workspace result: hero, stat row, and the states around them.
//
// The hero is the annualized SPREAD against the benchmark, not return on
// premium. A tail hedge's raw ROI is almost always negative, which on its own
// tells a reader nothing; the honest question is whether it beat the plain long
// they could have held instead, and that is one number.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('leads with the spread against the benchmark, not return on premium', async ({ page }) => {
  // -15.54%/yr hedge against a +11.32%/yr long = -26.9 points.
  await expect(page.locator('.pl-hero-num')).toHaveText('−26.9')
  await expect(page.getByText('Annualized spread vs SPY')).toBeVisible()
  // ROI is -48% and belongs in the stat row, never in the 78px figure.
  await expect(page.locator('.pl-hero-num')).not.toHaveText('−48%')
})

test('breaks the spread into its two terms and its error bar', async ({ page }) => {
  const broken = page.locator('.pl-brokenout')
  await expect(broken.locator('.pl-brokenout-row', { hasText: 'This hedge' })).toContainText('−15.5%')
  await expect(broken.locator('.pl-brokenout-row', { hasText: 'Benchmark long' })).toContainText('+11.3%')
  // The model's expected optimism is part of the result, so it prints beside it
  // rather than only inside the accuracy panel further down.
  await expect(broken.locator('.pl-brokenout-row', { hasText: 'Error bar' })).toContainText('+1.3%')
})

test('states the hit-rate and ROI bases the numbers actually use', async ({ page }) => {
  const stats = page.locator('.pl-stats')

  // hit_rate counts rolls whose payoff cleared the BUDGET (concepts.ts is
  // explicit that intrinsic value alone is not a hit) -- 2 of 12 here.
  const hit = stats.locator('div', { hasText: /^Hit rate/ }).first()
  await expect(hit).toContainText('17%')
  await expect(hit).toContainText('cleared the budget')

  // roi_on_premium divides by n_cycles x notional and carries NO brokerage
  // term. A "net of brokerage" caption next to it would be wrong; brokerage
  // belongs on Net P&L, and that is where the fee figure prints.
  const roi = stats.locator('div', { hasText: /^Return on premium/ }).first()
  await expect(roi).toContainText('−48%')
  await expect(roi).toContainText('12 rolls × $1,000 budget')
  await expect(roi).not.toContainText(/brokerage/i)

  const net = stats.locator('div', { hasText: /^Net P&L/ }).first()
  await expect(net).toContainText('−$5,858')
  await expect(net).toContainText('$78')

  // biggest_payoff_mult is max(payoff / notional) -- the budget, not the fill.
  await expect(stats.locator('div', { hasText: /^Biggest payoff/ }).first()).toContainText('3.2×')
})

test('surfaces a failed backtest rather than rendering an empty result', async ({ page }) => {
  await page.unrouteAll()
  await mockPutLabApi(page, { fail: ['/api/putlab/backtest'] })
  await page.reload()
  await expect(page.getByRole('alert')).toContainText(/failed: 500/)
  await expect(page.locator('.pl-hero-num')).toHaveCount(0)
})
