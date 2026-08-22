// Serves the fixture payloads to the browser so the UI suite is hermetic: no
// lake, no backend, no 35-backtest bake-off round-trip. The router matches on
// pathname only and reads query params where a fixture varies by them, which
// keeps the specs asserting on rendered behaviour rather than on fetch calls.

import type { Page, Route } from '@playwright/test'
import * as fx from './putlab'

export interface MockOptions {
  /** Endpoints to fail with a 500, by pathname (e.g. '/api/putlab/sweep'). */
  fail?: string[]
  /** Endpoints to answer 404 -- the client maps this to its 'no-data' state. */
  noData?: string[]
  /** Never resolve, so the loading state stays on screen for assertions. */
  hang?: string[]
}

const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

export async function mockPutLabApi(page: Page, opts: MockOptions = {}): Promise<void> {
  const requests: string[] = []
  // Matched by predicate, not by glob: '**/api/**' also swallows Vite's own
  // module requests for src/api/client.ts and the app never boots.
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    requests.push(path)

    if (opts.hang?.includes(path)) return // deliberately never fulfilled
    if (opts.fail?.includes(path)) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
    }
    if (opts.noData?.includes(path)) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"no data"}' })
    }

    switch (path) {
      case '/api/putlab/universe':
        return json(route, fx.UNIVERSE)
      case '/api/putlab/backtest':
        return json(route, {
          ...fx.BACKTEST,
          asset: url.searchParams.get('asset') ?? 'spy',
          notional: Number(url.searchParams.get('notional') ?? fx.NOTIONAL),
          moneyness_pct: Number(url.searchParams.get('moneyness_pct') ?? 5),
          tenor_weeks: Number(url.searchParams.get('tenor_weeks') ?? 4),
        })
      case '/api/putlab/sweep':
        return json(route, fx.SWEEP)
      case '/api/putlab/accuracy':
        return json(route, fx.ACCURACY)
      case '/api/putlab/cadence':
        return json(route, fx.CADENCE)
      case '/api/putlab/data-quality':
        return json(route, fx.DATA_QUALITY)
      case '/api/putlab/leaderboard':
        return json(route, fx.LEADERBOARD)
      case '/api/putlab/regime-verdict':
        return json(route, fx.REGIME_VERDICT)
      case '/api/putlab/regimes':
        return json(route, fx.REGIMES)
      case '/api/vix/stretch':
        return json(route, fx.VIX)
      case '/api/putlab/portfolio':
        return json(route, fx.PORTFOLIO)
      case '/api/putlab/metric-screen':
        // The two branches of the bake-off verdict are selected by basket size:
        // at Top 5 the composite beats the baseline, at Top 3 nothing does.
        return json(
          route,
          url.searchParams.get('top_k') === '3' ? fx.METRIC_SCREEN_NO_WINNER : fx.METRIC_SCREEN_WINNER,
        )
      case '/api/feedback':
        return json(route, { id: 'fb1', text: 'ok', kind: 'issue', created_at: '2026-08-21T00:00:00Z', status: 'open', resolved_at: null })
      default:
        return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"unmocked"}' })
    }
    },
  )
  // Exposed for specs that need to prove a request was (or was not) made --
  // e.g. the bake-off must not fire until the Run button is pressed.
  ;(page as Page & { apiRequests?: string[] }).apiRequests = requests
}

export function apiRequests(page: Page): string[] {
  return (page as Page & { apiRequests?: string[] }).apiRequests ?? []
}
