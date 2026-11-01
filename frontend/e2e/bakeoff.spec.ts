import { expect, test, type Locator, type Page } from '@playwright/test'
import { CONCEPTS } from '../src/content/concepts'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'
import { METRIC_SCREEN_WINNER } from './fixtures/putlab'
import {
  BEYOND_VOL_NO_EVIDENCE,
  SIGNAL_TEST_BEATS,
  SIGNAL_TEST_CRASH_CARRIED,
  SIGNAL_TEST_INSUFFICIENT,
  SIGNAL_TEST_NO_BETTER,
} from './fixtures/signal-test'

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

// ------------------------------------------- fragility beyond trailing vol ---
// docs/adr/0035. One sentence after the verdict, worded by the result's
// status at the pre-registered setting, and as an exploratory read (gamma_bar
// and its CI, no p, no status) everywhere else.

/** Serve one signal-test body for every read (the headline included) and
 *  reopen the tab. Registered after the mock, so it wins. */
async function serveHeadline(page: Page, body: unknown) {
  await page.route(
    (url) => url.pathname === '/api/putlab/signal-test',
    (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Bake-off' })).toBeVisible()
  await page.getByRole('tab', { name: 'Bake-off' }).click()
}

const beyond = (page: Page) => page.getByTestId('signal-beyond-vol').first()
/** A coefficient as the page prints it: signed, three decimals, a true minus. */
const s3 = (x: number) => `${x < 0 ? '−' : '+'}${Math.abs(x).toFixed(3)}`
const est = (bv: { gamma_bar: number | null; ci90: { low: number; high: number } | null }) =>
  `γ̄ = ${s3(bv.gamma_bar!)}, 90% CI [${s3(bv.ci90!.low)}, ${s3(bv.ci90!.high)}]`

test('says there is no evidence beyond trailing vol, with the CI and what the test could catch', async ({ page }) => {
  const line = beyond(page)
  await expect(line).toHaveAttribute('data-mode', 'confirmatory')
  await expect(line).toContainText(
    'Controlling for trailing vol, there is no evidence the composite predicts better put outcomes (γ̄ = −0.008, 90% CI [−0.033, +0.017]); this test would catch a pay-rate gap of about 9.6 points 80% of the time.',
  )
  // It follows the verdict line directly, and the verdict is untouched.
  const verdictBefore = await page.getByTestId('signal-verdict').evaluate((el, t) => !!(el.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING), await line.elementHandle())
  expect(verdictBefore).toBe(true)
  await expect(page.getByTestId('signal-status')).toHaveText('No better than random baskets')
  // The caveat sits behind a concept tooltip.
  await expect(page.getByTestId('signal-beyond-vol-caveat').first()).toHaveText('Beyond this vol surface only.')
  const info = line.getByRole('button', { name: /What is/ })
  await info.hover()
  await expect(page.getByRole('tooltip')).toHaveText(CONCEPTS.fama_macbeth.short)
})

test('words a pass with the crash-exposure and model-pricing caveats', async ({ page }) => {
  const bv = SIGNAL_TEST_BEATS.beyond_vol
  await serveHeadline(page, SIGNAL_TEST_BEATS)
  const line = beyond(page)
  await expect(line).toHaveAttribute('data-mode', 'confirmatory')
  await expect(line).toContainText(`Fragility predicted put outcomes beyond trailing vol (${est(bv)}, p = ${bv.p_value.toFixed(3)})`)
  await expect(line).toContainText('possibly through their exposure to market crashes')
  await expect(line).toContainText('under model pricing this means they moved more than their trailing vol implied, which real option prices may already reflect.')
  await expect(page.getByTestId('signal-beyond-vol-caveat').first()).toHaveText('Beyond this vol surface only.')
})

const DEMOTIONS = {
  intercept: 'it is no longer significant once the market’s own move is netted out',
  drop_best_period: 'it disappears without its single best period',
  drop_best_episode: 'it disappears without its best stress episode',
  equity_only: 'it is absent among equity names alone, or could not be checked there',
} as const

for (const [demotion, words] of Object.entries(DEMOTIONS)) {
  test(`names the ${demotion} demotion when a significant result is not robust`, async ({ page }) => {
    const bv = SIGNAL_TEST_CRASH_CARRIED.beyond_vol
    await serveHeadline(page, { ...SIGNAL_TEST_CRASH_CARRIED, beyond_vol: { ...bv, demotion } })
    const line = beyond(page)
    await expect(line).toContainText(`Controlling for trailing vol the composite looks significant (${est(bv)}, p = ${bv.p_value.toFixed(3)})`)
    await expect(line).toContainText(`not robust, so it is not a pass: ${words}.`)
    // Not worded as a pass.
    await expect(line).not.toContainText('Fragility predicted')
  })
}

test('says what is missing when there is too little to test, with no estimate and no chart', async ({ page }) => {
  await serveHeadline(page, SIGNAL_TEST_INSUFFICIENT)
  const line = beyond(page)
  // It does not blame "history": the test also needs stress episodes and names.
  await expect(line).toContainText(
    'Not enough to test: 10 periods measured (0 skipped for fewer than 40 names), and the test needs at least 24 periods and 3 stress episodes.',
  )
  await expect(line).not.toContainText(/history/i)
  await expect(line).not.toContainText('γ̄')
  await expect(page.getByTestId('gamma-chart')).toHaveCount(0)
  // The count of skipped periods is the response's own.
  const bv = SIGNAL_TEST_INSUFFICIENT.beyond_vol
  await serveHeadline(page, { ...SIGNAL_TEST_INSUFFICIENT, beyond_vol: { ...bv, n_periods: 61, n_skipped: 20 } })
  await expect(beyond(page)).toContainText('61 periods measured (20 skipped for fewer than 40 names)')
})

test('an exploratory run with too little to test says so without a status or a p', async ({ page }) => {
  await serveHeadline(page, { ...SIGNAL_TEST_INSUFFICIENT, beyond_vol_mode: 'exploratory' })
  const line = beyond(page)
  await expect(line).toHaveAttribute('data-mode', 'exploratory')
  expect((await line.innerText()).replace(/\s*ⓘ$/, '')).toBe(
    'Exploratory (not the pre-registered setting): not enough to test: 10 periods measured (0 skipped for fewer than 40 names), and the test needs at least 24 periods and 3 stress episodes.',
  )
  for (const banned of [/p\s*[=<]/, /insufficient/i, /status/i, /history/i, /γ̄/]) {
    await expect(line).not.toContainText(banned)
  }
})

test('says so, rather than staying silent, when the test could not run, and why', async ({ page }) => {
  await serveHeadline(page, { ...SIGNAL_TEST_NO_BETTER, beyond_vol: null, beyond_vol_unavailable_reason: null })
  await expect(beyond(page)).toContainText('could not be run on these periods')
  await serveHeadline(page, { ...SIGNAL_TEST_NO_BETTER, beyond_vol: null, beyond_vol_unavailable_reason: '400 periods is more than the 120 this server tests' })
  await expect(beyond(page)).toContainText('could not be run on these periods (400 periods is more than the 120 this server tests).')
  await expect(beyond(page)).toHaveAttribute('data-mode', 'unavailable')
  await expect(page.getByTestId('gamma-chart')).toHaveCount(0)
})

test('reads a response from before the field existed as "could not be run"', async ({ page }) => {
  const { beyond_vol: _bv, beyond_vol_mode: _m, beyond_vol_unavailable_reason: _r, ...old } = SIGNAL_TEST_NO_BETTER
  await serveHeadline(page, old)
  await expect(beyond(page)).toHaveAttribute('data-mode', 'unavailable')
})

test('a response with an estimate but no mode, or an unknown one, is read as exploratory, never confirmatory', async ({ page }) => {
  const { beyond_vol_mode: _m, ...noMode } = SIGNAL_TEST_BEATS
  for (const body of [noMode, { ...SIGNAL_TEST_BEATS, beyond_vol_mode: 'definitely' }]) {
    await serveHeadline(page, body)
    const line = beyond(page)
    await expect(line).toHaveAttribute('data-mode', 'exploratory')
    await expect(line).toContainText('Exploratory (not the pre-registered setting): γ̄ =')
    await expect(line).not.toContainText(/p\s*[=<]/)
    await expect(page.getByTestId('gamma-chart')).toHaveCount(0)
  }
})

test('the pre-registered setting on a later date is a monitoring re-run: no p, no status, the original result quoted', async ({ page }) => {
  const bv = SIGNAL_TEST_BEATS.beyond_vol // a "predicts" payload with p < 0.05, which must stay unshown
  await serveHeadline(page, { ...SIGNAL_TEST_BEATS, beyond_vol_mode: 'monitoring', as_of: '2026-11-14' })
  const line = beyond(page)
  await expect(line).toHaveAttribute('data-mode', 'monitoring')
  expect((await line.innerText()).replace(/\s*ⓘ$/, '')).toBe(
    `Re-run on data to 2026-11-14 (monitoring, not the pre-registered test): ${est(bv)}. ` +
      'The pre-registered test, on data to 2026-10-31, found no evidence (γ̄ −0.008, 90% CI [−0.033, +0.017], p = 0.71; ADR 0035).',
  )
  await expect(line).not.toContainText('Fragility predicted')
  await expect(page.getByTestId('gamma-chart')).toHaveCount(0)
  // Too little to test on a re-run still quotes the pre-registered result.
  await serveHeadline(page, { ...SIGNAL_TEST_INSUFFICIENT, beyond_vol_mode: 'monitoring', as_of: '2026-11-14' })
  await expect(beyond(page)).toContainText('Re-run on data to 2026-11-14 (monitoring, not the pre-registered test): not enough to test: 10 periods measured')
  await expect(beyond(page)).toContainText('The pre-registered test, on data to 2026-10-31, found no evidence')
})

test('a composite whose CI is wholly below zero is said to have predicted worse outcomes, not "no evidence"', async ({ page }) => {
  const bv = { ...BEYOND_VOL_NO_EVIDENCE, gamma_bar: -0.06, ci90: { low: -0.09, high: -0.03 }, p_value: 0.9999 }
  await serveHeadline(page, { ...SIGNAL_TEST_NO_BETTER, beyond_vol: bv })
  const line = beyond(page)
  await expect(line).toContainText('the composite predicted worse put outcomes, not better')
  await expect(line).toContainText(`(${est(bv)}; the 90% CI is wholly below zero)`)
  await expect(line).not.toContainText(/no evidence/i)
  await expect(line).not.toContainText(/p\s*[=<]/)
  // A CI that reaches zero is the ordinary null.
  await serveHeadline(page, { ...SIGNAL_TEST_NO_BETTER, beyond_vol: { ...bv, ci90: { low: -0.09, high: 0 } } })
  await expect(beyond(page)).toContainText('there is no evidence the composite predicts better put outcomes')
})

// A p-value is never printed as a number that contradicts its status.
const P_CASES: [string, 'predicts' | 'not_robust', number, string][] = [
  ['rounds up to 0.050', 'predicts', 0.0496, ', p < 0.05)'],
  ['rounds up to 0.050 on a demoted pass', 'not_robust', 0.0496, ', p < 0.05)'],
  ['is tiny', 'predicts', 0.0004, ', p < 0.001)'],
  ['is an ordinary pass', 'predicts', 0.0123, ', p = 0.012)'],
]
for (const [name, kind, p, expected] of P_CASES) {
  test(`p ${name}: prints "${expected.slice(2, -1)}"`, async ({ page }) => {
    const base = kind === 'predicts' ? SIGNAL_TEST_BEATS : SIGNAL_TEST_CRASH_CARRIED
    await serveHeadline(page, { ...base, beyond_vol: { ...base.beyond_vol, p_value: p } })
    const text = await beyond(page).innerText()
    expect(text).toContain(expected)
    expect(text).not.toContain('0.050')
  })
}

test('a pass whose p is not below 0.05 prints no p-value at all', async ({ page }) => {
  for (const base of [SIGNAL_TEST_BEATS, SIGNAL_TEST_CRASH_CARRIED]) {
    await serveHeadline(page, { ...base, beyond_vol: { ...base.beyond_vol, p_value: 0.0512 } })
    const text = await beyond(page).innerText()
    expect(text).not.toMatch(/p\s*[=<]/)
    expect(text).not.toContain('0.051')
  }
})

test('keeps the surface caveat on every verdict that states a result', async ({ page }) => {
  for (const body of [SIGNAL_TEST_NO_BETTER, SIGNAL_TEST_BEATS, SIGNAL_TEST_CRASH_CARRIED]) {
    await serveHeadline(page, body)
    await expect(page.getByTestId('signal-beyond-vol-caveat').first()).toHaveText('Beyond this vol surface only.')
  }
})

test('a demoted pass with no named demotion, or one the page does not know, still says it is not a pass', async ({ page }) => {
  const bv = SIGNAL_TEST_CRASH_CARRIED.beyond_vol
  for (const demotion of [null, 'something_new']) {
    await serveHeadline(page, { ...SIGNAL_TEST_CRASH_CARRIED, beyond_vol: { ...bv, demotion } })
    await expect(beyond(page)).toContainText('not robust, so it is not a pass: a robustness check failed.')
  }
})

test('a status the page does not know reads "could not be read", and nothing throws', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const bv = SIGNAL_TEST_BEATS.beyond_vol
  await serveHeadline(page, { ...SIGNAL_TEST_BEATS, beyond_vol: { ...bv, status: 'a_future_status' } })
  const line = beyond(page)
  await expect(line).toHaveAttribute('data-mode', 'unavailable')
  await expect(line).toContainText('The test of fragility beyond trailing vol could not be read.')
  await expect(line).not.toContainText(/p\s*[=<]/)
  await expect(page.getByTestId('signal-panel')).toBeVisible()
  expect(errors).toEqual([])
})

test('the concept keeps its two bear/skew limits apart and no longer claims a range for γ̄', () => {
  const c = CONCEPTS.fama_macbeth
  const text = `${c.intuition} ${c.howToRead}`
  expect(text).not.toMatch(/−1 to \+1|-1 to \+1/)
  expect(c.howToRead).toContain('average slope of the outcome rank on the composite rank')
  // Different simulated worlds: a skewed index (6.1% at zero drift, 2.8% at +10%/yr) ...
  expect(c.howToRead).toContain('simulated negatively skewed index, about 6.1% at zero drift and 2.8% at +10% a year')
  // ... and a Gaussian market (6.0% at -15%/yr), never "skewed ... 6.0% in a bear sample".
  expect(c.howToRead).toContain('separately, in a simulated Gaussian market drifting −15% a year, about 6.0%')
  expect(c.howToRead).toContain('no longer significant once the market move is netted out')
  expect(c.howToRead).not.toMatch(/vanishes once/)
  expect(c.intuition).toContain('on data to 2026-10-31')
  expect(c.intuition).toContain('monitoring re-run')
})

test('off the pre-registered setting it is exploratory: gamma_bar and CI, no p, no status, no chart', async ({ page }) => {
  // The server sends a p-value and a "predicts" status regardless of the
  // setting; the page must not show either off the confirmatory setting.
  const bv = SIGNAL_TEST_BEATS.beyond_vol
  expect(bv.status).toBe('predicts_beyond_trailing_vol')
  expect(bv.p_value).toBeLessThan(0.05)
  await serveHeadline(page, { ...SIGNAL_TEST_BEATS, beyond_vol_mode: 'exploratory' })
  const line = beyond(page)
  await expect(line).toHaveAttribute('data-mode', 'exploratory')
  // The whole line: the label and the estimate, and nothing else but the ⓘ button.
  expect((await line.innerText()).replace(/\s*ⓘ$/, '')).toBe(`Exploratory (not the pre-registered setting): ${est(bv)}.`)
  for (const banned of [/p\s*[=<]/, /predict/i, /robust/, /80%/, /evidence/, /pay-rate/]) {
    await expect(line).not.toContainText(banned)
  }
  await expect(page.getByTestId('gamma-chart')).toHaveCount(0)
})

test('a run at the rail is exploratory while the fixed headline stays confirmatory', async ({ page }) => {
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  await expect(runTable(page)).toBeVisible()
  const lines = page.getByTestId('signal-beyond-vol')
  await expect(lines).toHaveCount(2)
  await expect(lines.nth(0)).toHaveAttribute('data-mode', 'confirmatory')
  await expect(lines.nth(1)).toHaveAttribute('data-mode', 'exploratory')
  await expect(lines.nth(1)).toContainText('Exploratory (not the pre-registered setting): γ̄ =')
  await expect(page.getByTestId('gamma-chart')).toHaveCount(1) // the headline's only
})

test('charts the per-period slopes, labelled, with the mean and its CI', async ({ page }) => {
  const bv = BEYOND_VOL_NO_EVIDENCE
  const chart = page.getByTestId('gamma-chart')
  await expect(chart).toBeVisible()
  await expect(chart.locator('rect.pl-sig-bar')).toHaveCount(bv.per_period.length)
  await expect(chart.locator('rect.pl-sig-bar.is-pos')).toHaveCount(bv.per_period.filter((p) => p.gamma >= 0).length)
  await expect(chart.getByTestId('gamma-mean')).toHaveCount(1)
  await expect(chart.getByTestId('gamma-ci')).toHaveCount(1)
  const label = await chart.getByRole('img').getAttribute('aria-label')
  expect(label).toContain(`in each of ${bv.per_period.length} periods`)
  expect(label).toContain(`the mean is ${s3(bv.gamma_bar)}`)
  expect(label).toContain(`a 90% interval from ${s3(bv.ci90.low)} to ${s3(bv.ci90.high)}`)
  // The chart's own label carries a concept.
  await expect(chart.locator('.pl-pane-label').getByRole('button', { name: /What is/ })).toHaveCount(1)
  // Hovering a bar reads that period out.
  await chart.getByRole('img').hover({ position: { x: 5, y: 40 } })
  await expect(chart.locator('[aria-live="polite"]')).toContainText(bv.per_period[0]!.date)
})

test('draws the slopes where they belong: positive above zero, negative below, the CI band over the CI, the mean at γ̄', async ({ page }) => {
  const bv = BEYOND_VOL_NO_EVIDENCE
  const chart = page.getByTestId('gamma-chart')
  await expect(chart).toBeVisible()
  const num = (l: Locator, name: string) => l.getAttribute(name).then(Number)
  const zero = await num(chart.locator('line.pl-tape-zero'), 'y1')
  const bars = chart.locator('rect.pl-sig-bar')
  const geo = await bars.evaluateAll((els) =>
    els.map((e) => ({ y: Number(e.getAttribute('y')), h: Number(e.getAttribute('height')) })),
  )
  expect(geo).toHaveLength(bv.per_period.length)
  // Bars: a positive slope stands on the zero line and rises (above = smaller y);
  // a negative one hangs from it. (A zero slope is a 1-unit sliver.)
  const tol = 1.01
  bv.per_period.forEach((p, i) => {
    const { y, h } = geo[i]!
    if (p.gamma > 0) expect(y + h, `period ${i} bottom on the zero line`).toBeLessThanOrEqual(zero + tol)
    if (p.gamma > 0) expect(y, `period ${i} rises above zero`).toBeLessThan(zero)
    if (p.gamma < 0) expect(y, `period ${i} top on the zero line`).toBeGreaterThanOrEqual(zero - tol)
    if (p.gamma < 0) expect(y + h, `period ${i} hangs below zero`).toBeGreaterThan(zero)
  })
  // Scale in user units per unit of slope, from the tallest bar.
  const k = Math.max(...bv.per_period.map((p, i) => (Math.abs(p.gamma) > 0.05 ? geo[i]!.h / Math.abs(p.gamma) : 0)))
  expect(k).toBeGreaterThan(50)
  const near = (actual: number, expected: number) => expect(Math.abs(actual - expected)).toBeLessThan(0.2)
  // Every bar's length is its slope on that scale.
  bv.per_period.forEach((p, i) => {
    if (Math.abs(p.gamma) * k > 2) near(geo[i]!.h, Math.abs(p.gamma) * k)
  })
  // The CI band spans the CI: top at the upper end, bottom at the lower.
  const band = chart.getByTestId('gamma-ci')
  const bandY = await num(band, 'y')
  const bandH = await num(band, 'height')
  near(bandY, zero - bv.ci90.high * k)
  near(bandY + bandH, zero - bv.ci90.low * k)
  // The mean line sits at gamma_bar, not at zero.
  const mean = chart.getByTestId('gamma-mean')
  const meanY = await num(mean, 'y1')
  near(meanY, zero - bv.gamma_bar * k)
  expect(Math.abs(meanY - zero)).toBeGreaterThan(0.5)
  expect(await num(mean, 'y2')).toBe(meanY)
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
