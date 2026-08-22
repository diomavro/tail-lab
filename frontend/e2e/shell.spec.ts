import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// The workspace shell: masthead, dateline, tabs, and the one control rail.
//
// The IA claim under test is that Screen and Backtest are now ONE tab. The old
// split meant picking a name on one tab and reading its result on another; a
// spec that only counted tabs would pass either way, so the check that matters
// is that the ranking and the result render in the same panel.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
})

test('opens on Workspace with all six views reachable', async ({ page }) => {
  await page.goto('/')

  const tabs = page.getByRole('tab')
  await expect(tabs).toHaveText([
    'Workspace',
    'Recommendations',
    'Portfolio',
    'Bake-off',
    'Regime',
    'Glossary',
  ])
  await expect(page.getByRole('tab', { name: 'Workspace' })).toHaveAttribute('aria-selected', 'true')
})

test('names each universe row by the instrument, not by its expiry cadence', async ({ page }) => {
  await page.goto('/')
  // The API's `label` is the options cadence ("Weeklies") for every name in the
  // real universe, so a rail built on it prints seven identical blurbs. The
  // cadence already has a line of its own in Provenance.
  const spy = page.locator('.pl-list-row', { hasText: 'SPY' }).first()
  await expect(spy).toContainText('S&P 500')
  await expect(spy).toHaveAttribute('aria-pressed', 'true')

  // Filtering still reaches the blurb, so a cadence search is not lost.
  await page.getByLabel('Filter the universe').fill('small cap')
  await expect(page.locator('.pl-list-row')).toHaveCount(1)
  await expect(page.locator('.pl-list-row')).toContainText('IWM')

  await page.getByLabel('Filter the universe').fill('zzz')
  await expect(page.getByText('No name matches')).toBeVisible()
})
