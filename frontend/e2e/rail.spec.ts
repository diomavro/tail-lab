import { expect, test, type Page } from '@playwright/test'
import { ACCURACY } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The rail shows only the controls the open tab reads (docs/adr/0028). A
// control that changes nothing on screen invites the reader to believe it did,
// so the claims under test are both directions: every control a tab reads is
// there, and none it ignores is.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
})

const rail = (page: Page) => page.getByRole('complementary', { name: 'Position controls' })

async function expectSections(page: Page, present: RegExp[], absent: RegExp[]) {
  for (const label of present) await expect(rail(page).getByText(label).first()).toBeVisible()
  for (const label of absent) await expect(rail(page).getByText(label)).toHaveCount(0)
}

const UNIVERSE = /^Universe$/
const PREMIUM = /^Premium per roll/
const STRIKE = /^Strike presets$/
const TENOR = /^(Held to expiry|Expiry nearest)$/
const YEARS = /^Rolled over$/

test('Workspace carries all five controls', async ({ page }) => {
  await page.goto('/')
  await expect(rail(page).getByText('Controls Workspace reads')).toBeVisible()
  await expectSections(page, [UNIVERSE, PREMIUM, STRIKE, TENOR, YEARS], [])
})

for (const name of ['Recommendations', 'Bake-off']) {
  test(`${name} carries strike, tenor and years, and no name or premium`, async ({ page }) => {
    // Neither read keys on the name or on the premium (rankKey, metric-screen).
    await page.goto('/')
    await page.getByRole('tab', { name }).click()
    await expect(rail(page).getByText(`Controls ${name} reads`)).toBeVisible()
    await expectSections(page, [STRIKE, TENOR, YEARS], [UNIVERSE, PREMIUM])
    await expect(rail(page).getByLabel('Filter the universe')).toHaveCount(0)
  })
}

test('Surface keeps the name, the anchor and the expiry, and drops years and premium', async ({ page }) => {
  // The anchor IS the strike control and the tenor picks the expiry
  // (read_surface): dropping them would pin the Surface to one reading.
  await page.goto('/')
  await page.getByRole('tab', { name: 'Surface' }).click()
  await expectSections(page, [UNIVERSE, /^Anchor$/, STRIKE, /^Expiry nearest$/], [PREMIUM, YEARS])
})

test('the Surface refetches at the strike set on its rail', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Surface' }).click()
  const asked = page.waitForRequest(
    (r) => r.url().includes('/api/putlab/surface') && new URL(r.url()).searchParams.get('moneyness_pct') === '10',
  )
  await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
  await asked
})

for (const name of ['Portfolio', 'Book', 'Glossary']) {
  test(`${name} reads no shared control and gives main the full width`, async ({ page }) => {
    await page.goto('/')
    await page.getByRole('tab', { name }).click()
    await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.pl-side')).toHaveCount(0)
    const shell = (await page.locator('.pl-shell').boundingBox())!
    const main = (await page.locator('.pl-main').boundingBox())!
    expect(Math.abs(main.width - shell.width)).toBeLessThanOrEqual(1)
  })
}

test('Regime carries strike and tenor: they pick the program its residuals replicate', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Regime' }).click()
  await expect(rail(page).getByText('Controls Regime reads')).toBeVisible()
  await expectSections(page, [STRIKE, TENOR], [UNIVERSE, PREMIUM, YEARS])
})

test('the dateline names a single position only on tabs that read one', async ({ page }) => {
  // The backtest read is gated to the Workspace, so off-tab its spot and strike
  // are the last Workspace run's -- possibly for another name entirely.
  await page.goto('/')
  const dateline = page.locator('.pl-dateline')
  await expect(dateline).toContainText('$512.40')
  for (const name of ['Recommendations', 'Bake-off', 'Portfolio', 'Regime', 'Glossary']) {
    await page.getByRole('tab', { name }).click()
    await expect(dateline, name).not.toContainText('$512.40')
    await expect(dateline, name).not.toContainText('Name')
    await expect(dateline, name).not.toContainText('Data clean')
    await expect(dateline, name).toContainText('VIX')
  }
})

test('a strike set on Recommendations is the strike on the Workspace: one shared state', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Recommendations' }).click()
  await rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByText('10%').click()
  await page.getByRole('tab', { name: 'Workspace' }).click()
  await expect(rail(page).getByLabel('Out of the money, %')).toHaveValue('10')
  await expect(rail(page).getByRole('radiogroup', { name: 'Strike presets' }).getByRole('radio', { name: '10%' })).toBeChecked()
})

test('Regime names the program its residuals replicate, not a fixed PPUT', async ({ page }) => {
  // The reference is picked by the nearest strike and tenor, and Regime has no
  // rail to show them, so the copy has to say which program it used.
  await page.route(
    (url) => url.pathname === '/api/putlab/accuracy',
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...ACCURACY, model: { ...ACCURACY.model, reference: 'PPUT3M' } }),
      }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Regime' }).click()
  await expect(page.getByText(/replicating Cboe’s PPUT3M/)).toBeVisible()
  await expect(page.getByText(/PPUT3M is the program nearest the strike and tenor/)).toBeVisible()
})

test('Regime reads the residual’s sign off the numbers: PPUT3M does not flip in a crisis', async ({ page }) => {
  // docs/MODEL_RESIDUAL.md: PPUT3M stays positive through crises (+2.89%/yr);
  // the flip is PPUT's, a near-the-money effect. Copy that asserted it for
  // every reference would contradict the cards beneath it.
  await page.route(
    (url) => url.pathname === '/api/putlab/accuracy',
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...ACCURACY,
          model: {
            ...ACCURACY.model,
            reference: 'PPUT3M',
            residual_by_regime: { calm: 0.0256, elevated: 0.029, crisis: 0.0289 },
          },
        }),
      }),
  )
  await page.goto('/')
  await page.getByRole('tab', { name: 'Regime' }).click()
  await expect(page.getByText(/positive in every measured regime/)).toBeVisible()
  await expect(page.getByText(/flips sign/)).toHaveCount(0)
})

test.describe('on a phone-width page', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the rail opens collapsed behind a summary of only what the tab reads', async ({ page }) => {
    await page.goto('/')
    const toggle = rail(page).getByRole('button', { name: /Controls/ })
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('rail-summary')).toHaveText('SPY · $1,000 · 5% OOM · 1m · 4y')
    await expect(rail(page).getByLabel('Filter the universe')).toBeHidden()
    expect((await toggle.boundingBox())!.height).toBeGreaterThanOrEqual(44)

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(rail(page).getByLabel('Filter the universe')).toBeVisible()

    await page.getByRole('tab', { name: 'Recommendations' }).click()
    await expect(page.getByTestId('rail-summary')).toHaveText('5% OOM · 1m · 4y')
  })

  test('the tab row scrolls sideways and keeps the active tab in view', async ({ page }) => {
    // Driven by the keyboard, not by click/focus: Playwright's own actions
    // scroll their target into view, which would pass this test with no
    // placement code at all.
    await page.goto('/')
    const nav = page.getByRole('tablist', { name: 'Put Lab views' })
    const inView = async (name: string) => {
      const navBox = (await nav.boundingBox())!
      const tabBox = (await page.getByRole('tab', { name }).boundingBox())!
      return tabBox.x >= navBox.x - 1 && tabBox.x + tabBox.width <= navBox.x + navBox.width + 1
    }
    await page.getByRole('tab', { name: 'Workspace' }).focus()
    await page.keyboard.press('ArrowLeft') // wraps to the last tab, off the right edge at 390px
    await expect(page.getByRole('tab', { name: 'Glossary' })).toHaveAttribute('aria-selected', 'true')
    await expect.poll(() => inView('Glossary')).toBe(true)
    await expect.poll(() => nav.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0)
    // ...and the page itself did not jump: placement scrolls the row only.
    expect(await page.evaluate(() => window.scrollY)).toBe(0)

    await page.keyboard.press('ArrowRight') // wraps back to the first
    await expect(page.getByRole('tab', { name: 'Workspace' })).toHaveAttribute('aria-selected', 'true')
    await expect.poll(() => inView('Workspace')).toBe(true)

    // One row, not a wrapped stack.
    const ys = await page.getByRole('tab').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
    expect(new Set(ys).size).toBe(1)
  })

  test('no tab scrolls the page sideways', async ({ page }) => {
    // Measured once each tab's reads have landed: right after the click the
    // panel is a loading line, which never overflows. Surface is left to
    // surface.spec -- its pre-redesign alpha strip overflows a phone.
    await page.goto('/')
    for (const name of ['Workspace', 'Recommendations', 'Portfolio', 'Book', 'Bake-off', 'Regime', 'Glossary']) {
      await page.getByRole('tab', { name }).click()
      await page.waitForLoadState('networkidle')
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, name).toBeLessThanOrEqual(1)
    }
  })
})

test('the narrow layout follows the page’s own width, not the window’s', async ({ page }) => {
  // A wide window holding a narrow sheet (a split screen, an embed) must lay
  // out by the room it actually has.
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await expect(rail(page).getByRole('button', { name: /Controls/ })).toHaveCount(0)
  await page.addStyleTag({ content: '.putlab-root { max-width: 600px; }' })
  await expect(rail(page).getByRole('button', { name: /Controls/ })).toBeVisible()
})

test('a rail stacked above the result is never sticky over it', async ({ page }) => {
  // Between the phone layout and the side-by-side one the flex row wraps; a
  // rail that stayed sticky there scrolled the ranking underneath itself.
  for (const width of [760, 800, 820, 850, 880, 900, 1024, 1440]) {
    await page.setViewportSize({ width, height: 700 })
    await page.goto('/')
    await page.getByRole('tab', { name: 'Recommendations' }).click()
    await page.waitForLoadState('networkidle')
    const side = (await page.locator('.pl-side').boundingBox())!
    const main = (await page.locator('.pl-main').boundingBox())!
    const stacked = side.y + side.height <= main.y + 1
    if (stacked) {
      await expect(rail(page).getByRole('button', { name: /Controls/ }), `${width}px`).toBeVisible()
      expect(await page.locator('.pl-side').evaluate((el) => getComputedStyle(el).position), `${width}px`).toBe('static')
    } else {
      // Side by side: rail and main do not overlap horizontally.
      expect(side.x + side.width, `${width}px`).toBeLessThanOrEqual(main.x + 1)
    }
    await page.evaluate(() => window.scrollTo(0, 600))
    const s2 = (await page.locator('.pl-side').boundingBox())!
    const m2 = (await page.locator('.pl-main').boundingBox())!
    const overlap = s2.x < m2.x + m2.width && m2.x < s2.x + s2.width && s2.y < m2.y + m2.height && m2.y < s2.y + s2.height
    expect(overlap, `${width}px after scrolling`).toBe(false)
  }
})
