import { expect, test, type Page } from '@playwright/test'
import type { PutBacktestResponse } from '../src/api/client'
import { BACKTEST } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// "How the price is built": the latest roll taken apart, and the dividend yield
// redone by hand from the payments it came from. The point is that a reader can
// check q without trusting it -- so the test checks the arithmetic is on the
// page, not just that a section exists.

// No goto here: a test that overrides the backtest must register its route
// before the page loads, or the default fixture has already rendered.
test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
})

async function serveBacktest(page: Page, body: PutBacktestResponse) {
  await page.route(
    (url) => url.pathname === '/api/putlab/backtest',
    (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

async function open(page: Page) {
  await page.goto('/')
  const section = page.getByTestId('price-build')
  await section.locator('summary').click()
  return section
}

test('lists the latest roll inputs, q included', async ({ page }) => {
  const section = await open(page)
  const inputs = section.getByTestId('price-build-inputs')
  await expect(inputs).toContainText('Volatility σ')
  await expect(inputs).toContainText('(20-day realised)')
  await expect(section.getByTestId('price-build-q')).toHaveText('1.307%')
})

test('redoes q from the payments: -ln(1 - D / S)', async ({ page }) => {
  const section = await open(page)
  // 1.60 + 1.75 + 1.53 + 1.54 = 6.42 against the roll's 494.40 close.
  await expect(section.getByTestId('price-build-formula')).toHaveText(
    'q = −ln(1 − D / S) = −ln(1 − 6.42 / 494.40) = 1.307%',
  )
  await expect(section.locator('tbody tr')).toHaveCount(4)
  await expect(section.locator('tbody th')).toHaveText(['2024-06-21', '2024-09-20', '2024-12-20', '2025-03-21'])
  await expect(section).toContainText("measured from this name's own paid dividends")
})

test('an unknown yield says it priced at q = 0 and why that matters', async ({ page }) => {
  await serveBacktest(page, {
    ...BACKTEST,
    q_source: 'none',
    dividend_snapshot: null,
    cycles: BACKTEST.cycles.map((c) => ({ ...c, q: 0, q_source: 'unknown' })),
    dividend_basis: { ...BACKTEST.dividend_basis!, q: 0, source: 'unknown', close: null, payments: [] },
  })
  const section = await open(page)
  await expect(section.getByTestId('price-build-q')).toHaveText('0.000%')
  await expect(section.getByTestId('price-build-unknown')).toContainText('Priced at q = 0')
  await expect(section.getByTestId('price-build-formula')).toHaveCount(0)
})

test('a carried yield says how old it is', async ({ page }) => {
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, source: 'carried', age_days: 6 },
  })
  const section = await open(page)
  await expect(section).toContainText('(6 days old)')
  // The yield was measured 6 days before the roll: its share basis and its
  // close are that day's, and the page names it.
  await expect(section.getByTestId('price-build-d')).toContainText(
    "S is the as-traded close on 2025-03-30, the data's last day, 6 days before 2025-04-05",
  )
  await expect(section.locator('thead')).toContainText("On 2025-03-30’s basis")
})

test('the ranking says which rows priced at an unknown q = 0', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /All 7 names/ }).click()
  await expect(page.getByTestId('q-source-eem')).toHaveText('q = 0 rolls')
  await expect(page.getByTestId('q-source-spy')).toHaveText('measured')
})

test('prints the time the premium was priced with: trading days / 252', async ({ page }) => {
  const section = await open(page)
  await expect(section.getByTestId('price-build-inputs')).toContainText('0.0794 years (trading days / 252)')
})

test('sums the split-adjusted dividends, not the cash paid', async ({ page }) => {
  // A 4:1 split after the first payment: 4.00 paid is 1.00 on today's basis.
  const basis = {
    ...BACKTEST.dividend_basis!,
    annual: 4.0,
    payments: [
      { ex_date: '2024-06-21', cash: 4.0, adjusted: 1.0 },
      { ex_date: '2024-09-20', cash: 1.0, adjusted: 1.0 },
      { ex_date: '2024-12-20', cash: 1.0, adjusted: 1.0 },
      { ex_date: '2025-03-21', cash: 1.0, adjusted: 1.0 },
    ],
  }
  await serveBacktest(page, { ...BACKTEST, dividend_basis: basis })
  const section = await open(page)
  await expect(section.getByTestId('price-build-formula')).toContainText('−ln(1 − 4.00 / 494.40)')
  // The basis column shows the adjusted 1.0000, not the 4.0000 paid.
  await expect(section.locator('tbody tr').first().locator('td').nth(1)).toHaveText('1.0000')
})

test('a short history states the scaling, so the arithmetic still closes', async ({ page }) => {
  const basis = {
    ...BACKTEST.dividend_basis!,
    source: 'short_history' as const,
    annual: 6.4,
    per_year: 4,
    payments: BACKTEST.dividend_basis!.payments.slice(0, 2).map((p) => ({ ...p, cash: 1.6, adjusted: 1.6 })),
  }
  await serveBacktest(page, { ...BACKTEST, dividend_basis: basis })
  const section = await open(page)
  await expect(section.getByTestId('price-build-formula')).toContainText('−ln(1 − 6.40 / 494.40)')
  await expect(section.getByTestId('price-build-d')).toContainText('sum to 3.20, scaled by 4/2')
})

test('on the market path q is not used and says so', async ({ page }) => {
  await serveBacktest(page, { ...BACKTEST, priced_from: 'market', dividend_basis: null })
  const section = await open(page)
  await expect(section.getByTestId('price-build-q')).toHaveText('not used: the premium is a real quote')
  await expect(section.getByTestId('price-build-dividends')).toHaveCount(0)
})

// Black–Scholes put, as research/option_pricer.py prices it -- the reader's check.
function bsPut(S: number, K: number, T: number, r: number, sigma: number, q: number): number {
  const N = (x: number) => 0.5 * (1 + erf(x / Math.SQRT2))
  const d1 = (Math.log(S / K) + (r - q + (sigma * sigma) / 2) * T) / (sigma * Math.sqrt(T))
  const d2 = d1 - sigma * Math.sqrt(T)
  return K * Math.exp(-r * T) * N(-d2) - S * Math.exp(-q * T) * N(-d1)
}
function erf(x: number): number {
  // Abramowitz-Stegun 7.1.26, |error| < 1.5e-7 -- ample for cents.
  const t = 1 / (1 + 0.3275911 * Math.abs(x))
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x)
  return x >= 0 ? y : -y
}

test('the premium shown is the Black–Scholes price of the inputs shown', async ({ page }) => {
  const last = BACKTEST.cycles[BACKTEST.cycles.length - 1]!
  const premium = bsPut(last.spot, last.strike, last.t_years!, BACKTEST.rate, last.sigma, last.q)
  await serveBacktest(page, {
    ...BACKTEST,
    cycles: [...BACKTEST.cycles.slice(0, -1), { ...last, premium }],
  })
  const section = await open(page)
  await expect(section.getByTestId('price-build-premium')).toHaveText(`$${premium.toFixed(2)} per share`)
  await expect(section.getByTestId('price-build-floored')).toHaveCount(0)
})

test('a floored premium says it is a floor, not a model price', async ({ page }) => {
  const last = BACKTEST.cycles[BACKTEST.cycles.length - 1]!
  await serveBacktest(page, {
    ...BACKTEST,
    cycles: [...BACKTEST.cycles.slice(0, -1), { ...last, premium: last.spot * 1e-4, premium_floored: true }],
  })
  const section = await open(page)
  await expect(section.getByTestId('price-build-floored')).toContainText('a floor (spot × 0.0001), not a model price')
})


test('every displayed input reprices to the displayed premium, to the cent', async ({ page }) => {
  // The section's promise: a reader can redo the price from what it shows. Read
  // each input AS DISPLAYED (rounding included) and reprice.
  const section = await open(page)
  const value = async (term: string) =>
    (await section.locator('dt', { hasText: term }).locator('xpath=following-sibling::dd[1]').innerText()).trim()
  const num = (text: string) => Number(text.replace(/[^0-9.]/g, ''))
  const spot = num(await value('Spot'))
  const strike = num((await value('Strike')).split('(')[0]!)
  const T = num((await value('Time')).split(' ')[0]!)
  const r = num(await value('Rate r')) / 100
  const sigma = num((await value('Volatility σ')).split(' ')[0]!) / 100
  const q = num(await value('Dividend yield q')) / 100
  const shown = num((await value('Premium')).split(' ')[0]!)
  // Rounding hides at most this much: the displayed premium is to the cent, so
  // half a cent is the whole budget, and every input is shown to 3 decimals.
  expect(await value('Rate r')).toBe('4.210%')
  expect(Math.abs(bsPut(spot, strike, T, r, sigma, q) - shown)).toBeLessThan(0.005)
})


test('one day of carry is "1 day", not "1 days"', async ({ page }) => {
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, source: 'carried', age_days: 1 },
  })
  const section = await open(page)
  await expect(section).toContainText('(1 day old)')
  await expect(section.getByTestId('price-build-d')).toContainText("the data's last day, 1 day before")
})

test('an unknown q names every reason it can be unknown', async ({ page }) => {
  // Not just "no data": a first-ever dividend has no frequency to read yet.
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, source: 'unknown', payments: [] },
  })
  const section = await open(page)
  const why = section.getByTestId('price-build-dividends')
  await expect(why).toContainText('no dividend data for this name')
  await expect(why).toContainText('a single dividend so far')
  await expect(why).toContainText('a dividend the close cannot support')
})

test('the dividend table is legible: dates read as dates, headers meet AA', async ({ page }) => {
  const section = await open(page)
  const rowhead = section.locator('tbody th').first()
  await expect(rowhead).toHaveCSS('text-transform', 'none')
  await expect(rowhead).toHaveCSS('font-size', '13px')
  const contrast = await section.locator('thead th').first().evaluate((el) => {
    const rgb = (c: string) => (c.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
    const lum = ([r, g, b]: number[]) => {
      const f = (v: number) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
      return 0.2126 * f(r!) + 0.7152 * f(g!) + 0.0722 * f(b!)
    }
    let bg: Element | null = el
    let back = 'rgba(0, 0, 0, 0)'
    while (bg && (back === 'rgba(0, 0, 0, 0)' || back === 'transparent')) {
      back = getComputedStyle(bg).backgroundColor
      bg = bg.parentElement
    }
    const [a, b] = [lum(rgb(getComputedStyle(el).color)), lum(rgb(back))]
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
  })
  expect(contrast).toBeGreaterThanOrEqual(4.5)
})
