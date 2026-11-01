import { expect, test, type Locator, type Page } from '@playwright/test'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'
import { METRIC_SCREEN_WINNER } from './fixtures/putlab'
import { SIGNAL_TEST_BEATS, SIGNAL_TEST_CRASH_CARRIED, SIGNAL_TEST_INSUFFICIENT, SIGNAL_TEST_NO_BETTER } from './fixtures/signal-test'

// The Bake-off tab (docs/adr/0033).
//
// It leads with the OUT-OF-SAMPLE signal test: ranked on what was known at each
// date, did the top names' next put pay more than the alternatives? The
// headline reads the fixed STRIP_SIGNAL_TEST result on its own; "Run" runs the
// same test at the rail's settings and shows it below. The in-sample
// association (the older metric bake-off) is collapsed and fetches only when
// its own Run is pressed.
//
// The in-sample question, unchanged: the fragility ranking assumes its metrics
// are worth ranking on, and the bake-off tests that by holding an
// equal-weight basket on each screen's top K and comparing.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  // The Workspace's strip reads its fixed-parameter signal test on load; let it
  // land first, or leaving the Workspace inside its 250ms debounce would
  // sometimes cancel it and sometimes not, and the counts below would flicker.
  await expect(page.getByTestId('rank-signal')).toContainText('Signal test · 0.10Δ at realised vol · 4w · 4y')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
})

const signalTable = (page: Page) => page.getByRole('table', { name: 'Out-of-sample signal test', exact: true })
const runTable = (page: Page) => page.getByRole('table', { name: 'Out-of-sample signal test, run at the rail', exact: true })
const inSampleTable = (page: Page) => page.getByRole('table', { name: 'In-sample bake-off', exact: true })
const inSampleToggle = (page: Page) => page.getByRole('button', { name: /In-sample association/ })

const requestsTo = (page: Page, path: string) => apiRequests(page).filter((p) => p === path).length
const metricScreenReads = (page: Page) => requestsTo(page, '/api/putlab/metric-screen')

/** A header cell by its label. Its accessible name also carries the ⓘ button's
 *  ("What is …?"), so match the label up to that. */
const header = (table: Locator, label: string) =>
  table.getByRole('columnheader', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} What is `) })

async function openInSample(page: Page) {
  await inSampleToggle(page).click()
  await expect(page.getByRole('button', { name: 'Run the bake-off' })).toBeVisible()
}

async function runInSample(page: Page) {
  await openInSample(page)
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(inSampleTable(page)).toBeVisible()
}

// ---------------------------------------------------------------- headline ---

test('leads with the out-of-sample test, read automatically at its fixed settings', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Does the signal pick better puts going forward?' })).toBeVisible()
  const settings = page.getByTestId('signal-headline-settings')
  await expect(settings).toContainText('0.10Δ at realised vol · 4w · 4y signal window · top 5')
  await expect(settings).toContainText('whatever the rail says')
  await expect(page.getByTestId('signal-status')).toHaveText('No better than random baskets')
  await expect(signalTable(page)).toBeVisible()
  // No click was needed, and the in-sample read has not been asked for.
  expect(metricScreenReads(page)).toBe(0)
})

test('words the verdict from the API sentence, the cadence and the test window', async ({ page }) => {
  const verdict = page.getByTestId('signal-verdict')
  await expect(verdict).toContainText('did not beat random baskets')
  // The best volatility screen is always printed beside the composite, labelled.
  await expect(verdict).toContainText('picked after the fact')
  // A true minus, not the sentence's ASCII hyphen.
  await expect(verdict).not.toContainText(/-\d/)
  const win = page.getByTestId('signal-window')
  await expect(win).toContainText('Rebalanced every 4 weeks')
  await expect(win).toContainText(`${SIGNAL_TEST_NO_BETTER.n_periods} periods`)
  await expect(win).toContainText(`from ${SIGNAL_TEST_NO_BETTER.test_start} to ${SIGNAL_TEST_NO_BETTER.test_end}`)
})

test('puts both caveats beside the sentence, from the concept text', async ({ page }) => {
  const panel = page.getByTestId('signal-headline')
  await expect(panel.getByTestId('signal-caveat-survivorship')).toContainText('today')
  await expect(panel.getByTestId('signal-caveat-model_priced_signal')).toContainText('model prices')
  const verdictBox = (await panel.getByTestId('signal-verdict').boundingBox())!
  const caveatBox = (await panel.getByTestId('signal-caveat-survivorship').boundingBox())!
  expect(caveatBox.y).toBeGreaterThan(verdictBox.y)
  expect(caveatBox.y - verdictBox.y).toBeLessThan(200)
})

test('draws one bar per period over the random band, stress episodes shaded', async ({ page }) => {
  const chart = page.getByTestId('signal-chart').first()
  const n = SIGNAL_TEST_NO_BETTER.periods.length
  await expect(chart.locator('rect.pl-sig-bar')).toHaveCount(n)
  await expect(chart.locator('rect.pl-sig-band')).toHaveCount(n)
  await expect(chart.locator('rect.pl-sig-stress')).toHaveCount(SIGNAL_TEST_NO_BETTER.periods.filter((p) => p.stress).length)
  await expect(chart.getByRole('img')).toHaveAccessibleName(/random-basket 5th to 95th percentile band and stress episodes shaded/)
})

test('tabulates the 13 screens, the flag on the composite row only', async ({ page }) => {
  const table = signalTable(page)
  await expect(table.locator('tbody tr')).toHaveCount(13)
  for (const h of ['Basket', 'vs all names', 'vs SPY', 'p', 'Volatility as good?', 'Mean IC', 'IC > 0', 'IC periods', 'Without best period', 'Without best name-period', 'Periods / episodes']) {
    await expect(header(table, h), h).toBeVisible()
  }
  // The composite leads, as the pre-registered hypothesis.
  const first = table.locator('tbody tr').first()
  await expect(first).toContainText('Composite fragility')
  await expect(first).toContainText('pre-registered')
  await expect(first.getByTestId('volflag-fragility_score')).toContainText('volatility did as well')
  await expect(first.getByTestId('p-fragility_score')).toContainText('raw')
  // Every other row shows a dash where the flag would be.
  const flags = await table.locator('td[data-testid^="volflag-"]').allTextContents()
  expect(flags.filter((t) => t.trim() === '—')).toHaveLength(12)
  // n_ic_periods is its own column.
  await expect(first.locator('td').nth(9)).toHaveText(String(SIGNAL_TEST_NO_BETTER.screens.find((s) => s.screen === 'fragility_score')!.n_ic_periods))
})

test('labels the best volatility screen as picked after the fact, with its BH p', async ({ page }) => {
  const best = SIGNAL_TEST_NO_BETTER.verdict.best_vol_baseline!
  const row = signalTable(page).locator(`tbody tr[data-screen="${best}"]`)
  await expect(row).toContainText('best of 6, picked after the fact')
  await expect(row.getByTestId(`p-${best}`)).toContainText('BH')
  // Exactly one row carries the label.
  await expect(signalTable(page).getByText('best of 6, picked after the fact')).toHaveCount(1)
})

test('states the all-names and SPY levels it compares with, and its provenance', async ({ page }) => {
  const panel = page.getByTestId('signal-headline')
  await expect(panel.getByTestId('signal-baselines')).toContainText(`All ${SIGNAL_TEST_NO_BETTER.universe_size} names returned`)
  await expect(panel.getByTestId('signal-baselines')).toContainText('commission is excluded')
  await expect(panel.getByTestId('signal-provenance')).toContainText('abc1234')
  await expect(panel.getByTestId('signal-provenance')).toContainText('4 bronze snapshots')
})

test('keeps the numbers readable on the narrow page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
})

// -------------------------------------------------------------------- run ---

test('Run tests at the rail’s settings and shows the result below, labelled with them', async ({ page }) => {
  await expect(page.getByTestId('signal-run-settings')).toContainText('At the rail: 5% OOM · 4w · 4y signal window · top 5')
  const before = requestsTo(page, '/api/putlab/signal-test')
  const sent = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/putlab/signal-test' && r.url().includes('moneyness_pct=5'))
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  const qs = new URL((await sent).url()).searchParams
  expect(qs.get('strike_rule')).toBeNull()
  expect(qs.get('tenor_weeks')).toBe('4')
  expect(qs.get('signal_years')).toBe('4')
  expect(qs.get('top_k')).toBe('5')
  expect(requestsTo(page, '/api/putlab/signal-test')).toBe(before + 1)

  await expect(page.getByTestId('signal-run-heading')).toContainText('Your run: 5% OOM · 4w · 4y signal window · top 5')
  await expect(runTable(page)).toBeVisible()
  await expect(runTable(page).locator('tbody tr')).toHaveCount(13)
  await expect(page.getByTestId('signal-status').nth(1)).toHaveText('Beats random baskets')
  // The headline is untouched, and still above it.
  await expect(signalTable(page)).toBeVisible()
  const top = (await signalTable(page).boundingBox())!
  const run = (await runTable(page).boundingBox())!
  expect(run.y).toBeGreaterThan(top.y)
})

test('Run follows the rail: years drive the signal window, delta the rule, and the basket size the top K', async ({ page }) => {
  const rail = page.getByRole('complementary', { name: 'Position controls' })
  await rail.getByRole('radiogroup', { name: 'Strike chosen by' }).getByText('Delta').click()
  await rail.getByText('3y', { exact: true }).click()
  await page.getByRole('radiogroup', { name: 'Basket size' }).getByText('Top 8').click()
  const sent = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/putlab/signal-test' && r.url().includes('top_k=8'))
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  const qs = new URL((await sent).url()).searchParams
  expect(qs.get('strike_rule')).toBe('delta')
  expect(qs.get('target_delta')).toBe('0.1')
  expect(qs.get('signal_years')).toBe('3')
  expect(qs.has('moneyness_pct')).toBe(false)
  await expect(page.getByTestId('signal-run-heading')).toContainText('0.10Δ at realised vol · 4w · 3y signal window · top 8')
})

test('says too little history plainly when the run has too few periods', async ({ page }) => {
  await page.getByRole('radiogroup', { name: 'Basket size' }).getByText('Top 3').click()
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  await expect(runTable(page)).toBeVisible()
  await expect(page.getByTestId('signal-status').nth(1)).toHaveText('Too little history to judge')
  await expect(page.getByTestId('signal-verdict').nth(1)).toContainText('fewer than 24 periods')
  // The status label and the API sentence both open with "Too little history
  // to judge"; the verdict line says it once.
  const verdictText = (await page.getByTestId('signal-verdict').nth(1).textContent())!
  expect(verdictText.match(/Too little history to judge/g)).toHaveLength(1)
  expect(SIGNAL_TEST_INSUFFICIENT.verdict.status).toBe('insufficient_data')
  expect(SIGNAL_TEST_BEATS.verdict.status).toBe('beats_random')
})

test('a crash-carried verdict shows how much of the edge is left without the event', async ({ page }) => {
  // 3y at a distance rule is the planted crash world: the composite looks
  // significant (p = 0.003, +83.9%) but its whole edge is one period.
  await page.getByRole('complementary', { name: 'Position controls' }).getByText('3y', { exact: true }).click()
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  await expect(runTable(page)).toBeVisible()
  await expect(page.getByTestId('signal-status').nth(1)).toHaveText('Carried by one event')

  const v = SIGNAL_TEST_CRASH_CARRIED.verdict
  expect(v.status).toBe('crash_carried')
  // The worse of the two ex-best excesses, as a signed percent with a true minus.
  const worst = Math.min(v.excess_ex_best_period, v.excess_ex_best_name_period)
  expect(worst).toBeLessThan(0) // a zero would not tell the magnitude from its absence
  const magnitude = `${worst < 0 ? '−' : '+'}${Math.abs(worst * 100).toFixed(1)}%`
  const verdict = page.getByTestId('signal-verdict').nth(1)
  // The UI's own line (exBestText), distinct from the API sentence that also names it.
  await expect(verdict).toContainText(`Without its best period or name-period the excess is ${magnitude}.`)
  await expect(verdict).toContainText(`without the one that matters more: ${magnitude}`)
})

test('reports a failed run instead of an empty table', async ({ page }) => {
  await page.route((url) => url.pathname === '/api/putlab/signal-test' && url.searchParams.has('moneyness_pct'), (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }),
  )
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  await expect(page.getByRole('alert')).toContainText(/signal test failed: 500/)
  await expect(runTable(page)).toHaveCount(0)
})

test('reports a headline that cannot load, without hiding the rest of the tab', async ({ page }) => {
  await page.unrouteAll()
  await mockPutLabApi(page, { fail: ['/api/putlab/signal-test'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await expect(page.getByTestId('signal-headline-status')).toBeVisible()
  await expect(signalTable(page)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Run the signal test' })).toBeVisible()
  await expect(inSampleToggle(page)).toBeVisible()
})

// --------------------------------------------------------------- in sample ---

test('keeps the in-sample bake-off collapsed, and idle until asked — one backtest per screened name runs server-side', async ({ page }) => {
  const toggle = inSampleToggle(page)
  await expect(toggle).toHaveText(/In-sample association \(same window, not a forecast\)/)
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('button', { name: 'Run the bake-off' })).toHaveCount(0)
  await expect(inSampleTable(page)).toHaveCount(0)
  // Counted from the tab's arrival: the Workspace's own reads came before.
  const before = metricScreenReads(page)
  await page.waitForTimeout(600)
  expect(metricScreenReads(page)).toBe(before)

  // Expanding it fetches nothing either: the Run button still decides.
  await openInSample(page)
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await page.waitForTimeout(600)
  expect(metricScreenReads(page)).toBe(before)

  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(inSampleTable(page)).toBeVisible()
  expect(metricScreenReads(page)).toBe(before + 1)
})

test('cites the snapshots and code behind its result', async ({ page }) => {
  await runInSample(page)
  const provenance = page.getByTestId('bake-provenance')
  await expect(provenance).toContainText('bk00000')
  await expect(provenance).toContainText('4 bronze snapshots')
})

test('puts the in-sample caveat above its table, in the loss colour', async ({ page }) => {
  await openInSample(page)
  const caveat = page.locator('.pl-caveat').filter({ hasText: 'In sample' })
  await expect(caveat).toContainText('not a forward signal')

  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  const table = inSampleTable(page)
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
  await runInSample(page)
  const rows = inSampleTable(page).locator('tbody tr')
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
  await runInSample(page)
  const table = inSampleTable(page)
  for (const head of ['ROI', '/yr', 'Hit', 'Bleed', 'Spearman', 'Sig.', 'Lift', 'Verdict']) {
    await expect(header(table, head), head).toBeVisible()
  }
  const winner = table.locator('tbody tr').first()
  await expect(winner).toContainText('0.34') // spearman_vs_payoff
  await expect(winner).toContainText('+1.6') // lift_vs_baseline, in points
  await expect(winner).toContainText('−$4,216') // combined_max_drawdown
})

test('distinguishes FDR-corrected significance from the uncorrected reading', async ({ page }) => {
  await runInSample(page)
  const rows = inSampleTable(page).locator('tbody tr')

  // The composite wins ROI and survives Benjamini-Hochberg correction.
  await expect(rows.first()).toContainText('FDR-sig.')

  // Downside beta clears the uncorrected p < 0.05 hurdle alone -- exactly the
  // divergence this column exists to show, so it must read differently from
  // the composite's FDR-sig. tag, not just render a second copy of it.
  const downsideBeta = rows.filter({ hasText: 'Downside beta' })
  await expect(downsideBeta).toContainText('raw only')
  await expect(downsideBeta).not.toContainText('FDR-sig.')

  // A screen with no significant edge either way reads plainly as such.
  const tailBeta = rows.filter({ hasText: 'Tail beta' })
  await expect(tailBeta).toContainText('n.s.')

  const notes = page.locator('.pl-bake-notes')
  await expect(notes).toContainText('7 screens')
  await expect(notes).toContainText('Benjamini-Hochberg')
})

test('reads the winner as a screen chooser when lift is positive', async ({ page }) => {
  await runInSample(page)
  const verdict = page.locator('.pl-bake-verdict')
  await expect(verdict).toContainText('Composite fragility')
  await expect(verdict).toContainText('+1.6 points above')
  await expect(verdict).toContainText('all 35 names')
  await expect(verdict).toContainText('in sample')
})

test('says it charges no commission and how many floored rolls it left out', async ({ page }) => {
  await runInSample(page)
  const costs = page.getByTestId('bake-costs')
  await expect(costs).toContainText('No commission is charged')
  await expect(costs).toContainText('14 floored rolls left out; 1 name had only floored rolls and is not scored.')
})

test('counts several dropped names in the plural', async ({ page }) => {
  await page.route((url) => url.pathname === '/api/putlab/metric-screen', (route) =>
    route.fulfill({ json: { ...METRIC_SCREEN_WINNER, n_floored_legs_excluded: 30, n_names_all_floored: 2 } }),
  )
  await runInSample(page)
  await expect(page.getByTestId('bake-costs')).toContainText('30 floored rolls left out; 2 names had only floored rolls and are not scored.')
})

test('counts a single floored roll without a dropped-name clause', async ({ page }) => {
  await page.route((url) => url.pathname === '/api/putlab/metric-screen', (route) =>
    route.fulfill({ json: { ...METRIC_SCREEN_WINNER, n_floored_legs_excluded: 1, n_names_all_floored: 0 } }),
  )
  await runInSample(page)
  const costs = page.getByTestId('bake-costs')
  await expect(costs).toContainText('1 floored roll left out.')
  await expect(costs).not.toContainText('not scored')
})

test('names the selection the floor rule makes at fixed moneyness', async ({ page }) => {
  await runInSample(page)
  await expect(page.getByTestId('bake-costs')).toContainText('At a fixed distance below spot they dominated the ranking on quiet names.')
  await expect(page.getByTestId('bake-costs')).toContainText('Leaving floored rolls out is a selection too')
})

test('drops the fixed-moneyness clause under the delta rule', async ({ page }) => {
  await page.route((url) => url.pathname === '/api/putlab/metric-screen', (route) =>
    route.fulfill({ json: { ...METRIC_SCREEN_WINNER, strike_rule: 'delta', moneyness_pct: null, target_delta: 0.1 } }),
  )
  await runInSample(page)
  const costs = page.getByTestId('bake-costs')
  await expect(costs).toContainText('No commission is charged')
  await expect(costs).not.toContainText('fixed distance below spot')
  // The floor can still bind at the smallest deltas: when it did, the
  // selection it makes is named under delta too.
  await expect(costs).toContainText('Leaving floored rolls out is a selection too')
})

test('says no roll hit the floor when none did', async ({ page }) => {
  await page.getByRole('radiogroup', { name: 'Basket size' }).getByText('Top 3').click()
  await runInSample(page)
  const costs = page.getByTestId('bake-costs')
  await expect(costs).toContainText('No roll hit the floor in this run.')
  await expect(costs).not.toContainText('is a selection too')
})

test('says so plainly when no screen beats buying everything', async ({ page }) => {
  // Top 3 is the basket size where nothing clears the baseline. The segmented
  // control hides its inputs behind their labels, so click the label -- that is
  // the surface a reader actually has.
  await page.getByRole('radiogroup', { name: 'Basket size' }).getByText('Top 3').click()
  await expect(page.getByRole('radio', { name: 'Top 3' })).toBeChecked()
  await runInSample(page)

  const verdict = page.locator('.pl-bake-verdict')
  await expect(verdict).toContainText('No screen beat the buy-everything baseline')
  await expect(verdict).toContainText('−20%')
  await expect(verdict).toContainText('added nothing here')
})

test('explains the baseline, Spearman, and why co-kurtosis competes', async ({ page }) => {
  await runInSample(page)
  const notes = page.locator('.pl-bake-notes')
  await expect(notes).toContainText('Buying puts on every one of the 35')
  await expect(notes).toContainText('+1 a perfect ordering')
  // Co-kurtosis flatters broad indices -- backwards for a single-name screen,
  // which is why it competes here but is excluded from the composite.
  await expect(notes).toContainText('excluded from the composite')
  await expect(inSampleTable(page).locator('tbody tr', { hasText: 'Co-kurtosis' })).toContainText('SPY')
})

test('reports a failed screen instead of an empty table', async ({ page }) => {
  await page.unrouteAll()
  await mockPutLabApi(page, { fail: ['/api/putlab/metric-screen'] })
  await openInSample(page)
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(page.getByRole('alert')).toContainText(/metric-screen failed: 500/)
  await expect(inSampleTable(page)).toHaveCount(0)
})
