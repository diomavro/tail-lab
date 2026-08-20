# Human backlog

This is Dio's queue — account setup, paid services, and anything else only
a human can action. It is separate from `AGENT_TODO.md` (`docs/adr/0002`).
**The agent appends items here when it hits a real-world blocker; it never
acts on, removes, or reorders anything in this file.**

## Setup needed for v1

- [x] Create the GitHub repo `diomavro/tail-lab`, push `main`, enable
      Actions-can-create-PRs, add `CLAUDE_CODE_OAUTH_TOKEN`.
      **Done 2026-08-17** (private repo, PR-creation enabled, secret reuses
      the subscription token from the quizkit setup). Remaining before the
      daily agent runs: commit its GitHub Actions workflow (mirrors quizkit's
      hardened pattern; uses `docs/AGENT_MISSION.md` as the prompt).
- [x] ~~Create a lakeFS Cloud account + repo for the tail-lab lakehouse~~.
      **No longer needed — 2026-08-18**: the decision changed (`docs/adr/0012`
      supersedes `docs/adr/0006`'s lakeFS plan). lakeFS Cloud isn't free at
      this scale and self-hosting lakeFS OSS is a service to run for no
      strong reason here; the lakehouse is plain immutable Parquet on Tigris
      instead, with point-in-time/immutability enforced in application code.
- [x] Create the object-storage bucket (Fly Tigris) that the lakehouse lives
      on, and generate its access credentials.
      **Done 2026-08-18**: bucket `tail-lab-lake` provisioned on Fly Tigris
      (endpoint `https://fly.storage.tigris.dev`, region `auto`). Credentials
      are staged as Fly secrets on the `tail-lab` app for prod and in a
      gitignored `.env` at the repo root for local dev
      (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL_S3`,
      `AWS_REGION`, `TAIL_LAB_S3_BUCKET`, `TAIL_LAB_LAKE_BACKEND=tigris`).
- [x] Deploy the Fly.io app for the API/dashboard. **Done 2026-08-18** —
      live at https://tail-lab.fly.dev on the Tigris backend (SPA + `/api/*`,
      health-checked). The lake is seeded with VIX; the dashboard renders a
      real z-score. Redeploy with `flyctl deploy -a tail-lab --remote-only`.
      (The daily *agent* still never deploys — deploys stay human/operator.)
      *Superseded 2026-08-19 by `docs/adr/0016`: green `main` now
      auto-deploys via `.github/workflows/deploy.yml` once `FLY_API_TOKEN`
      is provisioned (item below); the agent itself still never deploys.*
- [x] Get a free FRED API key — unblocks the rates (`docs/DATA_CONTRACTS.md`
      #3) and credit (#4) ingestion adapters. **Done — already existed** in
      the `fred-data` MCP config (`~/.claude.json`); reused it and set it as
      the `FRED_API_KEY` repo secret (2026-08-17). For local dev, export
      `FRED_API_KEY` from that same value.

- [ ] Provision `TAIL_LAB_FEEDBACK_TOKEN` (`docs/adr/0014`, in-app feedback):
      generate a random secret and set it in **two** places — a Fly secret
      on the `tail-lab` app (`flyctl secrets set TAIL_LAB_FEEDBACK_TOKEN=...
      -a tail-lab`, so `GET /api/feedback` and the resolve endpoint stop
      404ing in prod) and a GitHub Actions repo secret of the same name +
      value (so `.github/workflows/daily-agent.yml`'s `Run the platform
      agent` step can read it — it's the one new secret the daily agent
      gets, deliberately not FRED/AWS/deploy creds). Until this is set, the
      feedback panel's writes (`POST /api/feedback`) still work — only the
      agent's read/resolve calls no-op (404, treated as "nothing pending").
- [ ] Provision `FLY_API_TOKEN` as a GitHub Actions repo secret
      (`flyctl tokens create deploy -a tail-lab`) so the new CD workflow
      (`.github/workflows/deploy.yml`, `docs/adr/0016`) can auto-deploy
      every green `main` commit. Until set, the workflow no-ops with a
      visible notice. This token lives only in the deploy workflow — the
      daily agent never receives it.

## Data sourcing (research 2026-08-19 — see `docs/DATA_SOURCING.md`)

Phase 1 — free accounts (~30 min total, all $0):

- [ ] Get a free **Tiingo** API key (tiingo.com) → repo secret
      `TIINGO_API_KEY` + local `.env`. Unblocks replacing the throttled
      Yahoo chart endpoint as OHLCV primary (500 unique symbols/month,
      30+ yrs history) and the delisted-name backfill (`docs/adr/0010`).
      **Also unblocks the cross-source price validation Dio asked for**
      (2026-08-20): a true independent second source to reconcile against
      Yahoo. Keyless second sources are now walled (Stooq gates behind a JS
      proof-of-work); until Tiingo, the single-source guard is the bad-tick /
      stale-feed detector in `research/data_quality.py` +
      `GET /api/putlab/data-quality`, which catches print errors without
      flagging real crashes.
- [ ] Create a free **optionsDX** account (optionsdx.com) and download
      the free SPY/SPX/QQQ EOD option-chain zips (2010–2023, bid/ask +
      IV + greeks); drop them somewhere the agent can ingest from (e.g.
      upload to Tigris under `raw-drops/optionsdx/`). Unblocks validating
      a real-quote `OptionPricer` v2 against the BS proxy at $0.
- [ ] Create a free **Alpaca** account (data-only, no funding) → API
      keys as repo secrets. Free historical SIP equity bars (~2016+) and
      OPRA option history (Feb 2024+); second forward-collection source.

Phase 2/3 — paid decisions (small):

- [ ] Subscribe to **Sharadar "Prices"** direct at sharadar.com —
      **$9/mo**, personal license. Closes survivorship bias (15k delisted
      names to Dec 1998), point-in-time S&P 500 constituents, and gives a
      dependable bulk-CSV broad-universe feed in one. Verify at checkout
      the plan includes the SP500 constituents table.
- [ ] Decide the real-quote historical options buy (`docs/adr/0004`,
      supersedes the old "budget for ORATS/CBOE/OptionMetrics" item):
      recommended **ORATS Data API $99/mo for 2–3 months** (~$200–300,
      EOD chains 2007→present, full US universe) — first check their
      bulk-download/fair-use terms; fallback: historicaloptiondata.com
      one-off ($945 5-yr / $1,495 full-history L2 CSVs, 2002+).
- [ ] (When ready to actually trade) open an **IBKR** account via IBKR
      Ireland — execution venue only, not a data source (no broker serves
      expired-option history); US listed options are Hungary-eligible;
      OPRA live data ~$10/mo, commission-waivable.
