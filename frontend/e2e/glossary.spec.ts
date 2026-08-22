import { expect, test } from '@playwright/test'
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  CONCEPTS,
  CONCEPT_LIST,
} from '../src/content/concepts'
import { mockPutLabApi } from './fixtures/mock-api'

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

test('keeps the see-also links pointing at real entries', async ({ page }) => {
  const roi = CONCEPTS.roi_on_premium!
  const entry = page.locator('#roi_on_premium')
  const links = entry.locator('.pl-gloss-see a')
  await expect(links).toHaveCount(roi.seeAlso?.length ?? 0)
  for (const id of roi.seeAlso ?? []) {
    await expect(page.locator(`#${id}`)).toHaveCount(1)
  }
})
