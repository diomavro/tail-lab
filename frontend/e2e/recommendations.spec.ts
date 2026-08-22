import { expect, test } from '@playwright/test'
import { LEADERBOARD, MODEL_PRICED_MAX } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The Recommendations view: each name's best strategy, ranked against each
// other, stated in full.
//
// The thing that makes this view honest is that every figure on a row comes
// from the SAME (strike, tenor) run. Ranking on an argmax return while showing
// a hit rate from whatever strike the rail happens to be set to would describe
// two different strategies on one line — the same class of bug as the ledger's
// Fill-vs-Budget column and the tape's marker basis.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Recommendations' }).click()
})

test('ranks every name by its own best strategy', async ({ page }) => {
  const rows = page.getByRole('table', { name: 'Ranked strategies' }).locator('tbody tr')
  await expect(rows).toHaveCount(LEADERBOARD.ranked.length)

  const returns = await rows.locator('td.pl-rec-ann').allTextContents()
  const asNumbers = returns.map((t) => Number(t.replace('−', '-').replace('%', '')))
  expect(asNumbers).toEqual([...asNumbers].sort((a, b) => b - a))

  // Ranked by best-cell return, NOT by fragility — the ranking strip already
  // answers "most fragile", and this view answers a different question.
  const byFragility = LEADERBOARD.ranked.map((r) => r.asset.toUpperCase())
  const byReturn = [...LEADERBOARD.ranked]
    .sort((a, b) => (b.best_annualized ?? -9) - (a.best_annualized ?? -9))
    .map((r) => r.asset.toUpperCase())
  expect(byReturn).not.toEqual(byFragility)
  await expect(rows.first()).toContainText(byReturn[0]!)
})

test('states each strategy in full — ticker, strike, and roll frequency', async ({ page }) => {
  const top = LEADERBOARD.ranked.reduce((a, b) =>
    (b.best_annualized ?? -9) > (a.best_annualized ?? -9) ? b : a,
  )
  const row = page.getByRole('table', { name: 'Ranked strategies' }).locator('tbody tr').first()

  await expect(row).toContainText(top.asset.toUpperCase())
  await expect(row).toContainText(`${top.best_moneyness_pct}% out of the money`)
  // The tenor is stated as a rolling cadence, not a bare number of weeks: the
  // instruction a reader has to act on is "how often do I re-buy this".
  await expect(row).toContainText(`rolled every ${top.best_tenor_weeks} weeks`)
})

test('takes every figure on a row from that same cell', async ({ page }) => {
  const top = LEADERBOARD.ranked.reduce((a, b) =>
    (b.best_annualized ?? -9) > (a.best_annualized ?? -9) ? b : a,
  )
  const row = page.getByRole('table', { name: 'Ranked strategies' }).locator('tbody tr').first()

  await expect(row.locator('td.pl-rec-hit')).toHaveText(`${Math.round(top.best_hit_rate! * 100)}%`)
  await expect(row.locator('td.pl-rec-rolls')).toHaveText(String(top.best_n_cycles))
  await expect(row.locator('td.pl-rec-verdict')).toContainText(top.best_verdict!.replace('_', ' '))
  // The screened-cell hit rate is a different number and must not appear here.
  expect(top.best_hit_rate).not.toBe(top.hit_rate)
})

test('never recommends a strike the model cannot price', async ({ page }) => {
  const strikes = await page
    .getByRole('table', { name: 'Ranked strategies' })
    .locator('.pl-rec-strike')
    .allTextContents()
  expect(strikes.length).toBeGreaterThan(0)
  for (const s of strikes) {
    expect(Number(s.replace('%', ''))).toBeLessThanOrEqual(MODEL_PRICED_MAX)
  }
})

test('says plainly that it is a screen, not advice', async ({ page }) => {
  // tail-lab never trades (adr/0007). A page called "Recommendations" that did
  // not say what it is would be the single most misreadable surface here.
  // Scoped to the panel: the dateline carries a .pl-caveat of its own.
  const caveat = page.getByRole('tabpanel').locator('.pl-caveat').first()
  await expect(caveat).toContainText('in sample')
  await expect(caveat).toContainText('not advice')
  await expect(page.getByRole('tabpanel')).toContainText('you place the trade by hand')
})

test('opens the workspace on the exact strategy a row describes', async ({ page }) => {
  const row = page.getByRole('table', { name: 'Ranked strategies' }).locator('tbody tr').first()
  const top = LEADERBOARD.ranked.reduce((a, b) =>
    (b.best_annualized ?? -9) > (a.best_annualized ?? -9) ? b : a,
  )
  await row.click()

  await expect(page.getByRole('tab', { name: 'Workspace' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByLabel('Out of the money, %')).toHaveValue(String(top.best_moneyness_pct))
})

test('prints fragility on a readable scale, not rounded to 0 or 1', async ({ page }) => {
  // `fragility_score` is a fractional rank in 0..1 (research/backtest/ranking.py),
  // so `Math.round` on it collapses the whole universe onto two values.
  const shown = await page
    .getByRole('table', { name: 'Ranked strategies' })
    .locator('td.pl-rec-frag')
    .allTextContents()
  expect(shown.length).toBe(LEADERBOARD.ranked.length)
  expect(new Set(shown).size).toBeGreaterThan(2)
  for (const t of shown) {
    const n = Number(t)
    expect(Number.isFinite(n)).toBe(true)
    expect(n).toBeGreaterThanOrEqual(0)
    expect(n).toBeLessThanOrEqual(100)
  }
})
