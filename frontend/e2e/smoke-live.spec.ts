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

// The whole-test budget covers the slowest wait below plus the page load.
test.describe.configure({ timeout: 300_000 })

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
  // `rank-pick`, not any strip button: the strip's bake-off line carries a
  // button of its own that shows before the ranking lands. After a deploy or
  // process restart (a Fly suspend/resume keeps the process, so no warm runs)
  // this wait can queue behind the startup warms on the server's one screen
  // slot: the ranking warm alone took 85s on the 2026-10-12 cold deploy.
  await expect(page.getByTestId('rank-pick').first()).toBeVisible({ timeout: 120_000 })

  await expect(page.getByRole('region', { name: 'Result accuracy' })).toBeVisible()
})

test('the metric-screen endpoint answers the bake-off', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('tab', { name: 'Bake-off' }).click()
  // Top 3, not the tab's default Top 5: 5% · 4w · 4y · Top 5 is the bake-off
  // the server pre-warms for the Workspace's line, so it would come from the
  // memo and never exercise the compute this test exists to check. A same-day
  // repeat of Top 3 may still be a memo hit (any key is served for 24h).
  await page.getByRole('radiogroup', { name: 'Basket size' }).getByText('Top 3').click()
  await page.getByRole('button', { name: 'Run the bake-off' }).click()

  // One server-side backtest per screened name (up to 70), about 53s. After a
  // deploy or restart it queues behind the startup warms on the one screen
  // slot: on the 2026-10-12 cold deploy it waited 104s (the 85s ranking warm,
  // then the Workspace's fixed Bake-off) before computing, ~155s in all.
  await expect(page.getByRole('table', { name: 'Metric bake-off' })).toBeVisible({ timeout: 240_000 })
  await expect(page.locator('.pl-bake-verdict')).toBeVisible()
})
