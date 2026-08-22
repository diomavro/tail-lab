import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// The page furniture, and the three chrome-level bugs the redesign fixes.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('does not inherit the app shell’s centred text', async ({ page }) => {
  // index.css sets `#root { text-align: center }` for the old dashboard layout
  // and nothing under .putlab-root ever reset it, so every paragraph and table
  // cell in the workspace was centred by inheritance.
  const rootCentred = await page.locator('#root').evaluate((el) => getComputedStyle(el).textAlign)
  expect(rootCentred).toBe('center')

  for (const sel of ['.pl-hero-sentence', '.pl-stat-note', '.pl-footer p']) {
    const align = await page.locator(sel).first().evaluate((el) => getComputedStyle(el).textAlign)
    expect(align, sel).toBe('left')
  }
  // ...and the ranged columns still range right.
  const num = await page
    .locator('.pl-table td.num')
    .first()
    .evaluate((el) => getComputedStyle(el).textAlign)
  expect(num).toBe('right')
})

test('gives the concept affordance a real hit target', async ({ page }) => {
  // Was 14x14px.
  const info = page.locator('.pl-concept').first()
  const box = (await info.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(24)
  expect(box.height).toBeGreaterThanOrEqual(24)

  await info.click()
  const pop = page.getByRole('dialog')
  await expect(pop).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(pop).toHaveCount(0)
})

test('remembers the sheet the reader chose', async ({ page }) => {
  const root = page.locator('.putlab-root')
  await expect(root).toHaveAttribute('data-theme', 'paper')

  await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Plate').click()
  await expect(root).toHaveAttribute('data-theme', 'plate')
  expect(await page.evaluate(() => localStorage.getItem('putlab.sheet'))).toBe('plate')

  // A trading tool should not re-infer this from the OS on every load.
  await page.reload()
  await expect(page.locator('.putlab-root')).toHaveAttribute('data-theme', 'plate')
})

test('inverts the whole page on the plate, not just the ground', async ({ page }) => {
  const inkOn = async () =>
    page.locator('.pl-hero-sentence').evaluate((el) => getComputedStyle(el).color)
  const paperInk = await inkOn()
  await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Plate').click()
  await expect(page.locator('.putlab-root')).toHaveAttribute('data-theme', 'plate')
  expect(await inkOn()).not.toBe(paperInk)
})

test('keeps the run’s identity in the dateline', async ({ page }) => {
  const dateline = page.locator('.pl-dateline')
  await expect(dateline).toContainText('SPY')
  await expect(dateline).toContainText('$512.40') // spot
  await expect(dateline).toContainText('(5% OOM)')
  await expect(dateline).toContainText('4.21%') // the rate the pricer used
  await expect(dateline).toContainText('elevated')
  await expect(dateline).toContainText('Data clean · 1003 bars')
  await expect(dateline).toContainText('Model-priced')
})

test('moves between tabs with the arrow keys', async ({ page }) => {
  const workspace = page.getByRole('tab', { name: 'Workspace' })
  await workspace.focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Recommendations' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.keyboard.press('ArrowLeft')
  await expect(workspace).toHaveAttribute('aria-selected', 'true')
  // ...and it wraps, rather than dead-ending at the first tab.
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('tab', { name: 'Glossary' })).toHaveAttribute('aria-selected', 'true')
})

test('keeps the feedback route reachable from the footer', async ({ page }) => {
  await expect(page.locator('.pl-footer')).toContainText('never trades')
  await expect(page.getByRole('button', { name: 'Send feedback' })).toBeVisible()
  // Disabled until there is something to send -- ADR 0014's public write is
  // unauthenticated, so an empty post is the one thing worth blocking client-side.
  await expect(page.getByRole('button', { name: 'Send feedback' })).toBeDisabled()
  await page.getByLabel('Feedback').fill('the sweep legend is unclear')
  await expect(page.getByRole('button', { name: 'Send feedback' })).toBeEnabled()
})

test('sets every ranged figure with a true minus, never a hyphen', async ({ page }) => {
  // U+2212 is what the rest of the page already uses (fmtDollar, fmtPct), and a
  // hyphen next to it in the same column is visibly shorter and sits lower. Any
  // bare `.toFixed()` reintroduces one.
  await page.getByRole('button', { name: /All 7 names/ }).click()
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(page.getByRole('table')).toBeVisible()
  await page.getByRole('tab', { name: 'Workspace' }).click()
  await expect(page.locator('.pl-hero-num')).toBeVisible()

  const offenders = await page.evaluate(() =>
    [...document.querySelectorAll('.pl-table td, .pl-stat-v, .pl-hero-num, .pl-brokenout-row dd, .pl-mem-v')]
      .map((el) => el.textContent ?? '')
      .filter((t) => /-\d/.test(t)),
  )
  expect(offenders).toEqual([])
})
