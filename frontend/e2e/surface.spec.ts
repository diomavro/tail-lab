import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// The Surface: implied tail index beside the realised one.
//
// Two things this guards. A fit that refused must read as the word REFUSED with
// its reason -- never a blank, a dash or a zero, which would pass for a
// measurement. And the caveat must ship in the same view as the number.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('a fitted payload shows the alphas, their ceilings and the dispersion', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const strip = page.getByRole('status', { name: 'Alpha strip' })
  await expect(strip).toContainText('Implied α')
  await expect(strip.getByTestId('implied-anchor').first()).toContainText('3.00')
  await expect(strip).toContainText('Realised α')
  await expect(strip).toContainText('2.40')
  await expect(strip).toContainText('Gap')
  await expect(strip).toContainText('0.60')
  await expect(strip).not.toContainText('REFUSED')
})

test('draws the survival plot, toggles to price-vs-strike, and caps the ladder at 8 rungs', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(page.getByRole('img', { name: /Survival curves/ })).toBeVisible()
  await page.getByText('Price vs strike').click()
  await expect(page.getByRole('img', { name: /Black-Scholes overlaid/ })).toBeVisible()
  const rows = page.getByRole('table', { name: 'Tail ladder rungs' }).locator('tbody tr')
  await expect(rows).toHaveCount(8)
})

test('a refused payload says REFUSED with the reason, not a blank or a zero', async ({ page }) => {
  await page.getByRole('button', { name: /TSLA/ }).click()
  await page.getByRole('tab', { name: 'Surface' }).click()
  const strip = page.getByRole('status', { name: 'Alpha strip' })
  await expect(strip).toContainText('REFUSED — fewer than 6 hygienic strikes below the anchor')
  await expect(strip).toContainText('REFUSED — fewer than two anchors accepted')
  await expect(strip).toContainText('REFUSED — no smile slope')
  await expect(strip).not.toContainText(/\b0\.00\b/)
})

test('the caveat and provenance travel with the number', async ({ page }) => {
  await page.getByRole('tab', { name: 'Surface' }).click()
  const caveat = page.locator('.pl-surface-caveat')
  await caveat.locator('summary').click()
  await expect(caveat).toContainText('A single implied alpha is not evidence of a power law')
  await expect(caveat).toContainText('fixed moneyness')
  await expect(page.getByRole('tabpanel')).toContainText('code abc1234')
})
