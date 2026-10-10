import { expect, test, type Page } from '@playwright/test'
import type { BookPlanResponse, HedgeOverlayResponse, OverlayWindow } from '../src/api/client'
import { CONCEPTS } from '../src/content/concepts'
import { BOOK_PLAN_MEASURED, bookPlanMeasuredWithoutRates } from './fixtures/book-plan'
import { HEDGE_OVERLAY, HEDGE_OVERLAY_MEASURED, hedgeOverlayWithoutRates } from './fixtures/hedge-overlay'
import { mockPutLabApi } from './fixtures/mock-api'

// "How much to hold" (research/backtest/hedge_sizing): the Book's size for
// each program and window, including "none". HEDGE_OVERLAY_MEASURED is the
// real result on SPY's measured total return, so these tests pin what a reader
// sees once Tiingo is ingested -- the put programs earn no place, VXTH's
// letter window earns half of its full-Kelly w* -- and the other two payloads
// reach the degraded inputs that withhold a size.

async function openBook(page: Page, body: HedgeOverlayResponse) {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/hedge-overlay', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
}

const hold = (page: Page) => page.getByRole('region', { name: 'How much to hold' })
const rows = (page: Page) => hold(page).getByTestId('hold-row')
const [PPUT, , VXTH] = HEDGE_OVERLAY_MEASURED.overlay.programs
const VXTH_COLE = VXTH!.windows[0]!

test('real data: the table shows full-Kelly w* beside the size held, and why', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  await expect(rows(page)).toHaveCount(6)
  await expect(hold(page).getByTestId('hold-table').locator('thead th')).toHaveText([
    'Program',
    'Window',
    'Full-Kelly w*',
    'Hold',
    'Why',
  ])
  // The put programs: none, because growth falls from the first step.
  await expect(rows(page).nth(0).locator('td')).toHaveText([
    'PPUT',
    "letter's window",
    '0%',
    'none (0%)',
    'growth falls at every step of the hedge ratio: no hedge grows fastest',
  ])
  // VXTH in the letter's window: an interior w*, held at half, the haircut named.
  await expect(rows(page).nth(4).locator('td')).toHaveText([
    'VXTH',
    "letter's window",
    '24.2%',
    '12.1%',
    'half of the full-Kelly w*: one history estimates w*, and full Kelly is sensitive to that error',
  ])
  // VXTH over its full history: a w* exists, but its edge is under the bar on both legs.
  await expect(rows(page).nth(5).locator('td')).toHaveText([
    'VXTH',
    'full history',
    '5.9%',
    'none (0%)',
    'margin +0.69bp/yr on the base leg, +0.61bp/yr after the cash-drag add-back: not above the 1bp/yr bar',
  ])
})

test('every size in the table is the payload own, row for row', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const windows = HEDGE_OVERLAY_MEASURED.overlay.programs.flatMap((p) => p.windows)
  for (let k = 0; k < windows.length; k++) {
    const s = windows[k]!.sizing
    const want = s.recommended_ratio === 0 ? 'none (0%)' : `${Math.round(s.recommended_ratio! * 1000) / 10}%`
    await expect(rows(page).nth(k).locator('td').nth(3)).toHaveText(want)
    await expect(rows(page).nth(k).locator('td').nth(4)).toHaveText(s.reason)
  }
})

test('the caveat sits above the table and says the put programs earn no place', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const caveat = hold(page).getByTestId('hold-caveat')
  await expect(caveat).toContainText('In sample, one history')
  await expect(caveat).toContainText('a size is half of it')
  await expect(caveat).toContainText('The put programs PPUT and PPUT3M currently earn no place in the book: hold none.')
  const order = await caveat.evaluate(
    (el, t) => el.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING,
    await hold(page).getByTestId('hold-table').elementHandle(),
  )
  expect(order).toBeTruthy()
  await expect(hold(page).getByTestId('hold-accounting')).toHaveText(
    'Self-financed: the hedge’s premium is paid from the book, as in the verdicts above (docs/adr/0027).',
  )
  await expect(hold(page).getByTestId('hold-method')).toContainText('found by golden-section search')
  await expect(hold(page).getByTestId('hold-method')).toContainText(
    'It sizes Cboe’s fixed-strike programs, not a Workspace strategy',
  )
  await expect(hold(page).getByTestId('hold-method')).toContainText('One of six program-windows carries a size.')
  await expect(hold(page).getByTestId('hold-not-prefilled')).toContainText('Not pre-filled into the monthly plan')
})

test('each program-window draws its growth curve with the markers its answer has', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const charts = hold(page).getByTestId('hold-chart')
  await expect(charts).toHaveCount(6)
  // PPUT, letter's window: w* = 0, so only the no-hedge mark.
  const pput = charts.nth(0)
  await expect(pput.getByTestId('hold-mark-zero')).toHaveCount(1)
  await expect(pput.getByTestId('hold-mark-star')).toHaveCount(0)
  // VXTH, letter's window: 0, w*/2, w* and the break-even past it.
  const vxth = charts.nth(4)
  for (const m of ['zero', 'half', 'star', 'even']) await expect(vxth.getByTestId(`hold-mark-${m}`)).toHaveCount(1)
  // The marks sit where the payload says, left to right: 0 < w*/2 < w* < break-even.
  const xs = await vxth.evaluate((svg) =>
    ['zero', 'half', 'star', 'even'].map((m) => {
      const d = svg.querySelector(`[data-testid="hold-mark-${m}"]`)!.getAttribute('d')!
      return Number(/^M([\d.]+)/.exec(d)![1])
    }),
  )
  expect(xs).toEqual([...xs].sort((a, b) => a - b))
  const s = VXTH_COLE.sizing
  // x = 46 + w * 244, the mark's left edge one radius short of it.
  expect(xs[2]! + 4.5).toBeCloseTo(46 + s.w_star * 244, 0)
  expect(xs[3]! + 3.2).toBeCloseTo(46 + s.break_even! * 244, 0)
})

test('the sizing in numbers: growth at each marker, both legs, stability by month', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  await hold(page).getByText('The sizing in numbers').click()
  const numbers = hold(page).getByTestId('hold-number')
  await expect(numbers).toHaveCount(6)
  const vxth = numbers.nth(4)
  await expect(vxth).toContainText(
    'Growth (CAGR) with no hedge 6.983%; at full-Kelly w* = 24.2%, 7.025% (+4.20bp/yr over no hedge); at w*/2, 7.015% (+3.16bp/yr) — half keeps 75% of the gain over no hedge',
  )
  await expect(vxth).toContainText('Gain over no hedge at w*, by leg: base +4.20bp/yr, conservative +3.96bp/yr.')
  await expect(vxth).toContainText('Break-even ratio: 49%.')
  await expect(vxth).toContainText('Naive (mean-variance) Kelly: 21.2%')
  // Leave-one-month-out names the month the answer rests on.
  await expect(vxth.getByTestId('hold-stability')).toContainText(
    'Leaving out one month at a time and re-solving, the months that move w* most: 2015-09 (to 100%, +75.8pp), 2009-11 (to 57.2%, +33.0pp), 2007-07 (to 0%, −24.2pp).',
  )
  // Where w* is 0 throughout, one line says so.
  await expect(numbers.nth(1).getByTestId('hold-stability')).toHaveText(
    'w* is 0 over the whole window and in both halves; no single month moves it.',
  )
  // ...but PPUT's letter window is 0 overall with an earlier half that was not: shown, not hidden.
  await expect(numbers.nth(0).getByTestId('hold-stability')).toContainText('w* is 16.6% in 2005-01 to 2010-07')
})

test('the fallback withholds every size and names why', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY)
  await expect(rows(page)).toHaveCount(6)
  for (let k = 0; k < 6; k++) {
    await expect(rows(page).nth(k).locator('td').nth(3)).toHaveText('withheld')
    await expect(rows(page).nth(k).locator('td').nth(4)).toHaveText('sizing needs measured dividends')
  }
  await expect(hold(page).getByTestId('hold-caveat')).toContainText(
    'No size is recommended here: sizing needs measured dividends.',
  )
})

test('without T-bills the size is withheld, never gated on one leg', async ({ page }) => {
  await openBook(page, hedgeOverlayWithoutRates())
  await expect(rows(page).nth(4).locator('td').nth(3)).toHaveText('withheld')
  await expect(rows(page).nth(4).locator('td').nth(4)).toHaveText('sizing needs T-bills for the cash-drag check')
  // The full-Kelly reading stays visible.
  await expect(rows(page).nth(4).locator('td').nth(2)).toHaveText('24.2%')
  const sens = page.getByRole('region', { name: /^VXTH The letter's window/ }).getByTestId('sensitivity')
  await expect(sens).toHaveText(
    'Best interior mix vs the better end, by index leg: base leg, paradox holds (best mix 20% hedged, +0.041pp/yr).',
  )
})

test('measured: each verdict is read on both legs, and the page names its leg', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const panel = page.getByRole('region', { name: /^VXTH The letter's window/ })
  await expect(panel.getByTestId('sensitivity')).toHaveText(
    'Best interior mix vs the better end, by index leg: ' +
      'base leg, paradox holds (best mix 20% hedged, +0.041pp/yr); ' +
      'conservative leg, paradox holds (best mix 20% hedged, +0.039pp/yr).',
  )
  await expect(panel.getByTestId('legs-disagree')).toHaveCount(0)
  const caveat = page.getByTestId('dividend-caveat')
  await expect(caveat).toContainText(
    "SPY total return, fee-adjusted, as the S&P 500 proxy (measured from 1993-01-29, SPY's listing)",
  )
  await expect(caveat).toContainText('conservative leg')
  await expect(caveat).not.toContainText('assumed flat yield')
  // Full history starts at SPY's listing, and says so beside PPUT's own start.
  await expect(page.getByRole('region', { name: /^PPUT Full history/ }).getByTestId('panel-measured-from')).toHaveText(
    `measured from 1993-01 (SPY’s listing); PPUT’s own history starts ${PPUT!.first_date}`,
  )
  await expect(page.getByText(/^Cboe snapshot/)).toContainText('· Tiingo tiingo_eod@2026-10-02 · rates rates@2026-10-02')
})

test('when the two legs disagree, the page says the headline reads the base', async ({ page }) => {
  const legs = VXTH_COLE.legs.map((r, k) => (k === 1 ? { ...r, outcome: 'inconclusive' as const, margin: 0.00005 } : r))
  const w: OverlayWindow = { ...VXTH_COLE, legs }
  const body: HedgeOverlayResponse = {
    ...HEDGE_OVERLAY_MEASURED,
    overlay: { ...HEDGE_OVERLAY_MEASURED.overlay, programs: [{ ...VXTH!, windows: [w] }] },
  }
  await openBook(page, body)
  await expect(page.getByRole('region', { name: /^VXTH The letter's window/ }).getByTestId('legs-disagree')).toHaveText(
    'The base and conservative legs disagree here; the verdict above reads the base leg.',
  )
})

test('a size that the grid and the refined search disagree on says which decided', async ({ page }) => {
  const note = 'The 0.1 grid’s best (0.0) gains +0.00bp/yr over no hedge, the refined w* (0.050) +1.20bp/yr: they fall on opposite sides of the 1bp bar; the refined value decides.'
  const w: OverlayWindow = { ...VXTH_COLE, sizing: { ...VXTH_COLE.sizing, grid_note: note } }
  await openBook(page, { ...HEDGE_OVERLAY_MEASURED, overlay: { ...HEDGE_OVERLAY_MEASURED.overlay, programs: [{ ...VXTH!, windows: [w] }] } })
  await hold(page).getByText('The sizing in numbers').click()
  await expect(hold(page).getByTestId('hold-grid-note')).toHaveText(note)
})

test('the section fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  await expect(hold(page).getByTestId('hold-table')).toBeVisible()
  await hold(page).getByText('The sizing in numbers').click()
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(375)
})

test('the monthly plan on measured dividends names its legs and points to the size', async ({ page }) => {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/book-plan**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(BOOK_PLAN_MEASURED satisfies BookPlanResponse) }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByText('Monthly contributions').click()
  await expect(page.getByTestId('dividend-caveat')).toContainText('SPY total return, fee-adjusted')
  await expect(page.getByTestId('plan-sizing-link')).toContainText('it recommends none')
  await page.getByText('The plan in numbers').click()
  await expect(page.getByTestId('plan-by-yield')).toHaveText(
    'Share of starts led, by index leg: base leg, 4.6%; conservative leg, 5.3%.',
  )
  await expect(page.getByRole('table').locator('tbody tr').nth(1).locator('td').first()).toHaveText(
    'S&P 500 total return — SPY, fee-adjusted',
  )
})

test('the Workspace premium budget says it is not a size', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await expect(page.getByTestId('notional-note')).toHaveText('Standalone · fixed premium, not a size recommendation')
  await expect(page.getByTestId('notional-book')).toHaveText('The Book sizes Cboe’s fixed-strike programs, not this strategy.')
})

test('the glossary thread runs from the strike to the size', () => {
  // put_delta -> strike_rule -> time_average_growth -> fractional_kelly: each
  // section of the page ends with a pointer to the next.
  expect(CONCEPTS.put_delta!.seeAlso).toContain('strike_rule')
  expect(CONCEPTS.strike_rule!.seeAlso).toContain('time_average_growth')
  expect(CONCEPTS.time_average_growth!.seeAlso).toContain('fractional_kelly')
})

/** The measured payload with ``edit`` applied to every window's sizing. */
function sized(edit: (key: string, w: OverlayWindow) => Partial<OverlayWindow['sizing']> | null): HedgeOverlayResponse {
  const m = HEDGE_OVERLAY_MEASURED
  return {
    ...m,
    overlay: {
      ...m.overlay,
      programs: m.overlay.programs.map((p) => ({
        ...p,
        windows: p.windows.map((w) => {
          const over = edit(`${p.index_symbol}-${w.key}`, w)
          return over ? { ...w, sizing: { ...w.sizing, ...over } } : w
        }),
      })),
    },
  }
}

test('one withheld window is named as one, beside the sizes that stand', async ({ page }) => {
  await openBook(
    page,
    sized((k) => (k === 'VXTH-full' ? { recommended_ratio: null, reason: 'sizing needs T-bills for the cash-drag check (T-bill history starts 2004-06-02)' } : null)),
  )
  const caveat = hold(page).getByTestId('hold-caveat')
  await expect(caveat).toContainText(
    'One of six program-windows is withheld: sizing needs T-bills for the cash-drag check (T-bill history starts 2004-06-02).',
  )
  await expect(caveat).not.toContainText('No size is recommended here')
  await expect(caveat).toContainText('The put programs PPUT and PPUT3M currently earn no place in the book')
  await expect(rows(page).nth(4).locator('td').nth(3)).toHaveText('12.1%')
})

test('the caveat speaks only for put programs on screen', async ({ page }) => {
  const m = HEDGE_OVERLAY_MEASURED
  await openBook(page, { ...m, overlay: { ...m.overlay, programs: [VXTH!] } })
  const caveat = hold(page).getByTestId('hold-caveat')
  await expect(caveat).not.toContainText('put program')
  await expect(caveat).not.toContainText('earns a place')
})

test('a put program that earns a size is named as earning one', async ({ page }) => {
  await openBook(page, sized((k) => (k === 'PPUT-cole' ? { recommended_ratio: 0.1, w_star: 0.2, reason: 'half of the full-Kelly w*' } : null)))
  const caveat = hold(page).getByTestId('hold-caveat')
  await expect(caveat).toContainText('The put program PPUT3M currently earns no place in the book: hold none.')
  await expect(caveat).toContainText('PPUT earns a place in at least one window.')
})

test('at the cap the half-way mark is called a cap, not w*/2', async ({ page }) => {
  await openBook(
    page,
    sized((k) => (k === 'VXTH-cole' ? { w_star: 1, at_cap: true, recommended_ratio: 0.5, break_even_status: 'beyond_1', break_even: null } : null)),
  )
  const chart = page.getByRole('figure', { name: /^VXTH, letter's window/ })
  await expect(chart.locator('.pl-chart-label', { hasText: 'cap 50%' })).toHaveCount(1)
  await expect(chart.locator('.pl-chart-label', { hasText: 'w*/2' })).toHaveCount(0)
  await expect(hold(page).getByTestId('hold-key-cap')).toHaveText('cap 50% (where w* is 100%; not half-Kelly)')
  // Other windows still have an interior w*, so their key entry stays.
  await expect(hold(page).getByTestId('hold-key-half')).toHaveCount(1)
  await hold(page).getByText('The sizing in numbers').click()
  const numbers = hold(page).getByTestId('hold-number').nth(4)
  await expect(numbers).toContainText('at 50%, the cap that applies because w* is 100% (not half-Kelly)')
  await expect(numbers).not.toContainText('w*/2')
  await expect(numbers).not.toContainText('half keeps')
  await expect(numbers).not.toContainText('75%')
})

test('with every size at the cap the key never mentions half-Kelly', async ({ page }) => {
  await openBook(
    page,
    sized((_k, w) => (w.sizing.w_star > 0 ? { w_star: 1, at_cap: true, recommended_ratio: 0.5, break_even_status: 'beyond_1', break_even: null } : null)),
  )
  await expect(hold(page).getByTestId('hold-key-half')).toHaveCount(0)
  await expect(hold(page).getByTestId('hold-key')).not.toContainText('half-Kelly;')
})

test('a withheld put window is never counted as a put earning a place', async ({ page }) => {
  await openBook(page, sized((k) => (k === 'PPUT-cole' ? { recommended_ratio: null, reason: 'sizing needs T-bills for the cash-drag check' } : null)))
  const caveat = hold(page).getByTestId('hold-caveat')
  await expect(caveat).toContainText('One of six program-windows is withheld')
  await expect(caveat).not.toContainText('earns a place')
  await expect(caveat).not.toContainText('earn no place')
})

test('a cap-sized w* just under 1 reads as the cap; a withheld one is offered no cap', async ({ page }) => {
  await openBook(
    page,
    sized((k) =>
      k === 'VXTH-cole'
        ? { w_star: 0.99995, at_cap: true, recommended_ratio: 0.5, break_even_status: 'beyond_1', break_even: null }
        : k === 'VXTH-full'
          ? { w_star: 1, at_cap: true, recommended_ratio: null, reason: 'sizing needs measured dividends', break_even_status: 'beyond_1', break_even: null }
          : null,
    ),
  )
  await expect(page.getByRole('figure', { name: /^VXTH, letter's window/ }).locator('.pl-chart-label', { hasText: 'cap 50%' })).toHaveCount(1)
  // A withheld w* = 1 holds nothing: no cap mark, and no half-Kelly either.
  const full = page.getByRole('figure', { name: /^VXTH, full history/ })
  await expect(full.locator('.pl-chart-label', { hasText: 'cap 50%' })).toHaveCount(0)
  await expect(full.locator('.pl-chart-label', { hasText: 'w*/2' })).toHaveCount(0)
})

test('a cap the gate withholds is not offered: no cap mark, and the text says not held', async ({ page }) => {
  await openBook(
    page,
    sized((k) =>
      k === 'VXTH-cole'
        ? {
            w_star: 1,
            at_cap: true,
            recommended_ratio: 0,
            margin_by_leg: { base: 0.00005, conservative: 0.00004 },
            reason: 'margin +0.50bp/yr on the base leg, +0.40bp/yr after the cash-drag add-back: not above the 1bp/yr bar',
            break_even_status: 'beyond_1',
            break_even: null,
          }
        : null,
    ),
  )
  await expect(rows(page).nth(4).locator('td').nth(3)).toHaveText('none (0%)')
  const chart = page.getByRole('figure', { name: /^VXTH, letter's window/ })
  await expect(chart.locator('.pl-chart-label', { hasText: 'cap 50%' })).toHaveCount(0)
  await expect(chart.getByTestId('hold-mark-half')).toHaveCount(0)
  // No window holds a cap, so the key does not name one.
  await expect(hold(page).getByTestId('hold-key-cap')).toHaveCount(0)
  await hold(page).getByText('The sizing in numbers').click()
  const numbers = hold(page).getByTestId('hold-number').nth(4)
  await expect(numbers).toContainText('the cap that would apply because w* is 100%')
  await expect(numbers).toContainText('not held: the table says none (0%)')
  await expect(numbers).not.toContainText('the cap that applies')
})

// ------------------------------------------------------------ round-1 review

/** WCAG contrast of an element's text colour (or ``prop``) on the page ground. */
async function contrastOf(page: Page, testid: string, prop: 'color' | 'border-top-color' = 'color') {
  return page.getByTestId(testid).first().evaluate((el, p) => {
    const rgb = (c: string) => (c.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
    const lum = (c: string) => {
      const [r, g, b] = rgb(c).map((v) => {
        const x = v / 255
        return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
    }
    const fg = getComputedStyle(el).getPropertyValue(p)
    const bg = getComputedStyle(document.querySelector('.putlab-root')!).backgroundColor
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x)
    return (a! + 0.05) / (b! + 0.05)
  }, prop)
}

async function plate(page: Page) {
  await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Plate').click()
  await expect(page.locator('.putlab-root')).toHaveAttribute('data-theme', 'plate')
}

for (const sheet of ['paper', 'plate'] as const) {
  test(`essential notes clear AA on the ${sheet}`, async ({ page }) => {
    await openBook(page, HEDGE_OVERLAY_MEASURED)
    if (sheet === 'plate') await plate(page)
    expect(await contrastOf(page, 'hold-not-prefilled')).toBeGreaterThanOrEqual(4.5)
    // The key's dash is a graphic: 3:1, and the same colour as the chart's line.
    expect(await contrastOf(page, 'hold-key-dash', 'border-top-color')).toBeGreaterThanOrEqual(3)
    const line = await hold(page).locator('.pl-c-parity').first().evaluate((el) => getComputedStyle(el).stroke)
    const dash = await page.getByTestId('hold-key-dash').evaluate((el) => getComputedStyle(el).borderTopColor)
    expect(dash).toBe(line)
    await page.getByText('Monthly contributions').click()
    expect(await contrastOf(page, 'plan-sizing-link')).toBeGreaterThanOrEqual(4.5)
    await page.getByRole('tab', { name: 'Workspace' }).click()
    expect(await contrastOf(page, 'notional-note')).toBeGreaterThanOrEqual(4.5)
    expect(await contrastOf(page, 'notional-book')).toBeGreaterThanOrEqual(4.5)
  })
}

test('no fixture cites a snapshot dated after its own as_of', () => {
  // A point-in-time read at as_of can only have seen snapshots up to it.
  for (const body of [HEDGE_OVERLAY, HEDGE_OVERLAY_MEASURED, BOOK_PLAN_MEASURED]) {
    const asOf = 'overlay' in body ? body.overlay.as_of : body.plan.as_of
    const cited = JSON.stringify(body).match(/@\d{4}-\d{2}-\d{2}/g) ?? []
    expect(cited.length).toBeGreaterThan(0)
    for (const id of cited) expect(id.slice(1) <= asOf, `${id} after as_of ${asOf}`).toBe(true)
  }
})

test('the no-hedge key is a circle, like its mark', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const box = (await page.getByTestId('hold-key-zero').boundingBox())!
  expect(Math.abs(box.width - box.height)).toBeLessThan(0.5)
})

test('without T-bills the Book caveat names no conservative leg it cannot build', async ({ page }) => {
  await openBook(page, hedgeOverlayWithoutRates())
  const caveat = page.getByTestId('dividend-caveat')
  await expect(caveat).not.toContainText('re-run on a conservative leg')
  await expect(caveat).toContainText('there is no conservative leg and no size is recommended')
})

async function openPlan(page: Page, body: BookPlanResponse) {
  await mockPutLabApi(page)
  await page.route('**/api/putlab/book-plan**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await page.getByText('Monthly contributions').click()
}

test('the plan caveat matches the rows it shows', async ({ page }) => {
  await openPlan(page, bookPlanMeasuredWithoutRates())
  const caveat = page.getByTestId('dividend-caveat')
  await expect(caveat).toContainText('There is no conservative row: adding SPY’s dividend cash drag back needs T-bill rates')
  await expect(caveat).not.toContainText('sizing')
  await expect(caveat).not.toContainText('row below')
})

test('a plan whose T-bills start late says so, by date', async ({ page }) => {
  const m = bookPlanMeasuredWithoutRates()
  await openPlan(page, { ...m, plan: { ...m.plan, dividend: { ...m.plan.dividend, conservative_from: '2004-06-02', conservative_reason: null } } })
  await expect(page.getByTestId('dividend-caveat')).toContainText(
    "There is no conservative row: T-bill rates start 2004-06-02, after this plan's history begins.",
  )
})

test('a refused plan blames its refusal, not T-bills that cover its history', async ({ page }) => {
  // T-bills from 1993-01-29, SPY's first day: the refusal, not a late rate
  // series, is why there are no rows.
  const m = BOOK_PLAN_MEASURED
  await openPlan(page, {
    ...m,
    plan: { ...m.plan, window: null, rolling: null, legs: [], refusal: 'no month start on or after 2026-09-15' },
  })
  await expect(page.getByTestId('plan-refusal')).toHaveText('no month start on or after 2026-09-15.')
  const caveat = page.getByTestId('dividend-caveat')
  await expect(caveat).toContainText('which flatters the hedge.')
  await expect(caveat).not.toContainText('There is no conservative row')
  await expect(caveat).not.toContainText('after this plan')
})

test('with both rows, the plan says the conservative row is usually, not always, harsher', async ({ page }) => {
  await openPlan(page, BOOK_PLAN_MEASURED)
  await expect(page.getByTestId('dividend-caveat')).toContainText('It usually narrows the hedge’s edge, but not always')
})

test('on a 390px phone the size table fits its region and the reason is readable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const table = (await hold(page).getByTestId('hold-table').boundingBox())!
  const region = (await hold(page).boundingBox())!
  expect(table.x).toBeGreaterThanOrEqual(region.x - 0.5)
  expect(table.x + table.width).toBeLessThanOrEqual(Math.min(region.x + region.width, 390) + 0.5)
  const why = (await rows(page).nth(5).locator('td.pl-hold-why').boundingBox())!
  expect(why.x).toBeGreaterThanOrEqual(0)
  expect(why.x + why.width).toBeLessThanOrEqual(390)
  // A full-width line, not a 95px column seven lines deep.
  expect(why.width).toBeGreaterThan(250)
  expect(why.height).toBeLessThan(80)
})

test('no two chart labels overprint, even where the marks crowd', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const figs = hold(page).locator('.pl-hold-chart')
  for (let k = 0; k < 6; k++) {
    const boxes = await figs.nth(k).locator('.pl-chart-label').evaluateAll((els) =>
      els.map((e) => e.getBoundingClientRect()).map((r) => ({ l: r.left, r: r.right, t: r.top, b: r.bottom })),
    )
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const [a, b] = [boxes[i]!, boxes[j]!]
        const overlap = a.l < b.r - 0.5 && b.l < a.r - 0.5 && a.t < b.b - 0.5 && b.t < a.b - 0.5
        expect(overlap, `figure ${k}: labels ${i} and ${j} overprint`).toBe(false)
      }
  }
})

test('break-even sits on the no-hedge line, and that line is at g(0)', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const chart = hold(page).getByTestId('hold-chart').nth(4)
  const ys = await chart.evaluate((svg) => {
    const markY = (m: string) => Number(/^M[\d.]+,([\d.]+)/.exec(svg.querySelector(`[data-testid="hold-mark-${m}"]`)!.getAttribute('d')!)![1])
    const parity = Number(/^M[\d.]+,([\d.]+)/.exec(svg.querySelector('.pl-c-parity')!.getAttribute('d')!)![1])
    return { zero: markY('zero'), even: markY('even'), star: markY('star'), parity }
  })
  expect(ys.even).toBeCloseTo(ys.zero, 1)
  expect(ys.parity).toBeCloseTo(ys.zero, 1)
  expect(Math.abs(ys.star - ys.zero)).toBeGreaterThan(1)
})

test('a sub-basis-point gain is spelled out under its chart', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  const fig = page.getByRole('figure', { name: /^VXTH, full history/ })
  await expect(fig.getByTestId('hold-flat-note')).toHaveText(
    'The marks sit almost on the no-hedge line: the gain at w* is +0.69bp/yr, under the 1bp bar.',
  )
  await expect(page.getByRole('figure', { name: /^VXTH, letter's window/ }).getByTestId('hold-flat-note')).toHaveCount(0)
})

test('a negative naive Kelly prints a true minus', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  await hold(page).getByText('The sizing in numbers').click()
  await expect(hold(page).getByTestId('hold-number').nth(0)).toContainText('Naive (mean-variance) Kelly: −344.7%')
})

test('only a program older than SPY says it is measured from SPY’s listing', async ({ page }) => {
  await openBook(page, HEDGE_OVERLAY_MEASURED)
  await expect(page.getByRole('region', { name: /^VXTH Full history/ }).getByTestId('panel-measured-from')).toHaveCount(0)
  await expect(page.getByRole('region', { name: /^PPUT Full history/ }).getByTestId('panel-measured-from')).toHaveCount(1)
})

/** The measured payload with its index-leg basis edited. */
function basis(over: Partial<HedgeOverlayResponse['overlay']['dividend']>): HedgeOverlayResponse {
  const m = HEDGE_OVERLAY_MEASURED
  return { ...m, overlay: { ...m.overlay, dividend: { ...m.overlay.dividend, ...over } } }
}

test('the caveat names why the leg was not carried to the window end', async ({ page }) => {
  await openBook(page, basis({ unextended_gap_days: 15, unextended_reason: 'missed_runs' }))
  await expect(page.getByTestId('dividend-caveat')).toContainText(
    'measured dividends trail the Cboe calendar by 15 days — two missed weekly runs',
  )
})

test('a missing Cboe SPX day is not called a missed run', async ({ page }) => {
  await openBook(page, basis({ unextended_gap_days: 3, unextended_reason: 'no_spx_anchor' }))
  const caveat = page.getByTestId('dividend-caveat')
  await expect(caveat).toContainText('by 3 days — the Cboe S&P 500 series lacks their last session to carry on from')
  await expect(caveat).not.toContainText('missed weekly runs')
})

test('a leg carried on SPX price says for how long', async ({ page }) => {
  const m = HEDGE_OVERLAY_MEASURED.overlay.dividend
  await openBook(
    page,
    basis({
      spans: [
        { ...m.spans[0]!, end: '2026-09-23' },
        { start: '2026-09-24', end: '2026-10-02', source: 'spx_price_only', days: 9 },
      ],
    }),
  )
  await expect(page.getByTestId('dividend-caveat')).toContainText(
    'its last 9 days (2026-09-24 to 2026-10-02) carry on S&P 500 price alone, with no dividend accrual',
  )
})

for (const [what, over] of [
  ['held at none', { recommended_ratio: 0, reason: 'margin +0.50bp/yr on the base leg, +0.40bp/yr after the cash-drag add-back: not above the 1bp/yr bar' }],
  ['withheld', { recommended_ratio: null, reason: 'sizing needs T-bills for the cash-drag check' }],
] as const) {
  test(`w* at the cap ${what} is never described as half-Kelly`, async ({ page }) => {
    await openBook(
      page,
      sized((k) => (k === 'VXTH-cole' ? { w_star: 1, at_cap: true, break_even_status: 'beyond_1', break_even: null, ...over } : null)),
    )
    await expect(page.getByRole('figure', { name: /^VXTH, letter's window/ }).locator('.pl-chart-label', { hasText: 'w*/2' })).toHaveCount(0)
    await hold(page).getByText('The sizing in numbers').click()
    const numbers = hold(page).getByTestId('hold-number').nth(4)
    await expect(numbers).toContainText('the cap that would apply because w* is 100%')
    await expect(numbers).not.toContainText('half keeps')
    await expect(numbers).not.toContainText('w*/2')
  })
}
