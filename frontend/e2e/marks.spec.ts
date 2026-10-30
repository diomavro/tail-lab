import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// Marks: where the lake holds a real put quote. What it must never do is
// draw "nothing collected" and "collected, nobody quoted" the same way -- the
// first is a data gap to buy or wait out, the second is a fact about the
// market -- and the headline must be years at the depth a reader picked.

const cell = (page: import('@playwright/test').Page, oom: number, col: number) =>
  page.locator(`.pl-marks-row[data-oom="${oom}"] rect`).nth(col)

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Marks' }).click()
})

test('leads with years of usable quotes at the focused depth, split by regime', async ({ page }) => {
  await expect(page.getByTestId('marks-hero')).toContainText('2.0')
  const hero = page.locator('.pl-hero-sentence')
  await expect(hero).toContainText('1.0 in calm')
  await expect(hero).toContainText('0.3 in crisis')
})

test('tenor and row picks re-read the headline', async ({ page }) => {
  await page.getByText('up to 2 weeks').click()
  await expect(page.getByTestId('marks-hero')).toContainText('0.0')
  await page.getByText('2–6 weeks').click()
  await page.locator('.pl-marks-row[data-oom="50"] rect').first().click()
  await expect(page.locator('.pl-hero .pl-kicker').first()).toContainText('50–52% out')
  await expect(page.getByTestId('marks-hero')).toContainText('0.1')
})

test('hatches what was never collected, and leaves held-but-unquoted blank', async ({ page }) => {
  // Columns: 2023-01, 02, 03, 04, 05, 06 -- one per calendar month, gap included.
  await expect(page.locator('.pl-marks-row[data-oom="0"] rect')).toHaveCount(6 + 3)
  await expect(cell(page, 16, 0)).toHaveAttribute('fill', /color-mix/) // 20/20 held
  await expect(cell(page, 2, 0)).toHaveAttribute('fill', 'var(--paper-2)') // held, unquoted
  await expect(cell(page, 16, 3)).toHaveAttribute('fill', 'url(#marks-hatch)') // no source that month
  await expect(cell(page, 44, 0)).toHaveAttribute('fill', 'url(#marks-hatch)') // archive stops at 40%
  await expect(cell(page, 50, 5)).toHaveAttribute('fill', /color-mix/) // the sweep reaches it

  await cell(page, 44, 1).hover()
  await expect(page.getByRole('tooltip')).toContainText('never stored strikes past 40% out')
  await cell(page, 16, 1).hover()
  await expect(page.getByRole('tooltip')).toContainText('5 of 19 sessions with a usable quote')
})

test('says so when the lake holds nothing for the name', async ({ page }) => {
  await mockPutLabApi(page, { noData: ['/api/putlab/marks'] })
  await page.getByLabel('Name').selectOption('qqq')
  await expect(page.getByRole('alert')).toContainText('no put quotes for QQQ')
})
