import { expect, test, type Page } from '@playwright/test'
import type { HedgeOverlayResponse, OverlayWindow } from '../src/api/client'
import { HEDGE_OVERLAY } from './fixtures/hedge-overlay'
import { mockPutLabApi } from './fixtures/mock-api'

// The Book: Rodman's Paradox (Artemis Capital, 2016) tested on Cboe's
// real-quote hedge programs. The fixture is the real computed result, so the
// first tests pin the verdicts a reader actually sees; the variant payloads
// after them reach the branches today's data does not (an end winning on
// CAGR / vol, an undefined ratio, a sliver of a loss, a missing program).

async function openBook(page: Page, body?: HedgeOverlayResponse) {
  await mockPutLabApi(page)
  if (body) {
    await page.route('**/api/putlab/hedge-overlay', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    )
  }
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
}

const region = (page: Page, name: RegExp) => page.getByRole('region', { name })
const [PPUT, , VXTH] = HEDGE_OVERLAY.overlay.programs
const PPUT_COLE = PPUT!.windows[0]!

/** The real payload cut to one PPUT window, with fields overridden. */
function variant(over: Partial<OverlayWindow>, missing: Record<string, string> = {}): HedgeOverlayResponse {
  return {
    ...HEDGE_OVERLAY,
    overlay: {
      ...HEDGE_OVERLAY.overlay,
      missing,
      programs: [{ ...PPUT!, windows: [{ ...PPUT_COLE, ...over }], unavailable: {} }],
    },
  }
}

const COLE = /^PPUT The letter's window/

/** Open a panel's "As numbers" table. */
async function numbers(page: Page, name: RegExp) {
  await region(page, name).getByText('As numbers').click()
  return region(page, name).locator('tbody tr')
}

test('real data: PPUT fails on growth, and the unhedged row is marked best', async ({ page }) => {
  await openBook(page)
  const pput = region(page, COLE)
  await expect(pput.getByTestId('verdict')).toHaveText(
    'No interior mix beats the better end; the best one trails it by 0.203pp/yr.',
  )
  await expect(pput.getByTestId('panel-tag')).toHaveText('Paradox fails')
  await expect(pput.getByTestId('panel-tag')).toHaveClass(/pl-tag-bad/)
  // The readout opens on the best mix: here the unhedged end.
  await expect(pput.getByTestId('panel-readout')).toHaveText(
    'S&P 500 only · CAGR 6.90% · vol 20.0% · max DD −55.6% · CAGR/vol 0.345',
  )
  // Nothing beats the better end, so no "out-grows" area is drawn.
  await expect(pput.getByTestId('panel-area')).toHaveCount(0)
  const rows = await numbers(page, COLE)
  await expect(pput.locator('tr[aria-current="true"] td').first()).toHaveText('S&P 500 only best growth')
  // Every column, in order, for the two ends: a swapped column cannot pass.
  await expect(rows.first().locator('td')).toHaveText(['S&P 500 only best growth', '6.90%', '20.0%', '−55.6%', '0.345'])
  await expect(rows.last().locator('td')).toHaveText(['PPUT only', '4.55%', '14.0%', '−42.0%', '0.324'])
})

test('real data: one section per window, one panel per program, the span actually covered', async ({ page }) => {
  await openBook(page)
  await expect(page.getByRole('heading', { level: 3 })).toHaveText(["The letter's window (2005 to Mar 2016)", 'Full history'])
  await expect(page.locator('.pl-panel')).toHaveCount(6)
  // VXTH starts 2006: the panel shows what the data cover, not what was asked.
  const vxth = region(page, /^VXTH The letter's window/)
  await expect(vxth.getByRole('heading', { level: 4 })).toHaveText('VXTH')
  await expect(vxth.locator('.pl-panel-span')).toHaveText('2006-03-31 to 2016-03-31')
  await expect(page.locator('.pl-lede').filter({ hasText: "Rodman's Paradox" })).toContainText('by more than 1bp a year')
  // Every hedge result names its accounting (README; docs/adr/0027).
  await expect(page.getByTestId('accounting')).toHaveText(
    "Accounting: self-financed — the hedge's premium is paid from the book (docs/adr/0027).",
  )
})

test('the headline is counted from the outcomes, and follows the test chosen', async ({ page }) => {
  await openBook(page)
  const headline = page.getByTestId('lump-headline')
  await expect(headline).toHaveText(
    "On growth, one of six tests holds: VXTH in the letter's window (2005 to Mar 2016), 40% hedged, by 0.125pp/yr. " +
      'The rest fail or are too close to call.',
  )
  await page.getByRole('radiogroup', { name: 'Judge each mix on' }).getByText('CAGR / vol').click()
  // In the real data every window's best CAGR / vol is an interior mix.
  const holds = HEDGE_OVERLAY.overlay.programs.flatMap((p) => p.windows).filter((w) => w.outcome_risk_adjusted === 'holds')
  expect(holds).toHaveLength(6)
  await expect(headline).toHaveText(
    'On CAGR per unit of vol, every one of the six tests favours an interior mix. This is the weaker test: it rewards any mix that lowers volatility.',
  )
  // ...and each panel re-tags on that test's own outcome, not on growth's.
  await expect(region(page, COLE).getByTestId('panel-tag')).toHaveText('Paradox holds')
  await expect(region(page, COLE).getByTestId('verdict')).toHaveText(
    'Best CAGR per unit of vol at 40% hedged (an interior mix wins). The weaker test: it favours any mix that lowers volatility.',
  )
})

test('a lone failing test reads as not holding', async ({ page }) => {
  await openBook(page, variant({}))
  await expect(page.getByTestId('lump-headline')).toHaveText(
    'On growth, the one test does not hold: it fails or is too close to call.',
  )
})

test('a window cut short at its end says so, with both end dates', async ({ page }) => {
  await openBook(page, variant({ clipped: true, end: '2010-06-30' }))
  await expect(region(page, COLE).getByTestId('clipped')).toContainText(
    'the data cover 2005-01-03 to 2010-06-30, not the 2005-01-03 to 2016-03-31 asked for',
  )
})

test('no page-wide sideways scroll on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await openBook(page)
  await expect(page.locator('.pl-panel').first()).toBeVisible()
  await numbers(page, COLE)
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(375)
})

test('real data: VXTH holds in the letter window, which is shortened and says by how much', async ({ page }) => {
  await openBook(page)
  const vxth = region(page, /^VXTH The letter's window/)
  await expect(vxth.getByTestId('verdict')).toHaveText('40% hedged beats both ends by 0.125pp/yr.')
  await expect(vxth.getByTestId('panel-tag')).toHaveClass(/pl-tag-ok/)
  // A mix out-grows the better end, so the area between them is drawn.
  await expect(vxth.getByTestId('panel-area')).toHaveCount(1)
  await expect(vxth.getByTestId('clipped')).toHaveText(
    'Shortened: the data cover 2006-03-31 to 2016-03-31, not the 2005-01-03 to 2016-03-31 asked for ' +
      '(VXTH or the S&P 500 starts late or stops early, or the as-of date falls inside the window).',
  )
  await expect(region(page, COLE).getByTestId('clipped')).toHaveCount(0)
})

test('the readout follows the pointer, and the arrow keys, to each mix', async ({ page }) => {
  await openBook(page)
  const vxth = region(page, /^VXTH The letter's window/)
  const chart = vxth.getByTestId('panel-chart')
  await chart.scrollIntoViewIfNeeded()
  const box = (await chart.boundingBox())!
  // x = 46 + w * 244 in a 300-wide viewBox: hover the 100%-hedged end.
  await page.mouse.move(box.x + (290 / 300) * box.width, box.y + box.height / 2)
  await expect(vxth.getByTestId('panel-readout')).toContainText('VXTH only')
  await page.mouse.move(box.x + (46 / 300) * box.width, box.y + box.height / 2)
  await expect(vxth.getByTestId('panel-readout')).toContainText('S&P 500 only')
  await chart.focus()
  await page.keyboard.press('ArrowRight')
  await expect(vxth.getByTestId('panel-readout')).toContainText('10% hedged')
})

test('real data: a win under a basis point is too close to call, never "trails"', async ({ page }) => {
  await openBook(page)
  const panel = region(page, /^VXTH Full history/)
  await expect(panel.getByTestId('verdict')).toHaveText(
    '10% hedged beats both ends, but only by 0.005pp/yr, under the 1bp/yr threshold.',
  )
  await expect(panel.getByTestId('panel-tag')).toHaveClass(/pl-tag-mute/)
})

test('real data: each verdict is re-run at every assumed dividend yield, with its outcome', async ({ page }) => {
  await openBook(page)
  await expect(region(page, /^VXTH Full history/).getByTestId('sensitivity')).toHaveText(
    'Best interior mix vs the better end, by assumed dividend yield: ' +
      'at 1.4%, paradox holds (best mix 20% hedged, +0.082pp/yr); ' +
      'at 1.9%, too close to call (best mix 10% hedged, +0.005pp/yr); ' +
      'at 2.4%, paradox fails (best is an end, −0.050pp/yr).',
  )
})

test('a sliver of a loss is too close to call, worded as a loss', async ({ page }) => {
  await openBook(page, variant({ margin: -0.00004, outcome: 'inconclusive', best_weight: 0 }))
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'No interior mix beats the better end, but the best one is within 0.004pp/yr of it, under the 1bp/yr threshold.',
  )
})

test('an exact tie is within zero of the better end, never a win', async ({ page }) => {
  await openBook(page, variant({ margin: 0, outcome: 'inconclusive', best_weight: 0 }))
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'No interior mix beats the better end, but the best one is within 0.000pp/yr of it',
  )
})

const ratio = (page: Page) => page.getByRole('radiogroup', { name: 'Judge each mix on' }).getByText('CAGR / vol').click()

test('the risk-adjusted test can go to an end, be too close, or be undefined', async ({ page }) => {
  await openBook(page, variant({ best_weight_risk_adjusted: 0, outcome_risk_adjusted: 'fails' }))
  await ratio(page)
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'Best CAGR per unit of vol at 0% hedged (an end wins).',
  )
  await expect(region(page, COLE).getByTestId('panel-tag')).toHaveText('Paradox fails')
})

test('the risk-adjusted test reports too close to call', async ({ page }) => {
  await openBook(page, variant({ best_weight_risk_adjusted: 0.3, outcome_risk_adjusted: 'inconclusive' }))
  await ratio(page)
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'Best CAGR per unit of vol at 30% hedged (too close to call).',
  )
  await expect(region(page, COLE).getByTestId('panel-tag')).toHaveText('Too close to call')
})

test('an undefined ratio says so instead of printing a number', async ({ page }) => {
  const points = PPUT_COLE.points.map((p, i) => (i === 0 ? { ...p, volatility: 0, cagr_per_vol: null } : p))
  await openBook(page, variant({ points, best_weight_risk_adjusted: null, outcome_risk_adjusted: null }))
  await ratio(page)
  const pput = region(page, COLE)
  await expect(pput.getByTestId('verdict')).toContainText(
    'CAGR per unit of vol is undefined here (a mix has zero volatility).',
  )
  await expect(pput.getByTestId('panel-tag')).toHaveText('Undefined')
  // No best mix to ring, and the undefined point is left off the line.
  await expect(pput.getByTestId('panel-best')).toHaveCount(0)
  const rows = await numbers(page, COLE)
  await expect(rows.first().locator('td').last()).toHaveText('n/a')
})

test('a window or a program the lake cannot cover is named with its reason', async ({ page }) => {
  const body = variant({}, { VXTH: 'not in the Cboe snapshot' })
  body.overlay.programs[0] = { ...body.overlay.programs[0]!, windows: [], unavailable: { full: 'only 12 common trading days' } }
  await openBook(page, body)
  await expect(page.getByTestId('missing')).toHaveText('VXTH: not tested (not in the Cboe snapshot).')
  await expect(page.getByTestId('unavailable')).toHaveText('PPUT, full history: not computed (only 12 common trading days).')
})

test('the result carries its provenance', async ({ page }) => {
  await openBook(page)
  await expect(page.getByText(/^Cboe snapshot/)).toHaveText(
    `Cboe snapshot ${HEDGE_OVERLAY.cboe_snapshot} · code ${HEDGE_OVERLAY.code_sha} · as of ${HEDGE_OVERLAY.overlay.as_of}`,
  )
  expect(VXTH!.windows).toHaveLength(2)
})

test('puts the dividend-assumption caveat above the first chart', async ({ page }) => {
  await openBook(page)
  const caveat = page.locator('.pl-caveat').filter({ hasText: 'assumed flat yield' })
  const table = page.getByTestId('panel-chart').first()
  await expect(table).toBeVisible()
  await expect(caveat).toContainText('1.9%')
  // The direction matters: a LOW guess handicaps the unhedged leg.
  await expect(caveat).toContainText('a low guess understates the unhedged leg against every hedged one')
  // A caveat after the number it qualifies has already lost.
  const order = await caveat.evaluate(
    (el, t) => el.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING,
    await table.elementHandle(),
  )
  expect(order).toBeTruthy()
})

test('an empty lake reads as missing data, not as a verdict', async ({ page }) => {
  await mockPutLabApi(page, { noData: ['/api/putlab/hedge-overlay'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await expect(page.getByText('Nothing to blend: no data.')).toBeVisible()
  await expect(page.locator('.pl-panel')).toHaveCount(0)
})

test('a server failure is reported as a failure, not as an empty lake', async ({ page }) => {
  await mockPutLabApi(page, { fail: ['/api/putlab/hedge-overlay'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await expect(page.locator('.pl-caveat').filter({ hasText: 'boom' })).toHaveText('boom')
  await expect(page.getByText(/Nothing to blend/)).toHaveCount(0)
})

test('a sensitivity row won by the fully hedged end says so', async ({ page }) => {
  const sensitivity = PPUT_COLE.sensitivity.map((r) => ({ ...r, best_weight: 1, margin: -0.002, outcome: 'fails' as const }))
  await openBook(page, variant({ sensitivity }))
  await expect(region(page, COLE).getByTestId('sensitivity')).toContainText('at 1.4%, paradox fails (best is an end, −0.200pp/yr)')
})

test('a snapshot without SPX says so, rather than claiming the lake is empty', async ({ page }) => {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/hedge-overlay', (route) =>
    route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'no SPX rows in the snapshot known as of 2026-10-03' }),
    }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await expect(page.getByText('Nothing to blend: no SPX rows in the snapshot known as of 2026-10-03.')).toBeVisible()
})
