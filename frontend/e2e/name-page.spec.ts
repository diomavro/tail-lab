import { expect, test, type Page } from '@playwright/test'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'

// The per-name page (Workspace): what the lake holds for the name, how the
// walk-forward signals treated it, and the strategy per entry regime.
// Summaries are always visible; the detail expands. Two rules the page must not
// bend: an uncollected name never requests /marks, and a group of fewer than 5
// periods says "too few to judge" instead of a rate.

const coverage = (page: Page) => page.getByRole('region', { name: 'Data coverage' })
const signals = (page: Page) => page.getByRole('region', { name: 'How the signals treated this name' })
const marksReads = (page: Page) => apiRequests(page).filter((p) => p === '/api/putlab/marks')
const pick = (page: Page, name: RegExp) =>
  page.getByRole('group', { name: 'Screen' }).getByTestId('rank-pick').filter({ hasText: name })

test.describe('data coverage', () => {
  test('a quote-collected name: one line, no quote read until the grid opens', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await expect(coverage(page).getByTestId('coverage-summary')).toHaveText(
      'Prices 2016–2026 · dividends 2016–2026 · quotes: expand to load',
    )
    expect(marksReads(page)).toHaveLength(0)

    await coverage(page).getByRole('button', { name: 'Show the grid' }).click()
    const grid = coverage(page).getByRole('table', { name: 'Data coverage by year' })
    await expect(grid).toBeVisible()
    // 11 calendar years across, the shared VIX row included.
    await expect(grid.locator('thead th[data-noexplain="index"]')).toHaveCount(11)
    await expect(grid.getByRole('row', { name: /^Prices/ }).locator('td[data-level="have"]')).toHaveCount(11)
    await expect(grid.getByRole('row', { name: /^VIX/ })).toContainText('shared by every name')
    await expect(grid.locator('td[title="210 of 210 SPY sessions in 2026"]').first()).toBeVisible()

    // Opening the grid is what fetches the quote chart for this name. (The dev
    // server's StrictMode double-mount aborts and repeats a read, so 'at least'.)
    await expect(coverage(page).getByTestId('coverage-quotes').locator('.pl-marks-grid')).toBeVisible()
    expect(marksReads(page).length).toBeGreaterThan(0)

    await coverage(page).getByRole('button', { name: 'Hide the grid' }).click()
    await expect(grid).toHaveCount(0)
  })

  test('an uncollected name says so and never requests quotes, even with the grid open', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await pick(page, /XLF/).click()
    await expect(coverage(page).getByTestId('coverage-summary')).toHaveText(
      'Prices 2018–2026 · dividends none · quotes: not collected for this name',
    )
    await coverage(page).getByRole('button', { name: 'Show the grid' }).click()
    const grid = coverage(page).getByRole('table', { name: 'Data coverage by year' })
    await expect(grid.getByRole('row', { name: /^Dividends/ })).toContainText('no rows in the lake')
    // A part-year at the start reads as partial, not as a gap.
    await expect(grid.getByRole('row', { name: /^Prices/ }).locator('td[data-level="partial"]')).toHaveCount(1)
    await expect(grid.getByRole('row', { name: /^Prices/ }).locator('td.pl-cov-none')).toHaveCount(2)
    await expect(coverage(page)).toContainText('Put quotes are not collected for XLF.')
    await expect(coverage(page).locator('.pl-marks-grid')).toHaveCount(0)
    expect(marksReads(page)).toHaveLength(0)
  })

  test('a name the lake has nothing for gets a plain sentence', async ({ page }) => {
    await mockPutLabApi(page, { noData: ['/api/putlab/coverage/spy'] })
    await page.goto('/')
    await expect(coverage(page).getByTestId('coverage-summary')).toHaveText(
      'The lake holds no price or dividend rows for SPY.',
    )
    await expect(coverage(page).getByRole('button', { name: /Show|Hide/ })).toHaveCount(0)
  })

  test('sits between the sweep heatmap and the accuracy section', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await expect(coverage(page)).toBeVisible()
    const order = await page.evaluate(() => {
      const at = (el: Element | null) => (el ? [...document.querySelectorAll('section')].indexOf(el as HTMLElement) : -1)
      const byHeading = (text: string) =>
        [...document.querySelectorAll('section')].find((s) => s.querySelector('h3')?.textContent?.startsWith(text)) ?? null
      return [byHeading('Strike'), byHeading('Data coverage'), byHeading('How the signals'), byHeading('Did this only')].map(at)
    })
    expect(order.every((i) => i >= 0)).toBe(true)
    expect(order).toEqual([...order].sort((a, b) => a - b))
  })
})

test.describe('how the signals treated this name', () => {
  test('one line from the composite, the rule in the header', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await expect(signals(page).getByTestId('name-signal-summary')).toHaveText(
      'Composite put it in the top 5 in 11 of 40 periods; 4 of those 11 paid (largest +7.2×), vs 12 of 29 otherwise.',
    )
    await expect(signals(page).getByTestId('name-signal-rule')).toHaveText('signal test rule: Δ0.10 · 4w, no commission')
    // The line is read from the per-name route, at the strip's settings.
    expect(apiRequests(page)).toContain('/api/putlab/signal-test/spy')
  })

  test('fewer than 5 picked periods is "too few to judge", in the line and in the table', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await pick(page, /TSLA/).click()
    await expect(signals(page).getByTestId('name-signal-summary')).toHaveText(
      'Composite put it in the top 5 in 3 of 40 periods; too few to judge, vs 15 of 37 otherwise.',
    )
    await signals(page).getByRole('button', { name: 'Show the detail' }).click()
    const row = signals(page).getByRole('row', { name: /^Composite fragility/ })
    // "When picked" reads too few; "Otherwise" (37 periods) reads a count.
    await expect(row.locator('td').nth(2)).toHaveText('too few to judge')
    await expect(row.locator('td').nth(3)).toHaveText('15 of 37 paid (largest +14×)')
  })

  test('the detail: composite, the after-the-fact best vol screen, exploratory behind a toggle, regimes', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await signals(page).getByRole('button', { name: 'Show the detail' }).click()
    const table = signals(page).getByRole('table', { name: 'How each screen treated this name' })
    await expect(table.locator('tbody tr')).toHaveCount(2)
    await expect(table.getByRole('row', { name: /^Composite fragility/ })).toContainText('4 of 11 paid (largest +7.2×)')
    const vol = table.getByRole('row', { name: /^Vol ratio/ })
    await expect(vol).toContainText('best of 6 plain-volatility screens, picked after the fact')
    // Contribution is one column, a signed sum in percentage points (it is a
    // sum of ROI fractions / k, so a bare "-0.31" read as unitless), for the
    // picked periods only.
    await expect(table.locator('thead th')).toHaveCount(5)
    await expect(table.getByRole('row', { name: /^Composite fragility/ }).locator('td').last()).toHaveText('−31.2 pts')

    await signals(page).getByRole('button', { name: 'Show 11 exploratory screens' }).click()
    await expect(table.locator('tbody tr')).toHaveCount(13)
    await expect(table.getByRole('row', { name: /^Downside beta/ })).toContainText('exploratory')
    await signals(page).getByRole('button', { name: 'Hide the exploratory screens' }).click()
    await expect(table.locator('tbody tr')).toHaveCount(2)

    // By regime at entry, over all the name's eligible periods; crisis has 2.
    const regimes = signals(page).getByRole('table', { name: 'This name by regime at entry' })
    await expect(regimes.getByRole('row', { name: /^calm/ })).toContainText('of 32 paid')
    await expect(regimes.getByRole('row', { name: /^crisis/ })).toContainText('too few to judge')
  })

  test('prints no p-value anywhere, open or shut', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await signals(page).getByRole('button', { name: 'Show the detail' }).click()
    await signals(page).getByRole('button', { name: /exploratory screens/ }).click()
    await expect(signals(page).getByRole('table').first()).toBeVisible()
    const text = (await signals(page).innerText()) + (await coverage(page).innerText())
    expect(text).not.toMatch(/p[\s_-]?value|p_random|p_bh|\bp\s*[<=]/i)
  })

  test('a name outside the signal test, and a failed read, are worded plainly', async ({ page }) => {
    await mockPutLabApi(page, { noData: ['/api/putlab/signal-test/spy'] })
    await page.goto('/')
    await expect(signals(page).getByTestId('name-signal-summary')).toContainText('SPY is not in the signal test')
    await expect(signals(page).getByRole('button', { name: /Show|Hide/ })).toHaveCount(0)

    await page.unrouteAll()
    await mockPutLabApi(page, { fail: ['/api/putlab/signal-test/spy'] })
    await page.reload()
    await expect(signals(page).getByRole('alert')).toContainText('unavailable')
  })
})

test.describe('the strategy per entry regime', () => {
  test('each regime shows its rolls, hit rate and biggest payoff, on the tape\'s own rule', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    const mem = page.getByRole('region', { name: 'Regime verdict' })
    await expect(mem.getByTestId('verdict-rule')).toContainText('this page’s own backtest, 5% out · 4-week rolls')
    const calm = mem.locator('.pl-mem-card', { hasText: 'calm' })
    await expect(calm).toContainText('8 rolls')
    await expect(calm).toContainText('Hit rate')
    await expect(calm).toContainText('13%')
    await expect(calm).toContainText('Biggest payoff')
    await expect(calm).toContainText('1.6×')
    await expect(mem.locator('.pl-mem-card', { hasText: 'crisis' })).toContainText('11.3×')
  })
})
