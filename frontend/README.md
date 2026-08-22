# frontend

The Put Lab workspace: React 19 + TypeScript (strict) + Vite, talking to the
FastAPI backend over HTTP only. `ARCHITECTURE.md` §Frontend has the layout rules
and `docs/adr/0017` the reasoning behind the current shape.

```bash
npm run dev        # Vite dev server (proxies /api to :8000 — see vite.config.ts)
npm run typecheck  # tsc -b --noEmit
npm run lint       # oxlint
npm run build      # tsc -b && vite build   (a CI gate)
npm run e2e        # Playwright
```

## The e2e suite

`npm run e2e` is **hermetic**. Playwright starts its own dev server on `:5178`
and `e2e/fixtures/mock-api.ts` answers every `/api/**` call from
`e2e/fixtures/putlab.ts`. No lake, no backend, no network — so it runs in about
30 seconds as a **CI gate** (the `e2e` job in `.github/workflows/ci.yml`; a
deploy is gated on it), and its assertions are about rendered behaviour rather
than about whatever the lake happens to hold today.

That last part is the point. A live backend cannot be relied on to produce a
roll that returned real intrinsic value *below* its premium budget, or a sweep
spanning all three verdict bands, or both branches of the bake-off's
beats-the-baseline copy — and those are exactly the cases worth pinning. The
fixtures are built to pose those questions; every number in them is invented and
the file says so.

```bash
npm run e2e                                     # hermetic, starts its own server
npm run e2e -- bakeoff                          # one spec
PLAYWRIGHT_BASE_URL=http://localhost:5173 \
  npm run e2e                                   # reuse a dev server you have
```

**Against a real backend.** `e2e/smoke-live.spec.ts` skips itself unless
`PLAYWRIGHT_LIVE=1`. It is deliberately shallow — it checks that the service
answers and the workspace reaches a result, not what the numbers are — and it
exists to catch what a mocked suite structurally cannot: a route that moved, a
renamed field, a lake with no data for the default asset.

```bash
# against the deployment
PLAYWRIGHT_LIVE=1 PLAYWRIGHT_BASE_URL=https://tail-lab.fly.dev \
  npm run e2e -- smoke-live

# against a local backend serving this build (from the repo root):
#   npm run build --prefix frontend
#   env -u PYTHONPATH .venv/bin/uvicorn tail_lab.api.main:app --port 8020
PLAYWRIGHT_LIVE=1 PLAYWRIGHT_BASE_URL=http://localhost:8020 \
  npm run e2e -- smoke-live
```

The Fly machine auto-stops when idle (`fly.toml`), so the first live request has
to wake it — the live spec's timeouts are widened to absorb that.

## Writing a spec

Locate by role and accessible name, not by class, except where the class *is*
the assertion (`.pl-sweep-cell.is-loss` says which band a cell landed in — that
is the behaviour). Two Playwright details this suite has already been bitten by:

- Route mocks must match on `url.pathname.startsWith('/api/')`, not the glob
  `**/api/**` — the glob also swallows Vite's module request for
  `src/api/client.ts` and the app never boots.
- `allTextContents()` does not auto-wait. Assert a count first.
