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

for (const name of ['Portfolio', 'Book', 'Regime', 'Glossary']) {
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
    await page.goto('/')
    const nav = page.getByRole('tablist', { name: 'Put Lab views' })
    for (const name of ['Glossary', 'Surface', 'Workspace']) {
      await page.getByRole('tab', { name }).focus()
      await page.getByRole('tab', { name }).click()
      const navBox = (await nav.boundingBox())!
      const tabBox = (await page.getByRole('tab', { name }).boundingBox())!
      expect(tabBox.x, name).toBeGreaterThanOrEqual(navBox.x - 1)
      expect(tabBox.x + tabBox.width, name).toBeLessThanOrEqual(navBox.x + navBox.width + 1)
    }
    // One row, not a wrapped stack.
    const ys = await page.getByRole('tab').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
    expect(new Set(ys).size).toBe(1)
  })

  test('no tab scrolls the page sideways', async ({ page }) => {
    await page.goto('/')
    for (const name of ['Workspace', 'Recommendations', 'Portfolio', 'Book', 'Bake-off', 'Regime', 'Surface', 'Glossary']) {
      await page.getByRole('tab', { name }).click()
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow, name).toBeLessThanOrEqual(1)
    }
  })
})
