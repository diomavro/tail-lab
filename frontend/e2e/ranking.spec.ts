import { expect, test, type Locator, type Page, type Route } from '@playwright/test'
import { cellContrast, cellPaint, outlineContrast, textContrastOnPage } from './contrast'
import { LEADERBOARD, MODEL_PRICED_MAX } from './fixtures/putlab'
import { SIGNAL_TEST_NO_BETTER } from './fixtures/signal-test'
import { type MockOptions, mockPutLabApi } from './fixtures/mock-api'
import { fmtPct } from '../src/components/putlab/format'
import type { PutLabLeaderboardResponse, RankedAsset } from '../src/api/client'

// The screen: the universe ranking as a strip at the top of the Workspace, in
// two orders over one response -- by fragility (the server's order) and by
// best return (what the Recommendations tab was, docs/adr/0028 amendment).
//
// The behaviour that matters is not "a table exists". It is that a pick's
// headline is `best_annualized` -- the argmax of that name's whole strike x
// tenor grid -- so clicking it has to land on the cell the pick ADVERTISED,
// not on whatever strike happened to be selected. That holds in both orders.
// And in the best-return order every figure on a row comes from that SAME
// cell: an argmax return beside a screened-cell hit rate would describe two
// strategies on one line.

const strip = (page: Page) => page.getByRole('group', { name: 'Screen' })
const rail = (page: Page) => page.getByRole('complementary', { name: 'Position controls' })
const sortTo = (page: Page, order: 'Fragility' | 'Best return') =>
  page.getByRole('radiogroup', { name: 'Sort the screen' }).getByText(order).click()
const expand = (page: Page) => page.getByRole('button', { name: /All \d+ names/ }).click()
const picks = (page: Page) => strip(page).getByTestId('rank-pick')
const bestTable = (page: Page) => page.getByRole('table', { name: 'Ranked strategies' })
const fragTable = (page: Page) => page.getByRole('table', { name: /fragility ranking/i })
/** A header of the fragility table by its label, AND its ⓘ button: a header that
 *  explains itself has the button's name ("What is …?") joined to its own, and
 *  the pattern requires it, so a bare header does not match. */
const colHead = (table: Locator, label: string) =>
  table.getByRole('columnheader', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} What is `) })

async function plate(page: Page) {
  await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Plate').click()
  await expect(page.locator('.putlab-root')).toHaveAttribute('data-theme', 'plate')
}

const cellContrastOnPage = (page: Page, testid: string) => textContrastOnPage(page.getByTestId(testid))

const byReturn = () =>
  [...LEADERBOARD.ranked]
    .filter((r) => r.best_annualized != null)
    .sort((a, b) => b.best_annualized! - a.best_annualized!)
const topByReturn = () => byReturn()[0]!

async function openBestReturn(page: Page) {
  await sortTo(page, 'Best return')
  await expand(page)
  await expect(bestTable(page)).toBeVisible()
}

/** Serve one leaderboard of our own, registered before the page loads. */
async function serveLeaderboard(page: Page, body: (url: URL) => PutLabLeaderboardResponse) {
  await page.route(
    (url) => url.pathname === '/api/putlab/leaderboard',
    (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body(new URL(route.request().url()))),
      }),
  )
}

/** Serve one signal-test result of our own, registered before the page loads. */
async function serveSignal(page: Page, body: unknown) {
  await page.route(
    (url) => url.pathname === '/api/putlab/signal-test',
    (route: Route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

const row = (asset: string, patch: Partial<RankedAsset>): RankedAsset => ({
  ...LEADERBOARD.ranked.find((r) => r.asset === asset)!,
  ...patch,
})

test.describe('with the shared fixture', () => {
  test.beforeEach(async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
  })

  test('opens as one line carrying the top three names', async ({ page }) => {
    await expect(strip(page).getByRole('button', { name: /TSLA/ })).toBeVisible()
    await expect(strip(page).getByRole('button', { name: /IWM/ })).toBeVisible()
    await expect(strip(page).getByRole('button', { name: /XLF/ })).toBeVisible()
    // Fourth-ranked name is not in the strip -- that is what makes it one line.
    await expect(strip(page).getByRole('button', { name: /EEM/ })).toHaveCount(0)
  })

  for (const order of ['Fragility', 'Best return'] as const) {
    test(`clicking a ${order} pick lands on the cell it advertised`, async ({ page }) => {
      // TSLA's best cell matches neither the 5% nor the 4wk the workspace opened
      // on, so a patch that only set the asset would leave both controls
      // untouched. It is a pick in both orders (first by fragility, second by
      // return); GLD, first by return, has a best strike of 5% and could not
      // prove the strike moved.
      const tsla = LEADERBOARD.ranked.find((r) => r.asset === 'tsla')!
      expect(tsla.best_moneyness_pct).not.toBe(5)
      expect(tsla.best_tenor_weeks).not.toBe(4)
      await sortTo(page, order)
      await strip(page).getByRole('button', { name: /TSLA/ }).click()

      await expect(page.getByLabel('Out of the money, %')).toHaveValue(String(tsla.best_moneyness_pct))
      await expect(page.getByRole('radio', { name: '1 quarter' })).toBeChecked()
      await expect(page.getByRole('term').filter({ hasText: 'Name' }).locator('..')).toContainText('TSLA')
    })
  }

  test('expands to the full table, each figure under the strategy that produced it', async ({ page }) => {
    await expand(page)
    const table = fragTable(page)
    for (const head of ['Fragility', 'Dn β', 'Co-skew', 'Co-kurt', 'Tail β', 'Dn cap', 'Vol β']) {
      await expect(colHead(table, head)).toBeVisible()
    }
    // The two strategy groups, named from the response -- and two "Per year"
    // columns, told apart by their accessible names.
    await expect(colHead(table, 'At 5% · 4w')).toBeVisible()
    await expect(colHead(table, 'Best cell')).toBeVisible()
    await expect(table.getByRole('columnheader', { name: 'Per year at 5% · 4w', exact: true })).toBeVisible()
    await expect(table.getByRole('columnheader', { name: 'Per year at best cell', exact: true })).toBeVisible()
    await expect(table.locator('tbody tr')).toHaveCount(LEADERBOARD.ranked.length)
    // The currently-selected name is marked in the table, not merely sorted into it.
    await expect(table.locator('tbody tr.is-current')).toContainText('SPY')
  })

  test('the fragility table shows the screened cell beside the best one, never mixed', async ({ page }) => {
    // The screened cell's own return, payoff and hit rate were fetched and
    // never shown; the row printed the argmax return beside them.
    await expand(page)
    for (const r of LEADERBOARD.ranked) {
      expect(r.annualized_return).not.toBe(r.best_annualized)
      expect(r.biggest_payoff_mult).not.toBe(r.best_biggest_payoff_mult)
    }
    const tsla = LEADERBOARD.ranked.find((r) => r.asset === 'tsla')!
    const pct = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(0)}%`
    await expect(page.getByTestId('screened-ann-tsla')).toHaveText(pct(tsla.annualized_return))
    await expect(page.getByTestId('best-ann-tsla')).toHaveText(pct(tsla.best_annualized!))
    await expect(page.getByTestId('screened-biggest-tsla')).toContainText(`${tsla.biggest_payoff_mult.toFixed(1)}×`)
    await expect(page.getByTestId('screened-hit-tsla')).toHaveText(`${Math.round(tsla.hit_rate * 100)}%`)
    const iwm = LEADERBOARD.ranked.find((r) => r.asset === 'iwm')!
    expect(iwm.verdict).not.toBe(iwm.best_verdict)
    await expect(page.getByTestId('screened-verdict-iwm')).toHaveText(iwm.verdict.replace('_', ' '))
  })

  test('prints fragility on a readable scale in the full table too', async ({ page }) => {
    await expand(page)
    // ...with a bar 36px at 100: TSLA's 0.884 is 32px.
    const bar = fragTable(page).locator('tbody tr').first().locator('.pl-frag-bar')
    expect(await bar.evaluate((el) => (el as HTMLElement).style.width)).toBe('32px')
    const cells = fragTable(page).locator('td.pl-rank-frag')
    await expect(cells).toHaveCount(LEADERBOARD.ranked.length)
    const shown = await cells.allTextContents()
    expect(new Set(shown).size).toBeGreaterThan(2)
  })

  test('says every row is model-priced, since the screen pays no real quotes yet', async ({ page }) => {
    // One printed line rather than a column repeating "Model" seven times.
    await expand(page)
    for (const r of LEADERBOARD.ranked) expect(r.priced_from).toBe('model')
    await expect(page.getByTestId('rank-model-priced')).toHaveText(' Every figure is model-priced, not quoted.')
    await expect(fragTable(page).getByRole('columnheader', { name: 'Basis' })).toHaveCount(0)
  })

  test('the screened cells are on screen at 1280px, the metrics after them', async ({ page }) => {
    // The fragility table is wider than the page: the two strategy groups come
    // first so a laptop shows them without a sideways scroll.
    await page.setViewportSize({ width: 1280, height: 900 })
    await expand(page)
    const scroller = (await fragTable(page).locator('xpath=..').boundingBox())!
    // Every column of both strategy groups, the screened group's last (q, which
    // carries the q = 0 warning) included.
    for (const name of ['Per year at best cell', 'At', 'Per year at 5% · 4w', 'Hit', 'Biggest', 'Verdict', 'q']) {
      // The two "Per year" headers carry an aria-label that tells them apart,
      // which replaces the "What is …?" suffix in their name: find them by it,
      // and assert their ⓘ button directly.
      const labelled = name.startsWith('Per year')
      const head = labelled ? fragTable(page).getByRole('columnheader', { name, exact: true }) : colHead(fragTable(page), name)
      if (labelled) await expect(head.locator('button.pl-concept')).toHaveCount(1)
      const box = (await head.boundingBox())!
      expect(box.x + box.width, name).toBeLessThanOrEqual(scroller.x + scroller.width)
    }
    // The pinned name and the current row's outline survive a sideways scroll.
    await expect(fragTable(page).locator('tbody tr.is-current')).toHaveCSS('outline-style', 'solid')
    // Keyboard focus on the current row must show: it out-ranks that outline.
    const current = fragTable(page).locator('tbody tr.is-current')
    const before = await current.evaluate((el) => getComputedStyle(el).outlineWidth)
    await current.focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    await expect(current).toBeFocused()
    expect(await current.evaluate((el) => getComputedStyle(el).outlineWidth)).not.toBe(before)
    // ...and on any other row, which is most of what a keyboard passes through.
    const other = fragTable(page).getByRole('row', { name: 'Open TSLA' })
    await page.keyboard.press('Shift+Tab')
    await other.focus()
    await page.keyboard.press('Tab')
    await page.keyboard.press('Shift+Tab')
    await expect(other).toBeFocused()
    await expect(other).toHaveCSS('outline-style', 'solid')
    await expect(other).toHaveCSS('outline-width', '3px')
    await expect(fragTable(page).locator('tbody tr').first().locator('td').nth(1)).toHaveCSS('position', 'sticky')
  })

  test('the switch reorders the strip, and switching back restores it', async ({ page }) => {
    // Guards against sorting the cached response in place (RANKING_CACHE in
    // PutLab.tsx): the best-return order would then leak into the fragility one.
    await expect(picks(page)).toHaveText([/TSLA/, /IWM/, /XLF/])
    await sortTo(page, 'Best return')
    await expect(picks(page)).toHaveText([/GLD/, /TSLA/, /IWM/])
    await expect(strip(page).locator('.pl-kicker')).toHaveText('Screen · by best return')
    await sortTo(page, 'Fragility')
    await expect(picks(page)).toHaveText([/TSLA/, /IWM/, /XLF/])
  })

  test('switching the order never re-screens the universe', async ({ page }) => {
    // Away from the warmed default, so a mutant that keyed the best-return
    // order on the default strike would miss the cache and fetch.
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
    await rail(page).getByRole('radiogroup', { name: 'Held to expiry' }).getByText('1 quarter').click()
    await page.waitForRequest((r) => {
      const u = new URL(r.url())
      return (
        u.pathname === '/api/putlab/leaderboard' &&
        u.searchParams.get('moneyness_pct') === '10' &&
        u.searchParams.get('tenor_weeks') === '12'
      )
    })
    await expect(picks(page).first()).toBeVisible()
    const fetched: string[] = []
    page.on('request', (r) => {
      const path = new URL(r.url()).pathname
      if (path === '/api/putlab/leaderboard' || path === '/api/putlab/metric-screen') fetched.push(path)
    })
    // Longer than the 250ms debounce, or toggling back would cancel the fetch
    // before it was ever sent.
    await sortTo(page, 'Best return')
    await page.waitForTimeout(600)
    await sortTo(page, 'Fragility')
    await page.waitForTimeout(600)
    expect(fetched).toEqual([])
  })

  test('the best-return table shades its losses too', async ({ page }) => {
    await openBestReturn(page)
    const spy = bestTable(page).locator('tbody tr').filter({ hasText: 'SPY' }).locator('td.pl-rec-ann')
    await expect(spy).toHaveClass(/pl-heat-loss/)
    expect(await spy.evaluate((el) => Number((el as HTMLElement).style.getPropertyValue('--pl-d')))).toBe(1)
    expect(await cellContrast(spy)).toBeGreaterThanOrEqual(4.5)
  })

  for (const sheet of ['paper', 'plate'] as const) {
    test(`a losing return on premium prints in the loss colour, on the ${sheet}`, async ({ page }) => {
      if (sheet === 'plate') await plate(page)
      await openBestReturn(page)
      const premium = (asset: string) =>
        bestTable(page).locator('tbody tr').filter({ hasText: asset }).locator('td.pl-rec-premium')
      // The sheet's loss ink, read off a probe so the test holds if the token moves.
      const loss = await page.locator('.putlab-root').evaluate((root) => {
        const probe = document.createElement('span')
        probe.style.color = 'var(--mag-2)'
        root.appendChild(probe)
        const c = getComputedStyle(probe).color
        probe.remove()
        return c
      })
      expect(LEADERBOARD.ranked.find((r) => r.asset === 'spy')!.best_roi_on_premium).toBeLessThan(0)
      await expect(premium('SPY')).toHaveCSS('color', loss)
      // A gain stays dimmed ink, not loss ink.
      await expect(premium('TSLA')).not.toHaveCSS('color', loss)
    })

    test(`the focused option of a checked segment shows its ring, on the ${sheet}`, async ({ page }) => {
      if (sheet === 'plate') await plate(page)
      const sort = page.getByRole('radiogroup', { name: 'Sort the screen' })
      // From the keyboard, so :focus-visible matches: the arrow checks and focuses.
      await sort.getByRole('radio', { name: 'Fragility' }).focus()
      await page.keyboard.press('ArrowRight')
      const best = sort.getByRole('radio', { name: 'Best return' })
      await expect(best).toBeChecked()
      expect(await best.evaluate((el) => el.matches(':focus-visible'))).toBe(true)
      const option = best.locator('..')
      // A computed outline-color exists even when no ring is drawn: check it paints, inside the fill.
      await expect(option).toHaveCSS('outline-style', 'solid')
      await expect(option).toHaveCSS('outline-width', '2px')
      await expect(option).toHaveCSS('outline-offset', '-4px')
      expect(await outlineContrast(option)).toBeGreaterThanOrEqual(3)
    })
  }

  test('every best-return row’s loss shade is scaled within the best cells', async ({ page }) => {
    // SPY is the worst row in both cells, so it reads 1 either way; EEM and QQQ
    // shade differently if the heat were taken from the screened cells.
    await openBestReturn(page)
    const worst = Math.min(...byReturn().map((r) => r.best_annualized!))
    const rows = bestTable(page).locator('tbody tr')
    for (const [i, r] of byReturn().entries()) {
      const cell = rows.nth(i).locator('td.pl-rec-ann')
      const d = await cell.evaluate((el) => (el as HTMLElement).style.getPropertyValue('--pl-d'))
      if (r.best_annualized! < 0) expect(Number(d)).toBeCloseTo(r.best_annualized! / worst, 2)
      else expect(d).toBe('')
    }
  })

  test('a gain prints in the gain colour, in every place a return is shown', async ({ page }) => {
    // TSLA gains in both its best and its screened cell; GLD tops Best return.
    await expect(picks(page).first().locator('.pl-pos')).toHaveText(fmtPct(LEADERBOARD.ranked[0]!.best_annualized!))
    await expand(page)
    const tsla = fragTable(page).locator('tbody tr').filter({ hasText: 'TSLA' }).locator('td')
    expect(LEADERBOARD.ranked[0]!.annualized_return).toBeGreaterThan(0)
    await expect(tsla.nth(4)).toHaveClass(/pl-pos/) // Per year, best cell
    await expect(tsla.nth(6)).toHaveClass(/pl-pos/) // Per year, screened cell
    await sortTo(page, 'Best return')
    await expect(bestTable(page).locator('tbody tr').first().locator('td.pl-rec-ann')).toHaveClass(/pl-pos/)
  })

  test('every name advertises its own best cell, in both orders', async ({ page }) => {
    const cell = (r: RankedAsset) => `${r.best_moneyness_pct}% · ${r.best_tenor_weeks}w`
    const byFragility = LEADERBOARD.ranked
    for (const [i, r] of byFragility.slice(0, 3).entries()) await expect(picks(page).nth(i)).toContainText(cell(r))
    await expand(page)
    for (const r of byFragility) {
      await expect(fragTable(page).locator('tbody tr').filter({ hasText: r.asset.toUpperCase() })).toContainText(cell(r))
    }
    await sortTo(page, 'Best return')
    const rows = bestTable(page).locator('tbody tr')
    for (const [i, r] of byReturn().entries()) {
      await expect(rows.nth(i).locator('.pl-rec-strike')).toHaveText(`${r.best_moneyness_pct}%`)
    }
    // At least one best strike differs from the screened 5%, or the recipe
    // could print the screen's strike and pass.
    expect(byReturn().some((r) => r.best_moneyness_pct !== LEADERBOARD.moneyness_pct)).toBe(true)
  })

  test('in delta mode the screened group says it is still by distance', async ({ page }) => {
    // By distance the note would be false: it is absent.
    await expand(page)
    await expect(fragTable(page)).toBeVisible()
    await expect(page.getByTestId('rank-screened-delta')).toHaveCount(0)
    await page.getByRole('button', { name: 'Collapse the screen' }).click()
    await rail(page).getByRole('radiogroup', { name: 'Strike chosen by' }).getByText('Delta').click()
    await expand(page)
    await expect(page.getByTestId('rank-screened-delta')).toContainText(
      'The “At 5% · 4w” group is screened by distance, at the distance the rail held before it switched to delta',
    )
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('rank-strike-note')).toHaveText(' Best-return rows don’t depend on the strike.')
  })

  test('the best-return table has exactly the strategy columns', async ({ page }) => {
    await openBestReturn(page)
    await expect(bestTable(page).locator('thead th')).toHaveText([
      '#',
      'The strategy',
      'Per yearⓘ',
      'On premiumⓘ',
      'Hitⓘ',
      'Rollsⓘ',
      'Biggest payoffⓘ',
      'Fragilityⓘ',
      'Verdictⓘ',
    ])
    // The screened cell's dividend and pricing basis are not best-cell figures.
    await expect(bestTable(page).getByRole('columnheader', { name: 'q', exact: true })).toHaveCount(0)
    await expect(bestTable(page).getByRole('columnheader', { name: 'Basis', exact: true })).toHaveCount(0)
  })

  test('the fragility table flags a q = 0 best cell beside its own figure', async ({ page }) => {
    // XLF's best cell priced at q = 0 while its screened cell did not; EEM the
    // reverse. Each flag sits beside the figure it applies to.
    await expand(page)
    await expect(page.getByTestId('best-cell-q-xlf')).toHaveText('q = 0')
    await expect(page.getByTestId('best-cell-q-eem')).toHaveCount(0)
    await expect(page.getByTestId('q-source-eem')).toContainText('q = 0 rolls')
    await expect(page.getByTestId('q-zero-note')).toContainText('beside the figure it applies to')
    // The pick advertising XLF's best cell carries the flag too.
    await expect(page.getByTestId('pick-q-xlf')).toHaveText('q = 0')
    // Collapsed, the note is not on the page: the tag explains itself.
    await expect(page.getByTestId('pick-q-xlf')).toHaveAttribute('aria-label', /unknown dividend yield/)
    await expect(page.getByTestId('pick-q-tsla')).toHaveCount(0)
  })

  test('each order shows its own cell’s dividend basis', async ({ page }) => {
    // The fixture's EEM and XLF are priced on different dividend bases at the
    // screened and the best cell, both ways round.
    await expand(page)
    await expect(page.getByTestId('q-source-eem')).toContainText('q = 0 rolls')
    await expect(page.getByTestId('q-source-xlf')).toContainText('measured')
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('best-q-source-xlf')).toHaveText('q = 0 rolls')
    await expect(page.getByTestId('best-q-source-eem')).toHaveCount(0)
    await expect(page.getByTestId('best-q-zero-note')).toBeVisible()
  })

  test('the biggest payoff shows in both orders, from each order’s own cell', async ({ page }) => {
    const tsla = LEADERBOARD.ranked.find((r) => r.asset === 'tsla')!
    await expand(page)
    await expect(page.getByTestId('screened-biggest-tsla')).toContainText(`${tsla.biggest_payoff_mult.toFixed(1)}×`)
    // The accessible name replaces the cell text for a screen reader: same cell.
    await expect(page.getByTestId('screened-biggest-tsla')).toHaveAttribute(
      'aria-label',
      `Biggest payoff ${tsla.biggest_payoff_mult.toFixed(2)} times its premium`,
    )
    // Bars are on a log scale, as the legend says.
    const spy = LEADERBOARD.ranked.find((r) => r.asset === 'spy')!
    const max = Math.max(...LEADERBOARD.ranked.map((r) => r.biggest_payoff_mult))
    const w = (a: string) =>
      page.getByTestId(`screened-biggest-${a}`).locator('.pl-mini-bar').evaluate((el) => (el as HTMLElement).offsetWidth)
    expect(Math.abs((await w('spy')) - Math.round((Math.log1p(spy.biggest_payoff_mult) / Math.log1p(max)) * 40))).toBeLessThanOrEqual(1)
    await sortTo(page, 'Best return')
    const tslaRow = bestTable(page).locator('tbody tr').filter({ hasText: 'TSLA' })
    await expect(tslaRow.locator('td.pl-rec-biggest')).toContainText(`${tsla.best_biggest_payoff_mult!.toFixed(1)}×`)
    // ...and its bar is that same cell's, on the table's own log scale.
    const maxBest = Math.max(...byReturn().map((r) => r.best_biggest_payoff_mult ?? 0))
    const width = await tslaRow.locator('td.pl-rec-biggest .pl-mini-bar').evaluate((el) => (el as HTMLElement).offsetWidth)
    expect(Math.abs(width - Math.round((Math.log1p(tsla.best_biggest_payoff_mult!) / Math.log1p(maxBest)) * 40))).toBeLessThanOrEqual(1)
  })

  test('metric shading follows each column’s own fragility rank', async ({ page }) => {
    await expand(page)
    const r = (metric: string, asset: string) =>
      page.getByTestId(`shade-${metric}-${asset}`).evaluate((el) => Number((el as HTMLElement).style.getPropertyValue('--pl-r')))
    // Co-skewness is fragile when LOW: the most negative value (TSLA, −0.94)
    // carries the top rank, and the most positive (GLD) the bottom one.
    expect(await r('co_skewness', 'tsla')).toBe(1)
    expect(await r('co_skewness', 'gld')).toBe(0)
    // Downside beta is fragile when HIGH.
    expect(await r('downside_beta', 'tsla')).toBe(1)
    expect(await r('downside_beta', 'gld')).toBe(0)
    await expect(page.getByText('Stronger shade = more fragile on that measure')).toBeVisible()
  })

  for (const sheet of ['paper', 'plate'] as const) {
    test(`shaded cells keep readable text and actually paint, on the ${sheet}`, async ({ page }) => {
      if (sheet === 'plate') await plate(page)
      await expand(page)
      // Gains take no heat; losses do.
      await expect(page.getByTestId('best-ann-tsla')).not.toHaveClass(/pl-heat-loss/)
      await expect(page.getByTestId('best-ann-spy')).toHaveClass(/pl-heat-loss/)
      const shaded = page.locator('.pl-rank-table td.pl-heat-loss, .pl-rank-table td.pl-shade-rank')
      const n = await shaded.count()
      expect(n).toBeGreaterThan(40)
      for (let i = 0; i < n; i++) {
        const cell = shaded.nth(i)
        expect(await cellContrast(cell), await cell.getAttribute('data-testid') ?? String(i)).toBeGreaterThanOrEqual(4.5)
      }
      // Contrast alone passes an unpainted cell. The worst-case cells -- SPY's
      // loss (the column's largest) and the top-ranked metric cell -- must
      // paint an opaque background unlike an unshaded cell's.
      // The legend and the signal-test verdict carry the reading: AA too.
      expect(await cellContrastOnPage(page, 'rank-legend')).toBeGreaterThanOrEqual(4.5)
      expect(await cellContrastOnPage(page, 'rank-signal')).toBeGreaterThanOrEqual(4.5)
      // A deeper loss shades deeper: SPY's is the column's largest, EEM's smaller.
      const d = (id: string) =>
        page.getByTestId(id).evaluate((el) => Number((el as HTMLElement).style.getPropertyValue('--pl-d')))
      expect(await d('best-ann-spy')).toBe(1)
      // EEM's best-cell loss over the column's largest (SPY's): 0.022 / 0.081 --
      // its own column's depth, not the screened column's (0.052 / 0.111).
      expect(await d('best-ann-eem')).toBeCloseTo(0.022 / 0.081, 2)
      expect(await d('screened-ann-eem')).toBeCloseTo(0.052 / 0.111, 2)
      const plain = await cellPaint(fragTable(page).locator('tbody tr').first().locator('td').nth(0))
      for (const id of ['best-ann-spy', 'screened-ann-spy', 'shade-co_skewness-tsla']) {
        const p = await cellPaint(page.getByTestId(id))
        expect(p.bgAlpha, id).toBe(255)
        expect(p.bg, id).not.toEqual(plain.bg)
      }
    })
  }

  test('the order survives a row click and a tab round trip', async ({ page }) => {
    await openBestReturn(page)
    // TSLA moves the rail to 12 weeks: a new screen, a cache miss. Wait for
    // that read itself, so the check cannot pass inside the 250ms debounce.
    const rescreen = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await bestTable(page).locator('tbody tr').filter({ hasText: 'TSLA' }).click()
    await rescreen
    await expect(bestTable(page)).toBeVisible()
    await expect(strip(page).locator('.pl-kicker')).toHaveText('Screen · by best return')
    // A tab round trip unmounts the Workspace; the order lives in PutLab.
    await page.getByRole('tab', { name: 'Regime' }).click()
    await page.getByRole('tab', { name: 'Workspace' }).click()
    await expect(strip(page).locator('.pl-kicker')).toHaveText('Screen · by best return')
  })

  test('both orders label the picks as in-sample, not advice', async ({ page }) => {
    await expect(strip(page).getByTestId('rank-insample')).toHaveText('in-sample best · not advice')
    // The collapsed strip's only carrier of the caveat: it must be readable
    // (AA) on both sheets, not set in the faint micro grey.
    for (const id of ['rank-insample', 'rank-click-note']) {
      expect(await cellContrastOnPage(page, id), id).toBeGreaterThanOrEqual(4.5)
    }
    await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Plate').click()
    for (const id of ['rank-insample', 'rank-click-note']) {
      expect(await cellContrastOnPage(page, id), `${id} on the plate`).toBeGreaterThanOrEqual(4.5)
    }
    await page.getByRole('radiogroup', { name: 'Sheet' }).getByText('Paper').click()
    await sortTo(page, 'Best return')
    await expect(strip(page).getByTestId('rank-insample')).toHaveText('in-sample best · not advice')
  })

  test('a row in either table opens from the keyboard', async ({ page }) => {
    // Names past the top three are reachable only through the tables.
    await expand(page)
    const eem = LEADERBOARD.ranked.find((r) => r.asset === 'eem')!
    await fragTable(page).getByRole('row', { name: 'Open EEM' }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByLabel('Out of the money, %')).toHaveValue(String(eem.best_moneyness_pct))
    await expect(page.getByRole('term').filter({ hasText: 'Name' }).locator('..')).toContainText('EEM')
    // Space opens a row too, as for any button-like control.
    const iwm = LEADERBOARD.ranked.find((r) => r.asset === 'iwm')!
    await fragTable(page).getByRole('row', { name: 'Open IWM' }).focus()
    await page.keyboard.press(' ')
    await expect(page.getByRole('term').filter({ hasText: 'Name' }).locator('..')).toContainText('IWM')
    expect(iwm.best_tenor_weeks).toBe(4)
  })

  test('the best-return order carries exactly one accounting label', async ({ page }) => {
    await openBestReturn(page)
    await expect(page.getByTestId('accounting')).toHaveCount(1)
  })

  test('the notes say a click loads the best cell, and the strike does not move best returns', async ({ page }) => {
    await expect(page.getByTestId('rank-click-note')).toContainText('Opening a name loads its best cell into the controls')
    await expect(page.getByTestId('rank-strike-note')).toHaveCount(0)
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('rank-click-note')).toContainText('Opening a name loads its best cell into the controls')
    await expect(page.getByTestId('rank-strike-note')).toContainText(
      'Best-return rows don’t depend on the strike; changing the distance re-screens the universe.',
    )
  })

  test('the strike note stays up while the re-screen it warns about runs', async ({ page }) => {
    // Let the first screen land, or there is nothing to hold yet.
    await expect(picks(page).first()).toBeVisible()
    await sortTo(page, 'Best return')
    // Hold the next screen open, then move the strike.
    await page.route(
      (url) => url.pathname === '/api/putlab/leaderboard' && url.searchParams.get('moneyness_pct') === '10',
      () => {},
    )
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
    // A strike-only change in this order holds its (identical) rows.
    await expect(strip(page).getByTestId('rank-status')).toContainText('Re-screening at the new strike')
    await expect(page.getByTestId('rank-strike-note')).toBeVisible()
  })

  test('a strike-only re-screen keeps the identical best-return rows up; a tenor change does not', async ({ page }) => {
    await openBestReturn(page)
    // Hold every re-screen open.
    await page.route(
      (url) => url.pathname === '/api/putlab/leaderboard' && url.searchParams.get('moneyness_pct') !== '5',
      () => {},
    )
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
    await expect(strip(page).getByTestId('rank-status')).toContainText('Re-screening at the new strike')
    await expect(bestTable(page)).toBeVisible()
    // The tenor decides coverage: rows could change, so they come down.
    await rail(page).getByRole('radiogroup', { name: 'Held to expiry' }).getByText('1 quarter').click()
    await expect(strip(page).getByTestId('rank-status')).toContainText('Screening the universe')
    await expect(bestTable(page)).toHaveCount(0)
  })

  test('a years change never holds the old window’s rows', async ({ page }) => {
    await openBestReturn(page)
    await page.route(
      (url) => url.pathname === '/api/putlab/leaderboard' && url.searchParams.get('years') !== '4',
      () => {},
    )
    await rail(page).getByText('3y', { exact: true }).click()
    await expect(strip(page).getByTestId('rank-status')).toContainText('Screening the universe')
    await expect(bestTable(page)).toHaveCount(0)
  })

  test('a mouse click on a pick does not arm the restore', async ({ page }) => {
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' }).click()
    await landed
    await expect(picks(page).first()).toBeVisible()
    await page.waitForTimeout(300)
    await expect(strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' })).not.toBeFocused()
  })

  test('a mouse click does not pull the reader back to the table', async ({ page }) => {
    await openBestReturn(page)
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await bestTable(page).locator('tbody tr').filter({ hasText: 'TSLA' }).click()
    await landed
    await expect(bestTable(page)).toBeVisible()
    await page.waitForTimeout(300)
    await expect(bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' })).not.toBeFocused()
  })

  test('the fragility order never holds a stale table: its screened cells move with the strike', async ({ page }) => {
    await expand(page)
    await page.route(
      (url) => url.pathname === '/api/putlab/leaderboard' && url.searchParams.get('moneyness_pct') !== '5',
      () => {},
    )
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
    await expect(strip(page).getByTestId('rank-status')).toContainText('Screening the universe')
    await expect(fragTable(page)).toHaveCount(0)
  })

  test('a reader who moves on during the re-screen keeps their place', async ({ page }) => {
    await openBestReturn(page)
    // Slow the 12-week screen TSLA's row asks for.
    await page.route(
      (url) => url.pathname === '/api/putlab/leaderboard' && url.searchParams.get('tenor_weeks') === '12',
      async (route) => {
        await new Promise((r) => setTimeout(r, 1500))
        await route.fallback()
      },
    )
    await bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' }).focus()
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await page.keyboard.press('Enter')
    // The table is gone while the 12-week screen runs; the reader moves on.
    await expect(bestTable(page)).toHaveCount(0)
    const strike = page.getByLabel('Out of the money, %')
    await strike.focus()
    await landed
    await expect(bestTable(page)).toBeVisible()
    await page.waitForTimeout(300)
    await expect(strike).toBeFocused()
  })

  test('a pick opened from the keyboard gets focus back when the screen returns', async ({ page }) => {
    // The collapsed strip's picks are what a keyboard reader meets first.
    const tslaPick = strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' })
    await tslaPick.focus()
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await page.keyboard.press('Enter')
    await landed
    await expect(strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' })).toBeFocused()
  })

  test('a restored target is spent: a later re-screen leaves focus alone', async ({ page }) => {
    const tslaPick = strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' })
    await tslaPick.focus()
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await page.keyboard.press('Enter')
    await landed
    await expect(tslaPick).toBeFocused()
    // An unrelated re-screen from the mouse, with focus left on the page.
    const again = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('moneyness_pct') === '15',
    )
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('15%').click()
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await again
    await expect(picks(page).first()).toBeVisible()
    await expect(tslaPick).not.toBeFocused()
  })

  test('the restore makes one attempt, then lets go', async ({ page }) => {
    await openBestReturn(page)
    await bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' }).focus()
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await page.keyboard.press('Enter')
    await landed
    await expect(bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' })).toBeFocused()
    // Then behave like a browser that does not focus a clicked button (Safari):
    // focus stays on the page while the table is collapsed, reopened and the
    // order switched -- each a chance for a stale target to pull focus back.
    await page.evaluate(() => {
      document.addEventListener(
        'focusin',
        (e) => {
          const el = e.target as HTMLElement
          if (el.tagName === 'BUTTON' || el.tagName === 'INPUT') el.blur()
        },
        true,
      )
      ;(document.activeElement as HTMLElement | null)?.blur()
    })
    await page.getByRole('button', { name: 'Collapse the screen' }).click()
    await expand(page)
    await page.getByRole('radiogroup', { name: 'Sort the screen' }).getByText('Fragility').click()
    await sortTo(page, 'Best return')
    await page.waitForTimeout(300)
    await expect(bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' })).not.toBeFocused()
  })

  test('a strike-only keyboard open gets focus back too', async ({ page }) => {
    // IWM's best cell is 10% · 4w: only the strike differs from the 5% · 4w
    // screen, and in the fragility order that still re-screens the table.
    await expand(page)
    await fragTable(page).getByRole('row', { name: 'Open IWM' }).focus()
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('moneyness_pct') === '10',
    )
    await page.keyboard.press('Enter')
    await landed
    await expect(fragTable(page).getByRole('row', { name: 'Open IWM' })).toBeFocused()
  })

  test('a tenor-only keyboard open gets focus back too', async ({ page }) => {
    // SPY's best cell is 5% · 12w: only the tenor differs from the 5% · 4w screen.
    await expand(page)
    await fragTable(page).getByRole('row', { name: 'Open SPY' }).focus()
    const landed = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === '/api/putlab/leaderboard' &&
        new URL(r.url()).searchParams.get('moneyness_pct') === '5' &&
        new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await page.keyboard.press('Enter')
    await landed
    await expect(fragTable(page).getByRole('row', { name: 'Open SPY' })).toBeFocused()
  })

  test('the restore does not scroll a reader who moved down the page', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 700 })
    await page.route(
      (url) => url.pathname === '/api/putlab/leaderboard' && url.searchParams.get('tenor_weeks') === '12',
      async (route) => {
        await new Promise((r) => setTimeout(r, 1500))
        await route.fallback()
      },
    )
    const landed = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' }).focus()
    await page.keyboard.press('Enter')
    await expect(strip(page).getByTestId('rank-status')).toBeVisible()
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    const y = await page.evaluate(() => window.scrollY)
    expect(y).toBeGreaterThan(200)
    await landed
    await expect(strip(page).getByTestId('rank-pick').filter({ hasText: 'TSLA' })).toBeFocused()
    // Not pulled back up to the strip (the page may shift a little as the
    // strip's own height changes; scroll anchoring keeps the reader's place).
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(y - 150)
  })

  test('a row opened from the keyboard gets focus back when its table returns', async ({ page }) => {
    await openBestReturn(page)
    const tsla = bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' })
    await tsla.focus()
    const rescreen = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('tenor_weeks') === '12',
    )
    await page.keyboard.press('Enter')
    await rescreen
    await expect(bestTable(page).getByRole('row', { name: 'Open TSLA at its best cell' })).toBeFocused()
  })

  test('a narrow page never scrolls sideways with the best-return table open', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await openBestReturn(page)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('the signal-test line reads the walk-forward verdict at fixed, stated settings', async ({ page }) => {
    const line = strip(page).getByTestId('rank-signal')
    await expect(line).toContainText('Signal test · 0.10Δ at realised vol · 4w · 4y · top 5')
    await expect(line).toContainText('out of sample, rebalanced every 4 weeks')
    await expect(line).toContainText('No better than random baskets.')
    // The API's own sentence, with the best volatility screen beside the composite.
    await expect(line).toContainText('did not beat random baskets')
    await expect(line).toContainText('picked after the fact')
    await expect(line).toContainText(`Test window ${SIGNAL_TEST_NO_BETTER.test_start} to ${SIGNAL_TEST_NO_BETTER.test_end}`)
    // Its own provenance, not the ranking's.
    await expect(line).toContainText(SIGNAL_TEST_NO_BETTER.code_sha.slice(0, 7))
    await expect(line).toContainText(`${SIGNAL_TEST_NO_BETTER.snapshot_ids.length} bronze snapshots`)
    await expect(line).not.toContainText(LEADERBOARD.code_sha)
    // No in-sample clause: that reading lives on the Bake-off tab, collapsed.
    await expect(line).not.toContainText('on premium')
    await expect(line).not.toContainText('correlation')
    // A verdict on the fragility order: shown with it, not with best return.
    await sortTo(page, 'Best return')
    await expect(strip(page).getByTestId('rank-signal')).toHaveCount(0)
  })

  test('the signal-test line keeps its own settings when the rail moves', async ({ page }) => {
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
    await rail(page).getByRole('radiogroup', { name: 'Held to expiry' }).getByText('1 quarter').click()
    await expect(strip(page).getByTestId('rank-signal')).toContainText('Signal test · 0.10Δ at realised vol · 4w · 4y · top 5')
    // It does not compare itself with the list: the rail's default is a
    // distance and the test's a delta, so the two would never match.
    await expect(strip(page).getByTestId('rank-signal')).not.toContainText('settings of the screen')
  })

  for (const [status, label] of [
    ['beats_random', 'Beats random baskets.'],
    ['crash_carried', 'Carried by one event.'],
    ['insufficient_data', 'Too little history to judge.'],
  ] as const) {
    test(`the signal-test line words a ${status} verdict plainly`, async ({ page }) => {
      await mockPutLabApi(page)
      await serveSignal(page, {
        ...SIGNAL_TEST_NO_BETTER,
        verdict: { ...SIGNAL_TEST_NO_BETTER.verdict, status, sentence: `A planted ${status} sentence.` },
      })
      await page.goto('/')
      const line = strip(page).getByTestId('rank-signal')
      await expect(line).toContainText(label)
      await expect(line).toContainText(`A planted ${status} sentence.`)
    })
  }

  test('the bake-off link opens the Bake-off tab', async ({ page }) => {
    await strip(page).getByRole('button', { name: 'Bake-off →' }).click()
    await expect(page.getByRole('tab', { name: 'Bake-off' })).toHaveAttribute('aria-selected', 'true')
  })

  // ---- the best-return order: what the Recommendations tab guaranteed ----

  test('ranks every name by its own best strategy', async ({ page }) => {
    await openBestReturn(page)
    const rows = bestTable(page).locator('tbody tr')
    await expect(rows).toHaveCount(byReturn().length)
    const returns = await rows.locator('td.pl-rec-ann').allTextContents()
    const asNumbers = returns.map((t) => Number(t.replace('−', '-').replace('%', '')))
    expect(asNumbers).toEqual([...asNumbers].sort((a, b) => b - a))
    // Ranked by best-cell return, NOT by fragility.
    const byFragility = LEADERBOARD.ranked.map((r) => r.asset.toUpperCase())
    expect(byReturn().map((r) => r.asset.toUpperCase())).not.toEqual(byFragility)
    await expect(rows.first()).toContainText(topByReturn().asset.toUpperCase())
  })

  test('states each strategy in full — ticker, strike, and roll frequency', async ({ page }) => {
    await openBestReturn(page)
    const top = topByReturn()
    const first = bestTable(page).locator('tbody tr').first()
    await expect(first).toContainText(top.asset.toUpperCase())
    await expect(first).toContainText(`${top.best_moneyness_pct}% out of the money`)
    // GLD's best cell rolls weekly: "every 1 week", never "1 weeks".
    await expect(first).toContainText(`rolled every ${top.best_tenor_weeks} week${top.best_tenor_weeks === 1 ? '' : 's'}`)
    await expect(first).not.toContainText('1 weeks')
  })

  test('takes every figure on a row from that same cell', async ({ page }) => {
    await openBestReturn(page)
    const top = topByReturn()
    const first = bestTable(page).locator('tbody tr').first()
    await expect(first.locator('td.pl-rec-hit')).toHaveText(`${Math.round(top.best_hit_rate! * 100)}%`)
    await expect(first.locator('td.pl-rec-rolls')).toHaveText(String(top.best_n_cycles))
    await expect(first.locator('td.pl-rec-verdict')).toContainText(top.best_verdict!.replace('_', ' '))
    // The screened-cell hit rate is a different number and must not appear here.
    expect(top.best_hit_rate).not.toBe(top.hit_rate)
  })

  test('every best-return row’s premium return and verdict are its best cell’s', async ({ page }) => {
    // The top row alone cannot tell the fields apart: only IWM's screened
    // verdict differs from its best cell's. Read every row.
    await openBestReturn(page)
    const rows = bestTable(page).locator('tbody tr')
    for (const [i, r] of byReturn().entries()) {
      await expect(rows.nth(i).locator('td.pl-rec-premium')).toHaveText(new RegExp(`^\\${fmtPct(r.best_roi_on_premium!)}`))
      await expect(rows.nth(i).locator('td.pl-rec-verdict')).toHaveText(r.best_verdict!.replace('_', ' '))
    }
    expect(byReturn().some((r) => r.verdict !== r.best_verdict)).toBe(true)
    expect(byReturn().some((r) => fmtPct(r.roi_on_premium!) !== fmtPct(r.best_roi_on_premium!))).toBe(true)
  })

  test('names the premium a return on premium was measured against', async ({ page }) => {
    await openBestReturn(page)
    const top = topByReturn()
    const premium = LEADERBOARD.notional * top.best_n_cycles!
    await expect(bestTable(page).locator('tbody tr').first().locator('td.pl-rec-premium')).toContainText(
      `$${premium.toLocaleString('en-US')}`,
    )
  })

  test('never recommends a strike the model cannot price', async ({ page }) => {
    await openBestReturn(page)
    const cells = bestTable(page).locator('.pl-rec-strike')
    await expect(cells).toHaveCount(byReturn().length)
    for (const s of await cells.allTextContents()) {
      expect(Number(s.replace('%', ''))).toBeLessThanOrEqual(MODEL_PRICED_MAX)
    }
  })

  test('says plainly that it is a screen, not advice', async ({ page }) => {
    // tail-lab never trades (adr/0007).
    await openBestReturn(page)
    const caveat = page.getByTestId('best-return-caveat')
    await expect(caveat).toContainText('in sample')
    await expect(caveat).toContainText('not advice')
    const view = page.getByTestId('best-return')
    await expect(view).toContainText('never places an order')
    await expect(view).toContainText('lives outside this platform')
  })

  test('prints fragility on a readable scale, not rounded to 0 or 1', async ({ page }) => {
    await openBestReturn(page)
    const cells = bestTable(page).locator('td.pl-rec-frag')
    await expect(cells).toHaveCount(byReturn().length)
    const shown = await cells.allTextContents()
    expect(new Set(shown).size).toBeGreaterThan(2)
    for (const t of shown) {
      const n = Number(t)
      expect(Number.isFinite(n)).toBe(true)
      expect(n).toBeGreaterThanOrEqual(0)
      expect(n).toBeLessThanOrEqual(100)
    }
  })

  test('hands the schedule to an executor rather than placing it', async ({ page }) => {
    await openBestReturn(page)
    const link = page.getByRole('link', { name: /roll schedule/i })
    await expect(link).toBeVisible()
    const url = new URL((await link.getAttribute('href'))!, 'http://localhost')
    expect(url.pathname).toBe('/api/putlab/roll-schedule')
    expect(url.searchParams.get('moneyness_pct')).toBe(String(LEADERBOARD.moneyness_pct))
    expect(url.searchParams.get('tenor_weeks')).toBe(String(LEADERBOARD.tenor_weeks))
    expect(url.searchParams.get('years')).toBe(String(LEADERBOARD.lookback_years))
    expect(url.searchParams.get('notional')).toBe(String(LEADERBOARD.notional))
    expect(url.searchParams.get('as_of')).toBe(LEADERBOARD.as_of)
    expect(Number(url.searchParams.get('top_k'))).toBeGreaterThan(0)
    const view = page.getByTestId('best-return')
    await expect(view).toContainText('never places')
    await expect(view).toContainText(/budget/i)
  })

  test('the screen’s provenance shows under both tables', async ({ page }) => {
    await expand(page)
    await expect(page.getByTestId('rank-provenance')).toContainText(LEADERBOARD.code_sha)
    await expect(page.getByTestId('rank-provenance')).toContainText(`${LEADERBOARD.snapshot_ids.length} bronze snapshots`)
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('rank-provenance')).toContainText(LEADERBOARD.code_sha)
  })
})

test.describe('with a response of its own', () => {
  // Each override is registered before the first load: the strip reads on load
  // and the client caches what it was served first.

  for (const [state, opts, text] of [
    ['loading', { hang: ['/api/putlab/leaderboard'] }, 'Screening the universe'],
    ['an error', { fail: ['/api/putlab/leaderboard'] }, 'GET /api/putlab/leaderboard failed: 500'],
    ['no data', { noData: ['/api/putlab/leaderboard'] }, 'No screen for this window yet'],
  ] as [string, MockOptions, string][]) {
    test(`the strip stays mounted and says so while the screen is ${state}`, async ({ page }) => {
      await mockPutLabApi(page, opts)
      await page.goto('/')
      await expect(strip(page).getByTestId('rank-status')).toContainText(text)
      // During a cold re-screen this line is all the strip shows: readable.
      expect(await textContrastOnPage(strip(page).getByTestId('rank-status'))).toBeGreaterThanOrEqual(4.5)
      await expect(page.getByRole('radiogroup', { name: 'Sort the screen' })).toBeVisible()
      await expect(picks(page)).toHaveCount(0)
      if (state === 'loading') {
        // The signal-test line's link is a button in the strip, which is why the
        // "ranking loaded" signal is `rank-pick`, never any strip button.
        await expect(strip(page).getByRole('button', { name: 'Bake-off →' })).toBeVisible()
      }
    })
  }

  test('the best-return order drops names with no scored best cell', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: [...LEADERBOARD.ranked, row('qqq', { asset: 'kre', name: 'Regional Banks', fragility_score: 0.05, best_annualized: null })],
    }))
    await page.goto('/')
    // The expand button counts the order's own rows.
    await expect(strip(page).getByRole('button', { name: `All ${LEADERBOARD.ranked.length + 1} names` })).toBeVisible()
    await sortTo(page, 'Best return')
    await expect(strip(page).getByRole('button', { name: `All ${byReturn().length} names` })).toBeVisible()
    await sortTo(page, 'Fragility')
    await expand(page)
    await expect(fragTable(page).locator('tbody tr')).toHaveCount(LEADERBOARD.ranked.length + 1)
    await expect(fragTable(page)).toContainText('KRE')
    await sortTo(page, 'Best return')
    await expect(bestTable(page).locator('tbody tr')).toHaveCount(byReturn().length)
    await expect(bestTable(page)).not.toContainText('KRE')
  })

  test('the best-return order says so when no name scored a best cell', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) => ({ ...r, best_annualized: null })),
    }))
    await page.goto('/')
    await sortTo(page, 'Best return')
    await expect(strip(page).getByTestId('rank-empty')).toHaveText('No name had enough history to score its grid.')
    await expect(picks(page)).toHaveCount(0)
    // No name listed, so no note about opening one.
    await expect(page.getByTestId('rank-click-note')).toHaveCount(0)
  })

  test('an expanded table that re-screens to nothing shows the empty line, not an empty table', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, (u) => (u.searchParams.get('moneyness_pct') === '15' ? { ...LEADERBOARD, ranked: [] } : LEADERBOARD))
    await page.goto('/')
    await expand(page)
    await expect(fragTable(page)).toBeVisible()
    const empty = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('moneyness_pct') === '15',
    )
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('15%').click()
    await empty
    await expect(strip(page).getByTestId('rank-empty')).toBeVisible()
    await expect(fragTable(page)).toHaveCount(0)
    await expect(page.getByTestId('rank-provenance')).toHaveCount(0)
  })

  test('the provenance dates the screen with its own as-of', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      as_of: '2026-08-20',
      // No snapshot may postdate the screen's as-of.
      snapshot_ids: LEADERBOARD.snapshot_ids.map((s) => s.replace('2026-08-21', '2026-08-20')),
    }))
    await page.goto('/')
    await expand(page)
    await expect(page.getByTestId('rank-provenance')).toContainText('As of 2026-08-20')
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('rank-provenance')).toContainText('As of 2026-08-20')
  })

  test('the group label and the budget come from the screen, not the rail', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({ ...LEADERBOARD, moneyness_pct: 7.5, tenor_weeks: 8, notional: 2500 }))
    await page.goto('/')
    await expand(page)
    await expect(colHead(fragTable(page), 'At 7.5% · 8w')).toBeVisible()
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('best-return')).toContainText('own $2,500 per roll')
    // The export names the screen shown, not the default strike.
    const href = (await page.getByRole('link', { name: /roll schedule/i }).getAttribute('href'))!
    expect(new URL(href, 'http://localhost').searchParams.get('moneyness_pct')).toBe('7.5')
    const top = topByReturn()
    await expect(bestTable(page).locator('tbody tr').first().locator('td.pl-rec-premium')).toContainText(
      `$${(2500 * top.best_n_cycles!).toLocaleString('en-US')}`,
    )
  })

  test('the fragility table’s q = 0 note follows the screened cells', async ({ page }) => {
    // Screened EEM priced at q = 0 while no best cell did: the tag in the
    // fragility table must point at a note that is on the page.
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) => ({ ...r, best_q_source: 'measured' as const })),
    }))
    await page.goto('/')
    await expand(page)
    await expect(page.getByTestId('q-source-eem')).toContainText('q = 0 rolls')
    await expect(page.getByTestId('q-zero-note')).toBeVisible()
  })

  test('the signal-test line says when it is from a different day than the list', async ({ page }) => {
    await mockPutLabApi(page)
    await serveSignal(page, {
      ...SIGNAL_TEST_NO_BETTER,
      as_of: '2026-08-20',
    })
    await page.goto('/')
    await expect(strip(page).getByTestId('rank-signal')).toContainText(
      'From a different day (2026-08-20) than the screen above.',
    )
  })

  test('the fragility table’s q = 0 note shows for a best cell’s tag alone', async ({ page }) => {
    // Every screened cell measured, only XLF's best cell at q = 0: its tag in
    // the Best-cell group must still point at a note that is on the page.
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) => ({ ...r, q_source: 'measured' as const })),
    }))
    await page.goto('/')
    await expand(page)
    await expect(page.getByTestId('best-cell-q-xlf')).toBeVisible()
    const noteId = await page.getByTestId('q-zero-note').getAttribute('id')
    await expect(page.getByTestId('best-cell-q-xlf')).toHaveAttribute('aria-describedby', noteId!)
  })

  test('the best-return q = 0 note follows the best cells', async ({ page }) => {
    // Only a best cell priced at q = 0: the best-return tag's note must show.
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) => ({ ...r, q_source: 'measured' as const })),
    }))
    await page.goto('/')
    await openBestReturn(page)
    await expect(page.getByTestId('best-q-source-xlf')).toBeVisible()
    await expect(page.getByTestId('best-q-zero-note')).toBeVisible()
    // The tag names its note for assistive tech.
    const noteId = await page.getByTestId('best-q-zero-note').getAttribute('id')
    await expect(page.getByTestId('best-q-source-xlf')).toHaveAttribute('aria-describedby', noteId!)
  })

  test('a losing pick prints its loss in the loss colour', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) => (r.asset === 'tsla' ? { ...r, best_annualized: -0.05 } : r)),
    }))
    await page.goto('/')
    await expect(picks(page).first().locator('.pl-neg')).toHaveText('−5%')
  })



  test('tied metric values share a shade', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) => (r.asset === 'iwm' ? { ...r, tail_beta: 1.06 } : r)),
    }))
    await page.goto('/')
    await expand(page)
    const r = (asset: string) =>
      page.getByTestId(`shade-tail_beta-${asset}`).evaluate((el) => Number((el as HTMLElement).style.getPropertyValue('--pl-r')))
    // IWM now equals XLF's 1.06: tied at the 5th and 6th of 7 places, they
    // share the mean rank (4.5 / 6), not the first one (4 / 6).
    expect(await r('iwm')).toBe(await r('xlf'))
    expect(await r('iwm')).toBeCloseTo(0.75, 5)
  })


  test('the best-return Market tag is the best cell’s own', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) =>
        r.asset === 'gld' ? { ...r, best_priced_from: 'market' as const, priced_from: 'model' as const } : r,
      ),
    }))
    await page.goto('/')
    await openBestReturn(page)
    await expect(page.getByTestId('best-basis-gld')).toHaveText('Market')
    // ...and on its pick (GLD leads the best-return order).
    await expect(page.getByTestId('pick-basis-gld')).toHaveText('Market')
    await expect(page.getByTestId('best-basis-tsla')).toHaveCount(0)
    // ...and the fragility table tags GLD's best-cell figure, not its
    // screened cells, and no longer claims every figure is model-priced.
    await page.getByRole('radiogroup', { name: 'Sort the screen' }).getByText('Fragility').click()
    await expect(page.getByTestId('best-cell-basis-gld')).toHaveText('Market')
    await expect(page.getByTestId('rank-model-priced')).toHaveCount(0)
  })

  test('a market-priced screened cell brings the Basis column, its own cell’s', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) =>
        r.asset === 'gld' ? { ...r, priced_from: 'market' as const, best_priced_from: 'model' as const } : r,
      ),
    }))
    await page.goto('/')
    await expand(page)
    await expect(colHead(fragTable(page), 'Basis')).toBeVisible()
    // Basis sits under the screened group's band, and the bands still cover
    // every column (the band widens by one rather than pushing Basis under
    // "Fragility measures").
    const bands = await fragTable(page).evaluate((table) => {
      const [groups, heads] = [...table.querySelectorAll('thead tr')] as HTMLTableRowElement[]
      let at = 0
      const spans = [...groups!.cells].map((c) => {
        const band = { text: c.textContent!.trim(), start: at, end: at + c.colSpan }
        at += c.colSpan
        return band
      })
      const basis = [...heads!.cells].findIndex((c) => c.textContent!.trim().startsWith('Basis'))
      return { spans, basis, columns: heads!.cells.length }
    })
    const screened = bands.spans.find((b) => b.text.startsWith('At '))!
    expect(bands.basis).toBeGreaterThanOrEqual(screened.start)
    expect(bands.basis).toBeLessThan(screened.end)
    expect(bands.spans.at(-1)!.end).toBe(bands.columns)
    await expect(page.getByTestId('basis-gld')).toHaveText('Market')
    await expect(page.getByTestId('basis-tsla')).toHaveText('Model')
    // The best cell is model-priced: no Market tag beside its figure or pick.
    await expect(page.getByTestId('best-cell-basis-gld')).toHaveCount(0)
    await sortTo(page, 'Best return')
    await expect(page.getByTestId('best-basis-gld')).toHaveCount(0)
    await expect(page.getByTestId('pick-basis-gld')).toHaveCount(0)
  })

  test('a pick’s q = 0 tag is its best cell’s, not its screened cell’s', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: LEADERBOARD.ranked.map((r) =>
        r.asset === 'tsla' ? { ...r, q_source: 'none' as const, best_q_source: 'measured' as const } : r,
      ),
    }))
    await page.goto('/')
    await expect(picks(page).first()).toContainText('TSLA')
    await expect(page.getByTestId('pick-q-tsla')).toHaveCount(0)
  })

  test('a keyboard open that lands on the screen already shown arms nothing', async ({ page }) => {
    // EEM's best cell is 5% · 4w, the screen already up: nothing re-screens,
    // so nothing may later pull focus back to it.
    await mockPutLabApi(page)
    await page.goto('/')
    await expand(page)
    const eem = fragTable(page).getByRole('row', { name: 'Open EEM' })
    await eem.focus()
    await page.keyboard.press('Enter')
    await page.evaluate(() => {
      document.addEventListener(
        'focusin',
        (e) => {
          const el = e.target as HTMLElement
          if (el.tagName === 'BUTTON' || el.tagName === 'INPUT') el.blur()
        },
        true,
      )
      ;(document.activeElement as HTMLElement | null)?.blur()
    })
    // A real re-screen, from the mouse: a wrongly armed target would fire on it.
    const again = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/putlab/leaderboard' && new URL(r.url()).searchParams.get('moneyness_pct') === '15',
    )
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('15%').click()
    await again
    await expect(picks(page).first()).toBeVisible()
    await expect(eem).not.toBeFocused()
  })

  test('each metric column is ranked on its own, not by the composite', async ({ page }) => {
    // KRE leads tail beta alone, with a low composite: built from GLD (which
    // leads no column) so nothing ties, and listed last.
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({
      ...LEADERBOARD,
      ranked: [...LEADERBOARD.ranked, row('gld', { asset: 'kre', name: 'Regional Banks', tail_beta: 1.9, fragility_score: 0.02 })],
    }))
    await page.goto('/')
    await expand(page)
    const r = (metric: string, asset: string) =>
      page.getByTestId(`shade-${metric}-${asset}`).evaluate((el) => Number((el as HTMLElement).style.getPropertyValue('--pl-r')))
    expect(await r('tail_beta', 'kre')).toBe(1)
    expect(await r('tail_beta', 'tsla')).toBeLessThan(1)
    expect(await r('downside_beta', 'tsla')).toBe(1)
  })

  test('the shading reads its directions from the response', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({ ...LEADERBOARD, fragile_high: { ...LEADERBOARD.fragile_high, co_skewness: true } }))
    await page.goto('/')
    await expand(page)
    const r = (asset: string) =>
      page.getByTestId(`shade-co_skewness-${asset}`).evaluate((el) => Number((el as HTMLElement).style.getPropertyValue('--pl-r')))
    // GLD has the most positive co-skewness: now the most fragile.
    expect(await r('gld')).toBe(1)
    expect(await r('tsla')).toBe(0)
    // ...and the legend says what the shading does, from the same response.
    await expect(page.getByTestId('rank-legend')).toContainText('Vol β is fragile when low')
    await expect(page.getByTestId('rank-legend')).not.toContainText('Co-skew and')
  })





  test('an empty screen says so in the fragility order too', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({ ...LEADERBOARD, ranked: [] }))
    await page.goto('/')
    await expect(strip(page).getByTestId('rank-empty')).toHaveText('No name could be screened for this window.')
    await sortTo(page, 'Best return')
    await expect(strip(page).getByTestId('rank-empty')).toHaveText('No name had enough history to score its grid.')
  })








  test('the signal-test line does not point at a screen that listed nothing', async ({ page }) => {
    await mockPutLabApi(page)
    await serveLeaderboard(page, () => ({ ...LEADERBOARD, ranked: [] }))
    await serveSignal(page, { ...SIGNAL_TEST_NO_BETTER, as_of: '2026-08-20' })
    await page.goto('/')
    await expect(strip(page).getByTestId('rank-signal')).toContainText('than the screen asked for.')
  })


  test('while the list loads, the signal-test line makes no claim about it', async ({ page }) => {
    await mockPutLabApi(page, { hang: ['/api/putlab/leaderboard'] })
    await page.goto('/')
    // No screen is on the page, so there is no day to compare with.
    await expect(strip(page).getByTestId('rank-signal')).toContainText('Signal test · 0.10Δ at realised vol')
    await expect(strip(page).getByTestId('rank-signal')).not.toContainText('different day')
  })

  for (const [state, opts, text] of [
    ['fails', { fail: ['/api/putlab/signal-test'] }, 'Signal test unavailable: GET /api/putlab/signal-test failed: 500'],
    ['has no data', { noData: ['/api/putlab/signal-test'] }, 'Signal test: no result for this window'],
  ] as [string, MockOptions, string][]) {
    test(`the signal-test line says so when the read ${state}`, async ({ page }) => {
      await mockPutLabApi(page, opts)
      await page.goto('/')
      await expect(strip(page).getByTestId('rank-signal')).toContainText(text)
    })
  }

  test('the signal-test line never follows the rail', async ({ page }) => {
    // The strip's first read happens while the rail is at its defaults, so a
    // fetch that leaked the rail would look right on load. Fail that read, move
    // the rail to non-default values on the Bake-off tab, and come back: the
    // retry must still ask for the fixed settings.
    await mockPutLabApi(page)
    let first = true
    await page.route(
      (url) => url.pathname === '/api/putlab/signal-test',
      async (route) => {
        if (first) {
          first = false
          return route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
        }
        return route.fallback()
      },
    )
    await page.goto('/')
    await expect(strip(page).getByTestId('rank-signal')).toContainText('Signal test unavailable')
    // The Bake-off headline is the other reader of this result, and retries the
    // failed read on its own: capture from here, so either tab's retry counts.
    const urls: URL[] = []
    page.on('request', (r) => {
      const u = new URL(r.url())
      if (u.pathname === '/api/putlab/signal-test') urls.push(u)
    })
    await page.getByRole('tab', { name: 'Bake-off' }).click()
    await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
    await rail(page).getByRole('radiogroup', { name: 'Held to expiry' }).getByText('1 quarter').click()
    await rail(page).getByText('3y', { exact: true }).click()
    await expect(rail(page).getByRole('radio', { name: '3y' })).toBeChecked()
    await rail(page).getByRole('radiogroup', { name: 'Strike chosen by' }).getByText('Distance').click()
    await page.getByRole('tab', { name: 'Workspace' }).click()
    await expect(strip(page).getByTestId('rank-signal')).toContainText('Signal test · 0.10Δ at realised vol · 4w · 4y')
    expect(urls.length).toBeGreaterThan(0)
    for (const u of urls) {
      expect(u.searchParams.get('strike_rule')).toBe('delta')
      expect(u.searchParams.get('target_delta')).toBe('0.1')
      expect(u.searchParams.get('tenor_weeks')).toBe('4')
      expect(u.searchParams.get('signal_years')).toBe('4')
      expect(u.searchParams.get('top_k')).toBe('5')
      expect(u.searchParams.has('moneyness_pct')).toBe(false)
    }
  })
})
