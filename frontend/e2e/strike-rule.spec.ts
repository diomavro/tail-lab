import { expect, test, type Page } from '@playwright/test'
import { BACKTEST, backtestByDelta, MODEL_PRICED_MAX, putDelta, STRIKE_PREVIEW } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// Strike by delta (docs/adr/0029). The switch lives only where the backtest
// can use it (Workspace, Bake-off); every label says "at realised vol", because
// a bare "0.10 delta" reads as the desk's market-delta put, which it is not.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
})

const rail = (page: Page) => page.getByRole('complementary', { name: 'Position controls' })
const ruleSwitch = (page: Page) => rail(page).getByRole('radiogroup', { name: 'Strike chosen by' })

async function toDelta(page: Page) {
  await ruleSwitch(page).getByText('Delta').click()
}

test('the switch is on Workspace and Bake-off only', async ({ page }) => {
  await page.goto('/')
  await expect(ruleSwitch(page)).toBeVisible()
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await expect(ruleSwitch(page)).toBeVisible()
  for (const tab of ['Surface', 'Regime', 'Recommendations']) {
    await page.getByRole('tab', { name: tab }).click()
    await expect(ruleSwitch(page), tab).toHaveCount(0)
  }
})

test('delta mode swaps the input and presets and sends the rule, not a leftover distance', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  await expect(rail(page).getByLabel('Out of the money, %')).toHaveCount(0)
  await expect(rail(page).getByLabel('Put delta, at realised vol')).toHaveValue('0.1')
  const sent = page.waitForRequest(
    (r) => r.url().includes('/api/putlab/backtest') && r.url().includes('target_delta=0.2'),
  )
  await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.20').click()
  await expect(page.getByTestId('hero-sentence')).toContainText('struck at 0.20Δ at realised vol')
  const qs = new URL((await sent).url()).searchParams
  expect(qs.get('strike_rule')).toBe('delta')
  expect(qs.get('target_delta')).toBe('0.2')
  expect(qs.has('moneyness_pct')).toBe(false)
})

test('a delta set on Workspace carries to Bake-off, and the Surface keeps its distance', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.30').click()
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await expect(rail(page).getByLabel('Put delta, at realised vol')).toHaveValue('0.3')
  await expect(page.locator('.pl-bake-controls').filter({ hasText: '0.30Δ at realised vol' }).first()).toBeVisible()
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expect(rail(page).getByLabel('Out of the money, %')).toHaveValue('5')
})

test('the Bake-off sends the rule and names it on the result', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await toDelta(page)
  const sent = page.waitForRequest((r) => r.url().includes('/api/putlab/metric-screen'))
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  expect(new URL((await sent).url()).searchParams.get('strike_rule')).toBe('delta')
  await expect(page.getByTestId('bake-rule')).toContainText('Screened at 0.10Δ at realised vol')
})

test('the dateline and tape say the rule the run used', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const last = backtestByDelta(0.1).cycles.at(-1)!
  await expect(page.locator('.pl-dateline')).toContainText(`0.10Δ at realised vol · latest roll K $${last.strike.toFixed(2)}`)
  await expect(page.locator('.pl-note').filter({ hasText: 'the ladder resets at 0.10Δ' })).toBeVisible()
})

test('ranking, sweep and accuracy say they are still by distance', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('rank-rule-note')).toHaveCount(0)
  await toDelta(page)
  await expect(page.getByTestId('rank-rule-note')).toContainText('not by delta')
  await expect(page.getByTestId('accuracy-rule-note')).toContainText('nearest 5% below spot')
  await expect(page.locator('.pl-note').filter({ hasText: 'This is a grid of distances below spot' })).toBeVisible()
})

test('a sweep click switches the rule back to distance', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  await page.getByRole('group', { name: 'Annualized return by strike and tenor' }).getByRole('button').first().click()
  await expect(ruleSwitch(page).getByRole('radio', { name: 'Distance' })).toBeChecked()
})

test('the moneyness prefetch does not run in delta mode', async ({ page }) => {
  // A name change in distance mode warms that name's preset backtests (with a
  // moneyness_pct); in delta mode it must warm nothing -- those reads would
  // never be shown.
  const sent: URL[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/putlab/backtest')) sent.push(new URL(r.url()))
  })
  await page.goto('/')
  await toDelta(page)
  await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
  await rail(page).locator('.pl-list-row', { hasText: 'QQQ' }).click()
  await expect(page.getByTestId('hero-sentence')).toContainText('QQQ')
  await page.waitForTimeout(1500) // well past PREFETCH_DELAY_MS
  const qqq = sent.filter((u) => u.searchParams.get('asset') === 'qqq')
  expect(qqq.length).toBeGreaterThan(0)
  expect(qqq.filter((u) => u.searchParams.has('moneyness_pct'))).toEqual([])
})

test('a typed delta runs only once it is committed, at two decimals', async ({ page }) => {
  const sent: string[] = []
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (u.pathname === '/api/putlab/backtest' && u.searchParams.get('strike_rule') === 'delta')
      sent.push(u.searchParams.get('target_delta') ?? '')
  })
  await page.goto('/')
  await toDelta(page)
  await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
  const input = rail(page).getByLabel('Put delta, at realised vol')
  await input.selectText()
  await page.keyboard.type('0.153')
  await page.waitForTimeout(800) // past the fetch debounce: nothing half-typed may run
  expect(sent.filter((t) => t !== '0.1')).toEqual([])
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('hero-sentence')).toContainText('struck at 0.15Δ at realised vol')
  expect(sent.filter((t) => t !== '0.1')).toEqual(['0.15'])
})

test('opening a Recommendations row switches the rule back to distance', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  await page.getByRole('tab', { name: 'Recommendations' }).click()
  await page.locator('tr.pick').first().click()
  await expect(ruleSwitch(page).getByRole('radio', { name: 'Distance' })).toBeChecked()
})

test('while a delta run loads, the page never labels the old run with the new rule', async ({ page }) => {
  // The rail flips at once; for a debounce the headline and dateline still
  // show the finished distance run, and must say so. A MutationObserver keeps
  // every text they showed, since the window is too short to poll.
  await page.route(
    (url) => url.pathname === '/api/putlab/backtest' && url.searchParams.get('strike_rule') === 'delta',
    () => {}, // never answers
  )
  await page.goto('/')
  await expect(page.getByTestId('hero-sentence')).toContainText('5% out of')
  await page.evaluate(() => {
    const seen: string[] = []
    ;(window as unknown as { seen: string[] }).seen = seen
    const record = () => {
      for (const sel of ['[data-testid="hero-sentence"]', '.pl-dateline']) {
        const el = document.querySelector(sel)
        if (el) seen.push(el.textContent ?? '')
      }
    }
    record()
    new MutationObserver(record).observe(document.body, { subtree: true, childList: true, characterData: true })
  })
  await toDelta(page)
  await page.waitForTimeout(800)
  const seen = await page.evaluate(() => (window as unknown as { seen: string[] }).seen)
  expect(seen.length).toBeGreaterThan(0)
  expect(seen.filter((t) => t.includes('Δ'))).toEqual([])
})

test('the Bake-off names the rule it ran, not the one the rail now shows', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await toDelta(page)
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(page.getByTestId('bake-rule')).toContainText('0.10Δ at realised vol')
  await ruleSwitch(page).getByText('Distance').click()
  await expect(page.getByTestId('bake-rule')).toContainText('0.10Δ at realised vol')
})

test('a delta verdict says it is not recorded instead of showing a hash', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const tag = page.getByRole('region', { name: 'Regime verdict' }).locator('.pl-section-head span').first()
  await expect(tag).toHaveAttribute('title', /Not recorded: hypothesis memory keeps moneyness rules only/)
  // ...and in visible text, for touch and keyboard readers who get no tooltip.
  await expect(page.getByTestId('verdict-not-recorded')).toBeVisible()
})

test('a moneyness verdict carries no "not recorded" note', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('region', { name: 'Regime verdict' }).locator('.pl-section-head span').first()).toBeVisible()
  await expect(page.getByTestId('verdict-not-recorded')).toHaveCount(0)
})

test('an out-of-range delta is refused out loud and the old one kept', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const input = rail(page).getByLabel('Put delta, at realised vol')
  await input.fill('0.6')
  await input.press('Enter')
  await expect(rail(page).getByRole('alert')).toContainText('A put delta from 0.01 to 0.50; kept 0.10')
  await expect(input).toHaveValue('0.1')
  await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
})

test('an at-the-money target says its strike sits above spot, not a negative distance', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const input = rail(page).getByLabel('Put delta, at realised vol')
  await input.fill('0.5')
  await input.press('Enter')
  const last = backtestByDelta(0.5).cycles.at(-1)!
  expect(last.entry_moneyness_pct!).toBeLessThan(0)
  await expect(page.getByTestId('hero-sentence')).toContainText(
    `${(-last.entry_moneyness_pct!).toFixed(1)}% above its entry spot`,
  )
  await expect(page.getByTestId('hero-sentence')).not.toContainText('-')
})

test.describe('How the strike is chosen', () => {
  const open = async (page: Page) => {
    const section = page.getByTestId('strike-build')
    await section.locator('summary').click()
    return section
  }

  test('under a distance rule it shows the delta swinging and no delta it never used', async ({ page }) => {
    const previews: string[] = []
    page.on('request', (r) => {
      if (r.url().includes('/api/putlab/strike-preview')) previews.push(r.url())
    })
    await page.goto('/')
    const section = await open(page)
    await expect(section.getByTestId('strike-build-rule')).toContainText('5% below')
    const deltas = BACKTEST.cycles.map((c) => -c.entry_delta!)
    await expect(section.getByTestId('strike-build-moves')).toContainText(
      `ran from ${Math.min(...deltas).toFixed(3)} to ${Math.max(...deltas).toFixed(3)} at entry`,
    )
    await expect(section.getByTestId('strike-build-chart').locator('circle')).toHaveCount(BACKTEST.cycles.length)
    await expect(section.getByTestId('strike-preview')).toHaveCount(0)
    await expect(section).not.toContainText('Δ at realised vol')
    await expect(section.getByTestId('strike-build-depth')).toHaveCount(0)
    expect(previews).toEqual([])
  })

  test('under a delta rule it shows today’s strike two ways, fetched only once opened', async ({ page }) => {
    const previews: string[] = []
    page.on('request', (r) => {
      if (r.url().includes('/api/putlab/strike-preview')) previews.push(r.url())
    })
    await page.goto('/')
    await toDelta(page)
    await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
    expect(previews).toEqual([])
    const section = await open(page)
    const model = section.getByTestId('strike-preview-model')
    await expect(model).toContainText('$497.33')
    await expect(model).toContainText('2.9% below spot')
    await expect(section.getByTestId('strike-preview-market')).toContainText('$481.00')
    // A "Rolled over" change re-runs the backtest but not today's strikes.
    await rail(page).getByRole('radiogroup', { name: 'Rolled over' }).getByText('2y').click()
    await expect(model).toContainText('$497.33')
    expect(previews.length).toBe(1)
  })

  test('the preview’s strikes reprice to their deltas under the one convention', async () => {
    const m = STRIKE_PREVIEW.model!
    const k = STRIKE_PREVIEW.market!
    expect(putDelta(m.spot, m.strike, m.sigma, m.t_years, STRIKE_PREVIEW.r, STRIKE_PREVIEW.q)).toBeCloseTo(-0.1, 3)
    expect(putDelta(k.spot, k.strike, k.iv, k.t_years, STRIKE_PREVIEW.r, STRIKE_PREVIEW.q)).toBeCloseTo(k.delta, 3)
  })

  test('under a delta rule it shows the distance moving and counts rolls past the model’s depth', async ({ page }) => {
    await page.goto('/')
    await toDelta(page)
    const input = rail(page).getByLabel('Put delta, at realised vol')
    await input.fill('0.03')
    await input.press('Enter')
    await expect(page.getByTestId('hero-sentence')).toContainText('0.03Δ')
    const section = await open(page)
    const run = backtestByDelta(0.03)
    const d = run.cycles.map((c) => c.entry_moneyness_pct!)
    await expect(section.getByTestId('strike-build-moves')).toContainText(
      `between ${Math.min(...d).toFixed(2)}% below spot and ${Math.max(...d).toFixed(2)}% below spot`,
    )
    const deep = d.filter((x) => x > 10).length
    expect(deep).toBeGreaterThan(0)
    // A count, never a rounded share: 1 of 208 must not read "0%".
    await expect(section.getByTestId('strike-build-depth')).toContainText(
      `${deep} of these ${d.length} rolls struck deeper than 10%`,
    )
    // ...and a shallower target that never goes past it says nothing.
    await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.10').click()
    await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
    await expect(section.getByTestId('strike-build-depth')).toHaveCount(0)
  })

  test('it stays open across a control change', async ({ page }) => {
    await page.goto('/')
    const section = await open(page)
    await expect(section).toHaveAttribute('open', '')
    await toDelta(page)
    await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
    await expect(page.getByTestId('strike-build')).toHaveAttribute('open', '')
  })

  test('dots take their regime colour, and rolls outside the record say so', async ({ page }) => {
    await page.goto('/')
    const section = await open(page)
    const chart = section.getByTestId('strike-build-chart')
    await expect(chart.locator('circle.pl-dot-elevated, circle.pl-dot-crisis')).not.toHaveCount(0)
    if ((await chart.locator('circle.pl-dot-none').count()) > 0) {
      await expect(chart.locator('figcaption')).toContainText('no regime label')
    }
  })

  test('a new target never shows the old target’s strikes, nor a false load error', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/putlab/strike-preview' && url.searchParams.get('target_delta') === '0.2',
      () => {}, // the new target's preview never answers
    )
    await page.goto('/')
    await toDelta(page)
    const section = await open(page)
    await expect(section.getByTestId('strike-preview-model')).toContainText('$497.33')
    await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.20').click()
    await expect(page.getByTestId('hero-sentence')).toContainText('0.20Δ')
    await expect(section.getByTestId('strike-preview-model')).not.toContainText('$497.33')
    await expect(section).not.toContainText('could not be loaded')
  })

  test('returning to a cached run shows its own strikes, never the last target’s', async ({ page }) => {
    // 0.10 -> 0.20 -> 0.10: the third run is cached, so the section stays
    // mounted -- the 0.20 preview must not linger under the 0.10 header.
    await page.goto('/')
    await toDelta(page)
    const section = await open(page)
    const model = section.getByTestId('strike-preview-model')
    await expect(model).toContainText('$497.33')
    const presets = rail(page).getByRole('radiogroup', { name: 'Delta presets' })
    await presets.getByText('0.20').click()
    await expect(page.getByTestId('hero-sentence')).toContainText('0.20Δ')
    await expect(model).not.toContainText('$497.33')
    await presets.getByText('0.10').click()
    await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
    await expect(section.locator('thead')).toContainText('−0.10')
    await expect(model).toContainText('$497.33')
  })

  test('a name the chain sweep does not collect says so', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/putlab/strike-preview',
      (route) =>
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ ...STRIKE_PREVIEW, market: null, market_status: 'not_collected' }),
        }),
    )
    await page.goto('/')
    await toDelta(page)
    const section = await open(page)
    await expect(section.getByTestId('strike-preview-status')).toContainText('not collected')
    await expect(section.getByTestId('strike-preview-market')).toContainText('—')
  })
})

test.describe('on a phone-width page', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the collapsed rail names the delta where it is read, the distance elsewhere', async ({ page }) => {
    await page.goto('/')
    await rail(page).getByRole('button', { name: /Controls/ }).click()
    await toDelta(page)
    await expect(page.getByTestId('rail-summary')).toHaveText('SPY · $1,000 · 0.10Δ at realised vol · 1m · 4y')
    await page.getByRole('tab', { name: 'Surface' }).click()
    await expect(page.getByTestId('rail-summary')).toHaveText('SPY · 5% OOM · 1m')
  })

  test('the strike table fits the screen', async ({ page }) => {
    await page.goto('/')
    await rail(page).getByRole('button', { name: /Controls/ }).click()
    await toDelta(page)
    const section = page.getByTestId('strike-build')
    await section.locator('summary').click()
    const table = section.getByTestId('strike-preview')
    await expect(section.getByTestId('strike-preview-model')).toContainText('$497.33')
    const [t, s] = await Promise.all([table.boundingBox(), section.boundingBox()])
    expect(t!.width).toBeLessThanOrEqual(s!.width + 1)
  })
})

test('a delta run past the model’s depth warns at the headline, without opening anything', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const input = rail(page).getByLabel('Put delta, at realised vol')
  await input.fill('0.03')
  await input.press('Enter')
  await expect(page.getByTestId('hero-sentence')).toContainText('0.03Δ')
  const d = backtestByDelta(0.03).cycles.map((c) => c.entry_moneyness_pct!)
  const deep = d.filter((x) => x > 10).length
  await expect(page.getByTestId('hero-depth')).toContainText(`${deep} of these ${d.length} rolls`)
  await expect(page.getByTestId('strike-build')).not.toHaveAttribute('open', '')
  await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.10').click()
  await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
  await expect(page.getByTestId('hero-depth')).toHaveCount(0)
})

test('a refusal is withdrawn once the target or the rule changes', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const input = rail(page).getByLabel('Put delta, at realised vol')
  await input.fill('0.6')
  await input.press('Enter')
  await expect(rail(page).getByRole('alert')).toBeVisible()
  await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.20').click()
  await expect(rail(page).getByRole('alert')).toHaveCount(0)
  await expect(input).not.toHaveAttribute('aria-invalid', 'true')
  await input.fill('0.9')
  await input.press('Enter')
  await expect(rail(page).getByRole('alert')).toBeVisible()
  await ruleSwitch(page).getByText('Distance').click()
  await toDelta(page)
  await expect(rail(page).getByRole('alert')).toHaveCount(0)
})

test.describe('the moves chart on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('keeps its labels legible', async ({ page }) => {
    await page.goto('/')
    await page.getByTestId('strike-build').locator('summary').click()
    const label = page.getByTestId('strike-build-chart').locator('text').first()
    const box = await label.boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(10)
  })
})

test('while a distance run loads from delta mode, the old delta run is never called a distance', async ({ page }) => {
  // The reverse race: a sweep click in delta mode starts an uncached moneyness
  // run (no prefetch in delta mode). Until it lands, the headline and dateline
  // describe the delta run they show.
  await page.goto('/')
  await toDelta(page)
  await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
  await page.route(
    (url) => url.pathname === '/api/putlab/backtest' && url.searchParams.get('strike_rule') !== 'delta',
    () => {}, // the distance run never answers
  )
  await page.evaluate(() => {
    const seen: string[] = []
    ;(window as unknown as { seen: string[] }).seen = seen
    const record = () => {
      for (const sel of ['[data-testid="hero-sentence"]', '.pl-dateline']) {
        const el = document.querySelector(sel)
        if (el) seen.push(el.textContent ?? '')
      }
    }
    record()
    new MutationObserver(record).observe(document.body, { subtree: true, childList: true, characterData: true })
  })
  await page.getByRole('group', { name: 'Annualized return by strike and tenor' }).getByRole('button').nth(3).click()
  await page.waitForTimeout(800)
  const seen = await page.evaluate(() => (window as unknown as { seen: string[] }).seen)
  expect(seen.length).toBeGreaterThan(0)
  expect(seen.filter((t) => t.includes('out of the money') || t.includes('OOM)') || t.includes('null'))).toEqual([])
})

test('the rule notes, the chart key and the strike table labels are legible on both sheets (AA)', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  await expect(page.getByTestId('hero-sentence')).toContainText('0.10Δ')
  await page.getByTestId('strike-build').locator('summary').click()
  const table = page.getByTestId('strike-preview')
  for (const sheet of ['Paper', 'Plate']) {
    await page.getByRole('radiogroup', { name: 'Sheet' }).getByText(sheet).click()
    for (const target of [
      page.getByTestId('rank-rule-note'),
      page.getByTestId('accuracy-rule-note'),
      page.getByTestId('strike-build-chart').locator('figcaption'),
      // The table's labels say which row is which vol: the reading itself.
      table.locator('thead th').first(),
      table.locator('tbody th').first(),
    ]) {
      const ratio = await target.evaluate((el) => {
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
      expect(ratio, sheet).toBeGreaterThanOrEqual(4.5)
    }
  }
})

test('a narrow range prints each axis value once', async ({ page }) => {
  // Every roll at the same 5% below spot: one tick, not "5.00%" twice, and no
  // duplicate React keys.
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  await page.goto('/')
  await toDelta(page)
  await page.route(
    (url) => url.pathname === '/api/putlab/backtest',
    (route) => {
      const run = backtestByDelta(0.1)
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ ...run, cycles: run.cycles.map((c) => ({ ...c, entry_moneyness_pct: 5 })) }),
      })
    },
  )
  await rail(page).getByRole('radiogroup', { name: 'Delta presets' }).getByText('0.20').click()
  await page.getByTestId('strike-build').locator('summary').click()
  await expect(page.getByTestId('strike-build-chart').locator('g text')).toHaveText(['5.00%'])
  expect(errors.filter((e) => e.includes('same key'))).toEqual([])
})

test('a strike above spot is labelled so on the chart, never "-0.4% below spot"', async ({ page }) => {
  await page.goto('/')
  await toDelta(page)
  const input = rail(page).getByLabel('Put delta, at realised vol')
  await input.fill('0.5')
  await input.press('Enter')
  await expect(page.getByTestId('hero-sentence')).toContainText('0.50Δ')
  await page.getByTestId('strike-build').locator('summary').click()
  const chart = page.getByTestId('strike-build-chart')
  await expect(chart.locator('figcaption')).toContainText('(negative: above spot)')
  const titles = await chart.locator('circle title').allTextContents()
  expect(titles.length).toBeGreaterThan(0)
  expect(titles.filter((t) => /-[\d.]+% below/.test(t))).toEqual([])
  expect(titles.some((t) => t.includes('above spot'))).toBe(true)
})

test('a new tenor fetches its own preview, never the old tenor\'s', async ({ page }) => {
  const sent: URL[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/putlab/strike-preview')) sent.push(new URL(r.url()))
  })
  await page.goto('/')
  await toDelta(page)
  await page.getByTestId('strike-build').locator('summary').click()
  await expect(page.getByTestId('strike-preview-model')).toContainText('$')
  const tenors = () => sent.map((u) => u.searchParams.get('tenor_weeks'))
  expect(tenors()).not.toContain('12')
  await rail(page).getByRole('radiogroup', { name: 'Held to expiry' }).getByText('1 quarter').click()
  await expect.poll(tenors).toContain('12')
})

test('a preview that fails to load says so', async ({ page }) => {
  await page.route(
    (url) => url.pathname === '/api/putlab/strike-preview',
    (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }),
  )
  await page.goto('/')
  await toDelta(page)
  await page.getByTestId('strike-build').locator('summary').click()
  await expect(page.getByTestId('strike-build')).toContainText('Today’s strikes could not be loaded.')
})

test('a failed preview that then loads drops its failure message', async ({ page }) => {
  // Fails until let through (a flag, not a call count: dev StrictMode fires
  // and aborts a first request of its own).
  let fail = true
  await page.route(
    (url) => url.pathname === '/api/putlab/strike-preview',
    (route) =>
      fail
        ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
        : route.fallback(),
  )
  await page.goto('/')
  await toDelta(page)
  const section = page.getByTestId('strike-build')
  await section.locator('summary').click()
  await expect(section).toContainText('Today’s strikes could not be loaded.')
  // Close and reopen: the retry succeeds, and the stale failure must go.
  fail = false
  await section.locator('summary').click()
  await section.locator('summary').click()
  await expect(section.getByTestId('strike-preview-model')).toContainText('$497.33')
  await expect(section).not.toContainText('could not be loaded')
})

test('a chain older than the price data says the rows are not the same day', async ({ page }) => {
  await page.route(
    (url) => url.pathname === '/api/putlab/strike-preview',
    (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...STRIKE_PREVIEW,
          market: { ...STRIKE_PREVIEW.market!, session: '2026-08-14' },
          market_is_older: true,
        }),
      }),
  )
  await page.goto('/')
  await toDelta(page)
  const section = page.getByTestId('strike-build')
  await section.locator('summary').click()
  await expect(section.getByTestId('strike-preview-older')).toContainText(
    'The chain’s session (2026-08-14) is older than the price data (2026-08-21): the two rows are not the same day.',
  )
})

test('the depth count uses the run’s own roll count, not the usual twelve', async ({ page }) => {
  // Seven rolls, not the fixture's twelve: a hard-coded 12 in either the count
  // ("of these 12") or the share-to-count step (share x 12) shows here.
  const full = backtestByDelta(0.03)
  const cycles = full.cycles.slice(-7)
  const deep = cycles.filter((c) => c.entry_moneyness_pct! > MODEL_PRICED_MAX).length
  expect(deep).toBeGreaterThan(0)
  expect(Math.round((deep / cycles.length) * 12)).not.toBe(deep)
  await page.route(
    (url) => url.pathname === '/api/putlab/backtest' && url.searchParams.get('strike_rule') === 'delta',
    (route) =>
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...full,
          cycles,
          n_cycles: cycles.length,
          beyond_model_depth_share: deep / cycles.length,
        }),
      }),
  )
  await page.goto('/')
  await toDelta(page)
  await expect(page.getByTestId('hero-depth')).toContainText(`${deep} of these 7 rolls`)
  const section = page.getByTestId('strike-build')
  await section.locator('summary').click()
  await expect(section.getByTestId('strike-build-depth')).toContainText(`${deep} of these 7 rolls struck deeper`)
})

for (const width of [640, 900]) {
  test.describe(`at ${width}px wide`, () => {
    test.use({ viewport: { width, height: 900 } })

    test('the moves chart draws its labels at a readable size', async ({ page }) => {
      // The frame follows the figure's measured width, so an 11px label is
      // drawn at ~11px: neither shrunk (a wide frame squeezed) nor blown up
      // (a narrow frame stretched across a mid-width page, ~17-21px).
      await page.goto('/')
      const section = page.getByTestId('strike-build')
      await section.locator('summary').click()
      const label = section.getByTestId('strike-build-chart').locator('text.pl-axis').first()
      await expect(label).toBeVisible()
      const px = await label.evaluate((el) => {
        const svg = (el as SVGTextElement).ownerSVGElement!
        const scale = svg.getBoundingClientRect().width / svg.viewBox.baseVal.width
        return parseFloat(getComputedStyle(el).fontSize) * scale
      })
      expect(px).toBeGreaterThanOrEqual(10)
      expect(px).toBeLessThanOrEqual(13)
    })
  })
}
