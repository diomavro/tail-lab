import { expect, test } from '@playwright/test'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'

// The metric bake-off — the panel the API already served and nothing called.
//
// `fetchMetricScreen` existed in api/client.ts and was never invoked anywhere,
// so `spearman_vs_payoff`, `lift_vs_baseline` and `combined_max_drawdown` were
// computed server-side and thrown away. This is the first caller.
//
// The question it answers sits UPSTREAM of the fragility ranking: the ranking
// assumes these metrics are worth ranking on, and the bake-off tests that by
// holding an equal-weight basket on each screen's top K and comparing.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
})

test('stays idle until asked — roughly 35 backtests run server-side', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Run the bake-off' })).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
  expect(apiRequests(page)).not.toContain('/api/putlab/metric-screen')

  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(page.getByRole('table')).toBeVisible()
  expect(apiRequests(page)).toContain('/api/putlab/metric-screen')
})

test('puts the in-sample caveat above the table, in the loss colour', async ({ page }) => {
  const caveat = page.locator('.pl-caveat').filter({ hasText: 'In sample' })
  await expect(caveat).toContainText('not a forward signal')

  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  const table = page.getByRole('table')
  await expect(table).toBeVisible()

  // A caveat placed after the number it qualifies has already lost, so assert
  // the document order rather than merely its presence.
  const order = await caveat.evaluate(
    (el, t) => el.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING,
    await table.elementHandle(),
  )
  expect(order).toBeTruthy()

  // Loss ink, not body ink -- it is a warning, not a footnote.
  const [r, g, b] = await caveat.evaluate((el) => {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = getComputedStyle(el).color
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0]!, d[1]!, d[2]!]
  })
  expect(r).toBeGreaterThan(g + 40)
  expect(b).toBeGreaterThan(g + 20)
})

test('ranks the seven screens by ROI and marks the winner', async ({ page }) => {
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  const rows = page.getByRole('table').locator('tbody tr')
  await expect(rows).toHaveCount(7)

  // The six raw metrics plus the composite, sorted best ROI first.
  const rois = await rows.locator('td.pl-bake-roi').allTextContents()
  const asNumbers = rois.map((t) => Number(t.replace('−', '-').replace('%', '')))
  expect(asNumbers).toEqual([...asNumbers].sort((a, b) => b - a))

  await expect(rows.first()).toHaveClass(/is-winner/)
  await expect(rows.first()).toContainText('Composite fragility')
  await expect(rows.filter({ has: page.locator('.is-winner') })).toHaveCount(0)
})

test('carries the three fields the app was fetching and discarding', async ({ page }) => {
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  const table = page.getByRole('table')
  for (const head of ['ROI', '/yr', 'Hit', 'Bleed', 'Spearman', 'Lift', 'Verdict']) {
    await expect(table.getByRole('columnheader', { name: head, exact: true })).toBeVisible()
  }
  const winner = table.locator('tbody tr').first()
  await expect(winner).toContainText('0.34') // spearman_vs_payoff
  await expect(winner).toContainText('+1.6') // lift_vs_baseline, in points
  await expect(winner).toContainText('−$4,216') // combined_max_drawdown
})

test('reads the winner as a screen chooser when lift is positive', async ({ page }) => {
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  const verdict = page.locator('.pl-bake-verdict')
  await expect(verdict).toContainText('Composite fragility')
  await expect(verdict).toContainText('+1.6 points above')
  await expect(verdict).toContainText('all 35 names')
  await expect(verdict).toContainText('in sample')
})

test('says so plainly when no screen beats buying everything', async ({ page }) => {
  // Top 3 is the basket size where nothing clears the baseline. The segmented
  // control hides its inputs behind their labels, so click the label -- that is
  // the surface a reader actually has.
  await page.getByRole('radiogroup', { name: 'Basket size' }).getByText('Top 3').click()
  await expect(page.getByRole('radio', { name: 'Top 3' })).toBeChecked()
  await page.getByRole('button', { name: 'Run the bake-off' }).click()

  const verdict = page.locator('.pl-bake-verdict')
  await expect(verdict).toContainText('No screen beat the buy-everything baseline')
  await expect(verdict).toContainText('−20%')
  await expect(verdict).toContainText('added nothing here')
})

test('explains the baseline, Spearman, and why co-kurtosis competes', async ({ page }) => {
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  const notes = page.locator('.pl-bake-notes')
  await expect(notes).toContainText('Buying puts on every one of the 35')
  await expect(notes).toContainText('+1 a perfect ordering')
  // Co-kurtosis flatters broad indices -- backwards for a single-name screen,
  // which is why it competes here but is excluded from the composite.
  await expect(notes).toContainText('excluded from the composite')
  await expect(page.getByRole('table').locator('tbody tr', { hasText: 'Co-kurtosis' })).toContainText('SPY')
})

test('reports a failed screen instead of an empty table', async ({ page }) => {
  await page.unrouteAll()
  await mockPutLabApi(page, { fail: ['/api/putlab/metric-screen'] })
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(page.getByRole('alert')).toContainText(/metric-screen failed: 500/)
  await expect(page.getByRole('table')).toHaveCount(0)
})
