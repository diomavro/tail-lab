// Serves the fixture payloads to the browser so the UI suite is hermetic: no
// lake, no backend, no bake-off round-trip (one backtest per screened name). The router matches on
// pathname only and reads query params where a fixture varies by them, which
// keeps the specs asserting on rendered behaviour rather than on fetch calls.

import type { Page, Route } from '@playwright/test'
import { BOOK_PLAN, BOOK_PLAN_BILLS_REFUSED } from './book-plan'
import { HEDGE_OVERLAY } from './hedge-overlay'
import { MARKS } from './marks'
import { coverage, nameRecord } from './name-record'
import { MODEL_PLAN } from './model-plan'
import * as fx from './putlab'
import { SIGNAL_TEST_BEATS, SIGNAL_TEST_CRASH_CARRIED, SIGNAL_TEST_INSUFFICIENT, SIGNAL_TEST_NO_BETTER } from './signal-test'

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

    // Per-name reads carry the symbol in the path.
    const name = /^\/api\/putlab\/(coverage|signal-test)\/([^/]+)$/.exec(path)
    if (name) {
      const symbol = decodeURIComponent(name[2]!).toLowerCase()
      if (name[1] === 'coverage') return json(route, coverage(symbol))
      const rec = nameRecord(symbol)
      return rec
        ? json(route, rec)
        : route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"not in this signal test"}' })
    }

    switch (path) {
      case '/api/putlab/universe':
        return json(route, fx.UNIVERSE)
      case '/api/putlab/backtest':
        if (url.searchParams.get('strike_rule') === 'delta') {
          return json(route, {
            ...fx.backtestByDelta(Number(url.searchParams.get('target_delta') ?? 0.1)),
            asset: url.searchParams.get('asset') ?? 'spy',
            tenor_weeks: Number(url.searchParams.get('tenor_weeks') ?? 4),
          })
        }
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
        // Memory records moneyness rules only: a delta verdict has no hash.
        return json(
          route,
          url.searchParams.get('strike_rule') === 'delta' ? { ...fx.REGIME_VERDICT, rule_hash: null } : fx.REGIME_VERDICT,
        )
      case '/api/putlab/strike-preview':
        return json(route, {
          ...fx.strikePreview(Number(url.searchParams.get('target_delta') ?? 0.1)),
          asset: url.searchParams.get('asset') ?? 'spy',
        })
      case '/api/putlab/marks':
        return json(route, { ...MARKS, symbol: url.searchParams.get('symbol') ?? 'spy' })
      case '/api/putlab/regimes':
        return json(route, fx.REGIMES)
      case '/api/vix/stretch':
        return json(route, fx.VIX)
      case '/api/putlab/portfolio':
        return json(route, fx.PORTFOLIO)
      case '/api/putlab/metric-screen':
        // The two branches of the bake-off verdict are selected by basket size:
        // at Top 5 the composite beats the baseline, at Top 3 nothing does.
        return json(route, {
          ...(url.searchParams.get('top_k') === '3' ? fx.METRIC_SCREEN_NO_WINNER : fx.METRIC_SCREEN_WINNER),
          ...(url.searchParams.get('strike_rule') === 'delta'
            ? {
                strike_rule: 'delta',
                target_delta: Number(url.searchParams.get('target_delta')),
                moneyness_pct: null,
              }
            : {}),
        })
      case '/api/putlab/signal-test': {
        // The fixed strip read (0.10Δ, top 5) is the no-better-than-random
        // instance; a run at any other rule is the beats-random one, a 3-year
        // signal window at a distance rule is the crash-carried one, and Top 3
        // is the thin-history one. The request's own settings are echoed, as
        // the real route does.
        const q = url.searchParams
        const base =
          q.get('top_k') === '3'
            ? SIGNAL_TEST_INSUFFICIENT
            : q.get('strike_rule') === 'delta'
              ? SIGNAL_TEST_NO_BETTER
              : q.get('signal_years') === '3'
                ? SIGNAL_TEST_CRASH_CARRIED
                : SIGNAL_TEST_BEATS
        const delta = q.get('strike_rule') === 'delta'
        // The beyond-trailing-vol test is confirmatory at the pre-registered
        // setting only (0.10 delta, 4w, 4y, top 5; the fixtures are as of the
        // pre-registered date), as the server decides it.
        const confirmatory =
          delta &&
          Number(q.get('target_delta')) === 0.1 &&
          Number(q.get('tenor_weeks')) === 4 &&
          Number(q.get('signal_years')) === 4 &&
          Number(q.get('top_k')) === 5
        return json(route, {
          ...base,
          beyond_vol_mode: confirmatory ? 'confirmatory' : 'exploratory',
          strike_rule: delta ? 'delta' : 'moneyness',
          target_delta: delta ? Number(q.get('target_delta')) : null,
          moneyness_pct: delta ? null : Number(q.get('moneyness_pct')),
          tenor_weeks: Number(q.get('tenor_weeks')),
          signal_years: Number(q.get('signal_years')),
          top_k: Number(q.get('top_k')),
        })
      }
      case '/api/putlab/surface':
        // TSLA is the all-refused payload; every other name is the fitted one.
        return json(route, url.searchParams.get('asset') === 'tsla' ? fx.SURFACE_REFUSED : fx.SURFACE_FITTED)
      case '/api/putlab/hedge-overlay':
        return json(route, HEDGE_OVERLAY)
      case '/api/putlab/book-plan/model':
        return json(route, MODEL_PLAN)
      case '/api/putlab/book-plan':
        // T-bills is the one comparator this fixture lake cannot offer.
        return json(route, url.searchParams.get('comparator') === 'bills' ? BOOK_PLAN_BILLS_REFUSED : BOOK_PLAN)
      case '/api/feedback':
        return json(route, { id: 'fb1', text: 'ok', kind: 'issue', created_at: '2026-08-21T00:00:00Z', status: 'open', resolved_at: null })
      default:
        return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"unmocked"}' })
    }
    },
  )
  // Exposed for specs that need to prove a request was (or was not) made --
  // e.g. the Bake-off tab must not run its own bake-off until the Run button
  // is pressed (the Workspace's strip reads one fixed-parameter bake-off on
  // load, so count from the tab's arrival).
  ;(page as Page & { apiRequests?: string[] }).apiRequests = requests
}

export function apiRequests(page: Page): string[] {
  return (page as Page & { apiRequests?: string[] }).apiRequests ?? []
}
