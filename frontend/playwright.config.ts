import { defineConfig, devices } from '@playwright/test'

// e2e config for the tail-lab dashboard.
//
// The default run is HERMETIC: Playwright starts the Vite dev server on :5178
// and every /api/** call is answered from e2e/fixtures/ (see mock-api.ts). No
// lake, no backend, no network — so the suite is CI-safe and its assertions are
// about rendered behaviour rather than about whatever the lake happens to hold.
// A live backtest takes seconds and the bake-off runs ~35 of them; mocking is
// what makes it possible to assert on both branches of the bake-off verdict in
// the same suite.
//
//   npm run e2e                                    # hermetic, starts its own dev server
//   PLAYWRIGHT_BASE_URL=http://localhost:5173 \
//     npm run e2e                                  # reuse a dev server you already have
//   PLAYWRIGHT_LIVE=1 PLAYWRIGHT_BASE_URL=https://tail-lab.fly.dev \
//     npm run e2e -- smoke-live                    # the one unmocked smoke spec
//
// The live smoke spec (e2e/smoke-live.spec.ts) skips itself unless
// PLAYWRIGHT_LIVE is set, so it never makes the default run network-dependent.
// Its timeouts are widened because the Fly machine auto-stops when idle
// (fly.toml `auto_stop_machines`) and a cold request has to wake it first.

const PORT = 5178
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${PORT}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  timeout: 30_000,
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  // Only manage a server when we are the ones choosing the URL. An explicit
  // PLAYWRIGHT_BASE_URL means the caller already has something running.
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: `npm run dev -- --port ${PORT} --strictPort`,
        url: `http://localhost:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
