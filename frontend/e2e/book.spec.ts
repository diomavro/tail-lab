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

test('real data: PPUT fails on growth, and the unhedged row is marked best', async ({ page }) => {
  await openBook(page)
  const pput = region(page, COLE)
  await expect(pput.getByTestId('verdict')).toHaveText(
    'Paradox fails On growth, no interior mix beats the better end; the best one trails it by 0.203pp/yr. ' +
      'On CAGR per unit of vol the best mix is 40% hedged (an interior mix wins).',
  )
  await expect(pput.getByTestId('verdict').locator('.pl-tag')).toHaveClass(/pl-tag-bad/)
  await expect(pput.locator('tr[aria-current="true"] td').first()).toHaveText('S&P 500 only best growth')
  // Every column, in order, for the two ends: a swapped column cannot pass.
  const rows = pput.locator('tbody tr')
  await expect(rows.first().locator('td')).toHaveText(['S&P 500 only best growth', '6.90%', '20.0%', '−55.6%', '0.345'])
  await expect(rows.last().locator('td')).toHaveText(['PPUT only', '4.55%', '14.0%', '−42.0%', '0.324'])
})

test('real data: headings name the program, the window and the span actually covered', async ({ page }) => {
  await openBook(page)
  await expect(page.getByRole('heading', { level: 3 }).first()).toHaveText(`PPUT: ${PPUT!.description}`)
  // VXTH starts 2006: the heading shows what the data cover, not what was asked.
  await expect(region(page, /^VXTH The letter's window/).getByRole('heading', { level: 4 })).toHaveText(
    "The letter's window (2005 to Mar 2016): 2006-03-31 to 2016-03-31",
  )
  await expect(page.locator('.pl-lede').first()).toContainText('by more than 1bp a year')
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
  await expect(page.getByRole('table').first()).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(375)
})

test('real data: VXTH holds in the letter window, which is shortened and says by how much', async ({ page }) => {
  await openBook(page)
  const vxth = region(page, /^VXTH The letter's window/)
  await expect(vxth.getByTestId('verdict')).toHaveText(
    'Paradox holds On growth, 40% hedged beats both ends by 0.125pp/yr. ' +
      'On CAGR per unit of vol the best mix is 80% hedged (an interior mix wins).',
  )
  await expect(vxth.getByTestId('verdict').locator('.pl-tag')).toHaveClass(/pl-tag-ok/)
  await expect(vxth.getByTestId('clipped')).toHaveText(
    'Shortened: the data cover 2006-03-31 to 2016-03-31, not the 2005-01-03 to 2016-03-31 asked for ' +
      '(VXTH or the S&P 500 starts late or stops early, or the as-of date falls inside the window).',
  )
  await expect(region(page, COLE).getByTestId('clipped')).toHaveCount(0)
})

test('real data: a win under a basis point is too close to call, never "trails"', async ({ page }) => {
  await openBook(page)
  const verdict = region(page, /^VXTH Full history/).getByTestId('verdict')
  await expect(verdict).toHaveText(
    'Too close to call On growth, 10% hedged beats both ends, but only by 0.005pp/yr, under the 1bp/yr threshold. ' +
      'On CAGR per unit of vol the best mix is 50% hedged (an interior mix wins).',
  )
  await expect(verdict.locator('.pl-tag')).toHaveClass(/pl-tag-mute/)
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
    'Too close to call On growth, no interior mix beats the better end, but the best one is within 0.004pp/yr of it, under the 1bp/yr threshold.',
  )
})

test('an exact tie is within zero of the better end, never a win', async ({ page }) => {
  await openBook(page, variant({ margin: 0, outcome: 'inconclusive', best_weight: 0 }))
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'no interior mix beats the better end, but the best one is within 0.000pp/yr of it',
  )
})

test('the risk-adjusted test can go to an end, be too close, or be undefined', async ({ page }) => {
  await openBook(page, variant({ best_weight_risk_adjusted: 0, outcome_risk_adjusted: 'fails' }))
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'On CAGR per unit of vol the best mix is 0% hedged (an end wins).',
  )
})

test('the risk-adjusted test reports too close to call', async ({ page }) => {
  await openBook(page, variant({ best_weight_risk_adjusted: 0.3, outcome_risk_adjusted: 'inconclusive' }))
  await expect(region(page, COLE).getByTestId('verdict')).toContainText(
    'On CAGR per unit of vol the best mix is 30% hedged (too close to call).',
  )
})

test('an undefined ratio says so instead of printing a number', async ({ page }) => {
  const points = PPUT_COLE.points.map((p, i) => (i === 0 ? { ...p, volatility: 0, cagr_per_vol: null } : p))
  await openBook(page, variant({ points, best_weight_risk_adjusted: null, outcome_risk_adjusted: null }))
  const pput = region(page, COLE)
  await expect(pput.getByTestId('verdict')).toContainText(
    'CAGR per unit of vol is undefined here (a mix has zero volatility).',
  )
  await expect(pput.locator('tbody tr').first().locator('td').last()).toHaveText('n/a')
})

test('a window or a program the lake cannot cover is named with its reason', async ({ page }) => {
  const body = variant({}, { VXTH: 'not in the Cboe snapshot' })
  body.overlay.programs[0] = { ...body.overlay.programs[0]!, windows: [], unavailable: { full: 'only 12 common trading days' } }
  await openBook(page, body)
  await expect(page.getByTestId('missing')).toHaveText('VXTH: not tested (not in the Cboe snapshot).')
  await expect(page.getByTestId('unavailable')).toHaveText('Full history: not computed (only 12 common trading days).')
})

test('the result carries its provenance', async ({ page }) => {
  await openBook(page)
  await expect(page.getByText(/^Cboe snapshot/)).toHaveText(
    `Cboe snapshot ${HEDGE_OVERLAY.cboe_snapshot} · code ${HEDGE_OVERLAY.code_sha} · as of ${HEDGE_OVERLAY.overlay.as_of}`,
  )
  expect(VXTH!.windows).toHaveLength(2)
})

test('puts the dividend-assumption caveat above the first table', async ({ page }) => {
  await openBook(page)
  const caveat = page.locator('.pl-caveat').filter({ hasText: 'assumed flat yield' })
  const table = page.getByRole('table').first()
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
  await expect(page.getByRole('table')).toHaveCount(0)
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
