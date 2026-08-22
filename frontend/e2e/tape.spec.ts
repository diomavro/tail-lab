import { expect, test } from '@playwright/test'
import { CYCLES, NOTIONAL } from './fixtures/putlab'
import { mockPutLabApi } from './fixtures/mock-api'

// The strategy tape.
//
// The bug worth the test: markers used to filter on `payoff > 0` -- any
// intrinsic value at all -- while `hit_rate` counts `payoff > notional`. So the
// dots on the chart and the stat card directly above them disagreed about how
// many rolls "paid off". The fixture has a roll that returned $420 against a
// $1,000 budget precisely to catch a regression back to the old basis.

test.beforeEach(async ({ page }) => {
  await mockPutLabApi(page)
  await page.goto('/')
})

test('marks only the rolls whose payoff cleared the premium budget', async ({ page }) => {
  const cleared = CYCLES.filter((c) => c.payoff > NOTIONAL).length
  const anyIntrinsic = CYCLES.filter((c) => c.payoff > 0).length
  expect(cleared).toBeLessThan(anyIntrinsic) // the fixture must keep posing the question

  await expect(page.locator('.pl-tape-marker')).toHaveCount(cleared)

  // And the stat card above the tape has to agree with the dots below it.
  await expect(page.locator('.pl-stats div', { hasText: /^Hit rate/ }).first()).toContainText(
    `${Math.round((cleared / CYCLES.length) * 100)}%`,
  )
})

test('plots the underlying on a log axis', async ({ page }) => {
  // A 4-year single-name path is exponential; against a linear axis it reads as
  // a flat line with one late kink. Log ticks are geometrically spaced, so
  // consecutive ratios are equal -- on a linear axis they are not.
  const yticks = page.locator('.pl-chart-price .pl-ytick')
  await expect(yticks.first()).toBeVisible()
  const ticks = await yticks.allTextContents()
  const values = ticks.map((t) => Number(t.replace(/[$,]/g, '')))
  expect(values.length).toBeGreaterThanOrEqual(4)

  const sorted = [...values].sort((a, b) => a - b)
  const ratios = sorted.slice(1).map((v, i) => v / sorted[i]!)
  for (const r of ratios) expect(Math.abs(r - ratios[0]!)).toBeLessThan(0.02)
})

test('keeps axis labels in HTML beside the SVG, not as SVG text', async ({ page }) => {
  // SVG <text> cannot inherit the theme's font stack reliably and cannot wrap.
  await expect(page.locator('.pl-chart svg text')).toHaveCount(0)
  await expect(page.locator('.pl-chart .pl-ytick').first()).toBeVisible()
  await expect(page.locator('.pl-xticks')).toBeVisible()
})

test('stacks a price pane and a P&L pane on one date axis', async ({ page }) => {
  await expect(page.locator('.pl-chart-price svg')).toHaveCount(1)
  await expect(page.locator('.pl-chart-pnl svg')).toHaveCount(1)
  // One strike bar per roll, reset every tenor below spot.
  await expect(page.locator('.pl-tape-strike')).toHaveCount(CYCLES.length)
})

test('bands the tape by regime so a payoff can be read against its backdrop', async ({ page }) => {
  // Regime is VIX LEVEL, not realized volatility: calm < 17, elevated 17-28,
  // crisis >= 28. The fixture timeline has one crisis stretch and two elevated.
  await expect(page.locator('.pl-tape-regime.is-crisis')).toHaveCount(1)
  await expect(page.locator('.pl-tape-regime.is-elevated')).toHaveCount(2)
  // Calm is the ground, so it prints as nothing rather than as a third fill.
  await expect(page.locator('.pl-tape-regime.is-calm')).toHaveCount(0)
})

test('holds the annualized line inside its pane when the early points explode', async ({ page }) => {
  // The first roll annualizes a few weeks of ROI, which is routinely thousands
  // of percent. Scaling the pane to that pins every later point to the axis, so
  // the domain is anchored on 0, the hurdle and the final rate, and early
  // spikes clip to the edge instead of setting the scale.
  const d = await page.locator('.pl-tape-ann').getAttribute('d')
  const ys = [...d!.matchAll(/[ML] [\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]))
  expect(ys.length).toBeGreaterThan(5)
  for (const y of ys) {
    expect(y).toBeGreaterThanOrEqual(0)
    expect(y).toBeLessThanOrEqual(120)
  }
  // The final rate must not be crushed against an edge by the early spike: it is
  // the number the headline converges on.
  const last = ys[ys.length - 1]!
  expect(last).toBeGreaterThan(12)
  expect(last).toBeLessThan(108)
})

test('keeps the dollar and rate scales in separate gutters', async ({ page }) => {
  // Two different units in one pane, so they cannot share a gutter -- overlaid,
  // "$0" and "+11%/yr" print on top of each other.
  const dollars = (await page.locator('.pl-chart-pnl .pl-ytick').first().boundingBox())!
  const rate = (await page.locator('.pl-chart-pnl .pl-ytick-rate').first().boundingBox())!
  const disjoint = rate.x + rate.width <= dollars.x || dollars.x + dollars.width <= rate.x
  expect(disjoint).toBe(true)
})

test('never stacks two dollar ticks on top of each other', async ({ page }) => {
  // The P&L pane's anchors are max, zero and min, and any two of them can land
  // within a few pixels — a curve that never goes positive puts max and zero on
  // the same row, and one dominated by a single crisis spike puts zero and min
  // there. Overlapping labels are unreadable and, worse, look like one number.
  const ticks = page.locator('.pl-chart-pnl .pl-ytick:not(.pl-ytick-rate)')
  await expect(ticks.first()).toBeVisible()
  const boxes = await ticks.evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom }
    }),
  )
  expect(boxes.length).toBeGreaterThan(0)
  const sorted = [...boxes].sort((a, b) => a.top - b.top)
  for (let i = 1; i < sorted.length; i++) {
    expect(sorted[i]!.top, `tick ${i}`).toBeGreaterThanOrEqual(sorted[i - 1]!.bottom - 1)
  }
})
