import { expect, test } from '@playwright/test'

// The one spec that talks to a real backend. Everything else in this directory
// is mocked from e2e/fixtures/, which is what keeps the suite CI-safe and lets
// it assert on both branches of the bake-off verdict in the same run.
//
// This one exists because a mocked suite can only prove the UI is right about
// the shapes it was handed. It cannot catch a renamed field, a route that moved,
// or a lake that has no data for the default asset. So: opt-in, and deliberately
// shallow -- it checks that the real service answers and that the workspace
// reaches a result, not what the numbers are.
//
//   PLAYWRIGHT_LIVE=1 PLAYWRIGHT_BASE_URL=https://tail-lab.fly.dev \
//     npm run e2e -- smoke-live
//
// Timeouts are wide because the Fly machine auto-stops when idle (fly.toml
// `auto_stop_machines`), so the first request has to wake it.

test.skip(!process.env.PLAYWRIGHT_LIVE, 'set PLAYWRIGHT_LIVE=1 to run against a real backend')

test.describe.configure({ timeout: 120_000 })

test('the deployed workspace reaches a real result', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('tab', { name: 'Workspace' })).toBeVisible({ timeout: 60_000 })
  // The dateline only fills in once the backtest resolves, so a real spot price
  // here means the whole read path worked: lake -> pricer -> API -> view.
  await expect(page.locator('.pl-dateline')).toContainText(/\$\d/, { timeout: 60_000 })
  await expect(page.locator('.pl-hero-num')).toHaveText(/^[+−]\d/, { timeout: 60_000 })

  // The sweep and the ranking are the two slowest reads; if either 500s, the
  // view says so rather than rendering blank.
  await expect(page.locator('.pl-sweep-cell').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.pl-rank-strip button').first()).toBeVisible({ timeout: 60_000 })

  await expect(page.getByRole('region', { name: 'Result accuracy' })).toBeVisible()
})

test('the metric-screen endpoint answers the bake-off', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  await page.getByRole('button', { name: 'Run the bake-off' }).click()

  // ~35 server-side backtests. This is the first frontend caller this endpoint
  // has ever had, so it is the one worth exercising for real.
  await expect(page.getByRole('table', { name: 'Metric bake-off' })).toBeVisible({ timeout: 110_000 })
  await expect(page.locator('.pl-bake-verdict')).toBeVisible()
})
