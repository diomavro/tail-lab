import { defineConfig, devices } from '@playwright/test'

// Minimal e2e config for the tail-lab dashboard.
//
// Base URL resolution (first match wins):
//   1. PLAYWRIGHT_BASE_URL env var — set this to point at a locally running
//      full stack (backend + built SPA on one origin, same as the Fly
//      deploy):
//        make api    # uvicorn on :8000, TAIL_LAB_STATIC_DIR=frontend/dist
//        PLAYWRIGHT_BASE_URL=http://localhost:8000 npm run e2e
//      NOTE: a bare `npm run build && npm run preview` is NOT enough on its
//      own — vite preview only serves static files, so /api/vix/stretch
//      falls through to the SPA's index.html (200, but HTML, not JSON) and
//      the tile lands in its error state. The backend has to be the one
//      serving frontend/dist/ (set TAIL_LAB_STATIC_DIR, per the Dockerfile)
//      for the API route to answer for real.
//   2. The deployed app, https://tail-lab.fly.dev — the default, so
//      `npm run e2e` with no setup exercises the real deployment. This
//      makes the suite network-dependent by default, which is why it is
//      NOT wired into CI (see .github/workflows/ci.yml) — run it manually
//      or from a separate, opt-in workflow.
//
// The deployed Fly machine auto-stops when idle (fly.toml
// `auto_stop_machines`), so the first request after a cold start can take
// several seconds to wake the machine — the spec's expect() timeouts are
// widened to absorb that instead of racing it.
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'https://tail-lab.fly.dev'

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
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
