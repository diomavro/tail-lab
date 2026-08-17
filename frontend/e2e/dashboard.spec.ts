import { test, expect } from '@playwright/test'

// Guards the one dashboard tile against silent regressions: the page must
// load, the VIX-stretch tile must resolve past its loading state and show a
// real z-score (not get stuck on "Loading…" or fall into its error state),
// and the load must not throw or log a console error along the way.
//
// Widened timeouts: the deployed Fly machine auto-stops when idle
// (fly.toml `auto_stop_machines = "stop"`), so a cold request can take
// several seconds to wake it before /api/vix/stretch responds.

test('VIX stretch tile renders a z-score with no console errors', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(String(err)))

  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'tail-lab', level: 1 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'VIX stretch', level: 2 })).toBeVisible()

  // Loading/error copy must not be what we end up on.
  await expect(page.getByText('Loading…')).toBeHidden({ timeout: 30_000 })
  await expect(page.locator('.tile-error')).toHaveCount(0)

  // The z-score renders as e.g. "-0.73σ" or "1.20σ".
  await expect(page.locator('.tile-zscore')).toHaveText(/^-?\d+\.\d{2}σ$/, { timeout: 30_000 })

  // The stat rows (date/close/rolling mean/rolling std) all have values.
  await expect(page.locator('.tile dd')).toHaveCount(4)

  expect(consoleErrors).toEqual([])
})
