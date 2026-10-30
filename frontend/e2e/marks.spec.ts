import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// Marks: where the lake holds a real put quote. What it must never do is
// draw "nothing collected" and "collected, nobody quoted" the same way -- the
// first is a data gap to buy or wait out, the second is a fact about the
// market -- and the headline must be years at the depth a reader picked.

const cell = (page: import('@playwright/test').Page, oom: number, col: number) =>
  page.locator(`.pl-marks-row[data-oom="${oom}"] .pl-marks-cell`).nth(col)

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
  await page.locator('.pl-marks-row[data-oom="50"] .pl-marks-cell').first().click()
  await expect(page.locator('.pl-hero .pl-kicker').first()).toContainText('50–52% out')
  await expect(page.getByTestId('marks-hero')).toContainText('0.1')
})

test('hatches what was never collected, and leaves held-but-unquoted blank', async ({ page }) => {
  // Columns: 2023-01, 02, 03, 04, 05, 06 -- one per calendar month, gap included.
  await expect(page.locator('.pl-marks-row[data-oom="0"] .pl-marks-cell')).toHaveCount(6)
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
  // The failed name must not strand the picker: every other name stays offered.
  await expect(page.getByLabel('Name').locator('option')).toHaveText(['IWM', 'NVDA', 'QQQ', 'SPY', 'TSLA'])
})

// Each ink step is its own colour-mix percentage (22 / 45 / 70 / 100), so the
// tests read the step, not just "inked".
const ink = (pct: number) => new RegExp(`var\\(--cyan\\) ${pct}%`)

test('inks each cell by its own tenor, step and measure', async ({ page }) => {
  await expect(cell(page, 16, 0)).toHaveAttribute('fill', ink(100)) // 20 of 20, 2-6w
  await expect(cell(page, 16, 1)).toHaveAttribute('fill', ink(45)) // 5 of 19
  await page.getByText('up to 2 weeks').click()
  await expect(cell(page, 16, 0)).toHaveAttribute('fill', ink(22)) // 2 of 20, <=2w only
  await expect(cell(page, 16, 1)).toHaveAttribute('fill', 'var(--paper-2)')
  await page.getByText('2–6 weeks').click()
  await page.locator('.pl-seg-opt', { hasText: 'Traded' }).click()
  await expect(cell(page, 16, 1)).toHaveAttribute('fill', ink(100)) // traded 19 of 19
})

test('hatches from the stored depth itself, not one bucket past it', async ({ page }) => {
  await expect(cell(page, 38, 0)).toHaveAttribute('fill', 'var(--paper-2)')
  await expect(cell(page, 40, 0)).toHaveAttribute('fill', 'url(#marks-hatch)')
  await expect(cell(page, 40, 5)).not.toHaveAttribute('fill', 'url(#marks-hatch)')
})

test('draws the regime ribbon, the regime bars and the price-series line', async ({ page }) => {
  // Fixture regimes: calm to 2023-02, elevated 2023-02..06, then two after the grid.
  await expect(page.locator('.pl-marks-ribbon')).toHaveCount(2)
  const bars = page.locator('.pl-marks-row[data-oom="16"] .pl-marks-bar')
  // 252 / 189 / 63 sessions against a 504-session widest row, on a 200-wide axis.
  await expect(bars.nth(0)).toHaveAttribute('width', '100')
  await expect(bars.nth(1)).toHaveAttribute('width', '75')
  await expect(bars.nth(2)).toHaveAttribute('width', '25')
  await expect(page.locator('.pl-marks-price line')).toHaveCount(1)
})

test('prints a short history in days and a long one in years', async ({ page }) => {
  await page.getByText('The same counts as a table').click()
  const table = page.getByRole('table', { name: 'Sessions of quotes held by depth' })
  await expect(table.locator('tr', { hasText: '16–18%' })).toContainText('2.0 y')
  await expect(table.locator('tr', { hasText: '50–52%' })).toContainText('21 d')
})

test('a row is reachable by keyboard', async ({ page }) => {
  // Tab in from the control before the grid, so focus-visible applies.
  await page.locator('.pl-seg-opt', { hasText: 'Traded' }).locator('input').focus()
  const row = page.getByRole('button', { name: 'Read 0–2% out' })
  for (let i = 0; i < 10 && !(await row.evaluate((el) => el === document.activeElement)); i++) {
    await page.keyboard.press('Tab')
  }
  await expect(row).toBeFocused()
  // The ring spans the row, not one month's cell.
  const ring = row.locator('.pl-marks-rowring')
  await expect(ring).toHaveCSS('stroke-width', '2px')
  const rowBox = (await row.boundingBox())!
  expect((await ring.boundingBox())!.width).toBeGreaterThan(rowBox.width * 0.7)
  await page.getByRole('button', { name: 'Read 30–32% out' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.pl-hero .pl-kicker').first()).toContainText('30–32% out')
})

test('the tooltip sits beside the cell it describes, at any width', async ({ page }) => {
  for (const width of [1280, 400]) {
    await page.setViewportSize({ width, height: 900 })
    const target = cell(page, 50, 5)
    await target.scrollIntoViewIfNeeded()
    await target.hover()
    const c = (await target.boundingBox())!
    const t = (await page.getByRole('tooltip').boundingBox())!
    // The last month sits past the middle, so the tip opens leftward: its
    // right edge meets the cell's left edge, and the scroll area never grows.
    expect(Math.abs(t.x + t.width - c.x)).toBeLessThan(24)
    expect(Math.abs(t.y - (c.y + c.height))).toBeLessThan(24)
    const wrap = page.locator('.pl-marks-wrap')
    expect(t.x + t.width).toBeLessThanOrEqual((await wrap.boundingBox())!.x + (await wrap.evaluate((el) => el.scrollWidth)) + 1)
    await cell(page, 16, 0).hover()
    const first = (await cell(page, 16, 0).boundingBox())!
    const tip = (await page.getByRole('tooltip').boundingBox())!
    expect(Math.abs(tip.x - (first.x + first.width))).toBeLessThan(24)
  }
})
