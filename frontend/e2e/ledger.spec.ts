import { expect, test } from '@playwright/test'
import { CYCLES, NOTIONAL } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The roll ledger.
//
// Fill is the entry brokerage (put_roll.py's `cost`), charged on top of the
// Budget the roll spends on puts, so Net = Payoff - Budget - Fill. An earlier
// version of this spec read Fill as the contracts' cash cost and reconciled Net
// as Payoff - Budget, against a fixture built the same wrong way; the backend
// never worked like that.

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
    // The header's name also carries its ⓘ button's ("What is …?").
    await expect(table.getByRole('columnheader', { name: new RegExp(`^${head} What is `) })).toBeVisible()
  }
  await expect(table.locator('tbody tr')).toHaveCount(CYCLES.length)
})

test('reconciles Net as Payoff minus Budget minus Fill on every row', async ({ page }) => {
  await page.getByRole('button', { name: /^Show all/ }).click()
  const rows = page.getByRole('table', { name: 'Roll ledger' }).locator('tbody tr')
  await expect(rows).toHaveCount(CYCLES.length)

  const count = await rows.count()
  for (let i = 0; i < count; i++) {
    const cells = await rows.nth(i).locator('td').allTextContents()
    // Entry, Expiry, Spot, Strike, sigma, Premium, Cts, Fill, Budget, Payoff, Net
    const [fill, budget, payoff, net] = cells.slice(7).map(money) as [number, number, number, number]
    expect(budget).toBe(NOTIONAL)
    expect(fill).toBeGreaterThan(0)
    // Within a dollar: each figure is printed rounded to the dollar.
    expect(Math.abs(payoff - budget - fill - net)).toBeLessThanOrEqual(1)
  }
})

test('prints Cts in whole shares', async ({ page }) => {
  // `contracts = notional / premium` is an unrounded float.
  await page.getByRole('button', { name: /^Show all/ }).click()
  const cells = page.getByRole('table', { name: 'Roll ledger' }).locator('tbody tr td:nth-child(7)')
  await expect(cells).toHaveText(CYCLES.map((c) => String(Math.round(c.contracts))))
})
