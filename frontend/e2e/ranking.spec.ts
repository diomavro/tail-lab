import { expect, test } from '@playwright/test'
import { LEADERBOARD } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The fragility ranking, as a strip at the top of the Workspace rather than a
// permanently pinned 232px panel above the tab bar.
//
// The behaviour that matters is not "a table exists". It is that a row's
// headline is `best_annualized` -- the argmax of that name's whole strike x
// tenor grid -- so clicking the row has to land on the cell the row ADVERTISED,
// not on whatever strike happened to be selected. Landing on the selected
// strike would show a different (usually worse) number than the one just
// clicked, which is the bug this arrangement exists to prevent.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('opens as one line carrying the top three names', async ({ page }) => {
  const strip = page.getByRole('group', { name: 'Fragility ranking' })
  await expect(strip.getByRole('button', { name: /TSLA/ })).toBeVisible()
  await expect(strip.getByRole('button', { name: /IWM/ })).toBeVisible()
  await expect(strip.getByRole('button', { name: /XLF/ })).toBeVisible()
  // Fourth-ranked name is not in the strip -- that is what makes it one line.
  await expect(strip.getByRole('button', { name: /EEM/ })).toHaveCount(0)
})

test('clicking a name lands on the cell that row advertised', async ({ page }) => {
  // TSLA's best cell matches neither the 5% nor the 4wk the workspace opened
  // on, so a patch that only set the asset would leave both controls untouched
  // and this would fail. Read from the fixture rather than hard-coded: the
  // argmax is bounded to the priced band, so the exact strike can move.
  const tsla = LEADERBOARD.ranked.find((r) => r.asset === 'tsla')!
  expect(tsla.best_moneyness_pct).not.toBe(5)
  expect(tsla.best_tenor_weeks).not.toBe(4)

  const strip = page.getByRole('group', { name: 'Fragility ranking' })
  await strip.getByRole('button', { name: /TSLA/ }).click()

  await expect(page.getByLabel('Out of the money, %')).toHaveValue(String(tsla.best_moneyness_pct))
  await expect(page.getByRole('radio', { name: '1 quarter' })).toBeChecked()
  await expect(page.getByRole('term').filter({ hasText: 'Name' }).locator('..')).toContainText('TSLA')
})

test('expands to the full table with every metric the API returned', async ({ page }) => {
  await page.getByRole('button', { name: /All 7 names/ }).click()

  const table = page.getByRole('table', { name: /fragility ranking/i })
  // downside_beta, co_skewness, co_kurtosis, tail_beta and downside_capture
  // were all fetched by the old table and only fragility_score was printed.
  for (const head of ['Fragility', 'Dn β', 'Co-skew', 'Co-kurt', 'Tail β', 'Dn cap', 'Best /yr']) {
    await expect(table.getByRole('columnheader', { name: head, exact: true })).toBeVisible()
  }
  await expect(table.locator('tbody tr')).toHaveCount(LEADERBOARD.ranked.length)

  // The currently-selected name is marked in the table, not merely sorted into it.
  await expect(table.locator('tbody tr.is-current')).toContainText('SPY')
})

test('prints fragility on a readable scale in the full table too', async ({ page }) => {
  await page.getByRole('button', { name: /All 7 names/ }).click()
  const shown = await page
    .getByRole('table', { name: /fragility ranking/i })
    .locator('td.pl-rank-frag')
    .allTextContents()
  expect(shown.length).toBe(LEADERBOARD.ranked.length)
  expect(new Set(shown).size).toBeGreaterThan(2)
})
