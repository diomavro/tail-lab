import { expect, test, type Page } from '@playwright/test'
import type { BookPlanResponse } from '../src/api/client'
import { BOOK_PLAN, BOOK_PLAN_BILLS_REFUSED } from './fixtures/book-plan'
import { MODEL_PLAN } from './fixtures/model-plan'
import { apiRequests, mockPutLabApi } from './fixtures/mock-api'

// The Book in monthly-contributions mode (docs/adr/0027 §3). The fixture is
// the real route output on Cboe data through 2026-10-02: PPUT 50% hedged,
// $10,000 + $500/month for 10 years, against the S&P 500.

async function openPlan(page: Page, body?: BookPlanResponse) {
  await mockPutLabApi(page)
  if (body) {
    await page.route('**/api/putlab/book-plan**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    )
  }
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
}

test('lump sum stays the default and the plan is not fetched until asked', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await expect(page.getByRole('radio', { name: 'Lump sum' })).toBeChecked()
  expect(apiRequests(page)).not.toContain('/api/putlab/book-plan')
})

test('headlines the rolling-start verdict, then one window, from real data', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('plan-verdict')).toHaveText(
    'Across 364 monthly starts (1986-07-01 to 2016-10-03), each a 10-year plan, the hedged book\'s IRR led by more ' +
      'than 1bp/yr in 5%, trailed in 95% and was within 1bp in 0%. Median gap −1.48pp/yr; 10th to 90th percentile ' +
      '−2.26pp to −0.51pp; worst −3.35pp, best +1.35pp.',
  )
  await expect(page.getByTestId('plan-by-yield')).toHaveText(
    'Share of starts led, by assumed S&P dividend yield: at 1.4%, 7%; at 1.9%, 5%; at 2.4%, 2%.',
  )
  await page.getByText('The plan in numbers').click()
  const rows = page.getByRole('table').locator('tbody tr')
  await expect(rows.nth(0).locator('td')).toHaveText(['50% hedged with PPUT', '$70,000', '$163,460', '14.18%', '−23.1%'])
  await expect(rows.nth(1).locator('td')).toHaveText([
    'S&P 500 total return (1.9% assumed yield)',
    '$70,000',
    '$183,424',
    '16.06%',
    '−33.8%',
  ])
})

test('draws the verdict: the share led, the gap’s spread, and each arm against what it was paid', async ({ page }) => {
  await openPlan(page)
  const r = BOOK_PLAN.plan.rolling!
  await expect(page.getByTestId('plan-share')).toHaveText('5%')
  await expect(page.getByText(/^of 364 monthly starts, the hedged book’s 10-year IRR led/)).toBeVisible()
  // The strip labels every summary statistic it draws, from the payload.
  const strip = page.locator('.pl-plot').filter({ has: page.getByTestId('gap-strip') })
  for (const t of ['worst −3.35pp', 'median −1.48pp', 'best +1.35pp', '10th −2.26pp', '90th −0.51pp', 'parity']) {
    await expect(strip).toContainText(t)
  }
  expect(r.worst_gap).toBeLessThan(r.median_gap)
  // Two arms, ends-with in bold, and the drawdown in the loss colour's track.
  const arms = page.getByTestId('plan-arm')
  await expect(arms).toHaveCount(2)
  await expect(arms.first()).toContainText('$163,460 · IRR 14.18%')
  await expect(arms.last()).toContainText('$183,424 · IRR 16.06%')
  await expect(arms.first()).toContainText('Paid in $70,000 · worst drawdown')
})

test('the overlap caveat is computed from the span and sits above the verdict', async ({ page }) => {
  await openPlan(page)
  const caveat = page.getByTestId('overlap-caveat')
  // 364 starts, 1986-07 to 2016-10, plus the 10-year horizon: 40 years, 4 periods.
  await expect(caveat).toHaveText(
    'These 364 starts are not independent: they cover 40 years of history, about 4 separate 10-year periods, so ' +
      'these shares describe this history; they do not test a hypothesis.',
  )
  // Above the headline figure it qualifies, not merely above the collapsed numbers.
  const verdict = page.getByTestId('plan-share')
  const order = await caveat.evaluate(
    (el, v) => el.compareDocumentPosition(v) & Node.DOCUMENT_POSITION_FOLLOWING,
    await verdict.elementHandle(),
  )
  expect(order).toBeTruthy()
})

test('a shorter history says so instead of claiming four decades', async ({ page }) => {
  const short: BookPlanResponse = {
    ...BOOK_PLAN,
    plan: {
      ...BOOK_PLAN.plan,
      rolling: { ...BOOK_PLAN.plan.rolling!, n_starts: 128, first_start: '2006-03-31', last_start: '2016-10-03' },
    },
  }
  await openPlan(page, short)
  await expect(page.getByTestId('overlap-caveat')).toContainText('cover 21 years of history, about 2 separate')
})

test('the dividend assumption and the currency are stated beside the plan', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('dividend-caveat')).toContainText('assumed 1.9% dividend yield, not a measured one')
  await expect(page.getByText(/Amounts are in US dollars, the indices' currency/)).toBeVisible()
})

test('every control re-runs the plan with exactly its own input', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const sent = (k: string, v: string) =>
    page.waitForRequest((r) => {
      const u = new URL(r.url())
      return u.pathname === '/api/putlab/book-plan' && u.searchParams.get(k) === v
    })
  let next = sent('program', 'VXTH')
  await page.getByRole('radiogroup', { name: 'Hedge program' }).getByText('VXTH').click()
  await next
  next = sent('e0', '25000')
  await page.getByLabel('Start with $').fill('25000')
  await next
  next = sent('monthly', '750')
  await page.getByLabel('Every month $').fill('750')
  await next
  next = sent('hedge_ratio', '0.8')
  await page.getByRole('slider').fill('0.8')
  await next
  next = sent('comparator', 'cash')
  await page.getByRole('radiogroup', { name: 'Compare with' }).getByText('Cash').click()
  await next
})

test('typing a number sends one request, not one per keystroke', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const seen: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/putlab/book-plan?')) seen.push(new URL(r.url()).searchParams.get('e0') ?? '')
  })
  await page.getByLabel('Start with $').fill('')
  await page.getByLabel('Start with $').pressSequentially('25000', { delay: 40 })
  await expect.poll(() => seen.includes('25000')).toBe(true)
  await page.waitForTimeout(600)
  expect(seen.length).toBe(1)
})

test('a server failure reads as a failure, not as nothing to plan', async ({ page }) => {
  await mockPutLabApi(page, { fail: ['/api/putlab/book-plan'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await expect(page.locator('.pl-caveat').filter({ hasText: 'boom' })).toHaveText('boom')
  await expect(page.getByText(/Nothing to plan/)).toHaveCount(0)
})

test('the window title and provenance are the payload\'s own', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByRole('heading', { name: /^One window/ })).toHaveText('One window, 2016-10-03 to 2026-10-01')
  await expect(page.getByText(/^Cboe snapshot/)).toHaveText(
    'Cboe snapshot cboe_strategy@2026-10-02 · rates none · code e2e0000 · as of 2026-10-02',
  )
})

test('the lede names the hedge ratio and program the plan actually uses', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByText(/^Pay in /)).toContainText(
    "Pay in $10,000 now and $500 every month. One arm holds the S&P 500 with 50% of the book carrying PPUT's " +
      "hedge, its premium paid from the book at Cboe's real-quote prices",
  )
})

test('T-bills are disabled with the reason until rates exist', async ({ page }) => {
  await openPlan(page)
  const bills = page.getByRole('radio', { name: 'T-bills' })
  await expect(bills).toBeDisabled()
  await expect(page.getByTestId('comparator-refusal')).toHaveText(
    'T-bills unavailable: no T-bill history in the lake yet (rates not ingested).',
  )
})

test('a refused comparator renders its refusal instead of a verdict', async ({ page }) => {
  const refused: BookPlanResponse = {
    ...BOOK_PLAN,
    plan: { ...BOOK_PLAN.plan, comparator: 'PUT', window: null, rolling: null, by_yield: [], refusal: 'history is shorter than one 10-year plan' },
  }
  await openPlan(page, refused)
  await expect(page.getByTestId('plan-refusal')).toHaveText('history is shorter than one 10-year plan.')
  await expect(page.getByTestId('plan-verdict')).toHaveCount(0)
  await expect(page.getByTestId('plan-share')).toHaveCount(0)
  await expect(page.getByRole('table')).toHaveCount(0)
})

test('the picker lists the other Cboe indices and marks the unavailable ones', async ({ page }) => {
  const body: BookPlanResponse = {
    ...BOOK_PLAN,
    plan: {
      ...BOOK_PLAN.plan,
      comparators: BOOK_PLAN.plan.comparators.map((o) =>
        o.key === 'PUT' ? { ...o, available: true, reason: null } : o,
      ),
    },
  }
  await openPlan(page, body)
  const picker = page.getByLabel('or another Cboe index')
  // toBeDisabled() reads an <option> inside a <select> as enabled even with
  // the attribute set, so assert the attribute itself.
  await expect(picker.locator('option', { hasText: /^PUT$/ })).not.toHaveAttribute('disabled')
  await expect(picker.locator('option', { hasText: 'CLLZ' })).toHaveAttribute('disabled', '')
  await expect(picker.locator('option', { hasText: 'CLLZ' })).toContainText('(unavailable)')
})

test('changing a control re-runs the plan with that input', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const next = page.waitForRequest((r) => r.url().includes('/api/putlab/book-plan') && r.url().includes('horizon_years=20'))
  await page.getByRole('radiogroup', { name: 'Horizon' }).getByText('20y').click()
  await next
})

test('an empty lake reads as nothing to plan, not as a verdict', async ({ page }) => {
  await mockPutLabApi(page, { noData: ['/api/putlab/book-plan'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await expect(page.getByText('Nothing to plan: no data.')).toBeVisible()
})

test('the real-quote plan names its accounting and why stocks are not offered', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('accounting')).toHaveText(
    "Accounting: self-financed — the hedge's premium is paid from the book (docs/adr/0027).",
  )
  await expect(page.getByTestId('no-stocks')).toContainText('split-adjusted prices only')
})

async function openModel(page: Page) {
  await openPlan(page)
  await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
}

test('the model source states its measured error above the numbers', async ({ page }) => {
  await openModel(page)
  const caveat = page.getByTestId('model-accuracy')
  await expect(caveat).toHaveText(MODEL_PLAN.plan.accuracy.statement)
  await expect(caveat).toContainText('+2.2 vol points')
  await expect(caveat).toContainText('which flatters the puts')
  const verdict = page.getByTestId('plan-share')
  await expect(verdict).toBeVisible()
  const order = await caveat.evaluate(
    (el, v) => el.compareDocumentPosition(v) & Node.DOCUMENT_POSITION_FOLLOWING,
    await verdict.elementHandle(),
  )
  expect(order).toBeTruthy()
  // Loss ink, not body ink.
  await expect(caveat).toHaveClass(/pl-caveat/)
})

test('the model source shows its contribution-funded accounting and real verdict', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('accounting')).toHaveText(
    "Accounting: contribution-funded — the starting $10,000 buys the S&P 500; all of each month's $500 buys S&P " +
      '500 puts 5% below spot; payoffs are reinvested in it. The comparator puts the same cash into the S&P 500 ' +
      "only. Amounts are in US dollars (a euro investor's result also carries the EUR/USD move); the S&P 500 " +
      'earns an assumed 1.9% dividend yield.',
  )
  await expect(page.getByTestId('plan-verdict')).toContainText(
    'Across 321 monthly starts (1990-01-19 to 2016-09-16), each a 10-year plan, the hedged book\'s IRR led by more ' +
      'than 1bp/yr in 7%, trailed in 93%',
  )
  await page.getByText('The plan in numbers').click()
  const rows = page.getByRole('table').locator('tbody tr')
  await expect(rows.nth(0).locator('td')).toHaveText([
    'S&P 500 + 100% of each month on puts (model)',
    '$70,000',
    '$195,368',
    '17.07%',
    '−47.3%',
  ])
  await expect(page.getByText(/^Cboe snapshot .* · VIX vix@2026-10-02/)).toBeVisible()
})

test('picking the other measured depth re-prices at 10% OTM', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const next = page.waitForRequest(
    (r) => r.url().includes('/api/putlab/book-plan/model') && r.url().includes('moneyness_pct=10'),
  )
  await page.getByRole('radiogroup', { name: 'Strike depth' }).getByText('10% OTM').click()
  await next
})

test('a missing VIX reads as nothing to price', async ({ page }) => {
  await mockPutLabApi(page, { noData: ['/api/putlab/book-plan/model'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
  await expect(page.getByText('Nothing to price: no data.')).toBeVisible()
})

// ------------------------------------------------------------ round-2 review

async function openModelWith(page: Page, body: unknown) {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/book-plan/model**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
}

test('every model control re-runs the plan with exactly its own input', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const sent = (k: string, v: string) =>
    page.waitForRequest((r) => {
      const u = new URL(r.url())
      return u.pathname === '/api/putlab/book-plan/model' && u.searchParams.get(k) === v
    })
  let next = sent('moneyness_pct', '10')
  await page.getByRole('radiogroup', { name: 'Strike depth' }).getByText('10% OTM').click()
  await next
  next = sent('put_share', '0.5')
  await page.getByRole('slider').fill('0.5')
  await next
  next = sent('e0', '25000')
  await page.getByLabel('Start with $').fill('25000')
  await next
  next = sent('monthly', '750')
  await page.getByLabel('Every month $').fill('750')
  await next
  next = sent('horizon_years', '5')
  await page.getByRole('radiogroup', { name: 'Horizon' }).getByText('5y', { exact: true }).click()
  await next
})

test('the model view debounces typing into one request', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const seen: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/putlab/book-plan/model?')) seen.push(new URL(r.url()).searchParams.get('monthly') ?? '')
  })
  await page.getByLabel('Every month $').fill('')
  await page.getByLabel('Every month $').pressSequentially('750', { delay: 40 })
  await expect.poll(() => seen.includes('750')).toBe(true)
  await page.waitForTimeout(600)
  expect(seen).toEqual(['750'])
})

test('the model view shows its refusal, and a 500 as a failure', async ({ page }) => {
  await openModelWith(page, {
    ...MODEL_PLAN,
    plan: { ...MODEL_PLAN.plan, window: null, rolling: null, refusal: 'history is shorter than one 10-year plan' },
  })
  await expect(page.getByTestId('plan-refusal')).toHaveText('history is shorter than one 10-year plan.')
})

test('a model server failure reads as a failure, not as nothing to price', async ({ page }) => {
  await mockPutLabApi(page, { fail: ['/api/putlab/book-plan/model'] })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
  await expect(page.locator('.pl-caveat').filter({ hasText: 'boom' })).toHaveText('boom')
  await expect(page.getByText(/Nothing to price/)).toHaveCount(0)
})

test('the model accounting line reads the yield from the payload', async ({ page }) => {
  await openModelWith(page, { ...MODEL_PLAN, plan: { ...MODEL_PLAN.plan, dividend_yield: 0.024 } })
  await expect(page.getByTestId('accounting')).toContainText('assumed 2.4% dividend yield')
})

test('one start and one period read in the singular, and periods are counted down', async ({ page }) => {
  const one = {
    ...BOOK_PLAN,
    plan: {
      ...BOOK_PLAN.plan,
      rolling: { ...BOOK_PLAN.plan.rolling!, n_starts: 1, first_start: '2016-10-03', last_start: '2016-10-03' },
    },
  }
  await openPlan(page, one as BookPlanResponse)
  await expect(page.getByTestId('overlap-caveat')).toHaveText(
    'This one start covers 10 years of history, about 1 separate 10-year period, so these shares describe ' +
      'this history; they do not test a hypothesis.',
  )
  await expect(page.getByTestId('plan-verdict')).toContainText('Across 1 monthly start (2016-10-03), each a 10-year')
})

test('nineteen and a half years hold one ten-year period, not two', async ({ page }) => {
  const body = {
    ...BOOK_PLAN,
    plan: {
      ...BOOK_PLAN.plan,
      rolling: { ...BOOK_PLAN.plan.rolling!, n_starts: 115, first_start: '2006-01-03', last_start: '2015-07-01' },
    },
  }
  await openPlan(page, body as BookPlanResponse)
  await expect(page.getByTestId('overlap-caveat')).toContainText('about 1 separate 10-year period,')
})

test('shares never round to a false 0% or 100%, and a zero gap reads +0.00', async ({ page }) => {
  const body = {
    ...BOOK_PLAN,
    plan: {
      ...BOOK_PLAN.plan,
      rolling: {
        ...BOOK_PLAN.plan.rolling!,
        share_ahead: 0.002,
        share_behind: 0.998,
        share_inconclusive: 0,
        median_gap: 0,
      },
    },
  }
  await openPlan(page, body as BookPlanResponse)
  const verdict = page.getByTestId('plan-verdict')
  await expect(verdict).toContainText('led by more than 1bp/yr in <1%, trailed in >99%')
  await expect(verdict).toContainText('Median gap +0.00pp/yr')
})

test('a window missing beside a rolling summary renders nothing rather than crashing', async ({ page }) => {
  await openPlan(page, { ...BOOK_PLAN, plan: { ...BOOK_PLAN.plan, window: null } } as BookPlanResponse)
  await expect(page.getByTestId('plan-verdict')).toHaveCount(0)
  await expect(page.getByTestId('plan-share')).toHaveCount(0)
  await expect(page.getByTestId('accounting')).toBeVisible()
})

test('a refused T-bills plan says so once', async ({ page }) => {
  await openPlan(page, BOOK_PLAN_BILLS_REFUSED)
  await expect(page.getByTestId('comparator-refusal')).toHaveCount(1)
  await expect(page.getByTestId('plan-refusal')).toHaveCount(0)
})

test('the currency is stated in full', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByText(/^Pay in /)).toContainText(
    "Amounts are in US dollars, the indices' currency; a euro investor's result also carries the EUR/USD move.",
  )
})

test('the page says it is updating while a new plan is pending', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  await page.getByLabel('Every month $').fill('900')
  await expect(page.getByTestId('updating')).toBeVisible()
})

test('a too-large amount shows the reason, not a bare status', async ({ page }) => {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/book-plan?**', (route) =>
    route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ detail: [{ msg: 'Input should be less than or equal to 1000000000' }] }),
    }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await expect(page.getByText('Input should be less than or equal to 1000000000')).toBeVisible()
})

for (const source of ['Real quotes', 'Our model'] as const) {
  test(`no sideways scroll on a phone in monthly mode (${source})`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await mockPutLabApi(page)
    await page.goto('/')
    await page.getByRole('tab', { name: 'Book' }).click()
    await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
    await page.getByRole('radiogroup', { name: 'Priced from' }).getByText(source).click()
    await expect(page.getByTestId('plan-share')).toBeVisible()
    const width = await page.evaluate(() => document.documentElement.scrollWidth)
    expect(width).toBeLessThanOrEqual(375)
  })
}

test('the Book hides the trailing-RV pricing badge, which describes other tabs', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await expect(page.getByText('Model-priced · Black–Scholes on trailing RV')).toBeVisible()
  await page.getByRole('tab', { name: 'Book' }).click()
  await expect(page.getByText('Model-priced · Black–Scholes on trailing RV')).toHaveCount(0)
})

// ------------------------------------------------------------ round-3 review

test('the model view says it is updating while a new plan is pending', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  await page.getByLabel('Every month $').fill('900')
  await expect(page.getByTestId('updating')).toBeVisible()
})

for (const source of ['Real quotes', 'Our model'] as const) {
  test(`a typed amount past the API's limit is clamped, not sent (${source})`, async ({ page }) => {
    await openPlan(page)
    if (source === 'Our model') await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
    await expect(page.getByTestId('plan-share')).toBeVisible()
    const path = source === 'Our model' ? '/api/putlab/book-plan/model' : '/api/putlab/book-plan'
    const sent = page.waitForRequest((r) => {
      const u = new URL(r.url())
      return u.pathname === path && u.searchParams.get('monthly') === '1000000000'
    })
    await page.getByLabel('Every month $').fill('5000000000')
    await sent
    await expect(page.getByLabel('Every month $')).toHaveValue('1000000000')
  })
}

test('a refused field is named in the message', async ({ page }) => {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/book-plan/model**', (route) =>
    route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ detail: [{ loc: ['query', 'monthly'], msg: 'Input should be greater than 0' }] }),
    }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
  await expect(page.getByText('monthly: Input should be greater than 0')).toBeVisible()
})

test('199 of 200 starts is never shown as 100%', async ({ page }) => {
  const body = {
    ...BOOK_PLAN,
    plan: {
      ...BOOK_PLAN.plan,
      rolling: { ...BOOK_PLAN.plan.rolling!, share_ahead: 0.995, share_behind: 0.005, share_inconclusive: 0 },
    },
  }
  await openPlan(page, body as BookPlanResponse)
  await expect(page.getByTestId('plan-verdict')).toContainText('led by more than 1bp/yr in >99%, trailed in 1%')
})

test('the model accounting sentence follows the share and the depth', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  await page.getByRole('slider').fill('0.35')
  await page.getByRole('radiogroup', { name: 'Strike depth' }).getByText('10% OTM').click()
  await expect(page.getByTestId('accounting')).toContainText(
    "35% of each month's $500 buys S&P 500 puts 10% below spot, the rest buys the S&P 500;",
  )
})

test('the model page says its shares are not evidence for the criterion, and the FX caveat', async ({ page }) => {
  await openModel(page)
  await expect(page.getByTestId('model-not-evidence')).toContainText(
    'not evidence for the success criterion (docs/adr/0004, docs/adr/0027)',
  )
  await expect(page.getByTestId('accounting')).toContainText("a euro investor's result also carries the EUR/USD move")
  await expect(page.getByTestId('model-accuracy')).toContainText('most so in the zero-rate years')
})

test('the trailing-RV badge and footer appear only on tabs that run that backtester', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  const badge = page.getByText('Model-priced · Black–Scholes on trailing RV')
  const footer = page.locator('footer.pl-footer')
  for (const tab of ['Recommendations', 'Portfolio', 'Bake-off', 'Workspace'] as const) {
    await page.getByRole('tab', { name: tab }).click()
    await expect(badge).toBeVisible()
    await expect(footer).toContainText('trailing realized volatility')
  }
  for (const tab of ['Surface', 'Regime', 'Book'] as const) {
    await page.getByRole('tab', { name: tab }).click()
    await expect(badge).toHaveCount(0)
    await expect(footer).not.toContainText('trailing realized')
    await expect(footer).toContainText('Each result on this tab states how it was priced')
  }
  // The Glossary prices nothing, so its footer promises nothing about pricing.
  await page.getByRole('tab', { name: 'Glossary' }).click()
  await expect(badge).toHaveCount(0)
  await expect(footer).not.toContainText('priced')
  await expect(footer).toContainText('tail-lab never trades')
})

test('a typed starting amount past the limit is clamped too', async ({ page }) => {
  await openPlan(page)
  await expect(page.getByTestId('plan-share')).toBeVisible()
  const sent = page.waitForRequest((r) => new URL(r.url()).searchParams.get('e0') === '1000000000')
  await page.getByLabel('Start with $').fill('5000000000')
  await sent
})

test('a history refusal on an available button comparator is shown, not swallowed', async ({ page }) => {
  await openPlan(page, {
    ...BOOK_PLAN,
    plan: { ...BOOK_PLAN.plan, window: null, rolling: null, by_yield: [], refusal: 'history is shorter than one 20-year plan' },
  })
  await expect(page.getByTestId('plan-refusal')).toHaveText('history is shorter than one 20-year plan.')
})

test('a drawdown past 60% widens the scale rather than filling the bar', async ({ page }) => {
  const w = BOOK_PLAN.plan.window!
  await openPlan(page, {
    ...BOOK_PLAN,
    plan: { ...BOOK_PLAN.plan, window: { ...w, hedged: { ...w.hedged, max_drawdown: -0.85 }, comparator: { ...w.comparator, max_drawdown: -0.6 } } },
  })
  const widths = await page.locator('.pl-arm-dd-track > span').evaluateAll((els) => els.map((e) => (e as HTMLElement).style.width))
  expect(widths[0]).toBe('100%')
  expect(parseFloat(widths[1]!)).toBeCloseTo((0.6 / 0.85) * 100, 1)
})

test('gap-strip labels never overprint, even when worst sits by the median', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const r = BOOK_PLAN.plan.rolling!
  await openPlan(page, { ...BOOK_PLAN, plan: { ...BOOK_PLAN.plan, rolling: { ...r, worst_gap: -0.03, median_gap: -0.029, p10_gap: -0.0295, p90_gap: -0.028 } } })
  const boxes = await page
    .locator('.pl-plot')
    .filter({ has: page.getByTestId('gap-strip') })
    .locator('.pl-chart-label')
    .evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((b) => [b.left, b.top, b.right, b.bottom]))
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const [a, b] = [boxes[i]!, boxes[j]!]
      const overlap = a[0]! < b[2]! && b[0]! < a[2]! && a[1]! < b[3]! && b[1]! < a[3]!
      expect(overlap, `labels ${i} and ${j}`).toBe(false)
    }
  }
})

// ------------------------------------------------------------- geometry

test('the stacked bar is drawn at the shares it labels', async ({ page }) => {
  await openPlan(page)
  const r = BOOK_PLAN.plan.rolling!
  const w = await page.locator('.pl-plan-stack > span').evaluateAll((els) => els.map((e) => parseFloat((e as HTMLElement).style.width)))
  expect(w[0]).toBeCloseTo(r.share_ahead * 100, 3)
  expect(w[1]).toBeCloseTo(r.share_inconclusive * 100, 3)
  expect(w[2]).toBeCloseTo(r.share_behind * 100, 3)
})

test('the gap box spans the 10th to 90th percentile, inside the worst-to-best whisker', async ({ page }) => {
  await openPlan(page)
  const r = BOOK_PLAN.plan.rolling!
  const g = await page.getByTestId('gap-strip').evaluate((svg) => {
    const rect = svg.querySelector('.pl-c-box')!
    const xs = [...svg.querySelector('.pl-c-whisker')!.getAttribute('d')!.matchAll(/M([\d.]+),/g)].map((m) => Number(m[1]))
    return { x: Number(rect.getAttribute('x')), w: Number(rect.getAttribute('width')), lo: Math.min(...xs), hi: Math.max(...xs) }
  })
  expect(g.w / (g.hi - g.lo)).toBeCloseTo((r.p90_gap - r.p10_gap) / (r.best_gap - r.worst_gap), 2)
  expect((g.x - g.lo) / (g.hi - g.lo)).toBeCloseTo((r.p10_gap - r.worst_gap) / (r.best_gap - r.worst_gap), 2)
})

test('each arm is drawn to what it ended with, with the paid-in tick at what was paid', async ({ page }) => {
  await openPlan(page)
  const w = BOOK_PLAN.plan.window!
  const fills = await page.locator('.pl-arm-fill').evaluateAll((els) => els.map((e) => parseFloat((e as HTMLElement).style.width)))
  expect(fills[0]! / fills[1]!).toBeCloseTo(w.hedged.terminal_wealth / w.comparator.terminal_wealth, 3)
  const ticks = await page.getByTestId('arm-paid').evaluateAll((els) => els.map((e) => parseFloat((e as HTMLElement).style.left)))
  expect(ticks[0]! / fills[0]!).toBeCloseTo(w.hedged.contributed / w.hedged.terminal_wealth, 3)
})

test('an arm that ended below what was paid in shows the tick in ink, past its fill', async ({ page }) => {
  const w = BOOK_PLAN.plan.window!
  await openPlan(page, { ...BOOK_PLAN, plan: { ...BOOK_PLAN.plan, window: { ...w, hedged: { ...w.hedged, terminal_wealth: 50_000 } } } })
  await expect(page.getByTestId('arm-paid').first()).toHaveClass(/is-beyond/)
  await expect(page.getByTestId('arm-paid').last()).not.toHaveClass(/is-beyond/)
})

test('by-yield bars are drawn in proportion to the shares they print', async ({ page }) => {
  await openPlan(page)
  const by = BOOK_PLAN.plan.by_yield
  const w = await page.locator('.pl-plan-yield-track > span').evaluateAll((els) => els.map((e) => parseFloat((e as HTMLElement).style.width)))
  const scale = Math.max(0.1, ...by.map((y) => y.share_ahead))
  by.forEach((y, k) => expect(w[k]).toBeCloseTo((y.share_ahead / scale) * 100, 3))
})

test('on a phone the gap strip uses its narrow frame', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openPlan(page)
  await expect(page.getByTestId('gap-strip')).toHaveAttribute('viewBox', /^0 0 360 /)
})
