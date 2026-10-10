import { expect, test, type Page } from '@playwright/test'
import type { PutBacktestResponse } from '../src/api/client'
import { BACKTEST, LEADERBOARD } from './fixtures/putlab'
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
    'q = −ln(1 − D / S) = −ln(1 − 6.42 / 494.4) = 1.307%',
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
  await expect(section).toContainText("(as of the data's last day, 6 days earlier)")
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
  // ...in the warning style, not the muted one a measured row wears.
  await expect(page.getByTestId('q-source-eem').locator('span')).toHaveClass(/pl-tag-bad/)
  await expect(page.getByTestId('q-source-spy')).toHaveText('measured')
})

test('a ranking row with no dividend basis (a real-quote run) says nothing about q', async ({ page }) => {
  await page.route(
    (url) => url.pathname === '/api/putlab/leaderboard',
    (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...LEADERBOARD,
          ranked: LEADERBOARD.ranked.map((r) => (r.asset === 'spy' ? { ...r, q_source: null } : r)),
        }),
      }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: /All 7 names/ }).click()
  await expect(page.getByTestId('q-source-spy')).toHaveText('—')
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
  await expect(section.getByTestId('price-build-formula')).toContainText('−ln(1 − 4 / 494.4)')
  // The basis column shows the adjusted 1, not the 4 paid.
  await expect(section.locator('tbody tr').first().locator('td').nth(1)).toHaveText('1')
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
  await expect(section.getByTestId('price-build-formula')).toContainText('−ln(1 − 6.4 / 494.4)')
  await expect(section.getByTestId('price-build-d')).toContainText('sum to 3.2, scaled by 4/2')
})

test('on the market path q is not used and says so', async ({ page }) => {
  await serveBacktest(page, { ...BACKTEST, priced_from: 'market', dividend_basis: null })
  const section = await open(page)
  await expect(section.getByTestId('price-build-q')).toHaveText('not used: the premium is a real quote')
  await expect(section.getByTestId('price-build-dividends')).toHaveCount(0)
  // Nothing on the market path claims a model price or a trailing yield.
  await expect(section.getByTestId('price-build-market')).toContainText('real listed ask, not a model price')
  await expect(section).not.toContainText('Every roll is priced as a Black')
  await expect(section).not.toContainText('What a trailing yield cannot see')
  await expect(section).not.toContainText('a model-priced backtest like this one')
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
  // half a cent is the whole budget; the rates are shown to 3 decimals, spot and
  // strike to the cent, T to 4 decimals.
  expect(await value('Rate r')).toBe('4.210%')
  expect(Math.abs(bsPut(spot, strike, T, r, sigma, q) - shown)).toBeLessThan(0.005)
})


test('one day of carry is "1 day", not "1 days"', async ({ page }) => {
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, source: 'carried', age_days: 1 },
  })
  const section = await open(page)
  await expect(section).toContainText("(as of the data's last day, 1 day earlier)")
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

for (const sheet of ['paper', 'plate'] as const) {
  test(`the dividend table is legible on the ${sheet} sheet: dates read as dates, every cell meets AA`, async ({
    page,
  }) => {
    await page.addInitScript((s) => localStorage.setItem('putlab.sheet', s), sheet)
    const section = await open(page)
    await expect(page.locator('.putlab-root')).toHaveAttribute('data-theme', sheet)
    const rowhead = section.locator('tbody th').first()
    const cell = section.locator('tbody td').first()
    await expect(rowhead).toHaveCSS('text-transform', 'none')
    await expect(rowhead).toHaveCSS('font-size', '13px')
    await expect(rowhead).toHaveCSS('font-weight', '400')
    for (const target of [
      section.locator('thead th').first(),
      rowhead,
      cell,
      section.getByTestId('price-build-formula'),
      section.locator('summary'),
    ]) {
      const contrast = await target.evaluate((el) => {
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
    }
  })
}

test('a real zero is not the unknown zero: no under-pricing warning, and each says why', async ({ page }) => {
  for (const [source, why] of [
    ['non_payer', 'never paid a dividend'],
    ['suspended', 'stopped paying (its last dividend is more than two periods old)'],
  ] as const) {
    await serveBacktest(page, {
      ...BACKTEST,
      dividend_basis: { ...BACKTEST.dividend_basis!, q: 0, source, payments: [], close: null },
    })
    const section = await open(page)
    await expect(section.getByTestId('price-build-dividends')).toContainText(why)
    await expect(section.getByTestId('price-build-unknown')).toHaveCount(0)
  }
})

test('a stale yield says how to refresh it; a current one claims no age', async ({ page }) => {
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, source: 'stale', age_days: 30 },
  })
  let section = await open(page)
  await expect(section.getByTestId('price-build-dividends')).toContainText(
    "carried more than three weeks past the data's last day — refresh Tiingo (as of the data's last day, 30 days earlier)",
  )
  await serveBacktest(page, BACKTEST)
  section = await open(page)
  await expect(section.getByTestId('price-build-dividends')).not.toContainText('earlier)')
})

test('the formula reproduces q to its last printed digit, even on a low-priced name', async ({ page }) => {
  // EEM-like: D = 1.04003, S = 43.12. Any fixed rounding of D printed a sum
  // that gave 2.441% beside a printed 2.442%.
  const D = 1.04003
  const S = 43.12
  const q = -Math.log(1 - D / S)
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, annual: D, close: S, q },
  })
  const section = await open(page)
  const text = await section.getByTestId('price-build-formula').innerText()
  const [, d, s, shown] = text.match(/−ln\(1 − ([\d.]+) \/ ([\d.]+)\) = ([\d.]+)%/)!
  expect(((-Math.log(1 - Number(d) / Number(s))) * 100).toFixed(3)).toBe(shown)
})

test('the payments add up to the D the formula uses, after a split', async ({ page }) => {
  // Four 0.37 payments before a 3:1 split: each 0.1233... on today's basis.
  const payments = BACKTEST.dividend_basis!.payments.map((p) => ({ ...p, cash: 0.37, adjusted: 0.37 / 3 }))
  const annual = payments.reduce((t, p) => t + p.adjusted, 0)
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, payments, annual, close: 160, q: -Math.log(1 - annual / 160) },
  })
  const section = await open(page)
  const cells = await section.locator('tbody tr td:nth-child(3)').allInnerTexts()
  expect(cells).toEqual(Array(4).fill('0.123333'))
  // Rounded rows say so, and sum to D within their rounding (4 x 5e-7).
  await expect(section.locator('thead')).toContainText('(6 dp)')
  const d = (await section.getByTestId('price-build-formula').innerText()).match(/1 − ([\d.]+) \//)![1]!
  expect(Math.abs(cells.reduce((t, c) => t + Number(c), 0) - Number(d))).toBeLessThanOrEqual(4 * 5e-7)
})

test('a current yield names the day its share basis and close are from', async ({ page }) => {
  const section = await open(page)
  const day = BACKTEST.dividend_basis!.date
  await expect(section.getByTestId('price-build-d')).toContainText(`each on the share basis of ${day}`)
  await expect(section.getByTestId('price-build-d')).toContainText(`S is the as-traded close on ${day}.`)
})

test('the derivation paragraphs are spaced like the rest of the section', async ({ page }) => {
  const section = await open(page)
  const gap = await section.getByTestId('price-build-formula').evaluate((el) => getComputedStyle(el).marginTop)
  // The same gap as between the section's own top-level blocks.
  const sectionGap = await section.locator('> p').first().evaluate((el) => getComputedStyle(el).marginTop)
  expect(parseFloat(sectionGap)).toBeGreaterThan(0)
  expect(gap).toBe(sectionGap)
})

test('the ex-date caveat gives both sides of the bias', async ({ page }) => {
  const section = await open(page)
  const caveat = section.locator('.pl-caveat').first()
  await expect(caveat).toContainText('under-priced')
  await expect(caveat).toContainText('expects the stock ~0.2% of spot too high and is under-priced')
  await expect(caveat).toContainText('~0.1% too low and is over-priced')
  // The shifts are in the expected stock price, not the premium.
  await expect(caveat).toContainText("those shifts times the put’s delta")
})

test('the "(6 dp)" mark appears only when a row is rounded, even if just one is', async ({ page }) => {
  // No split: every adjusted figure is the cash paid, nothing rounded.
  let section = await open(page)
  await expect(section.locator('thead')).not.toContainText('(6 dp)')
  // A split inside the window: earlier rows divided (0.37 / 3), later exact.
  const payments = BACKTEST.dividend_basis!.payments.map((p, i) =>
    i < 2 ? { ...p, cash: 0.37, adjusted: 0.37 / 3 } : { ...p, cash: 0.13, adjusted: 0.13 },
  )
  const annual = payments.reduce((t, p) => t + p.adjusted, 0)
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: { ...BACKTEST.dividend_basis!, payments, annual, close: 160, q: -Math.log(1 - annual / 160) },
  })
  section = await open(page)
  await expect(section.locator('thead')).toContainText('(6 dp)')
})

test('a short history after a split states its sum exactly', async ({ page }) => {
  const payments = BACKTEST.dividend_basis!.payments.slice(0, 2).map((p) => ({ ...p, cash: 0.37, adjusted: 0.37 / 3 }))
  const total = (0.37 / 3) * 2
  await serveBacktest(page, {
    ...BACKTEST,
    dividend_basis: {
      ...BACKTEST.dividend_basis!,
      source: 'short_history',
      payments,
      annual: total * 2,
      close: 160,
      q: -Math.log(1 - (total * 2) / 160),
    },
  })
  const section = await open(page)
  await expect(section.getByTestId('price-build-d')).toContainText(`sum to ${String(Number(total.toPrecision(10)))}, scaled`)
})
