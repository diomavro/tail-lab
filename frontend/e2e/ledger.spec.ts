import { expect, test } from '@playwright/test'
import { CYCLES, NOTIONAL } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The roll ledger.
//
// put_roll.py spends each roll's budget exactly (`contracts = budget / premium`
// shares, never rounded) and charges entry brokerage, the roll's `cost`, on top:
// `net = payoff - budget - cost`. The columns on screen are those three terms,
// so a reader can do the subtraction and be right. An earlier version called
// `cost` "Fill", described it as the cash the contracts cost, and had a fixture
// built to that description -- this suite passed while every live row said
// otherwise.

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

test('prints Budget and Brokerage as separate columns', async ({ page }) => {
  await page.getByRole('button', { name: /^Show all/ }).click()
  const table = page.getByRole('table', { name: 'Roll ledger' })
  for (const head of ['Budget', 'Brokerage', 'Payoff', 'Net', 'σ', 'Cts', 'Premium']) {
    // The header's name also carries its ⓘ button's ("What is …?").
    await expect(table.getByRole('columnheader', { name: new RegExp(`^${head} What is `) })).toBeVisible()
  }
  await expect(table.getByRole('columnheader', { name: /^Fill/ })).toHaveCount(0)
  await expect(table.locator('tbody tr')).toHaveCount(CYCLES.length)
})

test('reconciles Net as payoff − budget − brokerage on every row', async ({ page }) => {
  await page.getByRole('button', { name: /^Show all/ }).click()
  const rows = page.getByRole('table', { name: 'Roll ledger' }).locator('tbody tr')
  await expect(rows).toHaveCount(CYCLES.length)

  const count = await rows.count()
  for (let i = 0; i < count; i++) {
    const cells = await rows.nth(i).locator('td').allTextContents()
    // Entry, Expiry, Spot, Strike, sigma, Premium, Cts, Budget, Brokerage, Payoff, Net
    const [premium, shares, budget, brokerage, payoff, net] = cells.slice(5).map(money) as [
      number, number, number, number, number, number,
    ]
    expect(budget).toBe(NOTIONAL)
    // Cts is the budget's worth of shares, so the puts cost the budget itself.
    expect(Math.abs(shares * premium - budget)).toBeLessThan(premium)
    // Brokerage is a fee, not a fill: a few dollars, never the budget.
    expect(brokerage).toBeGreaterThan(0)
    expect(brokerage).toBeLessThan(budget / 10)
    // Every printed figure is rounded to the dollar, so allow the three roundings.
    expect(Math.abs(payoff - budget - brokerage - net)).toBeLessThanOrEqual(1.5)
  }
})

test('prints the share count rounded, not as a raw float', async ({ page }) => {
  // The API sends `contracts` unrounded (1000 / 8.2 = 121.95…); live it read
  // 1383.310233241102 in the cell.
  await page.getByRole('button', { name: /^Show all/ }).click()
  const cells = page.getByRole('table', { name: 'Roll ledger' }).locator('tbody tr td:nth-child(7)')
  await expect(cells).toHaveCount(CYCLES.length)
  expect(await cells.allTextContents()).toEqual(CYCLES.map((c) => String(Math.round(c.contracts))))
})
