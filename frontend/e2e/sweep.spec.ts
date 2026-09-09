import { expect, test, type Page } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// The strike x tenor grid, printed in three bands instead of one continuous
// cool -> amber -> hot ramp.
//
// The ramp had two problems. It reused --hot, which was the same hex as
// --loss, so "hot cell" and "losing cell" were the same colour meaning
// different things. And a continuous ramp cannot express the distinction that
// actually matters: a cell that made money but lost to the index is a different
// KIND of outcome from one that lost money, not a slightly darker shade of it.

// Computed colours come back as `color(srgb ...)` here and `rgb(...)` there
// depending on how the value was authored, so paint it and read the pixel.
async function paintedRgb(page: Page, selector: string): Promise<[number, number, number]> {
  return page.locator(selector).first().evaluate((el) => {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, 1, 1)
    ctx.fillStyle = getComputedStyle(el).backgroundColor
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0]!, d[1]!, d[2]!] as [number, number, number]
  })
}

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('sorts every cell into loss, under-benchmark, or beat', async ({ page }) => {
  const sweep = page.locator('.pl-sweep')
  await expect(sweep).toBeVisible()

  // Benchmark is +11.32%/yr. -21.2% lost money; +4.2% made money but lost to
  // the index; +18.7% beat it. Three cells, three different bands.
  await expect(sweep.getByRole('button', { name: /^5% OOM · 1 week/ })).toHaveClass(/is-loss/)
  await expect(sweep.getByRole('button', { name: /^10% OOM · 1 month/ })).toHaveClass(/is-under/)
  await expect(sweep.getByRole('button', { name: /^15% OOM · 1 month/ })).toHaveClass(/is-beat/)
  await expect(sweep.locator('.pl-sweep-cell')).toHaveCount(12)
})

test('keeps loss, interactive and elevated on three separate inks', async ({ page }) => {
  const tokens = await page.locator('.putlab-root').first().evaluate((el) => {
    const cs = getComputedStyle(el)
    const g = (n: string) => cs.getPropertyValue(n).trim().toLowerCase()
    return { mag: g('--mag'), ink: g('--ink'), cyan: g('--cyan'), warn: g('--warn-fill') }
  })
  // --hot and --loss used to be the same hex, and --accent was also --warm.
  // Loss, the beat band's ink, the interactive colour and the elevated fill are
  // four distinct jobs and must be four distinct values.
  expect(new Set(Object.values(tokens)).size).toBe(4)
})

test('prints losses in magenta and beats in ink, never the same colour', async ({ page }) => {
  const [lr, lg, lb] = await paintedRgb(page, '.pl-sweep-cell.is-loss')
  const [br, bg, bb] = await paintedRgb(page, '.pl-sweep-cell.is-beat')

  // Magenta reads red-dominant with a blue lift; the beat band is near-black
  // ink. If the two tokens collapsed back into one, so would these.
  expect(lr).toBeGreaterThan(lg + 40)
  expect(lb).toBeGreaterThan(lg + 20)
  expect(Math.max(br, bg, bb)).toBeLessThan(120)
})

test('rings the current cell in cyan and moves the position on click', async ({ page }) => {
  const sweep = page.locator('.pl-sweep')
  // The workspace opened on 5% / 4wk, so that is the ringed cell.
  await expect(sweep.locator('.pl-sweep-cell.is-current')).toHaveCount(1)
  await expect(sweep.getByRole('button', { name: /^5% OOM · 1 month/ })).toHaveClass(/is-current/)

  await sweep.getByRole('button', { name: /^15% OOM · 1 quarter/ }).click()

  await expect(page.getByLabel('Out of the money, %')).toHaveValue('15')
  await expect(page.getByRole('radio', { name: '1 quarter' })).toBeChecked()
  await expect(sweep.getByRole('button', { name: /^15% OOM · 1 quarter/ })).toHaveClass(/is-current/)
})

test('names the benchmark in the legend rather than leaving a colour scale', async ({ page }) => {
  const legend = page.locator('.pl-sweep-legend')
  await expect(legend).toContainText('Lost money')
  await expect(legend).toContainText('under SPY')
  await expect(legend).toContainText('Beat SPY')
})

test('renders the result without the sweep when the sweep alone fails', async ({ page }) => {
  await page.unrouteAll()
  await mockPutLabApi(page, { fail: ['/api/putlab/sweep'] })
  await page.reload()
  // Per-resource states: the headline still prints while the grid reports why
  // it is missing.
  await expect(page.locator('.pl-hero-num')).toBeVisible()
  await expect(page.locator('.pl-sweep')).toHaveCount(0)
  await expect(page.getByRole('alert')).toContainText(/sweep failed: 500/)
})

test('marks the cells the model cannot price', async ({ page }) => {
  // The grid shows strikes past MODEL_PRICED_MAX_MONEYNESS_PCT on purpose --
  // seeing the deep tail is the point of a tail-hedge lab. But a flat-vol model
  // prices a 20%-OOM put at essentially nothing (measured median market/model
  // premium ratio 21,663x), so a fixed premium budget buys an absurd number of
  // contracts and any payoff is inflated by the same factor. Those cells are
  // shown and marked, not shown as equals.
  const sweep = page.locator('.pl-sweep')
  await expect(sweep.getByRole('button', { name: /^5% OOM · 1 month/ })).not.toHaveClass(/is-unpriced/)
  await expect(sweep.getByRole('button', { name: /^10% OOM · 1 month/ })).not.toHaveClass(/is-unpriced/)
  await expect(sweep.getByRole('button', { name: /^15% OOM · 1 month/ })).toHaveClass(/is-unpriced/)
  await expect(sweep.getByRole('button', { name: /^20% OOM · 1 month/ })).toHaveClass(/is-unpriced/)

  // The mark has to be legible to a screen reader too, not just a hatch.
  await expect(sweep.getByRole('button', { name: /^15% OOM · 1 month/ })).toHaveAttribute(
    'aria-label',
    /beyond what the model can price/i,
  )
  await expect(page.locator('.pl-sweep-legend')).toContainText('Beyond the pricer')
})

test('shows the total-over-window figure the cell itself cannot print', async ({ page }) => {
  // The cell prints an ANNUALIZED number. `roi_on_premium` — the total over the
  // window it was derived from — is already in the response and displayed
  // nowhere, and on short tenors the two differ enormously because annualizing
  // a 2-week ROI raises (1+roi) to the 26th power. Without this the reader is
  // comparing cells across tenors with very different compounding baked in.
  const cell = page.getByRole('button', { name: /^5% OOM · 1 month/ })
  await cell.hover()
  const tip = page.getByRole('tooltip')
  await expect(tip).toBeVisible()

  // Pin the VALUES, not the labels. Asserting only that the strings
  // "Total over window" and "Rolls" appear passes even if the cell's own
  // annualized figure is printed under the total-over-window label — which is
  // precisely the confusion this popup exists to remove, so a test blind to it
  // is worse than no test. Mutating that line survived the whole suite until
  // these three assertions were added.
  const rows = tip.locator('.pl-tip-rows')
  await expect(rows).toContainText('−16%/yr') // annualized_return -0.1554
  await expect(rows).toContainText('−48%') //    roi_on_premium   -0.4817, 3.1x the above
  await expect(rows).toContainText('52') //      n_cycles
})

test('the popup is reachable by keyboard and dismissed by Escape', async ({ page }) => {
  // Hover does not exist on touch and is unreachable by keyboard, so a popup
  // that only opens on hover is a popup some readers never see. Focus opens it;
  // Escape closes it without moving focus, which is what a reader tabbing the
  // grid expects.
  const cell = page.getByRole('button', { name: /^5% OOM · 1 month/ })
  await cell.focus()
  await expect(page.getByRole('tooltip')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await expect(cell).toBeFocused()
})

test('warns inside the popup on a cell the model cannot price', async ({ page }) => {
  // The hatch says "not a measurement" visually; the popup says why. Both,
  // because the hatch alone is a texture a reader has to already know how to
  // read, and the popup alone is unreachable on touch.
  const cell = page.getByRole('button', { name: /^20% OOM · 1 month/ })
  await cell.hover()
  await expect(page.getByRole('tooltip')).toContainText('Beyond the pricer')
})
