import { expect, test } from '@playwright/test'
import { CYCLES, NOTIONAL } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The roll ledger.
//
// The old table showed one "Cost" column holding `contracts x premium x 100` --
// the cash actually filled -- next to a Net computed as `payoff - notional`, the
// per-roll premium BUDGET. Those are different numbers, so a reader subtracting
// the two visible columns got the wrong answer. Fill and Budget are now separate
// columns and the arithmetic on screen is the arithmetic behind it.

const money = (t: string) => Number(t.replace(/[$,]/g, '').replace('−', '-'))

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('stays collapsed until the reader asks for every roll', async ({ page }) => {
  const toggle = page.getByRole('button', { name: `Show all ${CYCLES.length} rolls` })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('table', { name: 'Roll ledger' })).toHaveCount(0)

  await toggle.click()
  await expect(page.getByRole('table', { name: 'Roll ledger' })).toBeVisible()
  await expect(page.getByRole('button', { name: `Hide ${CYCLES.length} rolls` })).toBeVisible()
})

test('prints Fill and Budget as separate columns', async ({ page }) => {
  await page.getByRole('button', { name: /^Show all/ }).click()
  const table = page.getByRole('table', { name: 'Roll ledger' })
  for (const head of ['Fill', 'Budget', 'Payoff', 'Net', 'σ', 'Cts', 'Premium']) {
    await expect(table.getByRole('columnheader', { name: head, exact: true })).toBeVisible()
  }
  await expect(table.locator('tbody tr')).toHaveCount(CYCLES.length)
})

test('reconciles Net against Budget on every visible row', async ({ page }) => {
  await page.getByRole('button', { name: /^Show all/ }).click()
  const rows = page.getByRole('table', { name: 'Roll ledger' }).locator('tbody tr')
  await expect(rows).toHaveCount(CYCLES.length)

  const count = await rows.count()
  for (let i = 0; i < count; i++) {
    const cells = await rows.nth(i).locator('td').allTextContents()
    // Entry, Expiry, Spot, Strike, sigma, Premium, Cts, Fill, Budget, Payoff, Net
    const [fill, budget, payoff, net] = cells.slice(7).map(money) as [number, number, number, number]
    // The reader can do this subtraction on screen and be right.
    expect(payoff - budget).toBeCloseTo(net, 2)
    // ...and doing it with Fill instead would be wrong, which is the whole point
    // of showing both.
    expect(fill).not.toBeCloseTo(budget, 2)
  }
})

test('shows a fill on either side of the budget', async ({ page }) => {
  // `max(1, floor(notional / (premium * 100)))` forces at least one contract, so
  // an expensive roll fills ABOVE the budget and a cheap one below.
  await page.getByRole('button', { name: /^Show all/ }).click()
  const fills = await page
    .getByRole('table', { name: 'Roll ledger' })
    .locator('tbody tr td:nth-child(8)')
    .allTextContents()
  const values = fills.map(money)
  expect(Math.min(...values)).toBeLessThan(NOTIONAL)
  expect(Math.max(...values)).toBeGreaterThan(NOTIONAL)
})
