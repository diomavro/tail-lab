import { expect, test } from '@playwright/test'
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  CONCEPTS,
  CONCEPT_LIST,
} from '../src/content/concepts'
import { mockPutLabApi } from './fixtures/mock-api'
import { putDelta } from './fixtures/putlab'

// The glossary renders content/concepts.ts and nothing else.
//
// It is rendered VERBATIM on purpose. concepts.ts is the source of truth for
// what every figure on the site means, and paraphrasing it into the view is how
// the four grounding errors this redesign corrects got in: a hit rate described
// as "finished in the money", regime bands described as realized volatility, an
// ROI described as net of brokerage.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Glossary' }).click()
})

test('carries every concept, grouped by category', async ({ page }) => {
  await expect(page.locator('.pl-gloss-entry')).toHaveCount(CONCEPT_LIST.length)
  for (const cat of CATEGORY_ORDER) {
    await expect(page.getByRole('heading', { name: CATEGORY_LABEL[cat] })).toBeVisible()
  }
})

test('prints each entry verbatim rather than paraphrasing it', async ({ page }) => {
  const hit = CONCEPTS.hit_rate!
  // By id, not by text: "Hit rate" also appears inside other entries' see-also
  // lists, and matching on the term picks whichever came first.
  const entry = page.locator('#hit_rate')
  await expect(entry).toContainText(hit.short)
  await expect(entry).toContainText(hit.intuition)
  await expect(entry).toContainText(hit.howToRead)
  if (hit.formula) await expect(entry.locator('.pl-gloss-formula')).toHaveText(hit.formula)
})

test('every see-also id in every entry names a real entry', () => {
  // A dangling id renders a dead #anchor and nothing else catches it; the
  // thread a reader follows (dividend yield -> pricing -> ...) runs on these.
  for (const concept of Object.values(CONCEPTS)) {
    for (const id of concept.seeAlso ?? []) {
      expect(CONCEPTS[id], `${concept.id} links to missing '${id}'`).toBeDefined()
    }
  }
})

test('keeps the see-also links pointing at real entries', async ({ page }) => {
  const roi = CONCEPTS.roi_on_premium!
  const entry = page.locator('#roi_on_premium')
  const links = entry.locator('.pl-gloss-see a')
  await expect(links).toHaveCount(roi.seeAlso?.length ?? 0)
  for (const id of roi.seeAlso ?? []) {
    await expect(page.locator(`#${id}`)).toHaveCount(1)
  }
})

test('the at-the-money delta condition is the one the formula gives', () => {
  // A put struck at spot has |Δ| = e^(-qT)·N(-d1), d1 = (r - q + σ²/2)√T/σ:
  // past 0.50 needs d1 < 0, i.e. q > r + σ²/2 -- q merely above r is not enough.
  const atm = (q: number, r: number, sigma: number, T: number) => -putDelta(100, 100, sigma, T, r, q)
  expect(atm(0.05, 0.04, 0.2, 1 / 12)).toBeLessThan(0.5) // q > r, but under r + σ²/2 = 6%
  expect(atm(0.065, 0.04, 0.2, 1 / 12)).toBeGreaterThan(0.5) // past it, short-dated
  expect(atm(0.065, 0.04, 0.2, 1)).toBeLessThan(0.5) // past it, but e^(-qT) pulls a year back under
  const text = CONCEPTS.put_delta!.howToRead
  expect(text).toContain('q > r + σ²/2')
  expect(text).toContain('short-dated')
  expect(text).not.toContain('exceeds the rate')
})
