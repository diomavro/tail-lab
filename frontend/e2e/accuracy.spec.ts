import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// "How wrong is this?" and "did it only work in one regime?" — the two panels
// the constitution requires to sit beside the result rather than behind a
// toggle. A silent accuracy panel is indistinguishable from an accurate result,
// which is the exact failure mode the principle exists to prevent.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('states the error bar beside the result, not behind a toggle', async ({ page }) => {
  const acc = page.getByRole('region', { name: 'Result accuracy' })
  await expect(acc).toBeVisible()
  await expect(acc).toContainText('too cheap')
  await expect(acc).toContainText('overstated by ~1.34%/yr')
  await expect(acc.locator('.pl-acc-badge')).toContainText('direct')
  await expect(acc.locator('.pl-acc-badge')).toContainText('PPUT')
})

test('says the result is model-priced even while real quotes are held', async ({ page }) => {
  // The misreading this block exists to prevent. The fixture holds a real
  // quote panel for SPY, and the displayed backtest still does not use it --
  // the roll engine prices every leg with Black-Scholes at a flat vol
  // (docs/adr/0004). A surface that showed only "real quotes: yes" would make
  // the number look more trustworthy than it is.
  const prov = page.getByRole('region', { name: 'Quote provenance' })
  await expect(prov).toBeVisible()
  await expect(prov).toContainText('Priced from the model')
  // Coverage is stated against THIS result's window, not the whole panel:
  // the same panel is total coverage of a 2015 backtest and 16/49 of this one.
  await expect(prov).toContainText('16 of this window’s 49 months')
  await expect(prov.locator('.pl-acc-flag')).toContainText('33 missing')
})

test('lets the per-regime residual flip sign instead of ramping', async ({ page }) => {
  // MODEL_RESIDUAL.md's headline finding is that the residual FLIPS SIGN in a
  // crisis rather than ramping monotonically from calm. A UI that only ever
  // prints one sign has quietly reintroduced the ramp.
  const mix = page.locator('.pl-mix-legend')
  await expect(mix.locator('li', { hasText: 'calm' })).toContainText('+2.19%/yr')
  await expect(mix.locator('li', { hasText: 'crisis' })).toContainText('−1.46%/yr')
  await expect(page.locator('.pl-mix-bar > span')).toHaveCount(3)
})

test('names what could have been bought instead', async ({ page }) => {
  const acc = page.getByRole('region', { name: 'Result accuracy' })
  await expect(acc.getByRole('table')).toContainText('PPUT3M')
  await expect(acc).toContainText('What this rests on (3)')
})

test('still reports itself when the accuracy read fails', async ({ page }) => {
  await page.unrouteAll()
  await mockPutLabApi(page, { fail: ['/api/putlab/accuracy'] })
  await page.reload()
  const acc = page.getByRole('region', { name: 'Result accuracy' })
  await expect(acc).toBeVisible()
  await expect(acc).toContainText('read the returns as unqualified')
  // And the hero's error-bar row must not silently print a number it does not
  // have.
  await expect(page.locator('.pl-brokenout-row', { hasText: 'Error bar' })).toContainText('—')
})

test('splits the verdict by the regime each roll was entered in', async ({ page }) => {
  const mem = page.getByRole('region', { name: 'Regime verdict' })
  await expect(mem).toContainText('regime only')
  await expect(mem).toContainText('paid off in only one regime')
  // Calm and elevated bled, crisis paid -- that is what makes it regime-only
  // rather than confirmed.
  await expect(mem.locator('.pl-mem-card', { hasText: 'calm' })).toContainText('−72%')
  await expect(mem.locator('.pl-mem-card', { hasText: 'crisis' })).toContainText('+186%')
  await expect(mem.locator('.pl-mem-card')).toHaveCount(3)
})
