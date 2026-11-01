import { expect, test, type Page } from '@playwright/test'
import { CONCEPTS } from '../src/content/concepts'
import { mockPutLabApi } from './fixtures/mock-api'

// Every header and stat label explains itself (docs/adr/0033, section 5.4):
// a short tooltip on a mouse hover or keyboard focus, the full concept on click.
// Two tiers, and never two surfaces at once.
//
// And a rule walk: every th / .pl-stat-k / .pl-readout-k in the converted
// surfaces carries a ⓘ button, or the th declares why it need not.

const surfaces = (page: Page) => page.locator('[role="tooltip"], [role="dialog"]')
const infoButton = (page: Page) => page.locator('.pl-stats button.pl-concept').first()

test.describe('two-tier ConceptInfo, with a mouse', () => {
  test.beforeEach(async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await expect(page.locator('.pl-stats')).toBeVisible()
  })

  test('hover shows the short text as a tooltip; click then shows exactly one surface, the dialog', async ({ page }) => {
    const button = infoButton(page)
    // The description is wired before any tooltip exists: aria-describedby is
    // never absent, and its target holds the same short text.
    const descId = (await button.getAttribute('aria-describedby'))!
    expect(descId).toBeTruthy()
    expect(await page.evaluate((id) => document.getElementById(id)?.textContent, descId)).toBe(CONCEPTS.roi_on_premium.short)
    await button.hover()
    const tip = page.getByRole('tooltip')
    await expect(tip).toBeVisible()
    await expect(tip).toHaveText(CONCEPTS.roi_on_premium.short)
    // The same id, now on the visible tooltip: one node answers to it.
    await expect(button).toHaveAttribute('aria-describedby', descId)
    await expect(tip).toHaveAttribute('id', descId)
    await expect(surfaces(page)).toHaveCount(1)

    await button.click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await expect(surfaces(page)).toHaveCount(1)
    // The dialog is the existing one: term, formula, how to read it.
    await expect(page.getByRole('dialog')).toContainText('How to read it.')

    // Hovering again while it is open adds nothing.
    await page.mouse.move(0, 0)
    await button.hover()
    await expect(surfaces(page)).toHaveCount(1)
  })

  test('the tooltip leaves with the pointer, and has no gap to cross', async ({ page }) => {
    const button = infoButton(page)
    await button.hover()
    const tip = page.getByRole('tooltip')
    await expect(tip).toBeVisible()
    const b = (await button.boundingBox())!
    const t = (await tip.boundingBox())!
    // Flush against the button (below it, or above it near the page foot),
    // not floating a gap away.
    const gap = Math.min(Math.abs(t.y - (b.y + b.height)), Math.abs(b.y - (t.y + t.height)))
    expect(gap).toBeLessThanOrEqual(1)
    await page.mouse.move(0, 0)
    await expect(page.getByRole('tooltip')).toHaveCount(0)
  })

  test('Escape closes the tooltip, and so does a scroll', async ({ page }) => {
    const button = infoButton(page)
    await button.hover()
    await expect(page.getByRole('tooltip')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('tooltip')).toHaveCount(0)

    await page.mouse.move(0, 0)
    await button.hover()
    await expect(page.getByRole('tooltip')).toBeVisible()
    await page.mouse.wheel(0, 300)
    await expect(page.getByRole('tooltip')).toHaveCount(0)
  })

  test('keyboard focus shows the tooltip; click then shows one surface', async ({ page }) => {
    const button = infoButton(page)
    await button.focus()
    // Arrive by keyboard, so :focus-visible matches whatever the pointer did before.
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    await expect(button).toBeFocused()
    await expect(page.getByRole('tooltip')).toBeVisible()
    await expect(surfaces(page)).toHaveCount(1)

    await page.keyboard.press('Enter')
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await expect(surfaces(page)).toHaveCount(1)
    await page.keyboard.press('Escape')
    await expect(surfaces(page)).toHaveCount(0)
  })

  test('a mouse click focuses the button without raising the tooltip', async ({ page }) => {
    // :focus-visible does not match a pointer focus, so click shows the dialog only.
    await infoButton(page).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
  })

  test('a tooltip raised inside a scrolling table is not clipped by it', async ({ page }) => {
    await page.getByRole('tab', { name: 'Bake-off' }).click()
    const table = page.getByRole('table', { name: 'Out-of-sample signal test', exact: true })
    const scroller = table.locator('xpath=ancestor::div[contains(@class,"pl-scroll")]')
    await expect(scroller).toHaveCount(1)
    const button = table.locator('thead button.pl-concept').first()
    await button.hover()
    const tip = page.getByRole('tooltip')
    await expect(tip).toBeVisible()
    // Portaled to the body, fixed: no scrolling ancestor can clip it.
    expect(await tip.evaluate((el) => el.parentElement === document.body && getComputedStyle(el).position === 'fixed')).toBe(true)
    await expect(tip).toBeInViewport({ ratio: 1 })
  })
})

test.describe('keyboard focus inside a sideways-scrolling table, at phone width', () => {
  test.use({ viewport: { width: 420, height: 900 } })

  test('Tabbing through every ⓘ of the signal-test table shows each tooltip, though focus scrolls the table', async ({ page }) => {
    // Focusing an ⓘ that sits off-screen scrolls .pl-scroll to it, and that
    // scroll event arrives after the tooltip opened. It used to close it (4 of
    // the 11 headers at this width); the tooltip must follow the button instead.
    await mockPutLabApi(page)
    await page.goto('/')
    await page.getByRole('tab', { name: 'Bake-off' }).click()
    const table = page.getByRole('table', { name: 'Out-of-sample signal test', exact: true })
    await expect(table).toBeVisible()
    const scroller = table.locator('xpath=ancestor::div[contains(@class,"pl-scroll")]')
    expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true) // it does scroll sideways here
    const buttons = table.locator('thead button.pl-concept')
    const n = await buttons.count()
    expect(n).toBe(11)

    // Arrive by keyboard, so :focus-visible matches.
    await buttons.first().focus()
    await page.keyboard.press('Shift+Tab')
    await page.keyboard.press('Tab')
    let scrolled = 0
    for (let i = 0; i < n; i++) {
      const button = buttons.nth(i)
      await expect(button).toBeFocused()
      const before = await scroller.evaluate((el) => el.scrollLeft)
      await expect(page.getByRole('tooltip'), `the tooltip of header ${i + 1} of ${n}`).toBeVisible()
      const id = (await button.getAttribute('data-concept')) as keyof typeof CONCEPTS
      await expect(page.getByRole('tooltip')).toHaveText(CONCEPTS[id].short)
      // ...on screen and flush against its button. Focus scrolls the table
      // just far enough to reach the button (it can leave a few pixels of it
      // under the table's edge), so only require that the button starts inside.
      const b = (await button.boundingBox())!
      const sc = (await scroller.boundingBox())!
      expect(b.x).toBeLessThan(sc.x + sc.width)
      expect(b.x + b.width).toBeGreaterThan(sc.x)
      await expect(page.getByRole('tooltip')).toBeInViewport({ ratio: 1 })
      const t = (await page.getByRole('tooltip').boundingBox())!
      expect(Math.abs(t.y - (b.y + b.height)) <= 1 || Math.abs(b.y - (t.y + t.height)) <= 1).toBe(true)
      if (before > 0) scrolled++
      await page.keyboard.press('Tab')
    }
    expect(scrolled, 'the walk must exercise focus-induced scrolling').toBeGreaterThanOrEqual(3)
  })
})

test.describe('two-tier ConceptInfo, on touch', () => {
  test.use({ hasTouch: true })

  test('a tap shows only the dialog, never the tooltip', async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    await expect(page.locator('.pl-stats')).toBeVisible()
    await infoButton(page).tap()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await expect(surfaces(page)).toHaveCount(1)
  })
})

// ------------------------------------------------------------ the rule walk ---

// Every th / .pl-stat-k / .pl-readout-k on every tab carries a ⓘ button, or the
// th declares why it need not (data-noexplain: a row index, a name, a row
// label). The pytest twin (tests/test_frontend_explained_headers.py) scans the
// source; this renders each tab with its tables open, because a header in a
// table that never mounted would pass the scan and nobody would see it.
const NOOP_ENUM = ['index', 'name', 'row-label']
const EXPLAIN = '.putlab-root th, .putlab-root .pl-stat-k, .putlab-root .pl-readout-k'

async function unexplained(page: Page, selector: string): Promise<string[]> {
  return page.evaluate(
    ({ selector, allowed }) =>
      [...document.querySelectorAll(selector)]
        .filter((el) => {
          if (el.querySelector('button.pl-concept')) return false
          const no = el.getAttribute('data-noexplain')
          return !(el.tagName === 'TH' && no != null && allowed.includes(no))
        })
        .map((el) => `${el.tagName}.${el.className}: ${(el.textContent ?? '').trim().slice(0, 40)}`),
    { selector, allowed: NOOP_ENUM },
  )
}

/** Controls with no accessible name: a button, link or field a screen reader
 *  would announce as nothing (an icon-only button without an aria-label). */
async function nameless(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const text = (el: Element | null) => (el?.textContent ?? '').trim()
    return [...document.querySelectorAll('.putlab-root button, .putlab-root a[href], .putlab-root input, .putlab-root select, .putlab-root [role="button"]')]
      .filter((el) => {
        if (el.getAttribute('aria-hidden') === 'true' || (el as HTMLInputElement).type === 'hidden') return false
        const labelledby = (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).map((id) => text(document.getElementById(id))).join('')
        const labels = 'labels' in el ? [...((el as HTMLInputElement).labels ?? [])].map((l) => text(l)).join('') : ''
        const name = el.getAttribute('aria-label') ?? (labelledby || labels || text(el) || el.getAttribute('title') || el.getAttribute('placeholder') || '')
        return name.trim() === ''
      })
      .map((el) => `${el.tagName.toLowerCase()}.${el.className}`)
  })
}

/** Open every <details> on the page, so the tables inside are mounted. */
const openDetails = (page: Page) => page.evaluate(() => document.querySelectorAll('details').forEach((d) => (d.open = true)))

const rail = (page: Page) => page.getByRole('complementary', { name: 'Position controls' })

/** A scene is a state of a tab with its tables open: how to get there, and the
 *  tables it must have mounted (so the walk cannot pass by looking at nothing). */
interface Scene {
  name: string
  setup: (page: Page) => Promise<void>
  /** CSS selectors, each of which must match at least one header cell. */
  mounted: string[]
}

// Keyed by the tab's label as the tab bar prints it.
const SCENES: Record<string, Scene[]> = {
  'Workspace': [
    {
      name: 'the strip, coverage, per-name signals, price build, accuracy and ledger',
      setup: async (page) => {
        await page.getByRole('button', { name: /All \d+ names/ }).click()
        await page.getByRole('region', { name: 'Data coverage' }).getByRole('button', { name: 'Show the grid' }).click()
        const signals = page.getByRole('region', { name: 'How the signals treated this name' })
        await signals.getByRole('button', { name: 'Show the detail' }).click()
        await signals.getByRole('button', { name: /exploratory screens/ }).click()
        await page.getByRole('button', { name: /^Show all/ }).click()
        await openDetails(page)
      },
      mounted: [
        'table[aria-label="Full fragility ranking"] th',
        'table[aria-label="Roll ledger"] th',
        'table[aria-label="Published put programs"] th',
        '[data-testid="price-build-dividends"] th',
        '[aria-label="Data coverage"] th',
        '[aria-label="How the signals treated this name"] th',
        '.pl-stats .pl-stat-k',
      ],
    },
    {
      name: 'the ranked strategies, sorted by best return',
      setup: async (page) => {
        await page.getByRole('radiogroup', { name: 'Sort the screen' }).getByText('Best return').click()
        await page.getByRole('button', { name: /All \d+ names/ }).click()
      },
      mounted: ['table[aria-label="Ranked strategies"] th'],
    },
    {
      name: 'the strike by delta',
      setup: async (page) => {
        await rail(page).getByRole('radiogroup', { name: 'Strike chosen by' }).getByText('Delta').click()
        // The section remounts with the new run: open it once it has.
        await expect(page.getByTestId('strike-build')).toBeVisible()
        await openDetails(page)
        await expect(page.getByTestId('strike-preview')).toBeVisible()
      },
      mounted: ['table[aria-label="Today\'s strike two ways"] th'],
    },
  ],
  'Portfolio': [
    {
      name: 'a backtested basket',
      setup: async (page) => {
        await page.getByRole('button', { name: 'Backtest the basket' }).click()
        await expect(page.getByRole('table', { name: 'Basket legs' })).toBeVisible()
      },
      mounted: ['table[aria-label="Basket legs"] th', '.pl-stats .pl-stat-k'],
    },
  ],
  'Book': [
    {
      name: 'the hold sizes and the mix tables',
      setup: async (page) => {
        await expect(page.getByTestId('hold-table')).toBeVisible()
        await openDetails(page)
      },
      mounted: ['[data-testid="hold-table"] th', '.pl-panel-numbers table th'],
    },
    {
      name: 'the monthly plan verdict',
      setup: async (page) => {
        await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
        await page.getByText('The plan in numbers').click()
        await openDetails(page)
      },
      mounted: ['[data-testid="plan-verdict"] ~ * table th, details table th'],
    },
  ],
  'Bake-off': [
    {
      name: 'both signal tables and the in-sample table',
      setup: async (page) => {
        await page.getByRole('button', { name: 'Run the signal test' }).click()
        await expect(page.getByRole('table', { name: 'Out-of-sample signal test, run at the rail' })).toBeVisible()
        await page.getByRole('button', { name: /In-sample association/ }).click()
        await page.getByRole('button', { name: 'Run the bake-off' }).click()
        await expect(page.getByRole('table', { name: 'In-sample bake-off' })).toBeVisible()
      },
      mounted: ['table[aria-label="In-sample bake-off"] th', 'table[aria-label="Out-of-sample signal test, run at the rail"] th'],
    },
  ],
  'Regime': [
    {
      name: 'the longest stretches',
      setup: async (page) => {
        await expect(page.getByRole('table', { name: 'Longest regime stretches' })).toBeVisible()
      },
      mounted: ['table[aria-label="Longest regime stretches"] th'],
    },
  ],
  'Marks': [
    {
      name: 'the depth table',
      setup: async (page) => {
        await expect(page.getByTestId('marks-hero')).toBeVisible()
      },
      mounted: ['table[aria-label="Sessions of quotes held by depth"] th'],
    },
  ],
  'Surface': [
    {
      name: 'the ladder as numbers, and the rung readout',
      setup: async (page) => {
        await page.getByText('The ladder as numbers').click()
        await openDetails(page)
        await page.getByTestId('ladder-rung').nth(3).click()
      },
      mounted: ['table[aria-label="Tail ladder rungs"] th', '[data-testid="ladder-readout"] .pl-readout-k'],
    },
  ],
  'Pitch': [
    {
      name: 'every evidence table',
      setup: async (page) => {
        await expect(page.getByTestId('pitch-schedule')).toBeVisible()
        await openDetails(page)
      },
      mounted: [
        '[data-testid="pitch-schedule"] th',
        '[data-testid="pitch-flips"] th',
        '[data-testid="pitch-data"] th',
        '[data-testid="pitch-standalone"] th',
        '[data-testid="pitch-hedge"] th',
        '[data-testid="pitch-2008"] th',
      ],
    },
  ],
  // The reference page has no table: the walk still opens it, so a header that
  // ever lands there is caught.
  'Glossary': [{ name: 'the reference', setup: async () => {}, mounted: [] }],
}

for (const [label, scenes] of Object.entries(SCENES)) {
  for (const scene of scenes) {
    test(`the ${label} tab explains every header and label: ${scene.name}`, async ({ page }) => {
      await mockPutLabApi(page)
      await page.goto('/')
      await page.getByRole('tab', { name: label, exact: true }).click()
      await scene.setup(page)
      for (const sel of scene.mounted) {
        expect(await page.locator(sel).count(), `${sel} mounted`).toBeGreaterThan(0)
      }
      expect(await unexplained(page, EXPLAIN)).toEqual([])
      expect(await nameless(page)).toEqual([])
    })
  }
}

test('every tab in the tab bar is walked, and every walked tab exists', async ({ page }) => {
  // A tab added to the app without scenes here would escape the rule; so the
  // tab bar itself is the list.
  await mockPutLabApi(page)
  await page.goto('/')
  const tabs = await page.getByRole('tab').allInnerTexts()
  expect(tabs.length).toBeGreaterThanOrEqual(9)
  expect([...tabs].sort()).toEqual(Object.keys(SCENES).sort())
})

test('the ParamRail controls carry a ⓘ where the control is not self-explanatory', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  const head = (text: string) => rail(page).locator('.pl-field-head').filter({ hasText: text })
  // The strike rule, the premium, the distance, the tenor and the look-back.
  for (const text of ['Strike chosen by', 'Premium per roll', 'Out of the money', 'Held to expiry', 'Rolled over']) {
    await expect(head(text).locator('button.pl-concept'), text).toHaveCount(1)
  }
  await rail(page).getByRole('radiogroup', { name: 'Strike chosen by' }).getByText('Delta').click()
  // ...and by delta the distance field gives way to the delta field.
  await expect(head('Put delta').locator('button.pl-concept')).toHaveCount(1)
})

test('the Bake-off tab explains every header, with both tables open', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await page.getByRole('button', { name: 'Run the signal test' }).click()
  await expect(page.getByRole('table', { name: 'Out-of-sample signal test, run at the rail' })).toBeVisible()
  await page.getByRole('button', { name: /In-sample association/ }).click()
  await page.getByRole('button', { name: 'Run the bake-off' }).click()
  await expect(page.getByRole('table', { name: 'In-sample bake-off' })).toBeVisible()

  expect(await page.locator('.putlab-root th').count()).toBeGreaterThan(30) // it walked something
  expect(await unexplained(page, '.putlab-root th, .putlab-root .pl-stat-k, .putlab-root .pl-readout-k')).toEqual([])
})

test('the Workspace stat row and strip table explain every header and label', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('button', { name: /All \d+ names/ }).click()
  await expect(page.getByRole('table', { name: 'Full fragility ranking' })).toBeVisible()

  expect(await page.locator('.pl-stats .pl-stat-k').count()).toBeGreaterThan(5)
  expect(await unexplained(page, '.pl-stats .pl-stat-k')).toEqual([])
  expect(await page.locator('table[aria-label="Full fragility ranking"] th').count()).toBeGreaterThan(10)
  expect(await unexplained(page, 'table[aria-label="Full fragility ranking"] th')).toEqual([])
})

test('the Workspace per-name sections explain every header and label, with the detail open', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('region', { name: 'Data coverage' }).getByRole('button', { name: 'Show the grid' }).click()
  await expect(page.getByRole('table', { name: 'Data coverage by year' })).toBeVisible()
  const signals = page.getByRole('region', { name: 'How the signals treated this name' })
  await signals.getByRole('button', { name: 'Show the detail' }).click()
  await signals.getByRole('button', { name: /exploratory screens/ }).click()
  await expect(signals.getByRole('table', { name: 'This name by regime at entry' })).toBeVisible()

  const sections =
    '[aria-label="Data coverage"] th, [aria-label="How the signals treated this name"] th, [aria-label="Regime verdict"] .pl-readout-k'
  expect(await page.locator('[aria-label="Data coverage"] th').count()).toBeGreaterThan(10)
  expect(await page.locator('[aria-label="How the signals treated this name"] th').count()).toBeGreaterThan(6)
  expect(await page.locator('[aria-label="Regime verdict"] .pl-readout-k').count()).toBe(6) // hit rate + biggest payoff x 3 regimes
  expect(await unexplained(page, sections)).toEqual([])
  // The quote chart mounted inside the coverage section brings its own table.
  expect(await page.locator('[aria-label="Data coverage"] table[aria-label="Sessions of quotes held by depth"] th').count()).toBeGreaterThan(4)
  expect(await unexplained(page, '[aria-label="Data coverage"] table[aria-label="Sessions of quotes held by depth"] th')).toEqual([])
})

test('the Marks tab explains every header', async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Marks' }).click()
  await expect(page.getByTestId('marks-hero')).toBeVisible()
  expect(await page.locator('table[aria-label="Sessions of quotes held by depth"] th').count()).toBeGreaterThan(4)
  expect(await unexplained(page, '.putlab-root th, .putlab-root .pl-stat-k, .putlab-root .pl-readout-k')).toEqual([])
})

test('the walk fails on a header that explains nothing', async ({ page }) => {
  // The rule itself must be able to fail, or the walks above prove nothing.
  await mockPutLabApi(page)
  await page.goto('/')
  await page.evaluate(() => {
    document.body.insertAdjacentHTML('beforeend', '<table id="probe"><tr><th>Bare</th><th data-noexplain="nonsense">Odd</th><th data-noexplain="index">#</th></tr></table>')
  })
  expect(await unexplained(page, '#probe th')).toHaveLength(2)
})
